# Run historical data pull: markets + prices
# Usage: .\scripts\run_historical_data_pull.ps1 [-Markets 100] [-Days 365] [-Prices 100]
# Requires: VPN connected, .env with DATABASE_URL
#
# Supabase: Use DATABASE_POOLER_URL (Transaction mode, port 6543) to avoid "MaxClientsInSessionMode".
# If you hit connection limits, add to .env: DB_POOL_SIZE=2 DATABASE_POOLER_URL=<Transaction string>

param(
    [int]$Markets = 100,
    [int]$Days = 365,
    [int]$Prices = 100
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "=== Historical Data Pull ===" -ForegroundColor Cyan
Write-Host "Markets: $Markets, Days: $Days, Prices: $Prices"
Write-Host ""

# Validate first
Write-Host "Validating pre-conditions..." -ForegroundColor Yellow
$validate = python scripts/run_ingestion_standalone.py --validate-only 2>&1
$validateResult = $validate | ConvertFrom-Json -ErrorAction SilentlyContinue
if (-not $validateResult -or -not $validateResult.success) {
    Write-Host "Validation failed. Ensure VPN is connected and DATABASE_URL is set." -ForegroundColor Red
    exit 1
}
Write-Host "Validation passed." -ForegroundColor Green
Write-Host ""

# Run pull-all
Write-Host "Running pull-all (Phase 1: markets, Phase 2: historical prices)..." -ForegroundColor Yellow
$result = python scripts/run_ingestion_standalone.py --pull-all --markets $Markets --days $Days --prices $Prices 2>&1
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "Pull failed with exit code $exitCode" -ForegroundColor Red
    Write-Host $result
    exit $exitCode
}

$json = $result | Select-String -Pattern '\{.*\}' | ForEach-Object { $_.Matches.Value } | Select-Object -Last 1
if ($json) {
    $parsed = $json | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($parsed) {
        Write-Host ""
        Write-Host "=== Result ===" -ForegroundColor Cyan
        Write-Host "Phase 1 (markets): $($parsed.phase1_count)"
        $p2 = $parsed.phase2_result
        if ($p2 -and $p2.diagnostics) {
            Write-Host "Phase 2 (prices): $($p2.diagnostics.prices_ingested) prices from $($p2.diagnostics.markets_processed) markets"
        }
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
exit 0
