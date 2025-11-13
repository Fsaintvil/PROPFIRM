# Inspect production_live logs for LIVE-start markers
$logDir = Join-Path $PSScriptRoot '..\artifacts\live_trading' | Resolve-Path -ErrorAction SilentlyContinue
if (-not $logDir) { $logDir = Join-Path (Get-Location) 'artifacts\live_trading' }
$files = Get-ChildItem -Path $logDir -Filter 'production_live_*.out.log' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 20
foreach ($f in $files) {
    Write-Output "--- $($f.Name) ($($f.LastWriteTime)) ---"
    $matches = Select-String -Path $f.FullName -Pattern 'Starting LIVE production|CONFIRM_PRODUCTION token valid|ALLOW_MT5_SEND=1' -AllMatches -ErrorAction SilentlyContinue
    if ($matches) { foreach ($m in $matches) { Write-Output $m.Line } } else { Write-Output '(no matches)' }
}
