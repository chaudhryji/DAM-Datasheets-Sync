# DAM Datasheet Sync

Recurring sync of datasheet PDFs from SharePoint Online into the AMMshare DAM,
keeping every existing long-term link intact. See `CLAUDE.md` for the rules.

## Plan
1. **Analyse SharePoint (read-only)** - full inventory of the PDF library with every column. *(this step)*
2. **Analyse the DAM (read-only)** - folders, files, metadata, existing long-term links.
3. **Compare and agree actions** - reconciliation buckets, mapping rebuilt from data, link-preservation test.
4. **Recurring sync + link register** - upload / in-place replace, metadata, long-term links, master register.

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
