"""Step 3: compare SharePoint (step 1 output) with the DAM (step 2 output). Offline, no API calls.

For every datasheet it decides what the sync would have to do, without doing it:
  content   UPLOAD (not in DAM) | REPLACE (size differs) | SAME
  placement OK | MOVE (wrong folder, no link) | KEEP_PROTECTED (wrong folder, has long-term link)
  metadata  list of ds* keys to add or correct
  link      HAS_LINK | CREATE_LINK
DAM files that are not in SharePoint are reported as orphans and never touched here.

Matching: by file name, with SharePoint's literal "%C2%BD" read as "½" and case ignored,
because the DAM stores the real "½" character.

Outputs in data/output/s3/: comparison.csv (one row per file) and summary.md.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402

S1, S2, OUT = env.output_dir("s1"), env.output_dir("s2"), env.output_dir("s3")
CONFIG = env.ROOT / "config"
SPO_NAME = re.compile(r"^.+_.+_[A-Z]{3}_(A4|LTR)\.PDF$", re.I)
DATE_TOLERANCE_S = 2 * 3600  # earlier loads shifted UTC by 1-2 h (CET/CEST); not a real change


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_csv(name: str) -> list[dict]:
    with (CONFIG / name).open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def key(name: str) -> str:
    return name.replace("%C2%BD", "½").casefold()


def t(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def parse_dt(v: str):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def same_value(k: str, expected: str, actual: str) -> bool:
    if k in ("dscreated", "dsmodified"):
        a, b = parse_dt(expected), parse_dt(actual)
        return bool(a and b and abs((a - b).total_seconds()) <= DATE_TOLERANCE_S)
    if k == "dsitemno":  # DAM stores it without leading zeros
        return expected.lstrip("0") == actual.lstrip("0")
    return expected == actual


def main():
    folders = {r["enu_product_group"].casefold(): r for r in read_csv("productgroup_folders.csv")}
    overrides = read_csv("productgroup_overrides.csv")
    skip = {r["file_name"] for r in read_csv("skip_files.csv")}

    spo = [x["fields"] for x in read_jsonl(S1 / "raw_list_items.jsonl")]
    drive = {d["name"]: d for d in read_jsonl(S1 / "raw_drive_items.jsonl")}
    sizes = {n: d.get("size") for n, d in drive.items()}
    dam_files = list(read_jsonl(S2 / "dam_files.jsonl"))

    # Product group per item code, from the ENU row (consistent across languages).
    enu_pg = {}
    for f in spo:
        if t(f.get("LCode")) == "ENU" and t(f.get("Product_x0020_Group")):
            enu_pg.setdefault(t(f.get("Item_x0020_Code")), t(f["Product_x0020_Group"]))

    def target(f):
        code = t(f.get("Item_x0020_Code"))
        pg = enu_pg.get(code)
        if not pg:
            own = t(f.get("Product_x0020_Group"))
            pg = own if own.casefold() in folders else None
        if not pg:
            pg = next((o["enu_product_group"] for o in overrides if code.startswith(o["item_code_prefix"])), None)
        return folders.get(pg.casefold()) if pg else None

    dam_by_key = defaultdict(list)
    for d in dam_files:
        dam_by_key[key(d["name"])].append(d)

    # Since Sep 2026 the generator writes some files as "%C2%BD" next to the older "½" file
    # with the same name. Both mean the same datasheet: the newest one is the source.
    by_key = defaultdict(list)
    for f in spo:
        if f["FileLeafRef"] not in skip:
            by_key[key(f["FileLeafRef"])].append(f)
    sources, stale = [], []
    for group in by_key.values():
        group.sort(key=lambda f: drive.get(f["FileLeafRef"], {}).get("lastModifiedDateTime", ""), reverse=True)
        sources.append(group[0])
        stale += [(g, group[0]["FileLeafRef"]) for g in group[1:]]

    rows, unmapped_pg = [], Counter()
    seen_keys = set()
    for f in sources:
        name = f["FileLeafRef"]
        k = key(name)
        seen_keys.add(k)
        tgt = target(f)
        if not tgt:
            unmapped_pg[t(f.get("Product_x0020_Group")) or "(blank)"] += 1
        expected = {
            "assetid": t(f.get("Item_x0020_Code")), "dsitemcode": t(f.get("Item_x0020_Code")),
            "dsitemdescription": t(f.get("Item_x0020_Description")), "dsitemno": t(f.get("Item_x0020_No")),
            "dslcode": t(f.get("LCode")), "dslmlid": t(f.get("LMLID")), "dslanguage": t(f.get("LDescription")),
            "dstypecode": t(f.get("TypeCode")), "dstemplate": t(f.get("Template")),
            "dspageformat": t(f.get("Pageformat")), "dsproofread": t(f.get("Proofread")),
            "dsid": t(f.get("id")), "dscreated": t(f.get("Created")), "dsmodified": t(f.get("Modified")),
            "dsproductgroup": tgt["dsproductgroup"] if tgt else "",
            "dssubproductgroup": tgt["dssubproductgroup"] if tgt else "",
        }
        matches = dam_by_key.get(k, [])
        # Prefer the copy that has a link, then the one already in the right folder.
        matches = sorted(matches, key=lambda d: (not d["link_urls"], d.get("folderPath") != (tgt or {}).get("dam_folder")))
        d = matches[0] if matches else None
        row = {"file_name": name, "dam_name": d["name"] if d else "", "item_code": expected["dsitemcode"],
               "lcode": expected["dslcode"], "target_folder": tgt["dam_folder"] if tgt else "",
               "spo_size": sizes.get(name), "spo_modified": expected["dsmodified"],
               "dam_id": d["id"] if d else "", "dam_folder": d.get("folderPath", "") if d else "",
               "dam_size": d.get("sizeBytes") if d else "", "long_term_link": d["link_urls"][0] if d and d["link_urls"] else "",
               "dam_copies": len(matches),
               "spo_older_twin": next((g["FileLeafRef"] for g, src in stale if src == name), "")}
        if not d:
            row.update(content="UPLOAD", placement="NEW", link="CREATE_LINK",
                       metadata_to_set=";".join(k2 for k2, v in expected.items() if v))
        else:
            row["content"] = "SAME" if sizes.get(name) == d.get("sizeBytes") else "REPLACE"
            if not tgt or d.get("folderPath") == tgt["dam_folder"]:
                row["placement"] = "OK"
            else:
                row["placement"] = "KEEP_PROTECTED" if d["link_urls"] else "MOVE"
            row["link"] = "HAS_LINK" if d["link_urls"] else "CREATE_LINK"
            md = d.get("metadata") or {}
            row["metadata_to_set"] = ";".join(k2 for k2, v in expected.items()
                                              if v and not same_value(k2, v, t(md.get(k2))))
            row["dup_metadata_keys"] = ";".join(d.get("dup_meta_keys") or [])
        rows.append(row)

    orphans = []
    for k, ds in dam_by_key.items():
        if k in seen_keys:
            continue
        for d in ds:
            orphans.append({"file_name": d["name"], "dam_id": d["id"], "dam_folder": d.get("folderPath"),
                            "dam_size": d.get("sizeBytes"), "long_term_link": d["link_urls"][0] if d["link_urls"] else "",
                            "content": "ORPHAN", "placement": "", "link": "HAS_LINK" if d["link_urls"] else "",
                            "origin": "SPO-style name" if SPO_NAME.match(d["name"]) else "not from SPO generator"})

    twins = [{"file_name": g["FileLeafRef"], "content": "SKIP_OLDER_TWIN", "spo_older_twin": src,
              "spo_size": sizes.get(g["FileLeafRef"])} for g, src in stale]

    cols = ["file_name", "dam_name", "item_code", "lcode", "content", "placement", "link", "metadata_to_set",
            "dup_metadata_keys", "target_folder", "dam_folder", "spo_size", "dam_size", "spo_modified",
            "dam_id", "dam_copies", "long_term_link", "spo_older_twin", "origin"]
    with (OUT / "comparison.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows + twins + orphans)

    # ---- summary ---------------------------------------------------------------
    c = Counter(r["content"] for r in rows)
    p = Counter(r["placement"] for r in rows)
    lk = Counter(r["link"] for r in rows)
    in_dam = [r for r in rows if r["dam_id"]]
    meta_keys = Counter(k2 for r in in_dam for k2 in r["metadata_to_set"].split(";") if k2)
    linked_replace = sum(1 for r in rows if r["content"] == "REPLACE" and r["link"] == "HAS_LINK")
    misplaced = Counter((r["dam_folder"], r["target_folder"], r["placement"]) for r in rows
                        if r["placement"] in ("MOVE", "KEEP_PROTECTED"))
    oc = Counter((o["origin"], bool(o["long_term_link"])) for o in orphans)
    uploads = Counter(r["target_folder"] or "(unmapped)" for r in rows if r["content"] == "UPLOAD")
    md = [
        "# Step 3: SharePoint vs DAM comparison\n",
        f"Datasheets in SharePoint (after skipping {len(skip)}): **{len(rows):,}** unique, plus "
        f"{len(stale):,} older '½' copies superseded by a newer '%C2%BD' file of the same name "
        f"({sum(1 for r in rows if '%C2%BD' in r['file_name']):,} '%C2%BD' files in total).\n\n"
        f"Found in DAM: **{len(in_dam):,}**. Not in DAM: **{c['UPLOAD']:,}**.\n",
        "## Content", "| Action | Files | Meaning |", "|---|---|---|",
        f"| UPLOAD | {c['UPLOAD']:,} | Not in the DAM yet |",
        f"| REPLACE | {c['REPLACE']:,} | Size differs, so content changed. {linked_replace:,} of these have a long-term link and must be replaced in place |",
        f"| SAME | {c['SAME']:,} | Same size, no upload needed |",
        "", "## Placement (files already in the DAM)", "| Status | Files |", "|---|---|",
        f"| OK | {p['OK']:,} |", f"| MOVE (wrong folder, no link) | {p['MOVE']:,} |",
        f"| KEEP_PROTECTED (wrong folder, has link) | {p['KEEP_PROTECTED']:,} |",
        "", "### Wrong-folder groups", "| In DAM folder | Should be | Status | Files |", "|---|---|---|---|"]
    md += [f"| {a} | {b} | {s} | {n:,} |" for (a, b, s), n in misplaced.most_common()]
    md += ["", "## Long-term links", f"- Already linked: **{lk['HAS_LINK']:,}**",
           f"- Need a new link (incl. uploads): **{lk['CREATE_LINK']:,}**",
           "", "## Metadata corrections on files already in the DAM", "| Key | Files to update |", "|---|---|"]
    md += [f"| {k2} | {n:,} |" for k2, n in meta_keys.most_common()]
    md += [f"\nFiles with a metadata key stored twice: {sum(1 for r in in_dam if r.get('dup_metadata_keys')):,}",
           f"\nSharePoint files with more than one copy in the DAM: {sum(1 for r in rows if r['dam_copies'] > 1):,}",
           "", "## Uploads by target folder", "| Folder | Files |", "|---|---|"]
    md += [f"| {k2} | {n:,} |" for k2, n in uploads.most_common()]
    md += ["", "## DAM files not in SharePoint (orphans, left untouched)", "| Origin | Has link | Files |", "|---|---|---|"]
    md += [f"| {o} | {'yes' if l else 'no'} | {n:,} |" for (o, l), n in oc.most_common()]
    if unmapped_pg:
        md += ["", "## Product groups without a folder mapping",
               ", ".join(f"{k2} ({n:,})" for k2, n in unmapped_pg.most_common())]
    (OUT / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Compared {len(rows):,} SharePoint datasheets with {len(dam_files):,} DAM files -> {OUT}")


if __name__ == "__main__":
    main()
