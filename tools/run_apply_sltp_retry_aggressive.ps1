$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Source central environment defaults then enable sends for this apply script
$envFile = Join-Path -Path $root -ChildPath "production_env.ps1"
if (Test-Path $envFile) { . $envFile }
$env:ALLOW_MT5_SEND = '1'
python tools\apply_sltp_retry_aggressive.py
