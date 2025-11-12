$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Source central environment defaults (if present) then enable ALLOW_MT5_SEND for this apply script
$envFile = Join-Path -Path $root -ChildPath "tools\production_env.ps1"
if (Test-Path $envFile) { . $envFile }
$env:ALLOW_MT5_SEND = '1'
python tools\apply_sltp_retry_stops_level.py
