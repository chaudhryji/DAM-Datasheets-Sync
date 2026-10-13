"""Step 1b: build readable reports from the raw SharePoint inventory (offline, re-runnable).

Reads data/output/s1/raw_*.jsonl and spo_columns.csv, writes:
  spo_items.csv     one row per file: file facts from the drive + every list field
  spo_profile.csv   per field: fill rate, distinct values, top values
  spo_profile.md    summary and data-quality checks
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402

OUT = env.output_dir("s1")
TOP_N = 15
SCI_NOTATION = re.compile(r"^\d(\.\d+)?E[+-]?\d+$", re.I)
GARBLED = re.compile(r"\?{2,}|�")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"{path.name} not found. Run src/s1_spo_inventory.py first.")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def as_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def main():
    list_items = read_jsonl(OUT / "raw_list_items.jsonl")
    drive_items = read_jsonl(OUT / "raw_drive_items.jsonl")
    columns = {}
    cols_path = OUT / "spo_columns.csv"
    if cols_path.exists():
        with cols_path.open(encoding="utf-8-sig") as f:
            columns = {r["name"]: r for r in csv.DictReader(f)}

    files = [d for d in drive_items if "folder" not in d]
    folders = [d for d in drive_items if "folder" in d]
    drive_by_name = {}
    dup_drive = Counter(d["name"] for d in files)
    for d in files:
        drive_by_name[d["name"]] = d

    # ---- flatten list items -------------------------------------------------
    rows, field_names = [], []
    seen_fields = set()
    for it in list_items:
        fields = it.get("fields", {})
        for k in fields:
            if k not in seen_fields:
                seen_fields.add(k)
                field_names.append(k)
        rows.append(fields)
    file_rows = [r for r in rows if r.get("ContentType") != "Folder" and r.get("FSObjType") not in ("1", 1)]
    name_key = "FileLeafRef" if "FileLeafRef" in seen_fields else "LinkFilename"

    out_cols = ["FileName", "drive_size", "drive_quickXorHash", "drive_sha256", "drive_created",
                "drive_modified", "drive_modifiedBy", "drive_mimeType", "drive_webUrl"] + field_names
    with (OUT / "spo_items.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(out_cols)
        for r in file_rows:
            name = r.get(name_key, "")
            d = drive_by_name.get(name, {})
            hashes = (d.get("file") or {}).get("hashes") or {}
            w.writerow([name, d.get("size", ""), hashes.get("quickXorHash", ""), hashes.get("sha256Hash", ""),
                        d.get("createdDateTime", ""), d.get("lastModifiedDateTime", ""),
                        ((d.get("lastModifiedBy") or {}).get("user") or {}).get("displayName", ""),
                        (d.get("file") or {}).get("mimeType", ""), d.get("webUrl", "")]
                       + [as_text(r.get(k)) for k in field_names])

    # ---- per-field profile ----------------------------------------------------
    total = len(file_rows)
    profile = []
    case_variants, garbled, sci, padded = {}, {}, {}, {}
    for k in field_names:
        vals = [as_text(r.get(k)) for r in file_rows]
        filled = [v for v in vals if v.strip()]
        counts = Counter(filled)
        profile.append({
            "field": k,
            "displayName": columns.get(k, {}).get("displayName", ""),
            "type": columns.get(k, {}).get("type", ""),
            "hidden": columns.get(k, {}).get("hidden", ""),
            "filled": len(filled),
            "fill_pct": round(100 * len(filled) / total, 1) if total else 0,
            "distinct": len(counts),
            "top_values": " | ".join(f"{v[:60]} ({n})" for v, n in counts.most_common(TOP_N)),
        })
        groups = defaultdict(Counter)
        for v, n in counts.items():
            groups[v.strip().casefold()][v] += n
        variants = {g: c for g, c in groups.items() if len(c) > 1}
        if variants:
            case_variants[k] = variants
        n_garbled = sum(n for v, n in counts.items() if GARBLED.search(v))
        if n_garbled:
            garbled[k] = n_garbled
        n_sci = sum(n for v, n in counts.items() if SCI_NOTATION.match(v.strip()))
        if n_sci:
            sci[k] = n_sci
        n_pad = sum(n for v, n in counts.items() if v != v.strip())
        if n_pad:
            padded[k] = n_pad

    with (OUT / "spo_profile.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(profile[0].keys()) if profile else ["field"])
        w.writeheader()
        w.writerows(profile)

    # ---- reconciliation of list vs drive ---------------------------------------
    list_names = Counter(r.get(name_key, "") for r in file_rows)
    only_list = sorted(set(list_names) - set(drive_by_name))
    only_drive = sorted(set(drive_by_name) - set(list_names))
    dup_names = sorted(n for n, c in list_names.items() if c > 1) + sorted(n for n, c in dup_drive.items() if c > 1)
    ci = Counter(n.casefold() for n in list_names)
    ci_dups = sorted(n for n in list_names if ci[n.casefold()] > 1)
    ext = Counter(Path(n).suffix.lower() or "(none)" for n in list_names)
    zero = sum(1 for d in files if not d.get("size"))
    sizes = [d.get("size", 0) for d in files]
    editors = Counter(((d.get("lastModifiedBy") or {}).get("user") or {}).get("displayName", "?") for d in files)
    defined_unused = sorted(n for n in columns if n not in seen_fields and columns[n].get("hidden") != "True")

    md = [f"# SharePoint datasheet library profile\n",
          f"- List items (files): **{total:,}**   folders in list: {len(rows) - total:,}",
          f"- Drive files: **{len(files):,}**   drive folders at root: {len(folders):,}"
          + (f" ({', '.join(d['name'] for d in folders[:10])})" if folders else ""),
          f"- Total size: {sum(sizes) / 1e9:.2f} GB; zero-byte files: {zero:,}",
          f"- File extensions: {', '.join(f'{e} ({n:,})' for e, n in ext.most_common())}",
          f"- Fields returned per item: {len(field_names)}; columns defined on library: {len(columns)}",
          "",
          "## Integrity checks",
          f"- In list but not found in drive root: {len(only_list):,}" + (f" e.g. {only_list[:5]}" if only_list else ""),
          f"- In drive root but not in list: {len(only_drive):,}" + (f" e.g. {only_drive[:5]}" if only_drive else ""),
          f"- Duplicate file names: {len(dup_names):,}" + (f" e.g. {dup_names[:5]}" if dup_names else ""),
          f"- Names that differ only by case: {len(ci_dups):,}" + (f" e.g. {ci_dups[:6]}" if ci_dups else ""),
          f"- Last modified by (drive): {', '.join(f'{e} ({n:,})' for e, n in editors.most_common(5))}",
          "",
          "## Data-quality checks per field",
          "| Check | Field: affected rows |", "|---|---|",
          f"| Garbled characters (`??`, U+FFFD) | {', '.join(f'{k}: {n:,}' for k, n in garbled.items()) or 'none'} |",
          f"| Scientific notation (e.g. `1.6E+11`) | {', '.join(f'{k}: {n:,}' for k, n in sci.items()) or 'none'} |",
          f"| Leading/trailing spaces | {', '.join(f'{k}: {n:,}' for k, n in padded.items()) or 'none'} |",
          "",
          "## Case/spacing variants of the same value",
          ]
    if not case_variants:
        md.append("None found.")
    for k, variants in case_variants.items():
        md.append(f"**{k}** ({len(variants)} groups)")
        for g, c in list(variants.items())[:20]:
            md.append(f"- " + " / ".join(f"`{v}` ({n:,})" for v, n in c.most_common()))
        md.append("")
    md += ["", "## Fields (fill rate, distinct values)", "| Field | Display name | Fill % | Distinct | Top values |",
           "|---|---|---|---|---|"]
    for p in profile:
        md.append(f"| {p['field']} | {p['displayName']} | {p['fill_pct']} | {p['distinct']:,} | "
                  f"{p['top_values'][:200].replace('|', '/')} |")
    if defined_unused:
        md += ["", "## Visible columns defined but never returned/filled", ", ".join(defined_unused)]
    (OUT / "spo_profile.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Profiled {total:,} files across {len(field_names)} fields -> {OUT}")


if __name__ == "__main__":
    main()
