Set-Location 'C:\Users\saint\Documents\PROPFIRM'
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$dest = Join-Path -Path 'artifacts' -ChildPath "backup_before_force_start_$ts"
New-Item -ItemType Directory -Path $dest -Force | Out-Null
# copy lock and recent live_trading artifacts
if (Test-Path 'control\production.lock') { Copy-Item 'control\production.lock' -Destination $dest -Force }
Get-ChildItem -Path 'artifacts\live_trading' -Filter 'production_live_*.*' -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName -Destination $dest -Force -ErrorAction SilentlyContinue }
Get-ChildItem -Path 'artifacts\live_trading' -Filter 'production_run_*.*' -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName -Destination $dest -Force -ErrorAction SilentlyContinue }
Copy-Item -Path 'artifacts\live_trading\close_all_positions_result*.json' -Destination $dest -ErrorAction SilentlyContinue

# remove the lock (force) so production can start
if (Test-Path 'control\production.lock') {
    Remove-Item 'control\production.lock' -Force
}

# start production detached with confirmation token
Write-Host "Starting production detached with confirmation token..."
& '.\tools\run_production.ps1' -StartLive -Detached -ConfirmProduction 'I_CONFIRM_ALLOW_MT5_SEND'

# small wait
Start-Sleep -Seconds 2

# show newest pid and tail of logs
$pidfile = Get-ChildItem -Path 'artifacts\live_trading' -Filter 'production_live_*.pid' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($pidfile) { Write-Host "PID file: $($pidfile.FullName)"; Get-Content -Path $pidfile.FullName }
$outlog = Get-ChildItem -Path 'artifacts\live_trading' -Filter 'production_live_*.out.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$errlog = Get-ChildItem -Path 'artifacts\live_trading' -Filter 'production_live_*.err.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($outlog) { Write-Host "--- Last lines of out log: $($outlog.Name) ---"; Get-Content -Path $outlog.FullName -Tail 200 }
if ($errlog) { Write-Host "--- Last lines of err log: $($errlog.Name) ---"; Get-Content -Path $errlog.FullName -Tail 200 }

Write-Host "Backup directory: $dest"
