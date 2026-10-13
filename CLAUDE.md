# DAM Datasheet Sync - working rules

Recurring, version-controlled sync of datasheet PDFs from SharePoint Online
(`sites/Datasheets`, library `PDF`) into the AMMshare DAM (Azure Blob based, live).

## Hard rules
1. SharePoint is the source of truth and is READ-ONLY. No writes of any kind.
2. A DAM file with a long-term link is PROTECTED: never delete, rename or move it.
   Only in-place content replace and metadata updates are allowed.
3. Any local OneDrive mirror of the SPO library is read-only (reading bytes only).
4. Secrets live in `.env` (git-ignored). Never commit or print them.
5. Every write stage has `--dry-run`, which is the default until a reviewed go-ahead.
6. Every write is logged to an append-only audit log and a resumable ledger (atomic writes).
7. An empty HTTP 200 from a listing is a warning, never "zero files".
8. Join key is the file name. SPO `Modified` is not a content-change signal.

## Approach
- Mappings and reference files are rebuilt from the data reviewed in this repo,
  not copied from earlier one-off runs.
- Read everything as text first (item codes, dates); normalise explicitly.
- Each stage is its own script in `src/`, idempotent and resumable.
