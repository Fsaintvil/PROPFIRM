# Live production — SL/TP Pack

Contenu :

* `tools/run_production.ps1` (patch complet, variables d'environnement SL/TP et commentaires)
* `order_manager_example.py` (snippet Python à coller dans le manager d'ordres / start_production.py)
* `tools/run_production_detached.ps1` (variante pour le Planificateur + gestion PID)
* `README_RUN_PRODUCTION_DETACHED.md` (comment utiliser / Register-ScheduledTask example)
* `env_defaults.json` (liste des variables d'environnement par défaut)

---

## 1) tools/run_production.ps1  (patch complet)

```powershell
# tools/run_production.ps1
# Patch: ajoute variables SL/TP, trailing, risk management, et quelques aides.
# Usage: conserver le reste du fichier existant — remplacer ou merger les variables d'env.

# 1) Basic repo / path setup
$repoRoot = Resolve-Path -Path "."
Set-Location $repoRoot

# 2) Environment preparation (adjust values as needed)
$env:PYTHONPATH = ".;$($repoRoot)"
$env:SYMBOLS = "BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash"

# Safety & confirmations
$env:LIVE_ENGINE_LIGHT_MODE = "0"
$env:CONFIRME_DEPLACEMENT = "YES_I_CONFIRM"
$env:CONFIRM_PRODUCTION = $env:CONFIRM_PRODUCTION
$env:ALLOW_MT5_SEND = "1"        # safe default = 0 (dry-run)
$env:FORCE = "0"

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

# Use existing risk controls
$env:TRADE_INTERVAL_SECONDS = "930"
$env:AUTO_CLOSE_MINUTES = "30"       # harmonisé avec 30 minutes
$env:MAX_OPEN_POSITIONS = "6"
$env:MAX_DRAWDOWN_PCT = "0.05"       # 5% = 0.05
$env:RISK_PER_TRADE_PCT = "0.1"      # 0.1% par trade
$env:DAILY_MAX_TRADES = "180"

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
$env:HEALTHCHECK_CMD = ""
$env:NOTIFY_ON_CRITICAL = ""
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

# Kill-switch checks
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
    Copy-Item -Path "artifacts\\live_trading\\*" -Destination $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "Backup done."
}

# 4) Command to start (dry-run safe)
$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\\live_trading\\production_run_$ts.out.log"
$logErr = "artifacts\\live_trading\\production_run_$ts.err.log"
$pidFile = "artifacts\\live_trading\\production_run_$ts.pid"

Write-Output "Starting production (DRY-RUN mode: ALLOW_MT5_SEND=$($env:ALLOW_MT5_SEND)) ..."
Start-Process -FilePath python -ArgumentList ".\\start_production.py" -WorkingDirectory $repoRoot -NoNewWindow -RedirectStandardOutput $logOut -RedirectStandardError $logErr
Write-Output "Production process started (dry-run). stdout -> $logOut, stderr -> $logErr"

# 5) Healthcheck / monitoring (simple loop, optional)
Write-Output "Launching simple tail of stdout (Ctrl+C to stop) ..."
Get-Content $logOut -Wait -Tail 20

# NOTE: LIVE start instructions remain manual and unchanged
```

---

## 2) order_manager_example.py (snippet Python)

```python
# order_manager_example.py
# Snippet: calcule SL/TP, taille de position, et prépare ordre MT5 (dry-run safe)
import json
from math import floor

# helper functions (à adapter avec ton code MT5 wrapper)

def load_env_map():
    import os
    return {
        'DEFAULT_SL_PTS': float(os.getenv('DEFAULT_SL_PTS','0')),
        'DEFAULT_TP_PTS': float(os.getenv('DEFAULT_TP_PTS','0')),
        'DEFAULT_RR': float(os.getenv('DEFAULT_RR','2.0')),
        'TRAILING_STOP_ENABLE': os.getenv('TRAILING_STOP_ENABLE','0') == '1',
        'TRAILING_STOP_PTS': float(os.getenv('TRAILING_STOP_PTS','0')),
        'PER_SYMBOL_SL_JSON': json.loads(os.getenv('PER_SYMBOL_SL_JSON','{}')),
        'SL_AS_ATR_MULT': float(os.getenv('SL_AS_ATR_MULT','1.5')),
        'RISK_PER_TRADE_PCT': float(os.getenv('RISK_PER_TRADE_PCT','0.1'))/100.0,
        'SL_MAX_PCT_ACCOUNT': float(os.getenv('SL_MAX_PCT_ACCOUNT','0.5'))/100.0,
    }


def compute_sl_tp_and_lots(symbol, side, atr, account_balance, tick_value, tick_size):
    # side: 'buy' or 'sell'
    env = load_env_map()

    # 1) determine SL pts
    per_sym = env['PER_SYMBOL_SL_JSON'].get(symbol)
    if per_sym and per_sym > 0:
        sl_pts = float(per_sym)
    elif env['DEFAULT_SL_PTS'] > 0:
        sl_pts = env['DEFAULT_SL_PTS']
    else:
        # ATR provided in pts
        sl_pts = env['SL_AS_ATR_MULT'] * float(atr)

    # 2) determine TP pts
    if env['DEFAULT_TP_PTS'] > 0:
        tp_pts = env['DEFAULT_TP_PTS']
    else:
        tp_pts = sl_pts * env['DEFAULT_RR']

    # 3) compute pip/tick value per lot to derive lots from risk
    # tick_value: $ per tick for 1 lot, tick_size: pts per tick (ex: 0.0001 for FX)
    # risk allowed
    risk_usd = account_balance * env['RISK_PER_TRADE_PCT']
    # cost if 1 lot = sl_pts / tick_size * tick_value
    cost_per_lot = (sl_pts / tick_size) * tick_value
    if cost_per_lot <= 0:
        lots = 0.0
    else:
        lots = risk_usd / cost_per_lot

    # enforce SL_MAX_PCT_ACCOUNT (monetary cap) if needed
    max_risk_usd = account_balance * env['SL_MAX_PCT_ACCOUNT']
    max_lots = max_risk_usd / cost_per_lot if cost_per_lot>0 else lots
    if lots * cost_per_lot > max_risk_usd:
        lots = max_lots

    # round lots to allowed step (example: 0.01)
    step = 0.01
    lots = floor(lots/step) * step
    if lots < step:
        lots = 0.0

    result = {
        'symbol': symbol,
        'side': side,
        'sl_pts': sl_pts,
        'tp_pts': tp_pts,
        'lots': lots,
        'risk_usd': lots * cost_per_lot,
        'trailing': env['TRAILING_STOP_ENABLE'],
        'trailing_start_pts': env['TRAILING_STOP_PTS'],
    }
    return result

# Example usage (dry-run)
if __name__ == '__main__':
    # fake market data (adapter à ton environnement)
    example = compute_sl_tp_and_lots('EURUSD','buy', atr=10, account_balance=100000, tick_value=10, tick_size=0.0001)
    print('Proposal:', example)

# Integrer: appeler compute_sl_tp_and_lots() depuis la logique d'ordre avant send_order_mt5()
# send_order_mt5(symbol, side, lots, sl_points, tp_points, comment='auto')
```

---

## 3) tools/run_production_detached.ps1 (planificateur variant)

```powershell
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
        if (-not [string]::IsNullOrEmpty($val)) { $env:$k = $val }
    }
}

$ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
$logOut = "artifacts\\live_trading\\production_live_$ts.out.log"
$logErr = "artifacts\\live_trading\\production_live_$ts.err.log"

# Start process detached and capture PID
$proc = Start-Process -FilePath python -ArgumentList ".\\start_production.py" -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $logOut -RedirectStandardError $logErr
$pid = $proc.Id
$pidFile = "artifacts\\live_trading\\production_live_$ts.pid"
Set-Content -Path $pidFile -Value $pid
Write-Output "Started detached production: PID=$pid, stdout=$logOut, stderr=$logErr"

# Optional: basic watchdog (runs in script) — will not block if scheduled task handles restarts
Start-Sleep -Seconds 2
if (-not (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
    Write-Warning "Process $pid not running after start. Check logs."
}

# exit (detached)
```

---

## 4) README_RUN_PRODUCTION_DETACHED.md (short)

````markdown
# README: run_production_detached.ps1

But: This file is a helper to start the production process in detached mode and save the PID + logs.

## Example Scheduled Task (Register-ScheduledTask) snippet

Open an elevated PowerShell and run the following (adapt paths and user):

```powershell
$action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument "-NoProfile -WindowStyle Hidden -File `"C:\path\to\tools\run_production_detached.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 06:00AM
$principal = New-ScheduledTaskPrincipal -UserId 'DOMAIN\\Username' -LogonType Password -RunLevel Highest
Register-ScheduledTask -TaskName 'PROPFIRM_RunProduction' -Action $action -Trigger $trigger -Principal $principal -Description 'Start PROPFIRM production (detached)'
````

Notes:

* Use a service account with rights and non-expiring password if possible.
* Ensure the working dir and python are accessible by that account.
* Add an email/notification wrapper on failure if required (external script).

````

---

## 5) env_defaults.json (documentation / secret manager)

```json
{
  "ALLOW_MT5_SEND": "0",
  "DEFAULT_SL_PTS": "0",
  "DEFAULT_TP_PTS": "0",
  "DEFAULT_RR": "2.0",
  "TRAILING_STOP_ENABLE": "1",
  "TRAILING_STOP_PTS": "10",
  "PER_SYMBOL_SL_JSON": "{\"BTCUSD\":300,\"US500.cash\":15,\"EURUSD\":25}",
  "SL_AS_ATR_MULT": "1.5",
  "SL_MAX_PCT_ACCOUNT": "0.5",
  "RISK_PER_TRADE_PCT": "0.1",
  "AUTO_CLOSE_MINUTES": "30"
}
````

---

## 6) Notes & integration steps

* Coller `order_manager_example.py` dans ton gestionnaire d'ordres (ou appeler la fonction compute_sl_tp_and_lots depuis ton pipeline avant send_order).
* Merge les variables d'environnement dans ton `tools/run_production.ps1` existant (ou remplacer si tu veux la version patch).
* Tester en dry-run (ALLOW_MT5_SEND=0) et vérifier `artifacts/live_trading/*.log` et `trade_proposals.csv` (si tu actives l'audit).
* Vérifier les unités (pts vs pips) selon instruments (indices/commodities utilisent points non pip-size).

---

Si tu veux, je peux maintenant :

* créer une branche Git avec ces fichiers prêts à committer (nom: `feature/sl-tp-pack`) et fournir le message de commit + PR draft.
* ou générer les snippets transformés pour ton style MT5 wrapper (ex : MetaTrader5 Python package).

Dis-moi la suite que tu veux.

```
```
