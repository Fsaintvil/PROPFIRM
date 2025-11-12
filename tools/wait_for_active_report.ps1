$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot = (Resolve-Path (Join-Path $root '..')).Path
# Polls for active_monitor_report_*.txt in artifacts/live_trading and prints it when found
$art = Join-Path $repoRoot 'artifacts\live_trading'
$timeoutSeconds = 1000
$deadline = (Get-Date).AddSeconds($timeoutSeconds)
Write-Output "Waiting up to ${timeoutSeconds}s for active_monitor_report_*.txt in $art"
$found = $null
while ((Get-Date) -lt $deadline) {
    $found = Get-ChildItem -Path $art -Filter 'active_monitor_report_*.txt' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($found) {
        Write-Output "Report found: $($found.FullName)"
        Write-Output '--- REPORT START ---'
        Get-Content $found.FullName -Raw
        Write-Output '--- REPORT END ---'
        exit 0
    }
    Start-Sleep -Seconds 15
}
Write-Output "Timed out waiting for report after ${timeoutSeconds}s"
exit 2
