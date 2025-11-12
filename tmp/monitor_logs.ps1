Set-Location 'C:\Users\saint\Documents\PROPFIRM'
$start = Get-Date
$durationSeconds = 900 # 15 minutes
$end = $start.AddSeconds($durationSeconds)

$outLog = Get-ChildItem -Path 'artifacts\\live_trading' -Filter 'production_live_*.out.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$errLog = Get-ChildItem -Path 'artifacts\\live_trading' -Filter 'production_live_*.err.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1

$monitorDir = Join-Path 'artifacts\\live_trading' 'monitor'
New-Item -ItemType Directory -Path $monitorDir -Force | Out-Null

if ($outLog) { $outCapture = Join-Path $monitorDir ("capture_out_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss')) } else { $outCapture = Join-Path $monitorDir ("capture_out_missing_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss')) }
if ($errLog) { $errCapture = Join-Path $monitorDir ("capture_err_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss')) } else { $errCapture = Join-Path $monitorDir ("capture_err_missing_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss')) }

Write-Host "Monitoring started. OutLog=$($outLog.FullName) ErrLog=$($errLog.FullName)"
Write-Host "Captures: Out->$outCapture, Err->$errCapture"

# Start background jobs to follow logs
if ($outLog) {
    Start-Job -Name monitorOut -ScriptBlock {
        param($inPath,$outPath)
        Get-Content -Path $inPath -Wait | ForEach-Object { "$(Get-Date -Format o) $_" } | Out-File -FilePath $outPath -Append -Encoding utf8
    } -ArgumentList $outLog.FullName,$outCapture | Out-Null
}
if ($errLog) {
    Start-Job -Name monitorErr -ScriptBlock {
        param($inPath,$outPath)
        Get-Content -Path $inPath -Wait | ForEach-Object { "$(Get-Date -Format o) $_" } | Out-File -FilePath $outPath -Append -Encoding utf8
    } -ArgumentList $errLog.FullName,$errCapture | Out-Null
}

# Sleep until end
$remaining = ($end - (Get-Date)).TotalSeconds
while ($remaining -gt 0) {
    Start-Sleep -Seconds ([Math]::Min(30,[Math]::Max(1,[int]$remaining)))
    $remaining = ($end - (Get-Date)).TotalSeconds
}

# Stop jobs
if (Get-Job -Name monitorOut -ErrorAction SilentlyContinue) { Stop-Job -Name monitorOut -Force -ErrorAction SilentlyContinue; Receive-Job -Name monitorOut -ErrorAction SilentlyContinue | Out-Null; Remove-Job -Name monitorOut -Force -ErrorAction SilentlyContinue }
if (Get-Job -Name monitorErr -ErrorAction SilentlyContinue) { Stop-Job -Name monitorErr -Force -ErrorAction SilentlyContinue; Receive-Job -Name monitorErr -ErrorAction SilentlyContinue | Out-Null; Remove-Job -Name monitorErr -Force -ErrorAction SilentlyContinue }

Write-Host "Monitoring finished. Captures saved to:"
Write-Host $outCapture
Write-Host $errCapture
