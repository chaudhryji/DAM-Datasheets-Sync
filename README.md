# DAM Datasheet Sync

Recurring sync of datasheet PDFs from SharePoint Online into the AMMshare DAM,
keeping every existing long-term link intact. See `CLAUDE.md` for the rules.

## Plan
1. **Analyse SharePoint (read-only)** - full inventory of the PDF library with every column.
2. **Analyse the DAM (read-only)** - folders, files, metadata, existing long-term links.
3. **Compare and agree actions** - reconciliation buckets, mapping rebuilt from data, link-preservation test.
4. **Sync** - upload / in-place replace, metadata, long-term links. Dry run by default.
5. **Master register** - one Excel file with every datasheet, its metadata and long-term link.

## Setup (Windows, PowerShell 7)
```powershell
cd DAM-Datasheets-Sync
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then edit .env
```

## Step 1 - SharePoint inventory
```powershell
# 1. Pull everything (read-only). Opens a browser sign-in; resumable if interrupted.
python src/s1_spo_inventory.py
#    If the browser sign-in fails, use:  python src/s1_spo_inventory.py --device-code

# 2. Build the profile report from what was pulled (offline, re-runnable).
python src/s1_spo_profile.py
```
Outputs land in `data/output/s1/` (git-ignored):

| File | What it is |
|---|---|
| `spo_columns.csv` | Every column defined on the library, including hidden/system ones |
| `spo_items.jsonl` | Raw dump, one item per line (all fields + file facts) |
| `spo_items.csv` | Same, flattened to one row per file, UTF-8 |
| `spo_profile.csv` | Per field: fill rate, distinct values, top values |
| `spo_profile.md` | Readable summary incl. data-quality checks |

Sign-in uses the app registration in `SPO_CLIENT_ID` with delegated `Sites.Read.All`
(Microsoft Graph). It only reads. If the sign-in says the app lacks that permission,
an admin needs to grant it to the app registration.

## Step 2 - DAM inventory (read-only)
Fill `DAM_API_KEY`, `DAM_USER_EMAIL` and `DAM_CONTAINER_ID` in `.env`, then:
```powershell
python src/s2_dam_probe.py       # quick check of the key and record shapes
python src/s2_dam_inventory.py   # full crawl: folders, files, links, metadata (resumable)
```
Outputs land in `data/output/s2/`: `dam_files.csv`, `dam_folders.csv`, `dam_summary.md`.

## Step 3 - Compare (offline)
```powershell
python src/s3_compare.py
```
Joins steps 1 and 2 on the file name and writes `data/output/s3/`: `comparison.csv` (one row per
file with the action), `summary.md` and `plan.jsonl` (input for step 4). Mappings come from
`config/productgroup_folders.csv`, `config/productgroup_overrides.csv` and `config/skip_files.csv`.

## Step 4 - Sync (dry run by default)
First, once: prove on a throwaway file that replacing content keeps the file id and link.
```powershell
python src/s4_link_test.py            # shows what it would do
python src/s4_link_test.py --apply    # runs it in _sync-test/ and cleans up the file
```
Then the sync itself. Without `--apply` nothing is sent; every intended write goes to `logs/audit_<date>.jsonl`.
```powershell
python src/s4_sync.py --folder "Ultrasync/"                     # dry run, one folder
python src/s4_sync.py --folder "Ultrasync/" --apply --limit 5   # pilot: 5 files per phase
python src/s4_sync.py --apply                                   # everything (resumable)
```
Phases run in order `replace`, `upload`, `metadata`, `links` (pick with `--phase`):
- **replace** uploads the new PDF to the file's current DAM path, so the record and its link stay
  the same. It stops at once if the DAM returns a different file id.
- **upload** adds new datasheets to their target folder (`½` in the name, like the existing files).
- **metadata** sets each key to the SharePoint value exactly once; a key stored twice is collapsed.
- **links** creates a long-term link only where none exists.

Nothing is moved, renamed or deleted. Progress is in `data/output/s4/ledger.json`; a rerun skips what is done.

### Files in the wrong folder
The DAM's move endpoint (`POST /api/internal/file/move`) is internal (browser sign-in), so the DAM
team runs the moves. This writes the request bodies for them, in batches of 200:
```powershell
python src/s4_move_list.py                   # unlinked files only
python src/s4_move_list.py --include-linked  # add linked files, once a move test shows links survive
```
Output: `data/output/s4/move_requests.json` and `move_list.csv`. After the moves, re-run steps 2 and 3
before `s4_sync.py`, so replaces go to the files' new paths.

## Step 5 - Master register
```powershell
python src/s5_master_register.py
```
Writes `data/output/s5/datasheet_register.xlsx` (sheets Datasheets, SharePoint twins, DAM only, About)
and the same list as `datasheet_register.csv`. For the final register after a sync, re-run steps 2
and 3 first so every id and link is read back from the DAM.

## Recurring run
`s1_spo_inventory` -> `s2_dam_inventory` -> `s3_compare` -> review `summary.md` ->
`s4_sync --apply` -> `s2_dam_inventory` -> `s3_compare` -> `s5_master_register`.
