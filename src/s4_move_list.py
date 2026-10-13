"""Step 4b: the list of DAM files that sit in the wrong folder, ready for the DAM team to move.

The move endpoints (POST /api/internal/file/move {fileIds[], destinationFolderPath}) are internal:
they need a signed-in browser session, not our API key. So this script does not move anything.
It writes the request bodies, grouped by destination and in batches, for the DAM team to run.

  python src/s4_move_list.py                 # files without a long-term link only (safe set)
  python src/s4_move_list.py --include-linked  # also linked files (only after a move test proves links survive)

Writes data/output/s4/move_requests.json (request bodies) and move_list.csv (one row per file).
Run after step 3; re-run steps 2 and 3 after the move to confirm every file landed and kept its link.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import env  # noqa: E402

S3, OUT = env.output_dir("s3"), env.output_dir("s4")
BATCH = 200


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-linked", action="store_true", help="also list files that have a long-term link")
    args = ap.parse_args()

    plan = [json.loads(l) for l in (S3 / "plan.jsonl").open(encoding="utf-8")]
    wanted = {"MOVE", "KEEP_PROTECTED"} if args.include_linked else {"MOVE"}
    rows = [p for p in plan if p["placement"] in wanted and p["dam_id"]]

    groups = defaultdict(list)
    for p in rows:
        groups[(p["dam_folder"], p["target_folder"], bool(p["long_term_link"]))].append(p)

    requests_out = []
    for (src, dst, linked), ps in sorted(groups.items()):
        ids = [p["dam_id"] for p in sorted(ps, key=lambda p: p["file_name"])]
        for i in range(0, len(ids), BATCH):
            requests_out.append({"from": src, "has_long_term_link": linked, "files": len(ids[i:i + BATCH]),
                                 "endpoint": "POST /api/internal/file/move",
                                 "body": {"fileIds": ids[i:i + BATCH], "destinationFolderPath": dst}})
        print(f"{len(ids):>6,}  {src} -> {dst}  ({'with' if linked else 'no'} long-term link)")

    (OUT / "move_requests.json").write_text(json.dumps(requests_out, indent=1, ensure_ascii=False), encoding="utf-8")
    with (OUT / "move_list.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file_name", "dam_file_id", "from_folder", "to_folder", "long_term_link"])
        for p in sorted(rows, key=lambda p: (p["dam_folder"], p["file_name"])):
            w.writerow([p["dam_name"], p["dam_id"], p["dam_folder"], p["target_folder"], p["long_term_link"]])
    print(f"{len(rows):,} files in {len(requests_out)} requests of up to {BATCH} -> {OUT / 'move_requests.json'}")


if __name__ == "__main__":
    main()
