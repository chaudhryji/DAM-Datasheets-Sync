<#
Scheduled datasheet sync (weekly, Monday 10:00 local), for Windows Task Scheduler.

  .\scripts\daily_sync.ps1           # dry run: refresh SharePoint + DAM, compare, dry-run s4, rebuild the Excel
  .\scripts\daily_sync.ps1 -Apply    # also write to the DAM, then re-crawl and rebuild the Excel

Every step must succeed before the next one starts. Output goes to logs\daily_<date>.log.
SharePoint sign-in uses the saved token cache; if it has expired, run any step once by hand to sign in again.
#>
param([switch]$Apply)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$python = Join-Path $repo ".venv\Scripts\python.exe"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force (Join-Path $repo "logs") | Out-Null
$log = Join-Path $repo ("logs\daily_{0:yyyyMMdd_HHmm}.log" -f (Get-Date))

function Log([string]$text) { $text | Tee-Object -FilePath $log -Append }

function Step([string]$title, [string[]]$arguments) {
    Log "`n### $title"
    # Every line the script prints goes to the console and the log (a transcript misses some of it).
    $ErrorActionPreference = "Continue"  # a line on stderr (e.g. a retry notice) must not stop the run; the exit code decides
    & $python @arguments 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        Log "STOPPED: '$title' failed (exit $LASTEXITCODE). Later steps were not run."
        exit $LASTEXITCODE
    }
}

Log ("Started {0:yyyy-MM-dd HH:mm} ({1})" -f (Get-Date), $(if ($Apply) { "APPLY" } else { "dry run" }))
$ErrorActionPreference = "Continue"
git pull --ff-only 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
Step "1 SharePoint inventory (read-only)" @("src/s1_spo_inventory.py", "--restart")
Step "2 DAM inventory (read-only)" @("src/s2_dam_inventory.py", "--restart")
Step "3 Compare" @("src/s3_compare.py")
if ($Apply) {
    Step "4 Sync (APPLY)" @("src/s4_sync.py", "--apply", "--workers", "6", "--progress", "500")
    Step "2 DAM re-crawl (read-only)" @("src/s2_dam_inventory.py", "--restart")
    Step "3 Compare again" @("src/s3_compare.py")
} else {
    Step "4 Sync (dry run, nothing is written)" @("src/s4_sync.py", "--workers", "6", "--progress", "1000")
}
Step "5 Master register" @("src/s5_master_register.py")
Log "`nDone. Log: $log"
