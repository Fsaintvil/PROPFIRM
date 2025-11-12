$baseDir = 'C:\Users\saint\Documents\PROPFIRM'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupDir = Join-Path -Path $baseDir -ChildPath "artifacts\\backup_before_force_start_$ts"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$lockPath = Join-Path -Path $baseDir -ChildPath 'control\\production.lock'
if (Test-Path $lockPath) {
    Copy-Item $lockPath -Destination $backupDir -Force -ErrorAction SilentlyContinue
    Write-Output "Copied $lockPath to $backupDir"
} else {
    Write-Output "No $lockPath found to backup"
}

$pidPattern = Join-Path -Path $baseDir -ChildPath 'artifacts\\live_trading\\production_live_*.pid'
Get-ChildItem $pidPattern -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        Copy-Item $_.FullName -Destination $backupDir -Force -ErrorAction SilentlyContinue
    } catch {}
}
Write-Output "Copied recent production_live_*.pid to $backupDir (if any)"

if (Test-Path $lockPath) {
    Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
    Write-Output "Removed $lockPath"
} else {
    Write-Output "No lock to remove"
}

# Set env and start detached
$Env:CONFIRM_PRODUCTION = 'I_CONFIRM_ALLOW_MT5_SEND'
$Env:ALLOW_MT5_SEND = '1'
Write-Output "Launching run_production.ps1 -StartLive -Detached -ConfirmProduction 'I_CONFIRM_ALLOW_MT5_SEND'"
& "$baseDir\tools\run_production.ps1" -Detached -StartLive -ConfirmProduction 'I_CONFIRM_ALLOW_MT5_SEND'

Start-Sleep -Seconds 3
$latest = Get-ChildItem (Join-Path $baseDir 'artifacts\\live_trading\\production_live_*.pid') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -ne $latest) {
    $pid = Get-Content $latest.FullName -ErrorAction SilentlyContinue
    Write-Output "PID_FILE=$($latest.FullName)"
    Write-Output "PID=$pid"
    $out = $latest.FullName -replace '\\.pid$','.out.log'
    $err = $latest.FullName -replace '\\.pid$','.err.log'
    if (Test-Path $out) { Write-Output '--- OUT LOG (tail 200) ---'; Get-Content $out -Tail 200 } else { Write-Output 'OUT log not found' }
    if (Test-Path $err) { Write-Output '--- ERR LOG (tail 200) ---'; Get-Content $err -Tail 200 } else { Write-Output 'ERR log not found' }
} else {
    Write-Output 'No PID file found after start'
}

Write-Output "Backup directory: $backupDir"
