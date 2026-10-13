"""Step 2b: read-only inventory of the DAM datasheet container. GET requests only.

1. Crawls every folder from the container root (folders are records with
   mimeType application/folder; a child folder's path is folderPath + name + "/").
2. Sweeps /search page by page, which returns each file's link and metadata in bulk.
3. For any file the search did not return, asks /file/{id}/link and /file/{id}/metadata directly.

Outputs in data/output/s2/:
  dam_folders.csv    every folder with its file count
  dam_files.jsonl    one record per file: DAM fields + link + metadata
  dam_files.csv      the same, flattened (one column per metadata key)
  dam_summary.md     counts, link coverage, metadata coverage, warnings

Folders that are not datasheets (CAD files, drawings, downloads...) are listed but not
crawled unless --include-all is given. Re-running resumes; --restart starts over.
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.dam import DamReader  # noqa: E402

OUT = env.output_dir("s2")
STATE = OUT / "inventory_state.json"
FOLDER_MIME = "application/folder"
NON_DATASHEET_ROOTS = {"2D Drawings", "CAD Files", "Downloads", "Megadynegroup Resources",
                       "Plastic and Steel Chains"}
SEARCH_TERM = ".PDF"
PAGE = 200


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(state: dict):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE)


def crawl(dam: DamReader, container: str, state: dict, include_all: bool):
    c = state.setdefault("crawl", {"queue": [""], "done": [], "folders": [], "empty": [], "skipped": []})
    files_path = OUT / "raw_folder_files.jsonl"
    if not c["done"]:
        files_path.unlink(missing_ok=True)
    while c["queue"]:
        path = c["queue"].pop(0)
        records = dam.get("/folder/files", {"containerId": container, "path": path}) or []
        n_files = 0
        with files_path.open("a", encoding="utf-8") as f:
            for r in records:
                if r.get("mimeType") == FOLDER_MIME:
                    child = f"{r.get('folderPath') or ''}{r['name']}/"
                    c["folders"].append({"path": child, "id": r.get("id"), "createdBy": r.get("createdBy"),
                                         "createdAt": r.get("createdAt")})
                    if path == "" and r["name"] in NON_DATASHEET_ROOTS and not include_all:
                        c["skipped"].append(child)
                    elif child not in c["done"] and child not in c["queue"]:
                        c["queue"].append(child)
                else:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    n_files += 1
        if not records:
            c["empty"].append(path)
            print(f"  WARNING empty response for '{path}' (not treated as 'no files')")
        c["done"].append(path)
        c.setdefault("counts", {})[path] = n_files
        save_state(state)
        print(f"  {path or '(root)'}: {n_files} files, {len(records) - n_files} sub-folders", flush=True)


def sweep_search(dam: DamReader, state: dict):
    s = state.setdefault("search", {"offset": 0, "done": False})
    path = OUT / "raw_search.jsonl"
    if s["offset"] == 0:
        path.unlink(missing_ok=True)
    while not s["done"]:
        page = (dam.get("/search", {"term": SEARCH_TERM, "limit": PAGE, "offset": s["offset"]}) or {})
        results = page.get("results", [])
        with path.open("a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        s["offset"] += len(results)
        s["done"] = len(results) < PAGE
        save_state(state)
        if s["offset"] % 5000 < PAGE:
            print(f"  search: {s['offset']:,} results", flush=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-all", action="store_true", help="also crawl non-datasheet folders")
    ap.add_argument("--restart", action="store_true", help="ignore saved progress")
    args = ap.parse_args()
    if args.restart:
        STATE.unlink(missing_ok=True)

    container = env.get("DAM_CONTAINER_ID")
    dam = DamReader()
    state = load_state()

    print("1/3 Crawling folders")
    crawl(dam, container, state, args.include_all)
    print("2/3 Sweeping search for links and metadata")
    sweep_search(dam, state)

    files = {r["id"]: r for r in read_jsonl(OUT / "raw_folder_files.jsonl")}
    found = {r["id"]: r for r in read_jsonl(OUT / "raw_search.jsonl")}

    print("3/3 Fetching link and metadata for files the search did not return")
    extra_path = OUT / "raw_direct.jsonl"
    direct = {r["id"]: r for r in read_jsonl(extra_path)}
    missing = [fid for fid in files if fid not in found and fid not in direct]
    with extra_path.open("a", encoding="utf-8") as f:
        for i, fid in enumerate(missing, 1):
            links = dam.get(f"/file/{fid}/link", allow_404=True) or []
            meta = dam.get(f"/file/{fid}/metadata", allow_404=True) or []
            rec = {"id": fid, "links": links, "metadata": [{"key": m.get("key"), "value": m.get("value")} for m in meta]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            direct[fid] = rec
            if i % 500 == 0:
                print(f"  direct: {i:,}/{len(missing):,}", flush=True)

    # ---- merge -----------------------------------------------------------------
    merged, meta_keys = [], Counter()
    for fid, r in files.items():
        s, d = found.get(fid), direct.get(fid)
        meta = (s or d or {}).get("metadata") or []
        if s:
            link_urls = [s["link"]] if s.get("link") else []
            source = "search"
        else:
            link_urls = [f"https://dam.ammega.com/api/file/redirect/{l['id']}" for l in (d or {}).get("links", [])
                         if l.get("id") and not l.get("expiresAt")]
            source = "direct"
        md = {}
        for m in meta:
            md.setdefault(m["key"], m["value"])
        meta_keys.update(md.keys())
        merged.append({**r, "link_urls": link_urls, "metadata": md, "link_source": source,
                       "dup_meta_keys": sorted(k for k, n in Counter(m["key"] for m in meta).items() if n > 1)})

    with (OUT / "dam_files.jsonl").open("w", encoding="utf-8") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    keys = sorted(meta_keys)
    with (OUT / "dam_files.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "folderPath", "id", "blobPath", "sizeBytes", "mimeType", "createdAt", "updatedAt",
                    "createdBy", "long_term_link", "link_count", "link_source", "duplicate_metadata_keys"] + keys)
        for m in merged:
            w.writerow([m.get("name"), m.get("folderPath"), m.get("id"), m.get("blobPath"), m.get("sizeBytes"),
                        m.get("mimeType"), m.get("createdAt"), m.get("updatedAt"), m.get("createdBy"),
                        m["link_urls"][0] if m["link_urls"] else "", len(m["link_urls"]), m["link_source"],
                        ";".join(m["dup_meta_keys"])] + [m["metadata"].get(k, "") for k in keys])

    c = state["crawl"]
    with (OUT / "dam_folders.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["path", "files_directly_inside", "crawled", "createdBy", "createdAt"])
        for fo in c["folders"]:
            w.writerow([fo["path"], c.get("counts", {}).get(fo["path"], ""), fo["path"] not in c["skipped"],
                        fo["createdBy"], fo["createdAt"]])

    # ---- summary ---------------------------------------------------------------
    names = Counter(m.get("name") for m in merged)
    dups = [n for n, k in names.items() if k > 1]
    with_link = sum(1 for m in merged if m["link_urls"])
    by_folder = Counter((m.get("folderPath") or "(root)") for m in merged)
    in_search_not_crawl = [r["name"] for fid, r in found.items() if fid not in files]
    md = ["# DAM datasheet container inventory\n",
          f"- Files found by folder crawl: **{len(merged):,}**   folders: {len(c['folders'])} "
          f"(not crawled: {', '.join(c['skipped']) or 'none'})",
          f"- Total size: {sum(m.get('sizeBytes') or 0 for m in merged) / 1e9:.2f} GB",
          f"- Files with a long-term link: **{with_link:,}**   without: {len(merged) - with_link:,}",
          f"- Link/metadata source: search {sum(m['link_source'] == 'search' for m in merged):,}, "
          f"direct {sum(m['link_source'] == 'direct' for m in merged):,}",
          f"- Same file name in more than one folder: {len(dups):,}" + (f" e.g. {dups[:5]}" if dups else ""),
          f"- Files with a metadata key stored twice: {sum(1 for m in merged if m['dup_meta_keys']):,}",
          f"- Search results outside the crawled folders: {len(in_search_not_crawl):,}"
          + (f" e.g. {in_search_not_crawl[:5]}" if in_search_not_crawl else ""),
          f"- Empty folder responses (warnings): {', '.join(p or '(root)' for p in c['empty']) or 'none'}",
          f"- MIME types: {', '.join(f'{k} ({n:,})' for k, n in Counter(m.get('mimeType') for m in merged).most_common())}",
          "", "## Files per folder", "| Folder | Files | With link |", "|---|---|---|"]
    for folder, n in sorted(by_folder.items()):
        nl = sum(1 for m in merged if (m.get("folderPath") or "(root)") == folder and m["link_urls"])
        md.append(f"| {folder} | {n:,} | {nl:,} |")
    md += ["", "## Metadata coverage", "| Key | Files |", "|---|---|"]
    md += [f"| {k} | {n:,} |" for k, n in meta_keys.most_common()]
    (OUT / "dam_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Done: {len(merged):,} files, {with_link:,} with long-term links -> {OUT}. Nothing was changed in the DAM.")


if __name__ == "__main__":
    main()
