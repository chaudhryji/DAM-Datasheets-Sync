"""Step 4: bring the DAM in line with SharePoint, using the step 3 plan. DRY-RUN BY DEFAULT.

Phases (run in this order; each is resumable and can be run on its own with --phase):
  relocate  files in the wrong folder that have NO long-term link: upload the SharePoint PDF to the
            target folder, set all metadata on the new record, check it, then delete the old record.
            The old record is re-checked live and kept if it has a link. Linked files are never moved.
  replace   changed content: upload the SharePoint PDF to the file's CURRENT DAM path, so the
            same record (and its long-term link) is updated in place. Stops if the id changes.
  upload    new datasheets: upload to the target folder.
  metadata  set every ds* key to the SharePoint value, exactly once per key (no duplicates).
  links     create a long-term link where none exists; existing links are never touched.

Not done here: moving linked files (they keep their place; their metadata is corrected) and
deleting anything other than the old copy of a relocated, unlinked file.

  python src/s4_sync.py                              # dry run, all phases, all files
  python src/s4_sync.py --folder "Ultrasync/"        # dry run for one folder (pilot)
  python src/s4_sync.py --folder "Ultrasync/" --apply --limit 5
  python src/s4_sync.py --phase relocate --folder "AMMdrive/" --apply --limit 1   # one-file move test
Every action (dry or real) is written to logs/audit_<date>.jsonl; progress to data/output/s4/ledger.json.
Two windows at once: give each its own folder and --ledger name; every run reads all ledger files.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402
from common.dam_writer import PROTECTED_ROOTS, DamWriter  # noqa: E402

S1, S3, OUT = env.output_dir("s1"), env.output_dir("s3"), env.output_dir("s4")
LEDGER = OUT / "ledger.json"
PHASES = ["relocate", "replace", "upload", "metadata", "links"]
CHECKPOINT_EVERY = 25


def load_ledger() -> dict:
    """Merge ledger.json and every ledger_<name>.json (written by parallel runs).
    For a file seen in several, the entry with the most finished phases wins."""
    merged = {}
    for f in sorted(OUT.glob("ledger*.json")):
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            if k not in merged or len(v.get("done", [])) > len(merged[k].get("done", [])):
                merged[k] = v
    return merged


def save_ledger(ledger: dict):
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, LEDGER)


def dam_name(row: dict, name_mode: str) -> str:
    if row.get("dam_name"):
        return row["dam_name"]
    return row["file_name"].replace("%C2%BD", "½") if name_mode == "decode" else row["file_name"]


def dam_value(key: str, value: str) -> str:
    """Format a SharePoint value the way the DAM stores it."""
    if key in ("dscreated", "dsmodified"):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if key == "dsitemno":
        return value.lstrip("0") or "0"
    return value


def snapshot_md(p: dict) -> list:
    """Dry run: the DAM metadata as crawled in step 2, shaped like GET /file/:id/metadata,
    so a full dry run needs no per-file API calls. --apply always re-reads live."""
    md = [{"id": f"snap-{k}", "key": k, "value": v} for k, v in (p.get("dam_metadata") or {}).items()]
    md += [{"id": f"snap-{k}-2", "key": k, "value": None} for k in filter(None, p.get("dup_metadata_keys", "").split(";"))]
    return md


def run_phases(phases, plan, args, dam, schema, ledger, graph, drive_id, stats, entry):
    for phase in phases:
        todo = []
        for p in plan:
            e = entry(p)
            if phase in e["done"]:
                continue
            moving = p["placement"] == "MOVE"
            if phase == "relocate" and moving and p["target_folder"]:
                todo.append(p)
            elif phase == "replace" and p["content"] == "REPLACE" and not moving:
                todo.append(p)
            elif phase == "upload" and p["content"] == "UPLOAD" and p["target_folder"]:
                todo.append(p)
            elif phase == "metadata" and not moving and (e["dam_id"] or not args.apply) and (
                    p["content"] == "UPLOAD" or p["metadata_to_set"] or p.get("dup_metadata_keys")):
                todo.append(p)
            elif phase == "links" and p["link"] == "CREATE_LINK" and (e["dam_id"] or not args.apply):
                todo.append(p)
        if args.limit:
            todo = todo[:args.limit]
        print(f"\n== {phase}: {len(todo):,} files")
        t0 = time.monotonic()
        for i, p in enumerate(todo, 1):
            e = entry(p)
            ctx = {"file": p["file_name"], "phase": phase}
            if phase == "relocate":
                old_id, path = p["dam_id"], p["target_folder"] + dam_name(p, args.name_mode)
                if args.apply:
                    live_links = [l for l in (dam.get(f"/file/{old_id}/link", allow_404=True) or []) if not l.get("expiresAt")]
                    if live_links:
                        dam.audit("relocate.skipped_linked", file_id=old_id, **ctx)
                        stats["relocate skipped (has link now)"] += 1
                        continue
                data = graph.download(drive_id, p["spo_drive_item_id"]) if args.apply else b""
                if args.apply and len(data) != p["spo_size"]:
                    raise SystemExit(f"Size mismatch downloading {p['file_name']}: {len(data)} != {p['spo_size']}")
                rec = dam.upload(path, data, **ctx, spo_size=p["spo_size"], moving_from=p["dam_folder"])
                if args.apply:
                    if not rec or rec.get("id") == old_id or rec.get("sizeBytes") != p["spo_size"]:
                        save_ledger(ledger)
                        raise SystemExit(f"STOP: upload of {path} did not give a new record of the right size: {rec}. "
                                         f"Old file {old_id} was NOT deleted.")
                    e.update(dam_id=rec["id"], old_dam_id=old_id, old_folder=p["dam_folder"])
                want = {k: dam_value(k, v) for k, v in p["expected_metadata"].items() if v}
                dam.set_metadata(e["dam_id"], want, schema, current=None if args.apply else [], **ctx)
                dam.delete_file(old_id, links=None if args.apply else [], **ctx)
                if args.apply:
                    e["done"].append("metadata")
            elif phase in ("replace", "upload"):
                folder = p["dam_folder"] if phase == "replace" else p["target_folder"]
                path = folder + dam_name(p, args.name_mode)
                data = graph.download(drive_id, p["spo_drive_item_id"]) if args.apply else b""
                if args.apply and len(data) != p["spo_size"]:
                    raise SystemExit(f"Size mismatch downloading {p['file_name']}: {len(data)} != {p['spo_size']}")
                rec = dam.upload(path, data, **ctx, spo_size=p["spo_size"])
                if args.apply:
                    if phase == "replace" and rec["id"] != p["dam_id"]:
                        dam.audit("replace.id_changed", old=p["dam_id"], new=rec["id"], **ctx)
                        save_ledger(ledger)
                        raise SystemExit(f"STOP: replacing {path} created a new id ({rec['id']}), "
                                         f"expected {p['dam_id']}. Links may not follow. Investigate before continuing.")
                    e["dam_id"] = rec["id"]
            elif phase == "metadata":
                # Existing files: only the keys step 3 flagged (outside tolerance) or stored twice.
                # New uploads: every key.
                keys = None if p["content"] == "UPLOAD" else set(
                    filter(None, (p["metadata_to_set"] + ";" + p.get("dup_metadata_keys", "")).split(";")))
                want = {k: dam_value(k, v) for k, v in p["expected_metadata"].items()
                        if v and (keys is None or k in keys)}
                dam.set_metadata(e["dam_id"], want, schema, current=None if args.apply else snapshot_md(p), **ctx)
            elif phase == "links":
                url = dam.ensure_link(e["dam_id"], links=None if args.apply else [], **ctx)
                if url:
                    e["link"] = url
            stats[phase] += 1
            if args.apply:
                e["done"].append(phase)
                e["ts"] = datetime.now(timezone.utc).isoformat()
                if i % CHECKPOINT_EVERY == 0:
                    save_ledger(ledger)
            if i % args.progress == 0 or i == len(todo):
                rate = i / max(time.monotonic() - t0, 1e-6)
                eta = (len(todo) - i) / rate if rate else 0
                print(f"  {i:,}/{len(todo):,} ({i / len(todo):.0%})  {rate * 60:,.0f} files/min  "
                      f"~{eta / 60:,.0f} min left  last: {p['file_name']}", flush=True)
        if args.apply:
            save_ledger(ledger)



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really write to the DAM (default: dry run)")
    ap.add_argument("--phase", choices=PHASES, action="append", help="run only these phases")
    ap.add_argument("--folder", help="only files whose target or current DAM folder is this, e.g. 'Ultrasync/'")
    ap.add_argument("--progress", type=int, default=10, help="print a progress line every N files (default 10)")
    ap.add_argument("--ledger", help="progress file name for a run in a second window, e.g. 'uni' -> ledger_uni.json. "
                                     "Parallel runs must each use their own name and work on different folders.")
    ap.add_argument("--limit", type=int, help="stop after this many files per phase")
    ap.add_argument("--name-mode", choices=["decode", "keep"], default="decode",
                    help="new uploads: write '%%C2%%BD' as '½' like the existing DAM files (default) or keep as-is")
    args = ap.parse_args()
    phases = args.phase or PHASES
    if args.ledger:
        global LEDGER
        LEDGER = OUT / f"ledger_{''.join(c for c in args.ledger if c.isalnum() or c in '-_')}.json"

    plan = [json.loads(l) for l in (S3 / "plan.jsonl").open(encoding="utf-8")]
    plan = [p for p in plan if not ({p["target_folder"].split("/", 1)[0] + "/", p["dam_folder"].split("/", 1)[0] + "/"}
                                    & PROTECTED_ROOTS)]
    if args.folder:
        plan = [p for p in plan if args.folder in (p["target_folder"], p["dam_folder"])]
    dam = DamWriter(apply=args.apply)
    schema = {s["key"]: s["id"] for s in dam.get("/metadata-schema")}
    ledger = load_ledger() if args.apply else {}
    graph, drive_id = None, None
    if args.apply and {"relocate", "replace", "upload"} & set(phases):
        from common.graph import Graph
        graph = Graph()
        drive_id = json.loads((S1 / "library_info.json").read_text(encoding="utf-8"))["drive"]["id"]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}: {len(plan):,} datasheets in scope, phases {', '.join(phases)}")
    stats = Counter()

    def entry(p):
        return ledger.setdefault(p["file_name"], {"dam_id": p["dam_id"] or None, "done": []})

    try:
        run_phases(phases, plan, args, dam, schema, ledger, graph, drive_id, stats, entry)
    finally:
        if args.apply:
            save_ledger(ledger)  # also on a crash, so a rerun skips what was already done
    print(f"\n{mode} finished: " + ", ".join(f"{k} {n:,}" for k, n in stats.items()))
    print(f"Not moved: {sum(1 for p in plan if p['placement'] == 'KEEP_PROTECTED'):,} linked files kept in their current folder.")
    print(f"Audit log: {dam.audit_path}")
    if not args.apply:
        print("Nothing was changed. Review the audit log, then re-run with --apply.")


if __name__ == "__main__":
    main()
