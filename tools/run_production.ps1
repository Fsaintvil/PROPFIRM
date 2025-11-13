<#
tools/run_production.ps1

Usage:
  - Dry-run (safe): run without parameters -> starts in dry-run mode (ALLOW_MT5_SEND=0)
  - Detached/background: run with -Detached to start the python process in background suitable for scheduling

WARNING: Live sends will trade real capital. Only enable live after explicit human confirmation.
#>
param(
    [switch]$Detached,
    [switch]$StartLive  # if provided and CONFIRM_PRODUCTION token is set to I_CONFIRM_ALLOW_MT5_SEND, will start with ALLOW_MT5_SEND=1
)

# Repo root and working dir
$repoRoot = Resolve-Path -Path "."
Set-Location $repoRoot

# ---------------------
# Load environment defaults from JSON (docs/production_env_defaults.json)
# ---------------------
$jsonEnvFile = Join-Path -Path $repoRoot -ChildPath "docs\production_env_defaults.json"
if (Test-Path $jsonEnvFile) {
    Write-Output "Loading environment defaults from $jsonEnvFile"
    try {
        $jsonText = Get-Content -Path $jsonEnvFile -Raw -ErrorAction Stop
        $defaults = $jsonText | ConvertFrom-Json
        # Apply defaults to environment (override current values)
        foreach ($prop in $defaults.PSObject.Properties) {
            $name = $prop.Name
            $value = [string]$prop.Value
            Write-Output "Setting env:$name = $value"
            try {
                Set-Item -Path "env:$name" -Value $value -ErrorAction Stop
            } catch {
                Write-Warning "Failed to set env:$name = $value : $_"
            }
        }
        } catch {
        Write-Warning ("Failed to load or parse {0}: {1}" -f $jsonEnvFile, $_)
    }
} else {
    Write-Warning "Environment JSON $jsonEnvFile not found — proceeding without central defaults"
}

# ---------------------
# Pre-launch checks
# ---------------------
Write-Output "=== Pre-launch checks ==="
if (-not (Test-Path ".\\config\\mt5_credentials.env")) {
    Write-Warning "MT5 credentials file missing: .\\config\\mt5_credentials.env"
} else {
    Write-Output "MT5 credentials file found."
}

if (-not (Test-Path $env:AUDIT_DIR)) {
    New-Item -ItemType Directory -Path $env:AUDIT_DIR -Force | Out-Null
    Write-Output "Created $env:AUDIT_DIR"
} else {
    Write-Output "$env:AUDIT_DIR exists"
}

if (Test-Path "control\\disable_trading") {
    Write-Output "Kill-switch present: control\\disable_trading (will block sends if present)"
} else {
    Write-Output "No kill-switch file (control\\disable_trading) found."
}

if (Test-Path "control\\emergency_stop") {
    Write-Warning "Emergency stop active: control\\emergency_stop present"
}

# Backup artifacts
if ($env:BACKUP_ARTIFACTS_ON_START -eq "1") {
    $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $backupDir = Join-Path -Path "artifacts" -ChildPath "backup_$ts"
    Write-Output "Backing up artifacts to $backupDir ..."
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    Copy-Item -Path "artifacts\\live_trading\\*" -Destination $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "Backup done."
}

# ---------------------
# Start command logic
# ---------------------
$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\\live_trading\\production_run_$ts.out.log"
$logErr = "artifacts\\live_trading\\production_run_$ts.err.log"
$pidFile = "artifacts\\live_trading\\production_run_$ts.pid"

function Start-ProductionProcess([switch]$DetachedMode) {
    # $DetachedMode est fourni dans la signature; pas de 'param()' supplémentaire nécessaire
    if ($DetachedMode) {
        # Start detached with PID capture, hidden window
        $startInfo = @{
            FilePath = 'python'
            ArgumentList = '.\\start_production.py'
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
            # write pid file
            Set-Content -Path $pidFile -Value $procId -Encoding utf8
            return $procId
        } else {
            Write-Warning "Failed to start detached process"
            return $null
        }
    } else {
        # Interactive start (stdout/stderr redirected to files)
        Start-Process -FilePath python -ArgumentList '.\\start_production.py' -WorkingDirectory $repoRoot -NoNewWindow -RedirectStandardOutput $logOut -RedirectStandardError $logErr
        Write-Output "Production process started (interactive). stdout -> $logOut, stderr -> $logErr"
        return $null
    }
}

        # --- Préparer l'environnement d'exécution (seuil et mode adaptatif) ---
        try {
            # Charger un seuil persistant s'il existe
            $ctrlFile = Join-Path -Path $repoRoot -ChildPath 'control\base_confidence_threshold.txt'
            if (Test-Path $ctrlFile) {
                $persisted = (Get-Content -Path $ctrlFile -Raw).Trim()
                if ($persisted) {
                    Write-Output "Applying persisted BASE_CONFIDENCE_THRESHOLD=$persisted from control\\base_confidence_threshold.txt"
                    $env:BASE_CONFIDENCE_THRESHOLD = $persisted
                }
            }
            if (-not $env:AUTO_THRESHOLD_MODE) { $env:AUTO_THRESHOLD_MODE = '1' }
            Write-Output "AUTO_THRESHOLD_MODE=$($env:AUTO_THRESHOLD_MODE)"
        } catch {}

# If user requested to start live immediately, validate token
if ($StartLive) {
    if ($env:CONFIRM_PRODUCTION -eq 'I_CONFIRM_ALLOW_MT5_SEND') {
        Write-Output "CONFIRM_PRODUCTION token valid — will start in LIVE mode"
        $env:ALLOW_MT5_SEND = '1'
    } else {
        Write-Warning "CONFIRM_PRODUCTION token not set to 'I_CONFIRM_ALLOW_MT5_SEND' — refusing to start live"
        # keep whatever ALLOW_MT5_SEND is currently set to by defaults, but warn
        if ($env:ALLOW_MT5_SEND -ne '1') {
            Write-Output "ALLOW_MT5_SEND is not set to '1' — running in DRY-RUN"
        }
    }
} else {
    # If StartLive not provided but JSON explicitly enabled ALLOW_MT5_SEND, warn prominently
    if ($env:ALLOW_MT5_SEND -eq '1') {
        Write-Warning "Configuration requests ALLOW_MT5_SEND=1 from defaults. This will enable real trading when process runs. Ensure you have explicitly reviewed and accept the risk."
    }
}

# Start process according to Detached switch
if ($Detached) {
    $startedPid = Start-ProductionProcess -DetachedMode
    if ($startedPid) {
        Write-Output "PID written to $pidFile"
        Write-Output "To stop: Stop-Process -Id $startedPid"
    }
    Write-Output "Detached start complete."
    Write-Output "# To schedule via Task Scheduler: register a task that runs PowerShell.exe -File \"$($repoRoot)\\tools\\run_production.ps1\" -Detached"
} else {
    Start-ProductionProcess -DetachedMode:$false
    Write-Output "Tailing stdout (Ctrl+C to stop): $logOut"
    Write-Output "To start detached later: powershell -File tools\\run_production.ps1 -Detached"
    Get-Content $logOut -Wait -Tail 20
}

# End of script
#>