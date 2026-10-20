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
  Field names follow the DAM schema of 19 Oct 2026 (`src/common/metadata_map.py`: productcode,
  description, language, templatecode, proofread, sourcerecordid, sourcecreated, sourcemodified,
  productfamily, pageformat). DAM-team fields (access level, rights, asset type, ...) are never
  changed on existing files; new uploads copy them from same-item-code files only with `--copy-dam-fields`.
  s4 stops if any of these fields is missing from the live schema.
- **links** creates a long-term link only where none exists.

Nothing is moved, renamed or deleted. Progress is in `data/output/s4/ledger.json`; a rerun skips what is done. After a new step 3 plan, the first `--apply` run moves the old progress files to `data/output/s4/archive/` and starts clean, so files SharePoint changed since are synced again.

### Files in the wrong folder
Unlinked files in the wrong folder are moved by the `relocate` phase (it runs first): upload the
SharePoint PDF to the target folder, set all metadata on the new record, check its size, then delete
the old record. The old record is re-checked live and kept if it has a link. Linked files never move.
```powershell
python src/s4_sync.py --phase relocate --folder "AMMdrive/" --limit 1           # dry run, one file
python src/s4_sync.py --phase relocate --folder "AMMdrive/" --apply --limit 1   # move one file
```
`src/s4_move_list.py` writes the same moves as request bodies for the DAM's internal move endpoint,
in case the DAM team runs them instead.

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

`scripts/daily_sync.ps1` runs this chain and stops at the first failing step (log in `logs/daily_<date>.log`).
Without `-Apply` it is a dry run that writes nothing to the DAM. To schedule it every Monday at 10:00 (the laptop's local time):
```powershell
$a = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -File C:\Scripts\DAM-Datasheets-Sync\scripts\daily_sync.ps1" -WorkingDirectory "C:\Scripts\DAM-Datasheets-Sync"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 10am
$t.StartBoundary = (Get-Date "10:00").ToString("s")   # no UTC offset, so 10:00 stays 10:00 after the clocks change
Register-ScheduledTask -TaskName "DAM datasheet sync" -Action $a -Trigger $t
```
Add ` -Apply` after `daily_sync.ps1` in the argument once the weekly dry runs look routine.
