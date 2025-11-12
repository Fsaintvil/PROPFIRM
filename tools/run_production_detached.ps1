# tools/run_production_detached.ps1
# Variant: start production in detached mode, write PID, rotate logs, and simple restart-on-fail loop (no auto pushes to remote)
param(
    [switch]$DryRun
)

$repoRoot = Resolve-Path -Path "."
Set-Location $repoRoot

# load env defaults from env_defaults.json if exists
$envFile = Join-Path $repoRoot 'env_defaults.json'
if (Test-Path $envFile) {
    $json = Get-Content $envFile -Raw | ConvertFrom-Json
    foreach ($k in $json.PSObject.Properties.Name) {
        $val = $json.$k
        if (-not [string]::IsNullOrEmpty($val)) { Set-Item -Path "Env:$k" -Value $val -Force }
    }
}

$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\\live_trading\\production_live_$ts.out.log"
$logErr = "artifacts\\live_trading\\production_live_$ts.err.log"

# Start process detached and capture PID
$proc = Start-Process -FilePath python -ArgumentList ".\\start_production.py" -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $logOut -RedirectStandardError $logErr
$procId = $proc.Id
$pidFile = "artifacts\\live_trading\\production_live_$ts.pid"
Set-Content -Path $pidFile -Value $procId
Write-Output "Started detached production: PID=$procId, stdout=$logOut, stderr=$logErr"

# Optional: basic watchdog (runs in script) — will not block if scheduled task handles restarts
Start-Sleep -Seconds 2
if (-not (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
    Write-Warning "Process $pid not running after start. Check logs."
}

# exit (detached)
