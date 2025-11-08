# Wrapper to run the artifact integrity check (designed for scheduled task)
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = 'python'
$manifest = 'C:\Users\saint\Documents\PROPFIRM\tmp\file_hashes.csv'
$script = 'C:\Users\saint\Documents\PROPFIRM\scripts\check_artifact_integrity.py'
$prefix = 'artifacts/auto_improve'

Write-Output "Running integrity check: $script --manifest $manifest --prefix $prefix"
& $python $script --manifest $manifest --prefix $prefix
if ($LASTEXITCODE -ne 0) {
    Write-Output "Integrity check returned non-zero exit code: $LASTEXITCODE"
    # Optionally: add email/webhook here to alert
}
