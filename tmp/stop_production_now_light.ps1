# Lightweight backup: copy pid files, live logs and monitor folder; create kill-switch; stop PID
$base = 'C:\Users\saint\Documents\PROPFIRM'
$src = Join-Path $base 'artifacts\live_trading'
if (-not (Test-Path $src)) { Write-Output 'ARTIFACTS_MISSING'; exit 2 }
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$dest = Join-Path $src ("backup_before_stop_light_$ts")
New-Item -ItemType Directory -Path $dest -Force | Out-Null
# copy pid files
Get-ChildItem -Path $src -Filter '*.pid' -File -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item -Path $_.FullName -Destination $dest -Force }
# copy recent logs (*.log) in artifacts/live_trading
Get-ChildItem -Path $src -Filter '*.log' -File -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item -Path $_.FullName -Destination $dest -Force }
# copy monitor folder if exists
$monitor = Join-Path $src 'monitor'
if (Test-Path $monitor) { Copy-Item -Path $monitor -Destination $dest -Recurse -Force }
Write-Output "LIGHT_BACKUP_CREATED:$dest"

$control = Join-Path $base 'control'
if (-not (Test-Path $control)) { New-Item -ItemType Directory -Path $control | Out-Null }
$killFile = Join-Path $control 'disable_trading'
New-Item -Path $killFile -ItemType File -Force | Out-Null
Write-Output 'DISABLE_TRADING_CREATED'

$pidToStop = 34368
$p = Get-Process -Id $pidToStop -ErrorAction SilentlyContinue
if ($p) {
    Write-Output "ATTEMPTING_STOP:$pidToStop"
    try {
        Stop-Process -Id $pidToStop -ErrorAction Stop -PassThru | ForEach-Object { Write-Output ("STOPPED:" + $_.Id + " " + $_.Name) }
    } catch {
        Write-Output 'STOP_ERROR'
        Write-Output $_.Exception.Message
    }
} else {
    Write-Output ("NO_PROCESS:$pidToStop")
}

# Re-run PID listing script
& "C:\Users\saint\Documents\PROPFIRM\tmp\list_live_pids.ps1"
Write-Output 'LIGHT_STOP_SCRIPT_DONE'
