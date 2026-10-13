"""Step 1a: read-only inventory of the SharePoint datasheet library.

Pulls three things into data/output/s1/:
  spo_columns.csv        every column defined on the library (incl. hidden/system)
  raw_list_items.jsonl   every list item with all of its field values
  raw_drive_items.jsonl  every file's facts from the drive (name, size, hashes, dates)

Nothing is written to SharePoint. Interrupted runs resume from the last saved page.
Run src/s1_spo_profile.py afterwards to build the readable reports.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.graph import Graph  # noqa: E402

OUT = env.output_dir("s1")
STATE = OUT / "inventory_state.json"

COLUMN_TYPES = ("text", "choice", "number", "dateTime", "lookup", "boolean", "personOrGroup",
                "currency", "calculated", "hyperlinkOrPicture", "term", "thumbnail",
                "contentApprovalStatus", "geolocation")


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)


def pull_pages(g: Graph, state: dict, key: str, first_url: str, params: dict, out_file: Path):
    """Follow @odata.nextLink pages, appending each item as a JSON line. Resumable."""
    s = state.setdefault(key, {"next": None, "count": 0, "done": False})
    if s["done"]:
        print(f"{key}: already complete ({s['count']} items), skipping")
        return
    if s["next"] is None:
        out_file.unlink(missing_ok=True)
        url, p = first_url, params
    else:
        print(f"{key}: resuming after {s['count']} items")
        url, p = s["next"], None
    while url:
        page = g.get(url, p)
        values = page.get("value", [])
        if not values and s["count"] == 0:
            print(f"WARNING {key}: first page came back empty; check the library/permissions")
        with out_file.open("a", encoding="utf-8") as f:
            for v in values:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        s["count"] += len(values)
        url, p = page.get("@odata.nextLink"), None
        s["next"] = url
        save_state(state)
        print(f"  {key}: {s['count']} items", flush=True)
    s["done"] = True
    save_state(state)


def write_columns(columns: list[dict]):
    path = OUT / "spo_columns.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "displayName", "type", "hidden", "readOnly", "required", "indexed",
                    "columnGroup", "description", "details"])
        for c in columns:
            ctype = next((t for t in COLUMN_TYPES if t in c), "")
            w.writerow([c.get("name"), c.get("displayName"), ctype, c.get("hidden"),
                        c.get("readOnly"), c.get("required"), c.get("indexed"),
                        c.get("columnGroup"), c.get("description"),
                        json.dumps(c.get(ctype), ensure_ascii=False) if ctype else ""])
    print(f"Wrote {len(columns)} column definitions to {path.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device-code", action="store_true", help="sign in with a device code instead of a browser")
    ap.add_argument("--restart", action="store_true", help="ignore saved progress and pull everything again")
    args = ap.parse_args()

    if args.restart:
        STATE.unlink(missing_ok=True)
    state = load_state()
    g = Graph(device_code=args.device_code)

    host, path, lib = env.get("SPO_SITE_HOST"), env.get("SPO_SITE_PATH"), env.get("SPO_LIBRARY")
    site = g.get(f"/sites/{host}:{path}")
    print(f"Site: {site.get('webUrl')}")

    lists = g.get(f"/sites/{site['id']}/lists", {"$select": "id,name,displayName,list,webUrl"})["value"]
    matches = [l for l in lists if lib in (l.get("displayName"), l.get("name"))]
    if len(matches) != 1:
        names = ", ".join(sorted(l.get("displayName", "") for l in lists))
        raise SystemExit(f"Expected one library named '{lib}', found {len(matches)}. Lists on site: {names}")
    lst = matches[0]
    list_base = f"/sites/{site['id']}/lists/{lst['id']}"
    drive = g.get(f"{list_base}/drive", {"$select": "id,name,webUrl,quota"})
    print(f"Library: {lst['displayName']} ({lst['webUrl']})")

    columns = g.get(f"{list_base}/columns")["value"]
    write_columns(columns)
    (OUT / "library_info.json").write_text(json.dumps(
        {"site": {k: site.get(k) for k in ("id", "webUrl", "displayName")},
         "list": lst, "drive": {k: drive.get(k) for k in ("id", "name", "webUrl")}},
        indent=2, ensure_ascii=False))

    pull_pages(g, state, "list_items", f"{list_base}/items",
               {"$expand": "fields", "$top": "999"}, OUT / "raw_list_items.jsonl")
    # Library is flat; the drive pass gives size and content hashes without downloading files.
    # Sub-folders, if any, show up as items with a "folder" facet and are reported by the profile.
    pull_pages(g, state, "drive_items", f"/drives/{drive['id']}/root/children",
               {"$top": "999", "$select": "id,name,size,file,folder,createdDateTime,lastModifiedDateTime,"
                                          "lastModifiedBy,createdBy,parentReference,webUrl,eTag,cTag"},
               OUT / "raw_drive_items.jsonl")
    print("Done. Next: python src/s1_spo_profile.py")


if __name__ == "__main__":
    main()
