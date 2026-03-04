# Polymarket AI V2 — Deploy from Windows to AWS Lightsail Dublin
# Usage: .\deploy\deploy-from-windows.ps1 -VpsIp YOUR_LIGHTSAIL_IP [-KeyFile path\to\key.pem]
#
# Prerequisites:
#   1. Lightsail instance created in Dublin (eu-west-1)
#   2. SSH key downloaded from Lightsail console
#   3. setup-vps.sh already run on the VPS
#
# This script:
#   - Copies the project to the VPS via scp (Windows 10+ has scp built-in)
#   - Excludes junk (venv, __pycache__, .git, model cache)
#   - Prints next-steps to finish deployment

param(
    [Parameter(Mandatory=$true)]
    [string]$VpsIp,

    [Parameter(Mandatory=$false)]
    [string]$KeyFile = "$env:USERPROFILE\.ssh\LightsailDefaultKey-eu-west-1.pem",

    [Parameter(Mandatory=$false)]
    [string]$User = "ubuntu",

    [Parameter(Mandatory=$false)]
    [string]$RemotePath = "/opt/polymarket-ai-v2"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host ""
Write-Host "=== Polymarket AI V2 — Deploy to VPS ===" -ForegroundColor Cyan
Write-Host "Target: $User@$VpsIp:$RemotePath"
Write-Host "Source: $ProjectRoot"
Write-Host ""

# Validate SSH key exists
if (-not (Test-Path $KeyFile)) {
    Write-Host "ERROR: SSH key not found at: $KeyFile" -ForegroundColor Red
    Write-Host ""
    Write-Host "Download your Lightsail SSH key:" -ForegroundColor Yellow
    Write-Host "  1. Go to: https://lightsail.aws.amazon.com/ls/webapp/account/keys"
    Write-Host "  2. Download the eu-west-1 default key"
    Write-Host "  3. Save to: $env:USERPROFILE\.ssh\LightsailDefaultKey-eu-west-1.pem"
    Write-Host ""
    Write-Host "Or specify the path: .\deploy\deploy-from-windows.ps1 -VpsIp $VpsIp -KeyFile path\to\key.pem"
    exit 1
}

# Create exclude list (temp file for rsync-style exclusion via tar)
$ExcludePatterns = @(
    ".git",
    "__pycache__",
    "*.pyc",
    "venv",
    ".venv",
    "node_modules",
    "data/model_cache.pkl",
    ".env"
)

Write-Host "[1/3] Creating deployment archive..." -ForegroundColor Green

# Build tar archive excluding junk (Windows tar supports --exclude)
$TarFile = Join-Path $env:TEMP "polymarket-deploy.tar.gz"
$ExcludeArgs = ($ExcludePatterns | ForEach-Object { "--exclude=$_" }) -join " "

Push-Location $ProjectRoot
try {
    $tarCmd = "tar czf `"$TarFile`" $ExcludeArgs ."
    Invoke-Expression $tarCmd
    $SizeMB = [math]::Round((Get-Item $TarFile).Length / 1MB, 1)
    Write-Host "  Archive created: $SizeMB MB" -ForegroundColor Gray
} finally {
    Pop-Location
}

Write-Host "[2/3] Uploading to VPS..." -ForegroundColor Green

# Upload archive
scp -i $KeyFile -o StrictHostKeyChecking=no $TarFile "${User}@${VpsIp}:/tmp/polymarket-deploy.tar.gz"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: scp failed. Check your IP and SSH key." -ForegroundColor Red
    exit 1
}

Write-Host "[3/3] Extracting on VPS and running recovery..." -ForegroundColor Green

# Extract on VPS, set ownership, run recover-and-start.sh diagnostics (not full start yet)
$sshCmd = @"
sudo mkdir -p $RemotePath && sudo chown polymarket:polymarket $RemotePath && sudo -u polymarket tar xzf /tmp/polymarket-deploy.tar.gz -C $RemotePath && rm /tmp/polymarket-deploy.tar.gz && echo 'DEPLOY_OK'
"@
$result = ssh -i $KeyFile -o StrictHostKeyChecking=no "${User}@${VpsIp}" $sshCmd
if ($result -notmatch "DEPLOY_OK") {
    Write-Host "ERROR: Extraction failed on VPS." -ForegroundColor Red
    exit 1
}

# Clean up local temp
Remove-Item $TarFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DEPLOY COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Code is on the VPS at $RemotePath" -ForegroundColor Gray
Write-Host ""

# ── Quick next-step: configure .env on VPS ──────────────────────
Write-Host "NEXT: Configure .env on VPS (set PG + Redis passwords):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  ssh -i `"$KeyFile`" $User@$VpsIp" -ForegroundColor White
Write-Host "  cp $RemotePath/deploy/env.vps $RemotePath/.env" -ForegroundColor White
Write-Host "  nano $RemotePath/.env  # set VPS_PG_PASSWORD and VPS_REDIS_PASSWORD" -ForegroundColor White
Write-Host ""
Write-Host "THEN: Run the recovery script (installs deps, runs migrations, starts bot):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  sudo bash $RemotePath/deploy/recover-and-start.sh" -ForegroundColor White
Write-Host ""
Write-Host "MONITOR:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  journalctl -u polymarket-ai -f --no-pager" -ForegroundColor White
Write-Host "  http://${VpsIp}:8501   # Dashboard" -ForegroundColor Cyan
Write-Host ""
Write-Host "If this is a FIRST deploy (setup-vps.sh not yet run):" -ForegroundColor Yellow
Write-Host "  sudo bash $RemotePath/deploy/setup-vps.sh" -ForegroundColor White
Write-Host "  # Note the PG + Redis passwords it prints, then:" -ForegroundColor Gray
Write-Host "  sudo bash $RemotePath/deploy/recover-and-start.sh" -ForegroundColor White
Write-Host ""
