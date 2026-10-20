"""Diagnostic, read-only: how the DAM's metadata fields line up with our SharePoint fields.

The DAM team changed the metadata schema on 2026-10-19 (e.g. dsitemcode -> productcode).
This report finds, for every SharePoint field, which DAM field holds the same value and how often,
so the new mapping is built from the data rather than guessed.

  python src/metadata_map_report.py

Needs a fresh step 2 crawl and step 3 plan. Writes data/output/s3/metadata_map_report.md. Writes nothing to the DAM.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.dam import DamReader  # noqa: E402

S2, S3 = env.output_dir("s2"), env.output_dir("s3")


def norm(v) -> str:
    s = str(v or "").strip()
    s = re.sub(r"\.000Z$", "Z", s)  # dates: 2024-12-18T11:45:31.000Z == 2024-12-18T11:45:31Z
    return s.casefold()


def main():
    dam = {}
    with (S2 / "dam_files.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            dam[r["id"]] = r.get("metadata") or {}
    pairs = []
    with (S3 / "plan.jsonl").open(encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            if p.get("dam_id") in dam:
                pairs.append((p["expected_metadata"], dam[p["dam_id"]]))

    schema = DamReader().get("/metadata-schema")
    spo_keys = sorted({k for e, _ in pairs for k in e})
    dam_keys = sorted({k for _, d in pairs for k in d})
    present = Counter(k for _, d in pairs for k in d)

    md = ["# DAM metadata mapping report", "", f"Files compared: {len(pairs):,}", "",
          "## DAM metadata schema", "| key | name | type | other fields |", "|---|---|---|---|"]
    for s in schema:
        other = {k: v for k, v in s.items() if k not in ("id", "key", "name", "type", "createdAt", "updatedAt")}
        md.append(f"| {s.get('key')} | {s.get('name', '')} | {s.get('type', '')} | {json.dumps(other, ensure_ascii=False)[:200]} |")

    md += ["", "## DAM keys present on files", "| key | files |", "|---|---|"]
    md += [f"| {k} | {present[k]:,} |" for k in dam_keys]

    md += ["", "## Best DAM match per SharePoint field (exact, ignoring case and .000Z)",
           "| SharePoint field | files with a value | best DAM key | matches | 2nd DAM key | matches |", "|---|---|---|---|---|---|"]
    value_maps = {}
    for sk in spo_keys:
        hits, n = Counter(), 0
        for e, d in pairs:
            v = norm(e.get(sk))
            if not v:
                continue
            n += 1
            for dk, dv in d.items():
                if norm(dv) == v:
                    hits[dk] += 1
        top = hits.most_common(2) + [("", 0)] * 2
        md.append(f"| {sk} | {n:,} | {top[0][0]} | {top[0][1]:,} | {top[1][0]} | {top[1][1]:,} |")
        if not top[0][1] or top[0][1] < 0.9 * n:
            value_maps[sk] = n

    # For fields without a clean match, show how SharePoint values pair with candidate DAM values.
    candidates = ["language", "proofread", "productcategories", "documenttype", "assettype",
                  "lifecyclestatus", "accesslevel", "rightsstatus", "pageformat"]
    for sk in sorted(value_maps):
        md += ["", f"## Value pairs for {sk} (no clean match)"]
        for dk in [k for k in candidates if k in dam_keys]:
            c = Counter((str(e.get(sk) or ""), str(d.get(dk) or "")) for e, d in pairs if e.get(sk))
            md.append(f"### {sk} -> {dk}: {len(c)} distinct pairs")
            md += [f"- `{a}` -> `{b}`: {k:,}" for (a, b), k in c.most_common(15)]

    md += ["", "## Language codes", "| dslcode | dslanguage | DAM language | files |", "|---|---|---|---|"]
    lang = Counter((e.get("dslcode", ""), e.get("dslanguage", ""), d.get("language", "")) for e, d in pairs)
    md += [f"| {a} | {b} | {c} | {k:,} |" for (a, b, c), k in lang.most_common()]

    out = S3 / "metadata_map_report.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"{len(pairs):,} files compared -> {out}")


if __name__ == "__main__":
    main()
