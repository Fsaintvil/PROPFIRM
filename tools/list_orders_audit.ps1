# List recent orders_audit_*.json files with timestamps (last 5 days)
$logDir = Join-Path $PSScriptRoot '..\artifacts\live_trading' | Resolve-Path -ErrorAction SilentlyContinue
if (-not $logDir) { $logDir = Join-Path (Get-Location) 'artifacts\live_trading' }
$cutoff = (Get-Date).AddDays(-5)
Get-ChildItem -Path $logDir -Filter 'orders_audit_*.json' -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $cutoff } | Sort-Object LastWriteTime -Descending | ForEach-Object {
    Write-Output "--- $($_.Name) ($($_.LastWriteTime.ToString('o'))) ---"
    try { $first = Get-Content -Path $_.FullName -TotalCount 5 -ErrorAction Stop; $first | ForEach-Object { Write-Output $_ } } catch { Write-Output 'Could not read file (maybe large)'}
}
