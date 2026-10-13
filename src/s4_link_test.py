"""Step 4 pre-check: prove on ONE throwaway file that the DAM keeps a long-term link
when a file's content is replaced, and that metadata is never stored twice.

What it does with --apply (without it, it only prints the plan):
  1. creates folder _sync-test/ and uploads a tiny generated PDF (version 1)
  2. creates a long-term link and checks the link serves version 1
  3. uploads version 2 to the same path and checks: same file id, same link, link serves version 2
  4. sets a metadata key twice and checks it exists exactly once
  5. deletes the test file (the empty _sync-test/ folder stays; remove it in the UI)
No real datasheet is touched. Every call is in logs/audit_<date>.jsonl.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.dam_writer import DamWriter  # noqa: E402

FOLDER = "_sync-test/"


def tiny_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 50 750 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def fetch_link(url: str) -> bytes:
    r = requests.get(url, timeout=60, allow_redirects=True)
    r.raise_for_status()
    return r.content


def check(ok: bool, label: str, results: list):
    results.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}", flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really run the test (default: show plan only)")
    args = ap.parse_args()
    if not args.apply:
        print(__doc__)
        print("Dry run: nothing sent. Add --apply to run the test.")
        return

    dam = DamWriter(apply=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"{FOLDER}sync_test_{stamp}.pdf"
    v1, v2 = tiny_pdf(f"sync test {stamp} version 1"), tiny_pdf(f"sync test {stamp} version 2 (replaced)")
    schema = {s["key"]: s["id"] for s in dam.get("/metadata-schema")}
    results, file_id = [], None
    try:
        print(f"1. Upload version 1 to {path}")
        if not dam.get("/folder", {"containerId": dam.container, "folderPath": FOLDER}, allow_404=True):
            dam._send("POST", "/folder", json={"containerId": dam.container, "folderPath": FOLDER})
        rec1 = dam.upload(path, v1, test=True)
        file_id = rec1["id"]
        check(rec1.get("sizeBytes") == len(v1), f"uploaded, id {file_id}", results)

        print("2. Create long-term link")
        link = dam.ensure_link(file_id, test=True)
        check(bool(link), f"link {link}", results)
        check(fetch_link(link) == v1, "link serves version 1", results)

        print("3. Replace content at the same path")
        rec2 = dam.upload(path, v2, test=True)
        check(rec2["id"] == file_id, "same file id after replace", results)
        links_after = [l for l in dam.get(f"/file/{file_id}/link") if not l.get("expiresAt")]
        check(len(links_after) == 1 and link.endswith(links_after[0]["id"]), "same single long-term link after replace", results)
        check(fetch_link(link) == v2, "link now serves version 2", results)

        print("4. Metadata is never duplicated")
        dam.set_metadata(file_id, {"dsitemcode": "SYNCTEST-1"}, schema, test=True)
        dam.set_metadata(file_id, {"dsitemcode": "SYNCTEST-2"}, schema, test=True)
        md = [m for m in dam.get(f"/file/{file_id}/metadata") if m.get("key") == "dsitemcode"]
        check(len(md) == 1 and md[0].get("value") == "SYNCTEST-2", "dsitemcode stored once with the new value", results)
    finally:
        if file_id:
            print("5. Clean up the test file")
            dam.delete_file(file_id, allow_linked=True, test=True)
    passed = all(ok for ok, _ in results)
    print(f"\n{'ALL CHECKS PASSED' if passed else 'SOME CHECKS FAILED'}: "
          f"{sum(ok for ok, _ in results)}/{len(results)}. Details in {dam.audit_path}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
