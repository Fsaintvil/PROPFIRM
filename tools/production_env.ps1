<# Production environment loader (sanitized): sets env flags and starts production safely #>
$ErrorActionPreference = 'Stop'

# 1) Repo path setup
$repoRoot = Resolve-Path -Path "."
Set-Location $repoRoot

# 2) Core environment (from repo defaults and live snapshots)
$env:PYTHONPATH = ".;$($repoRoot)"
$env:SYMBOLS = "BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash"

# Live control gates
$env:ALLOW_MT5_SEND = "1"                 # live mode enabled
$env:CONFIRME_DEPLACEMENT = "YES_I_CONFIRM"
$env:CONFIRM_PRODUCTION = $env:CONFIRM_PRODUCTION

# Intelligent modules / AUTO / AI flags
$env:LIVE_ENGINE_LIGHT_MODE = "0"         # full engine, not light
$env:AUTO_APPLY = "1"
$env:AUTO_DEPLOY = "1"
$env:AUTO_LEARN = "1"
$env:AUTO_ADAPT = "1"
$env:AUTO_ENRICH = "1"
$env:AI_AUTOMATE = "1"
$env:AI_VOLUME = "0.01"
$env:INIT_ALL_AI = "1"
$env:META_LEARNING_TRADING_SYSTEM = "1"
$env:REINFORCEMENT_LEARNING_TRADING_SYSTEM = "1"
$env:MULTI_ASSET_PORTFOLIO_OPTIMIZER = "1"

# SL/TP & risk policy (kept from prior live session)
$env:DEFAULT_SL_PTS = "0"
$env:DEFAULT_TP_PTS = "0"
$env:DEFAULT_RR = "2.0"
$env:TRAILING_STOP_ENABLE = "1"
$env:TRAILING_STOP_PTS = "5"
$env:PER_SYMBOL_SL_JSON = '{"BTCUSD":300,"US500.cash":15,"EURUSD":25}'
$env:SL_AS_ATR_MULT = "0.8"
$env:SL_MAX_PCT_ACCOUNT = "0.5"

# Breakeven policy
$env:ENABLE_BREAKEVEN = "1"
$env:BREAKEVEN_AFTER_SECONDS = "300"
$env:BREAKEVEN_PROFIT_PTS = "10"

# Runtime risk and pacing
$env:RISK_PER_TRADE_PCT = "0.1"
$env:MAX_OPEN_POSITIONS = "6"
$env:MAX_DRAWDOWN_PCT = "0.05"
$env:DAILY_MAX_TRADES = "180"
$env:TRADING_INTERVAL = "120"
$env:AUTO_CLOSE_MINUTES = "15"
$env:ORDER_TIMEOUT_SECONDS = "30"
$env:SL_RETRY_MAX = "10"
$env:SL_RETRY_BACKOFF_SECONDS = "2"

# Audits / monitoring
$env:AUDIT_DIR = "artifacts\live_trading"
$env:BACKUP_ARTIFACTS_ON_START = "1"
$env:HEALTHCHECK_CMD = ""
$env:NOTIFY_ON_CRITICAL = ""
$env:METRICS_ENABLE = "1"
$env:METRICS_PORT = "9090"
$env:LOG_LEVEL = "INFO"
$env:PYTHONUNBUFFERED = "1"

# 3) Pre-launch checks
Write-Output "=== Pre-launch checks ==="
if (-not (Test-Path ".\config\mt5_credentials.env")) {
  Write-Warning "MT5 credentials file missing: .\config\mt5_credentials.env"
} else {
  Write-Output "MT5 credentials file found."
}

if (-not (Test-Path $env:AUDIT_DIR)) {
  New-Item -ItemType Directory -Path $env:AUDIT_DIR -Force | Out-Null
  Write-Output "Created $env:AUDIT_DIR"
}

if (Test-Path "control\disable_trading") {
  Write-Warning "Kill-switch present: control\disable_trading (sends will be blocked)"
}
if (Test-Path "control\emergency_stop") {
  Write-Warning "Emergency stop active: control\emergency_stop present"
}

if ($env:BACKUP_ARTIFACTS_ON_START -eq "1") {
  $tsbk = (Get-Date).ToString('yyyyMMdd_HHmmss')
  $backupDir = Join-Path -Path "artifacts" -ChildPath "backup_$tsbk"
  New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
  Copy-Item -Path "artifacts\live_trading\*" -Destination $backupDir -Recurse -Force -ErrorAction SilentlyContinue
  Write-Output "Artifacts backup -> $backupDir"
}

# 4) Start production if not already running (lock check)
$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\live_trading\production_run_$ts.out.log"
$logErr = "artifacts\live_trading\production_run_$ts.err.log"
$pidFile = "artifacts\live_trading\production_run_$ts.pid"

if (Test-Path "control\production.lock") {
  Write-Output "Production appears to be already running (control\production.lock). Skipping start."
} else {
  Write-Output "Starting production (ALLOW_MT5_SEND=$($env:ALLOW_MT5_SEND)) ..."
  # Qualité > quantité: seuil de confiance relevé (0.70)
  $proc = Start-Process -FilePath python -ArgumentList "./start_production.py --threshold 0.70 --interval 120 --yes" -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $logOut -RedirectStandardError $logErr
  $proc.Id | Set-Content $pidFile
  Write-Output "Started PID=$($proc.Id) stdout=$logOut stderr=$logErr"
}

# 5) One-shot healthcheck (best-effort)
if (Test-Path 'tools\healthcheck.py') {
  try { python .\tools\healthcheck.py | Out-Null } catch { Write-Warning "Healthcheck error: $_" }
  if (Test-Path 'artifacts\live_trading\healthcheck.json') { Write-Output "Healthcheck.json generated." }
}

Write-Output "production_env.ps1 done."
