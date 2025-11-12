$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Source central environment defaults then enable sends for this apply script
$envFile = Join-Path -Path $root -ChildPath "production_env.ps1"
if (Test-Path $envFile) { . $envFile }
$env:ALLOW_MT5_SEND = '1'

# First pass: RETRY_X=20, STOPS_MULT=2, mode price_and_market
$env:RETRY_X='20'
$env:STOPS_MULT='2'
$env:RETRY_MODE='price_and_market'
python tools\apply_sltp_retry_aggressive.py

# Second pass: same params but market-only
$env:RETRY_X='20'
$env:STOPS_MULT='2'
$env:RETRY_MODE='market-only'
python tools\apply_sltp_retry_aggressive.py
