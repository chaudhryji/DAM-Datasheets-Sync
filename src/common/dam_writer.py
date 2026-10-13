"""DAM (AMMshare) write client. Every method is a no-op that only logs unless apply=True.

Safety rules enforced here, not left to callers:
  - every intended or real write goes to the append-only audit log (logs/audit_<date>.jsonl)
  - delete_file refuses a file that has a long-term link (re-checked live), unless the caller
    passes allow_linked=True (used only by the throwaway link test)
  - no endpoint for moving or renaming files is used (the move endpoint is internal-only);
    an unlinked file is moved as upload-to-new-folder, verify, then delete the old record
"""
import json
import threading
import time
from datetime import datetime, timezone

import requests

from common import env
from common.dam import DamReader


# Root folders in the datasheet container that do not come from the SharePoint datasheet site
# (uni components etc.). Never written to by this sync.
PROTECTED_ROOTS = {"CAD Files/", "Downloads/", "2D Drawings/", "Megadynegroup Resources/", "Plastic and Steel Chains/"}


class DamWriter(DamReader):
    def __init__(self, apply: bool = False, write_throttle: float = 0.2, **kw):
        super().__init__(**kw)
        self.apply = apply
        self.write_throttle = write_throttle
        self.container = env.get("DAM_CONTAINER_ID")
        logs = env.ROOT / "logs"
        logs.mkdir(exist_ok=True)
        self._audit_lock = threading.Lock()
        self.audit_path = logs / f"audit_{datetime.now(timezone.utc):%Y%m%d}.jsonl"

    # ---- plumbing -------------------------------------------------------------
    def audit(self, action: str, **details):
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "mode": "APPLY" if self.apply else "DRY-RUN",
               "action": action, **details}
        with self._audit_lock, self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _send(self, method: str, path: str, **kw):
        url = self.base + path
        for attempt in range(6):
            time.sleep(self.write_throttle)
            resp = self.session.request(method, url, timeout=self.timeout, **kw)
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  HTTP {resp.status_code} on {method} {path}, retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            if not resp.ok:
                raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content and "json" in resp.headers.get("Content-Type", "") else None
        raise RuntimeError(f"{method} {path} kept failing after retries")

    # ---- files ----------------------------------------------------------------
    def upload(self, path: str, data: bytes, content_type: str = "application/pdf", **ctx) -> dict | None:
        """Upload to container/path. If a file already exists at that path, the DAM updates
        that record in place (same file id), which is how content is replaced.
        A failed step restarts the whole sequence (new reservation, blob PUT, complete): the DAM
        drops the reservation after a failed complete, so retrying complete alone cannot work."""
        if path.split("/", 1)[0] + "/" in PROTECTED_ROOTS:
            raise RuntimeError(f"Refusing to write {path}: that folder is not part of the datasheet sync")
        self.audit("upload", path=path, bytes=len(data), **ctx)
        if not self.apply:
            return None
        last = None
        for attempt in range(4):
            if attempt:
                time.sleep(2 ** attempt)
                self.audit("upload.retry", path=path, attempt=attempt, error=str(last)[:300], **ctx)
                print(f"  retrying upload of {path} ({last})", flush=True)
            try:
                sas = self.get("/file/upload-url", {"containerId": self.container, "path": path})["url"]
                r = requests.put(sas, data=data, timeout=300,
                                 headers={"x-ms-blob-type": "BlockBlob", "Content-Type": content_type})
                if not r.ok:
                    raise RuntimeError(f"blob PUT HTTP {r.status_code} {r.text[:200]}")
                resp = self.session.post(self.base + "/file/upload/complete", timeout=self.timeout,
                                         json={"containerId": self.container, "path": path})
                if not resp.ok:
                    raise RuntimeError(f"complete HTTP {resp.status_code} {resp.text[:200]}")
                rec = resp.json()
            except (RuntimeError, requests.RequestException) as exc:
                last = exc
                continue
            self.audit("upload.done", path=path, file_id=rec.get("id"), size=rec.get("sizeBytes"), **ctx)
            return rec
        raise RuntimeError(f"Upload of {path} failed after retries: {last}")

    def delete_file(self, file_id: str, allow_linked: bool = False, links: list | None = None, **ctx):
        if links is None or self.apply:  # real deletes always re-check live
            links = self.get(f"/file/{file_id}/link", allow_404=True) or []
        if any(not l.get("expiresAt") for l in links) and not allow_linked:
            self.audit("delete.refused_linked", file_id=file_id, **ctx)
            raise RuntimeError(f"Refusing to delete {file_id}: it has a long-term link")
        self.audit("delete", file_id=file_id, **ctx)
        if self.apply:
            try:
                self._send("DELETE", f"/file/{file_id}")
            except RuntimeError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                self.audit("delete.already_gone", file_id=file_id, **ctx)  # e.g. rerun after a crash

    # ---- metadata -------------------------------------------------------------
    def set_metadata(self, file_id: str, values: dict, schema_ids: dict, current: list | None = None, **ctx):
        """Set each key to exactly one value: existing entries for the key are removed first,
        so a key is never stored twice. Keys already holding the right single value are left alone."""
        if current is None or self.apply:  # real writes always decide on the live state
            current = (self.get(f"/file/{file_id}/metadata", allow_404=True) or []) if file_id else []
        by_key = {}
        for m in current:
            by_key.setdefault(m.get("key"), []).append(m)
        for k, v in values.items():
            existing = by_key.get(k, [])
            if len(existing) == 1 and str(existing[0].get("value")) == str(v):
                continue
            if k not in schema_ids:
                self.audit("metadata.unknown_key", file_id=file_id, key=k, **ctx)
                continue
            for m in existing:
                self.audit("metadata.delete", file_id=file_id, key=k, old=m.get("value"), **ctx)
                if self.apply:
                    self._send("DELETE", f"/file/{file_id}/metadata/{m['id']}")
            self.audit("metadata.add", file_id=file_id, key=k, value=v, **ctx)
            if self.apply:
                self._send("POST", f"/file/{file_id}/metadata",
                           json={"key": k, "metadataSchemaId": schema_ids[k], "value": v})

    # ---- links ----------------------------------------------------------------
    def ensure_link(self, file_id: str, links: list | None = None, **ctx) -> str | None:
        """Return the file's long-term link, creating one only if none exists."""
        if links is None or self.apply:  # real writes always check live
            links = (self.get(f"/file/{file_id}/link", allow_404=True) or []) if file_id else []
        permanent = [l for l in links if not l.get("expiresAt")]
        if permanent:
            return f"{self.base}/file/redirect/{permanent[0]['id']}"
        self.audit("link.create", file_id=file_id, **ctx)
        if not self.apply:
            return None
        rec = self._send("POST", f"/file/{file_id}/link") or {}
        return rec.get("redirectUrl")
