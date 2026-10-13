"""DAM (AMMshare) public API client. READ-ONLY: only GET requests are possible here.

Write calls (upload, metadata, links) will live in a separate client added in step 4,
behind --dry-run. Keeping this class GET-only makes steps 2 and 3 safe by construction.
"""
import threading
import time

import requests

from common import env


class DamReader:
    def __init__(self, throttle: float = 0.05, timeout: int = 60):
        self.base = env.get("DAM_API_BASE").rstrip("/")
        self.throttle = throttle
        self.timeout = timeout
        self.headers = {"x-api-key": env.get("DAM_API_KEY"), "Accept": "application/json"}
        email = env.get("DAM_USER_EMAIL", required=False)
        if email:
            self.headers["user-email"] = email
        domain = env.get("DAM_SITE_DOMAIN", required=False)
        if domain:
            self.headers["site-domain"] = domain
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        """One HTTP session per thread, so parallel workers do not share a connection pool."""
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
            self._local.session.headers.update(self.headers)
        return self._local.session

    def get(self, path: str, params: dict | None = None, allow_404: bool = False):
        url = self.base + path
        for attempt in range(6):
            time.sleep(self.throttle)
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  HTTP {resp.status_code} on {path}, retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            if allow_404 and resp.status_code == 404:
                return None
            if not resp.ok:
                raise SystemExit(f"GET {path} {params or ''} -> HTTP {resp.status_code}: {resp.text[:300]}")
            if not resp.content:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "json" not in ctype:
                raise SystemExit(f"GET {path} returned {ctype or 'no content type'}, not JSON. "
                                 f"Check DAM_API_BASE (it should end in /api).")
            return resp.json()
        raise SystemExit(f"GET {path} kept failing after retries")
