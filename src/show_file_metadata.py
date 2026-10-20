"""Diagnostic, read-only: show one datasheet's metadata from every source we use.

  python src/show_file_metadata.py 161100100720_DS_ENU_A4.PDF

Prints the live DAM metadata (GET /file/:id/metadata), what the step 2 crawl stored (search sweep
or direct read), the raw search record, and what step 3 expects from SharePoint. Writes nothing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.dam import DamReader  # noqa: E402

S2, S3 = env.output_dir("s2"), env.output_dir("s3")


def find(path: Path, name: str, key: str):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        for line in f:
            if name in line:
                r = json.loads(line)
                if r.get(key) == name or name in str(r.get(key, "")):
                    return r
    return None


def show(title, obj):
    print(f"\n=== {title}")
    print(json.dumps(obj, indent=1, ensure_ascii=False)[:4000])


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "161100100720_DS_ENU_A4.PDF"
    rec = find(S2 / "dam_files.jsonl", name, "name")
    if not rec:
        raise SystemExit(f"{name} not found in data/output/s2/dam_files.jsonl")
    fid = rec["id"]
    show("live DAM metadata (GET /file/:id/metadata)", DamReader().get(f"/file/{fid}/metadata"))
    show(f"step 2 stored metadata (source: {rec.get('link_source')})", rec.get("metadata"))
    raw = None
    with (S2 / "raw_search.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if fid in line:
                raw = json.loads(line)
                break
    show("raw search record", raw)
    plan = find(S3 / "plan.jsonl", name, "file_name")
    if plan:
        show("step 3 expected from SharePoint", plan.get("expected_metadata"))
        show("step 3 to write, DAM field names", plan.get("dam_expected"))
        print("\nmetadata_to_set:", plan.get("metadata_to_set"))


if __name__ == "__main__":
    main()
