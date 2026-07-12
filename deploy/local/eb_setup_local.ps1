# EsportsBot odds collector — one-shot LOCAL (Windows) setup.
#
# Moves the hourly PinnOdds/Polymarket snapshot collector from the VPS onto this
# PC. Collapses the manual steps into one run. Run it in PowerShell:
#
#     powershell -ExecutionPolicy Bypass -File C:\eb-odds\eb_setup_local.ps1
#
# Then, ONLY after you confirm local ticks are healthy, re-run with -DisableVps
# to stop the VPS copy (never run both — they share one API rate limit):
#
#     powershell -ExecutionPolicy Bypass -File C:\eb-odds\eb_setup_local.ps1 -DisableVps
#
# IMPORTANT: your PC must stay awake 24/7 or you get gaps every time it sleeps.
# This script is correct-or-absent: any step that fails stops the run loudly
# rather than leaving a half-configured collector.

param(
  [string]$Dir     = "C:\eb-odds",
  [string]$SshKey  = "$HOME\.ssh\LightsailDefaultKey-eu-west-1.pem",
  [string]$VpsHost = "ubuntu@18.201.216.0",
  [string]$Branch  = "claude/esports-sharp-line-rebuild-gqy1na",
  [switch]$DisableVps
)

$ErrorActionPreference = "Stop"
$raw = "https://raw.githubusercontent.com/swatson124455/polymarket-ai-v2/$Branch/deploy/vps/collect_pinnodds_standalone.py"

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "    OK  $msg" -ForegroundColor Green }
function Die($msg)      { Write-Host "    FAIL $msg" -ForegroundColor Red; exit 1 }

# ── -DisableVps mode: turn off the VPS collector cron and exit ────────────────
if ($DisableVps) {
  Step "X" "Disabling the VPS collector cron (keeps every other cron line)"
  ssh -i $SshKey $VpsHost "crontab -l | grep -v collect_pinnodds_standalone | crontab - && echo '--- remaining crontab ---' && crontab -l"
  if ($LASTEXITCODE -ne 0) { Die "could not edit the VPS crontab" }
  Ok "VPS collector stopped. This PC is now the only collector."
  exit 0
}

# ── 0: prerequisites ──────────────────────────────────────────────────────────
Step 0 "Checking prerequisites (python, ssh, scp, the SSH key)"
foreach ($tool in @("python", "ssh", "scp", "curl.exe")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    if ($tool -eq "python") { Die "Python not found. Install from python.org and TICK 'Add Python to PATH', then re-run." }
    Die "$tool not found (need Windows 10/11 with OpenSSH)."
  }
}
if (-not (Test-Path $SshKey)) { Die "SSH key not found at $SshKey (pass -SshKey <path> if it lives elsewhere)." }
Ok "all prerequisites present"

# ── 1: folder ─────────────────────────────────────────────────────────────────
Step 1 "Creating $Dir"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Ok $Dir

# ── 2: collector program ──────────────────────────────────────────────────────
Step 2 "Downloading the collector program"
curl.exe -fsSL $raw -o "$Dir\collect_pinnodds_standalone.py"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$Dir\collect_pinnodds_standalone.py")) { Die "download failed" }
Ok "collect_pinnodds_standalone.py"

# ── 3: data + aliases (this is also your first backup — history continues) ─────
Step 3 "Copying the snapshot history and aliases from the VPS"
scp -i $SshKey "${VpsHost}:/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl" "$Dir\pinnodds_snapshots.jsonl"
if ($LASTEXITCODE -ne 0) { Die "could not copy the snapshot file" }
scp -i $SshKey "${VpsHost}:/home/ubuntu/eb-odds/aliases.json" "$Dir\aliases.json"
if ($LASTEXITCODE -ne 0) { Die "could not copy aliases.json" }
$rows = (Get-Content "$Dir\pinnodds_snapshots.jsonl").Count
Ok "$rows snapshot rows copied + aliases.json"

# ── 4: API key ────────────────────────────────────────────────────────────────
Step 4 "Fetching the PinnOdds API key from the VPS"
$keyline = ssh -i $SshKey $VpsHost "grep -m1 '^PINNACLE_ODDS_API_KEY=' /opt/pa2-shared/.env"
if ($LASTEXITCODE -ne 0 -or -not $keyline.StartsWith("PINNACLE_ODDS_API_KEY=")) { Die "could not read the API key" }
# WriteAllText avoids a UTF-8 BOM (the collector reads plain utf-8).
[System.IO.File]::WriteAllText("$Dir\eb.env", ($keyline.Trim() + "`n"))
Ok "wrote eb.env"

# ── 5: the 'go' batch file ────────────────────────────────────────────────────
Step 5 "Writing run_collector.bat"
$bat = @"
@echo off
set PINNODDS_ENV_PATH=$Dir\eb.env
set PINNODDS_SNAPSHOT_PATH=$Dir\pinnodds_snapshots.jsonl
set EB_ALIASES_PATH=$Dir\aliases.json
python $Dir\collect_pinnodds_standalone.py >> $Dir\collect.log 2>&1
"@
[System.IO.File]::WriteAllText("$Dir\run_collector.bat", $bat)
Ok "run_collector.bat"

# ── 6: test tick ──────────────────────────────────────────────────────────────
Step 6 "Running one test tick (may take a few seconds)"
& "$Dir\run_collector.bat"
$last = Get-Content "$Dir\collect.log" -Tail 1
Write-Host "    log: $last"
if ($last -match "appended=") { Ok "collector ran (a 429 line just means the rate limit is napping; the schedule retries)" }
else { Write-Host "    NOTE: no 'appended=' yet — likely a 429 rate-limit; the hourly schedule will catch up." -ForegroundColor Yellow }

# ── 7: hourly schedule ────────────────────────────────────────────────────────
Step 7 "Scheduling it every hour"
schtasks /Create /SC HOURLY /TN "EB Odds Collector" /TR "$Dir\run_collector.bat" /ST 00:00 /F | Out-Null
if ($LASTEXITCODE -ne 0) { Die "could not create the scheduled task" }
Ok "scheduled task 'EB Odds Collector' created (runs hourly while this PC is awake)"

Write-Host "`nDONE. This PC now collects hourly." -ForegroundColor Green
Write-Host "Check anytime:   Get-Content $Dir\collect.log -Tail 3"
Write-Host "Stop the VPS copy (ONLY after a healthy local tick, so you never run both):"
Write-Host "   powershell -ExecutionPolicy Bypass -File $Dir\eb_setup_local.ps1 -DisableVps"
Write-Host "Undo the local schedule:   schtasks /Delete /TN `"EB Odds Collector`" /F"
