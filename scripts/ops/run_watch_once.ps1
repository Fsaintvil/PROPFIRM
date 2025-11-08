<# Wrapper that runs the Python watch script once and appends output to a log.
   Safe, idempotent.
#>
try {
    $python = 'python'
    $script = (Resolve-Path -Path '.\scripts\ops\watch_active_model.py').Path
    $logDir = Join-Path (Get-Location) 'artifacts\reports'
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $logPath = Join-Path $logDir 'watch_active_model.log'

    & $python -u $script --once 2>&1 | Out-File -FilePath $logPath -Append -Encoding utf8
} catch {
    Write-Output "Error running watch script: $_"
 
}
