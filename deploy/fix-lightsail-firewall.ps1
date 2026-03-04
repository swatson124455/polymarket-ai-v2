# Polymarket AI V2 — Fix Lightsail Firewall to restore SSH access
# Root cause: Lightsail instance firewall has port 22 locked to a specific source IP,
# blocking both CLI SSH (from Surfshark VPN) and browser SSH (from AWS proxy IPs).
#
# Usage:
#   .\deploy\fix-lightsail-firewall.ps1 -AccessKey YOUR_KEY -SecretKey YOUR_SECRET
#   .\deploy\fix-lightsail-firewall.ps1   # will prompt for credentials
#
# Requires: pip install awscli  (already installed)

param(
    [string]$AccessKey   = "",
    [string]$SecretKey   = "",
    [string]$Region      = "eu-west-1",
    [string]$Instance    = "LockePicks",
    [string]$VpnIp       = "45.87.212.182"   # current Surfshark IP (auto-detected below)
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Fix Lightsail Firewall ===" -ForegroundColor Cyan
Write-Host "Instance: $Instance  |  Region: $Region"
Write-Host ""

# Auto-detect current public IP (the Surfshark VPN exit IP)
try {
    $detected = (Invoke-WebRequest -Uri "https://api.ipify.org" -TimeoutSec 5).Content.Trim()
    $VpnIp = $detected
    Write-Host "Auto-detected your IP: $VpnIp" -ForegroundColor Green
} catch {
    Write-Host "Could not auto-detect IP — using $VpnIp" -ForegroundColor Yellow
}

# Prompt for credentials if not provided
if (-not $AccessKey) {
    $AccessKey = Read-Host "Enter AWS Access Key ID"
}
if (-not $SecretKey) {
    $secureKey = Read-Host "Enter AWS Secret Access Key" -AsSecureString
    $SecretKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    )
}

# Set AWS credentials in environment
$env:AWS_ACCESS_KEY_ID     = $AccessKey
$env:AWS_SECRET_ACCESS_KEY = $SecretKey
$env:AWS_DEFAULT_REGION    = $Region

Write-Host ""
Write-Host "[1/3] Checking current firewall rules..." -ForegroundColor Green
aws lightsail get-instance-port-states --instance-name $Instance --region $Region 2>&1

Write-Host ""
Write-Host "[2/3] Opening port 22 (SSH) to 0.0.0.0/0 (all IPs including VPN + AWS browser)..." -ForegroundColor Green

# Put-instance-public-ports REPLACES all existing rules.
# Keep port 22, 80, 443, 8501 open from everywhere.
aws lightsail put-instance-public-ports `
    --instance-name $Instance `
    --region $Region `
    --port-infos '[
        {"fromPort":22,"toPort":22,"protocol":"tcp","cidrs":["0.0.0.0/0"]},
        {"fromPort":80,"toPort":80,"protocol":"tcp","cidrs":["0.0.0.0/0"]},
        {"fromPort":443,"toPort":443,"protocol":"tcp","cidrs":["0.0.0.0/0"]},
        {"fromPort":8501,"toPort":8501,"protocol":"tcp","cidrs":["0.0.0.0/0"]}
    ]' 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: AWS CLI command failed. Check credentials and instance name." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[3/3] Verifying new rules..." -ForegroundColor Green
aws lightsail get-instance-port-states --instance-name $Instance --region $Region 2>&1

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  FIREWALL FIXED" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "SSH should now work from your VPN IP ($VpnIp) and from the Lightsail browser console."
Write-Host ""
Write-Host "Test CLI SSH:"
Write-Host "  ssh -i `"$env:USERPROFILE\.ssh\LightsailDefaultKey-eu-west-1.pem`" ubuntu@3.249.183.5" -ForegroundColor White
Write-Host ""
Write-Host "After connecting, deploy the bot:"
Write-Host "  .\deploy\deploy-from-windows.ps1 -VpsIp 3.249.183.5" -ForegroundColor White
Write-Host ""

# Clear credentials from environment
$env:AWS_ACCESS_KEY_ID     = ""
$env:AWS_SECRET_ACCESS_KEY = ""
