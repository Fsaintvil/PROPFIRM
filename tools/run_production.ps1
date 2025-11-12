<#
tools/run_production.ps1

Usage:
  - Dry-run (safe): run without parameters -> starts in dry-run mode (ALLOW_MT5_SEND=0)
  - Detached/background: run with -Detached to start the python process in background suitable for scheduling

WARNING: Live sends will trade real capital. Only enable live after explicit human confirmation.
#>
param(
    [switch]$Detached,
    [switch]$StartLive,  # if provided and CONFIRM_PRODUCTION token is set to I_CONFIRM_ALLOW_MT5_SEND, will start with ALLOW_MT5_SEND=1
    [string]$ConfirmProduction
)

# Repo root and working dir
$repoRoot = Resolve-Path -Path "."
#!/usr/bin/env pwsh
# tools/run_production.ps1
# =========================
# tools/run_production.ps1
# =========================
# Usage:
#  - Dry-run (safe): just run this file as-is. It will start with ALLOW_MT5_SEND=0.
#  - To actually enable live sends: set $env:CONFIRM_PRODUCTION to "I_CONFIRM_ALLOW_MT5_SEND" and re-run the "Start Live" section below.
#
# WARNING: Live sends will trade real capital. Only proceed after explicit human review.
# =========================

# 1) Basic repo / path setup
$repoRoot = Resolve-Path -Path "."
Set-Location $repoRoot

# 2) Environment preparation (adjust values as needed)
$env:PYTHONPATH = ".;$($repoRoot)"
$env:SYMBOLS = "BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash"

# Safety & confirmations
$env:LIVE_ENGINE_LIGHT_MODE = "0"
$env:CONFIRME_DEPLACEMENT = "YES_I_CONFIRM"
# NOTE: CONFIRM_PRODUCTION must be EXACT to allow live below
$env:CONFIRM_PRODUCTION = $env:CONFIRM_PRODUCTION                      # leave unset by default; set to "I_CONFIRM_ALLOW_MT5_SEND" to enable
# If a ConfirmProduction param is provided, prefer it (helps CI/automation)
if ($ConfirmProduction) {
    $env:CONFIRM_PRODUCTION = $ConfirmProduction
}
$env:ALLOW_MT5_SEND = "1"        # safe default = 0 (dry-run)
$env:FORCE = "0"                 # override (dangerous) - don't use unless necessary

# Automation / AI flags
$env:AUTO_APPLY = "1"
$env:AUTO_DEPLOY = "1"
$env:AUTO_LEARN = "1"
$env:AUTO_ADAPT = "1"
$env:AUTO_ENRICH = "1"
$env:AI_AUTOMATE = "1"
$env:INIT_ALL_AI = "1"
$env:AI_VOLUME = "0.01"
$env:META_LEARNING_TRADING_SYSTEM = "1"
$env:REINFORCEMENT_LEARNING_TRADING_SYSTEM = "1"
$env:MULTI_ASSET_PORTFOLIO_OPTIMIZER = "1"

# Trading timing / risk controls
$env:TRADE_INTERVAL_SECONDS = "930"
$env:AUTO_CLOSE_MINUTES = "30"
$env:MAX_OPEN_POSITIONS = "6"
$env:MAX_DRAWDOWN_PCT = "0.05"   # 5% = 0.05
$env:RISK_PER_TRADE_PCT = "0.1"
$env:DAILY_MAX_TRADES = "180"

# ---------------------- NEW: SL/TP & Risk settings ----------------------
# Default behaviour: if DEFAULT_SL_PTS == 0 -> use ATR-based SL (SL_AS_ATR_MULT * ATR)
$env:DEFAULT_SL_PTS = "0"            # points (0 = use ATR-based)
$env:DEFAULT_TP_PTS = "0"            # points (0 = use RR * SL)
$env:DEFAULT_RR = "2.0"              # TP = RR * SL when DEFAULT_TP_PTS == 0
$env:TRAILING_STOP_ENABLE = "1"      # 0 = off, 1 = on
$env:TRAILING_STOP_PTS = "10"        # trailing start distance in pts
$env:PER_SYMBOL_SL_JSON = '{"BTCUSD":300,"US500.cash":15,"EURUSD":25}'
$env:SL_AS_ATR_MULT = "1.5"          # multiplier for ATR when DEFAULT_SL_PTS=0
$env:SL_MAX_PCT_ACCOUNT = "0.5"      # max % of account to risk per trade if using monetary cap (0.5 = 0.5%)

# SL retry / order behavior
$env:SL_RETRY_MAX = "10"
$env:SL_RETRY_BACKOFF_SECONDS = "2"
$env:ORDER_TIMEOUT_SECONDS = "30"

# Breakeven rules
$env:ENABLE_BREAKEVEN = "1"
$env:BREAKEVEN_AFTER_SECONDS = "300"
$env:BREAKEVEN_PROFIT_PTS = "10"

# Audits / backups / monitoring
$env:AUDIT_DIR = "artifacts\\live_trading"
$env:BACKUP_ARTIFACTS_ON_START = "1"
$env:HEALTHCHECK_CMD = ""      # optional
$env:NOTIFY_ON_CRITICAL = ""   # optional webhook
$env:METRICS_ENABLE = "0"
$env:METRICS_PORT = "9090"

# Logging behaviour
$env:LOG_LEVEL = "INFO"
$env:PYTHONUNBUFFERED = "1"

# 3) Pre-launch checks (fail fast if something is clearly wrong)
Write-Output "=== Pre-launch checks ==="
if (-not (Test-Path ".\\config\\mt5_credentials.env")) {
    Write-Warning "MT5 credentials file missing: .\\config\\mt5_credentials.env (ensure credentials are present)"
} else {
    Write-Output "MT5 credentials file found."
}

# Make artifacts folder
if (-not (Test-Path $env:AUDIT_DIR)) {
    New-Item -ItemType Directory -Path $env:AUDIT_DIR -Force | Out-Null
    Write-Output "Created $env:AUDIT_DIR"
} else {
    Write-Output "$env:AUDIT_DIR exists"
}

# Check kill-switch files
if (Test-Path "control\\disable_trading") {
    Write-Output "Kill-switch present: control\\disable_trading (will block sends if present)"
} else {
    Write-Output "No kill-switch file (control\\disable_trading) found."
}

if (Test-Path "control\\emergency_stop") {
    Write-Warning "Emergency stop active: control\\emergency_stop present"
}

# Backup artifacts if requested
if ($env:BACKUP_ARTIFACTS_ON_START -eq "1") {
    $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $backupDir = Join-Path -Path "artifacts" -ChildPath "backup_$ts"
    Write-Output "Backing up artifacts to $backupDir ..."
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    # Copy only live_trading artifacts to avoid huge copies
    Copy-Item -Path "artifacts\\live_trading\\*" -Destination $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "Backup done."
}

# 4) Command to start (dry-run safe)
$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\\live_trading\\production_run_$ts.out.log"
$logErr = "artifacts\\live_trading\\production_run_$ts.err.log"
$pidFile = "artifacts\\live_trading\\production_run_$ts.pid"

Write-Output "Starting production (DRY-RUN mode: ALLOW_MT5_SEND=$($env:ALLOW_MT5_SEND)) ..."
if ($StartLive) {
    # START LIVE: verify token and allow live sends
    if ($env:CONFIRM_PRODUCTION -eq 'I_CONFIRM_ALLOW_MT5_SEND') {
        Write-Output "CONFIRM_PRODUCTION token valid - proceeding with LIVE start"
        $env:ALLOW_MT5_SEND = '1'
    } else {
        Write-Warning "CONFIRM_PRODUCTION token not set or invalid - aborting LIVE start"
        return
    }

    $tsLive = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $logOutLive = "artifacts\\live_trading\\production_live_$tsLive.out.log"
    $logErrLive = "artifacts\\live_trading\\production_live_$tsLive.err.log"
    $pidFileLive = "artifacts\\live_trading\\production_live_$tsLive.pid"

    Write-Output "Starting LIVE production (ALLOW_MT5_SEND=$($env:ALLOW_MT5_SEND)) ..."
    if ($Detached) {
        $proc = Start-Process -FilePath python -ArgumentList '.\\start_production.py --auto-confirm' -WorkingDirectory $repoRoot -NoNewWindow -RedirectStandardOutput $logOutLive -RedirectStandardError $logErrLive -PassThru
        Start-Sleep -Seconds 940
        try {
            $proc.Id | Out-File $pidFileLive -Force
            Write-Output "LIVE production started detached. PID=$($proc.Id). stdout -> $logOutLive, stderr -> $logErrLive"
        } catch {
            Write-Warning "Impossible d'écrire le PID file: $_"
        }
    } else {
        # Interactive live start in current console
        Write-Output "Starting LIVE production (interactive) - logs -> $logOutLive / $logErrLive"
        & python .\start_production.py --auto-confirm 2> $logErrLive | Tee-Object -FilePath $logOutLive
    }

    return
} else {
    # Dry-run start
    Start-Process -FilePath python -ArgumentList ".\\start_production.py" -WorkingDirectory $repoRoot -NoNewWindow -RedirectStandardOutput $logOut -RedirectStandardError $logErr
    Write-Output "Production process started (dry-run). stdout -> $logOut, stderr -> $logErr"
}

# 5) Healthcheck / monitoring (simple loop, optional)
Write-Output "Launching simple tail of stdout (Ctrl+C to stop) ..."
Get-Content $logOut -Wait -Tail 20

# -----------------------------
# START LIVE (manual explicit step)
# -----------------------------
# To actually enable live sends you MUST:
# 1) Set exact token: $env:CONFIRM_PRODUCTION = 'I_CONFIRM_ALLOW_MT5_SEND'
# 2) Ensure you have removed control\\disable_trading and emergency_stop (if you want sends)
# 3) Then run the following lines manually (copy/paste):

# $env:CONFIRM_PRODUCTION = 'I_CONFIRM_ALLOW_MT5_SEND'
# if ($env:CONFIRM_PRODUCTION -eq 'I_CONFIRM_ALLOW_MT5_SEND') {
#     $env:ALLOW_MT5_SEND = '1'
#     $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
#     $logOutLive = "artifacts\\live_trading\\production_live_$ts.out.log"
#     $logErrLive = "artifacts\\live_trading\\production_live_$ts.err.log"
#     Start-Process -FilePath python -ArgumentList '.\\start_production.py' -WorkingDirectory $repoRoot -NoNewWindow -RedirectStandardOutput $logOutLive -RedirectStandardError $logErrLive
#     Write-Output "LIVE started; stdout=$logOutLive stderr=$logErrLive"
# } else {
#     Write-Warning 'CONFIRM_PRODUCTION token not set - aborting live start'
# }

