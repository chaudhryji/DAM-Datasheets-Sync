"""Step 2a: read-only probe of the DAM API. Run this first.

Checks the key works, shows the real shape of the records the API returns, and lists
the metadata schema. Writes data/output/s2/probe.json for review. GET requests only.

  python src/s2_dam_probe.py                    # uses DAM_CONTAINER_ID from .env
  python src/s2_dam_probe.py --search 161100    # also find files by name to locate the container
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.dam import DamReader  # noqa: E402

OUT = env.output_dir("s2")


def show(title, value, n=3):
    print(f"\n== {title}")
    if isinstance(value, list):
        print(f"   {len(value)} records; first {min(n, len(value))}:")
        value = value[:n]
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str)[:3000])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search", default="_DS_ENU_A4", help="search term for /search (min 3 chars)")
    args = ap.parse_args()

    dam = DamReader()
    probe = {}

    probe["metadata_schema"] = dam.get("/metadata-schema")
    show("Metadata schema (GET /metadata-schema)", probe["metadata_schema"], n=50)

    probe["search"] = dam.get("/search", {"term": args.search, "limit": 5})
    show(f"Search '{args.search}' (GET /search)", (probe["search"] or {}).get("results", probe["search"]))

    container = env.get("DAM_CONTAINER_ID", required=False)
    if not container:
        print("\nDAM_CONTAINER_ID is blank: set it in .env and run again to probe folders.")
    else:
        root = dam.get("/folder/files", {"containerId": container, "path": ""})
        probe["root_files"] = root
        show("Root of container (GET /folder/files path='')", root, n=5)
        if not root:
            print("   WARNING: empty response at root. This does not mean the container is empty.")
        first_file = next((r for r in (root or []) if isinstance(r, dict) and r.get("id")), None)
        if first_file:
            fid = first_file["id"]
            probe["sample_metadata"] = dam.get(f"/file/{fid}/metadata")
            probe["sample_links"] = dam.get(f"/file/{fid}/link")
            show(f"Metadata of {first_file.get('name')}", probe["sample_metadata"], n=20)
            show(f"Links of {first_file.get('name')}", probe["sample_links"], n=5)

    path = OUT / "probe.json"
    path.write_text(json.dumps(probe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved {path}. Nothing was changed in the DAM.")


if __name__ == "__main__":
    main()
