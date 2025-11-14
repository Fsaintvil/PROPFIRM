<#
tools/run_production.ps1

Usage:
  - Dry-run (safe): run without parameters -> starts in dry-run mode (ALLOW_MT5_SEND=0)
  - Detached/background: run with -Detached to start the python process in background suitable for scheduling

WARNING: Live sends will trade real capital. Only enable live after explicit human confirmation.
#>
param(
    [switch]$Detached,
    [switch]$StartLive,
    [switch]$Yes
)

$repoRoot = (Resolve-Path -Path "." | Select-Object -ExpandProperty Path)
Set-Location $repoRoot

# Load environment defaults
$jsonEnvFile = Join-Path -Path $repoRoot -ChildPath "docs\production_env_defaults.json"
if (Test-Path $jsonEnvFile) {
    Write-Output "Loading environment defaults from $jsonEnvFile"
    try {
        $defaults = Get-Content -Path $jsonEnvFile -Raw | ConvertFrom-Json
        foreach ($p in $defaults.PSObject.Properties) {
            $name = $p.Name
            $value = [string]$p.Value
            Write-Output "Setting env:$name = $value"
            try { Set-Item -Path "env:$name" -Value $value -ErrorAction Stop } catch { Write-Warning ("Failed to set env:{0} : {1}" -f $name, $_) }
        }
    } catch { Write-Warning "Failed to load defaults: $_" }
} else {
    Write-Warning ("Environment JSON {0} not found - proceeding without central defaults" -f $jsonEnvFile)
}

# Prepare adaptive threshold env
try {
    $ctrlFile = Join-Path -Path $repoRoot -ChildPath 'control\base_confidence_threshold.txt'
    if (Test-Path $ctrlFile) {
        $persisted = (Get-Content -Path $ctrlFile -Raw).Trim()
        if ($persisted) { Write-Output "Applying persisted BASE_CONFIDENCE_THRESHOLD=$persisted"; $env:BASE_CONFIDENCE_THRESHOLD = $persisted }
    }
    if (-not $env:AUTO_THRESHOLD_MODE) { $env:AUTO_THRESHOLD_MODE = '1' }
    Write-Output "AUTO_THRESHOLD_MODE=$($env:AUTO_THRESHOLD_MODE)"
} catch {}

# Pre-launch checks
Write-Output "=== Pre-launch checks ==="
if (-not (Test-Path ".\config\mt5_credentials.env")) { Write-Warning "MT5 credentials file missing: .\config\mt5_credentials.env" } else { Write-Output "MT5 credentials file found." }
if (-not (Test-Path $env:AUDIT_DIR)) { New-Item -ItemType Directory -Path $env:AUDIT_DIR -Force | Out-Null; Write-Output "Created $env:AUDIT_DIR" } else { Write-Output "$env:AUDIT_DIR exists" }
if (Test-Path "control\disable_trading") { Write-Output "Kill-switch present: control\disable_trading" } else { Write-Output "No kill-switch file (control\disable_trading) found." }
if (Test-Path "control\emergency_stop") { Write-Warning "Emergency stop active: control\emergency_stop present" }

# Backup artifacts if requested
if ($env:BACKUP_ARTIFACTS_ON_START -eq "1") {
    $tsb = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $backupDir = Join-Path -Path "artifacts" -ChildPath "backup_$tsb"
    Write-Output "Backing up artifacts to $backupDir ..."
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    Copy-Item -Path "artifacts\live_trading\*" -Destination $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "Backup done."
}

# Paths and filenames
$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\live_trading\production_run_$ts.out.log"
$logErr = "artifacts\live_trading\production_run_$ts.err.log"
$pidFile = "artifacts\live_trading\production_run_$ts.pid"

function Start-ProductionProcess([switch]$DetachedMode) {
    $script:PassYes = $false
    if ($StartLive -or $Yes) { $script:PassYes = $true }
    $pyArgs = '.\start_production.py'
    if ($script:PassYes) { $pyArgs = "$pyArgs --yes" }
    if ($DetachedMode) {
        $startInfo = @{
            FilePath = 'python'
            ArgumentList = $pyArgs
            WorkingDirectory = $repoRoot
            WindowStyle = 'Hidden'
            RedirectStandardOutput = $logOut
            RedirectStandardError = $logErr
            PassThru = $true
        }
        $proc = Start-Process @startInfo
        if ($null -ne $proc) {
            $procId = $proc.Id
            Write-Output "Started detached production (PID=$procId). stdout=$logOut stderr=$logErr"
            Set-Content -Path $pidFile -Value $procId -Encoding utf8
            return $procId
        } else {
            Write-Warning "Failed to start detached process"
            return $null
        }
    } else {
        Start-Process -FilePath python -ArgumentList $pyArgs -WorkingDirectory $repoRoot -NoNewWindow -RedirectStandardOutput $logOut -RedirectStandardError $logErr
        Write-Output "Production process started (interactive). stdout -> $logOut, stderr -> $logErr"
        return $null
    }
}

# Live token handling
if ($StartLive) {
    if ($env:CONFIRM_PRODUCTION -eq 'I_CONFIRM_ALLOW_MT5_SEND') { Write-Output "CONFIRM_PRODUCTION token valid - will start in LIVE mode"; $env:ALLOW_MT5_SEND = '1' }
    else { Write-Warning "CONFIRM_PRODUCTION token not set to expected value - refusing to start live"; if ($env:ALLOW_MT5_SEND -ne '1') { Write-Output "ALLOW_MT5_SEND is not set to '1' - running in DRY-RUN" } }
} else {
    if ($env:ALLOW_MT5_SEND -eq '1') { Write-Warning "Configuration requests ALLOW_MT5_SEND=1 from defaults. Ensure you accept the risk." }
}

# Start
if ($Detached) {
    $startedPid = Start-ProductionProcess -DetachedMode
    if ($startedPid) { Write-Output "PID written to $pidFile"; Write-Output "To stop: Stop-Process -Id $startedPid" }
    Write-Output "Detached start complete."
    $taskExample = "PowerShell.exe -NoProfile -ExecutionPolicy Bypass -File `"$repoRoot\tools\run_production.ps1`" -Detached"
    Write-Output "# Schedule via Task Scheduler (example): $taskExample"
}
else {
    $null = Start-ProductionProcess -DetachedMode:$false
    Write-Output ("Tailing stdout (Ctrl+C to stop): {0}" -f $logOut)
    Write-Output 'To start detached later: powershell -File tools\run_production.ps1 -Detached'
    Get-Content -Path $logOut -Wait -Tail 20
}

# End of script