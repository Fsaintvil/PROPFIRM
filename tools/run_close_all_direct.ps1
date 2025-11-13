Param([switch]$Force)
$ErrorActionPreference = 'Stop'
if ($Force) { New-Item -ItemType File -Path control\force_close_all -Force | Out-Null }
if (-not (Test-Path control\apply_live.confirm)) { Set-Content -Path control\apply_live.confirm -Value 'APPLY LIVE' }
$env:ALLOW_MT5_SEND='1'
Write-Output "[RUN] ALLOW_MT5_SEND=$($env:ALLOW_MT5_SEND) Force=$Force"
if ($Force) { Write-Output '[RUN] force_close_all marker créé.' }
python .\tmp_close_all_direct.py