# Helper to run mt5_enforce_sltp_rr.py with ALLOW_MT5_SEND=1 in this repo
# Run this from an interactive PowerShell session in repo root.
$env:ALLOW_MT5_SEND = '1'
python 'c:/Users/saint/Documents/PROPFIRM/tools/mt5_enforce_sltp_rr.py'
if ($LASTEXITCODE -ne 0) { Write-Error "enforce script exited with $LASTEXITCODE"; exit $LASTEXITCODE }
