"""Step 1a: read-only inventory of the SharePoint datasheet library (SharePoint REST API).

Pulls into data/output/s1/:
  spo_columns.csv        every column defined on the library (incl. hidden/system)
  raw_list_items.jsonl   every list item with all of its field values
  raw_drive_items.jsonl  every file's facts (name, size, dates, editor, url)

Only GET requests are made; nothing is written to SharePoint.
Interrupted runs resume from the last saved page. Then run src/s1_spo_profile.py.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.spo import SharePoint  # noqa: E402

OUT = env.output_dir("s1")
STATE = OUT / "inventory_state.json"
PAGE_SIZE = 2000
EXTRA_SELECT = ("FileLeafRef,FileRef,FileDirRef,FSObjType,UniqueId,Created,Modified,"
                "Author/Title,Author/EMail,Editor/Title,Editor/EMail,"
                "File/Length,File/TimeCreated,File/TimeLastModified,File/ETag,File/UIVersionLabel,"
                "File/ServerRelativeUrl")


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)


def flatten(item: dict) -> dict:
    """Turn expanded lookups (Author/Editor) into plain 'Author.Title' style fields."""
    out = {}
    for k, v in item.items():
        if k == "File":
            continue
        if isinstance(v, dict) and k not in ("odata.type",):
            for sk, sv in v.items():
                out[f"{k}.{sk}"] = sv
        else:
            out[k] = v
    return out


def file_record(item: dict, host: str) -> dict:
    f = item.get("File") or {}
    rec = {
        "name": item.get("FileLeafRef"),
        "size": f.get("Length") and int(f["Length"]),
        "createdDateTime": f.get("TimeCreated"),
        "lastModifiedDateTime": f.get("TimeLastModified"),
        "lastModifiedBy": {"user": {"displayName": (item.get("Editor") or {}).get("Title")}},
        "file": {"mimeType": "", "eTag": f.get("ETag"), "version": f.get("UIVersionLabel")},
        "webUrl": f"https://{host}{f.get('ServerRelativeUrl') or item.get('FileRef', '')}",
        "parentPath": item.get("FileDirRef"),
    }
    if str(item.get("FSObjType")) == "1":
        rec["folder"] = {}
    return rec


def write_columns(fields: list[dict]):
    path = OUT / "spo_columns.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "displayName", "type", "hidden", "readOnly", "required", "indexed",
                    "columnGroup", "description", "details"])
        for c in fields:
            w.writerow([c.get("InternalName"), c.get("Title"), c.get("TypeAsString"), c.get("Hidden"),
                        c.get("ReadOnlyField"), c.get("Required"), c.get("Indexed"), c.get("Group"),
                        c.get("Description"), json.dumps(c.get("Choices"), ensure_ascii=False)
                        if c.get("Choices") else ""])
    print(f"Wrote {len(fields)} column definitions to {path.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device-code", action="store_true", help="sign in with a device code instead of a browser")
    ap.add_argument("--restart", action="store_true", help="ignore saved progress and pull everything again")
    args = ap.parse_args()

    if args.restart:
        STATE.unlink(missing_ok=True)
    state = load_state()
    sp = SharePoint(device_code=args.device_code)
    host, lib = env.get("SPO_SITE_HOST"), env.get("SPO_LIBRARY")

    web = sp.get("/_api/web", {"$select": "Title,Url"})
    print(f"Site: {web.get('Url')}")
    lists = sp.get("/_api/web/lists", {"$select": "Id,Title,EntityTypeName,ItemCount,BaseTemplate,RootFolder/ServerRelativeUrl",
                                       "$expand": "RootFolder"})["value"]
    matches = [l for l in lists if lib in (l.get("Title"), l.get("EntityTypeName"))]
    if len(matches) != 1:
        names = ", ".join(sorted(l.get("Title", "") for l in lists))
        raise SystemExit(f"Expected one library named '{lib}', found {len(matches)}. Lists on site: {names}")
    lst = matches[0]
    base = f"/_api/web/lists(guid'{lst['Id']}')"
    print(f"Library: {lst['Title']} ({lst['ItemCount']:,} items, root {lst['RootFolder']['ServerRelativeUrl']})")
    (OUT / "library_info.json").write_text(json.dumps({"web": web, "list": lst}, indent=2, ensure_ascii=False))

    write_columns(sp.get(f"{base}/fields")["value"])

    s = state.setdefault("items", {"next": None, "count": 0, "done": False})
    if s["done"]:
        print(f"Items already complete ({s['count']:,}). Use --restart to pull again.")
        return
    list_file, drive_file = OUT / "raw_list_items.jsonl", OUT / "raw_drive_items.jsonl"
    if s["next"] is None:
        list_file.unlink(missing_ok=True)
        drive_file.unlink(missing_ok=True)
        url = f"{base}/items"
        params = {"$select": f"*,{EXTRA_SELECT}", "$expand": "File,Author,Editor", "$top": str(PAGE_SIZE)}
    else:
        print(f"Resuming after {s['count']:,} items")
        url, params = s["next"], None
    while url:
        page = sp.get(url, params)
        values = page.get("value", [])
        if not values and s["count"] == 0:
            print("WARNING: first page came back empty; check the library and your permissions")
        with list_file.open("a", encoding="utf-8") as lf, drive_file.open("a", encoding="utf-8") as df:
            for v in values:
                lf.write(json.dumps({"id": v.get("Id"), "fields": flatten(v)}, ensure_ascii=False) + "\n")
                df.write(json.dumps(file_record(v, host), ensure_ascii=False) + "\n")
        s["count"] += len(values)
        url, params = page.get("odata.nextLink"), None
        s["next"] = url
        save_state(state)
        print(f"  {s['count']:,} / {lst['ItemCount']:,} items", flush=True)
    s["done"] = True
    save_state(state)
    print("Done. Next: python src/s1_spo_profile.py")


if __name__ == "__main__":
    main()
