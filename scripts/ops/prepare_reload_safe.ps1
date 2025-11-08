<#
Backup + rename helper for safe reload testing of model files.

This script PREPARES a rollback-safe rename of `best_lightgbm_large.txt`.
It will only perform changes when called with -Execute. By default it prints
the planned actions and creates the backup directory.

Usage (dry-run):
  pwsh -NoProfile -File .\scripts\ops\prepare_reload_safe.ps1

To actually perform the rename (destructive):
  pwsh -NoProfile -File .\scripts\ops\prepare_reload_safe.ps1 -Execute

After performing the rename, restart of the AI component is required to
observe the effect; this script does NOT restart any services.

#>

param(
    [switch]$Execute
)

Set-StrictMode -Version Latest

$root = Resolve-Path -Path '.'
$art = Join-Path $root 'artifacts\auto_improve'
$backupRoot = Join-Path $root 'backups\model_reload_$(Get-Date -Format yyyyMMdd_HHmmss)'

Write-Host "Auto-improve artifacts dir: $art"
Write-Host "Backup root (will be created): $backupRoot"

if (-not (Test-Path $art)) {
    Write-Error "Artifacts directory not found: $art"
    exit 1
}

$large = Join-Path $art 'best_lightgbm_large.txt'
$small = Join-Path $art 'best_lightgbm.txt'

if (-not (Test-Path $large)) {
    Write-Host "No 'best_lightgbm_large.txt' found; nothing to do."; exit 0
}

Write-Host "Planned actions (dry-run):"
Write-Host "  1) Create backup dir: $backupRoot"
Write-Host "  2) Copy '$large' to backup dir"
Write-Host "  3) Rename '$large' -> 'best_lightgbm_large.txt.disabled'"
Write-Host "  4) Wait for process restart or perform controlled restart (NOT performed by this script)"
Write-Host "  5) To rollback: move backup copy back to original name"

if (-not $Execute) {
    Write-Host "Dry-run only. To execute the actions, re-run with -Execute"
    exit 0
}

# Execute changes
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
Copy-Item -Path $large -Destination $backupRoot -Force

$disabled = Join-Path $art 'best_lightgbm_large.txt.disabled'
if (Test-Path $disabled) {
    Write-Host "Found existing disabled file '$disabled' — aborting to avoid overwrite."; exit 1
}

Rename-Item -Path $large -NewName 'best_lightgbm_large.txt.disabled'

Write-Host "Rename complete. The 'large' file is disabled. Please perform a controlled restart of the AI component now to force model selection."
Write-Host "To rollback: Move the file back from '$backupRoot' into '$art' and restart the AI component."
