param(
    [switch]$Force
)

Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Source central environment defaults if present
$envFile = Join-Path -Path $root -ChildPath "tools\production_env.ps1"
if (Test-Path $envFile) {
    Write-Output "Sourcing environment defaults from $envFile"
    . $envFile
} else {
    Write-Warning "Environment file $envFile not found — continuing with script-local overrides"
}

# ======================================================
# run_live_production.ps1
# Wrapper pour lancer start_production.py
# Env vars requested by operator
# ======================================================
$env:LIVE_ENGINE_LIGHT_MODE = "0"
$env:CONFIRME_DEPLACEMENT = "YES_I_CONFIRM"
$env:ALLOW_MT5_SEND = '1'
$env:AUTO_APPLY = '1'
$env:AUTO_DEPLOY = '1'
$env:AUTO_LEARN = '1'
$env:AUTO_ADAPT = '1'
$env:AUTO_ENRICH = '1'
$env:PYTHONPATH='.'
$env:AI_AUTOMATE='1'
$env:AI_VOLUME='0.01'
$env:ACTIVATION_MODE='FULL'
$env:META_LEARNING_TRADING_SYSTEM='1'
$env:REINFORCEMENT_LEARNING_TRADING_SYSTEM='1'
$env:MULTI_ASSET_PORTFOLIO_OPTIMIZER='1'
$env:INIT_ALL_AI='1'

Write-Host "🚀 Lancement LIVE Production PROPFIRM"
python start_production.py

$env:SYMBOLS = 'BTCUSD, ETHUSD, XAUUSD, USDCAD, AUDNZD, EURJPY, GBPCHF, NZDJPY, EURUSD, EURAUD, US500.cash, JP225.cash'

Write-Host "Prepared environment variables for LIVE run." -ForegroundColor Yellow

$confirmFile = Join-Path -Path (Join-Path $root '..') -ChildPath 'control\apply_live.confirm'
$autoConfirmFile = Join-Path -Path (Join-Path $root '..') -ChildPath 'control\apply_live.auto.confirm'

if (-not (Test-Path $confirmFile)) {
    Write-Host "Missing apply_live.confirm -> create 'control/apply_live.confirm' with the line 'APPLY LIVE' to enable real sends." -ForegroundColor Red
}
if (-not (Test-Path $autoConfirmFile)) {
    Write-Host "Missing apply_live.auto.confirm -> create 'control/apply_live.auto.confirm' with the line 'APPLY LIVE AUTO' to enable AI auto sends." -ForegroundColor Red
}

if ($Force -and (Test-Path $confirmFile) -and (Test-Path $autoConfirmFile)) {
    Write-Host "FORCE specified and confirmations present -> launching start_production.py in live mode" -ForegroundColor Green
    # Launch start_production with enforced env; operator chose to force.
    $py = "python"
    & $py (Join-Path -Path (Join-Path $root '..') -ChildPath 'start_production.py') --force --symbols $env:SYMBOLS
} else {
    Write-Host "Not launching start_production. To actually run in LIVE pass -Force and ensure both confirmation files exist." -ForegroundColor Yellow
    Write-Host "Example (PowerShell Admin):" -ForegroundColor Cyan
    Write-Host "  .\tools\run_live_production.ps1 -Force" -ForegroundColor Cyan
}
