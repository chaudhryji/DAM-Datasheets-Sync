<#
Daily datasheet sync, for Windows Task Scheduler.

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
$log = Join-Path $repo ("logs\daily_{0:yyyyMMdd_HHmm}.log" -f (Get-Date))
New-Item -ItemType Directory -Force (Join-Path $repo "logs") | Out-Null
Start-Transcript -Path $log | Out-Null

function Step([string]$title, [string[]]$arguments) {
    Write-Host "`n### $title"
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "STOPPED: '$title' failed (exit $LASTEXITCODE). Later steps were not run."
        exit $LASTEXITCODE
    }
}

try {
    git pull --ff-only
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
    Write-Host "`nDone. Log: $log"
} finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
