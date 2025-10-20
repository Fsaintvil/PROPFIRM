param(
    [string]$RepoRoot = $PSScriptRoot,
    [string]$CircuitBreakerRelative = 'MT5_FTMO_IA/control/circuit_breaker.json',
    [string]$ArchiveRelative = 'MT5_FTMO_IA/control/archive',
    [int]$PruneDays = 30,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
try {
    $scriptPath = $PSScriptRoot
    if (-not $scriptPath) { $scriptPath = (Get-Location).Path }
    $root = Resolve-Path -Path $RepoRoot
    $cbPath = Join-Path $root $CircuitBreakerRelative
    $archiveDir = Join-Path $root $ArchiveRelative
    if (-not (Test-Path $cbPath)) {
        Write-Output "[backup_cb] circuit_breaker not found at $cbPath"
        exit 0
    }
    if (-not (Test-Path $archiveDir)) { New-Item -ItemType Directory -Path $archiveDir | Out-Null }
    $stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $dest = Join-Path $archiveDir "circuit_breaker_$stamp.json"
    if ($DryRun) {
        Write-Output "[backup_cb] DRYRUN: would copy $cbPath -> $dest"
    } else {
        Copy-Item -Path $cbPath -Destination $dest -Force
        Write-Output "[backup_cb] Archived $cbPath -> $dest"
    }

    # Prune old archives
    try {
        $cutoff = (Get-Date).AddDays(-1 * $PruneDays)
        Get-ChildItem -Path $archiveDir -Filter 'circuit_breaker_*.json' | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
            if ($DryRun) { Write-Output "[backup_cb] DRYRUN: would remove $_.FullName" } else { Remove-Item -Path $_.FullName -Force }
        }
    }
    catch {
        Write-Output "[backup_cb] Prune error: $_"
    }
}
catch {
    Write-Output "[backup_cb] Error: $_"
    exit 1
}
