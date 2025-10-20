param(
    [int]$IntervalHours = 1,
    [switch]$RunOnce
)

Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
$script = Join-Path $RepoRoot 'ops' 'backup_circuit_breaker.ps1'
if (-not (Test-Path $script)) { Write-Output "backup script not found: $script"; exit 1 }

Write-Output "Starting circuit_breaker backup loop. IntervalHours=$IntervalHours RunOnce=$RunOnce"
do {
    & $script -RepoRoot $RepoRoot -PruneDays 90
    if ($RunOnce) { break }
    Start-Sleep -Seconds ($IntervalHours * 3600)
} while ($true)
