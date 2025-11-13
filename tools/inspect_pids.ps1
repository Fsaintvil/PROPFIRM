# Inspect production_live pid files and test whether the PIDs are still alive
$logDir = Join-Path $PSScriptRoot '..\artifacts\live_trading' | Resolve-Path -ErrorAction SilentlyContinue
if (-not $logDir) { $logDir = Join-Path (Get-Location) 'artifacts\live_trading' }
$pidFiles = Get-ChildItem -Path $logDir -Filter 'production_live_*.pid' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 20
foreach ($f in $pidFiles) {
    Write-Output "--- $($f.Name) (LastWrite: $($f.LastWriteTime.ToString('o'))) ---"
    try {
        $content = Get-Content -Path $f.FullName -ErrorAction Stop | Where-Object { $_ -match '\d+' }
        if ($content -and $content.Length -gt 0) {
                foreach ($line in $content) {
                    $pidVal = [int]$line
                    Write-Output "PID: $pidVal"
                try {
                        $proc = Get-Process -Id $pidVal -ErrorAction Stop
                        Write-Output "Process running: Id=$($proc.Id), Name=$($proc.ProcessName), StartTime=$($proc.StartTime)"
                } catch {
                    Write-Output "Process not running (or insufficient permissions)"
                }
            }
        } else { Write-Output 'PID file empty or no numeric content' }
    } catch { Write-Output 'Could not read PID file' }
}
