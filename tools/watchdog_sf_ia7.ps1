<#!
.WATCHDOG SPEC
Watchdog SF_IA.7 ultime : notifications, anti-duplication PID, logs optimisés, cooldown réactif, lockfile résilient
- Intègre MTF M15 convergence, multi-indicateurs techniques et institutionnels
- Backtest 7 ans + historique trades réels MT5
- Tous les modèles IA disponibles / live
- Trading live toutes les 930 secondes par symbole
- Auto-close après 31 min si SL/TP non atteint
#>
[CmdletBinding()]
param (
    [string]$BaseDir = "C:\Users\saint\Documents\PROPFIRM",
    [int]$MaxRelanceParHeure = 6,
    [int]$WatchIntervalSeconds = 60,
    [int]$RelanceCooldownMinutes = 30,
    [string]$NotificationWebhook = "",
    [string]$BotName = "SF_IA.7"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Paths ---
$artifactsDir = Join-Path $BaseDir "artifacts/live_trading"
New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null
$lockFile = Join-Path $artifactsDir "$BotName.run.lock"
$pidFile = Join-Path $artifactsDir "$BotName.active.pid"
$stateFile = Join-Path $artifactsDir "$BotName.session_state.json"
$relanceLog = Join-Path $artifactsDir "$BotName.relaunch.log"
$stopFlag = Join-Path $BaseDir "STOP_WATCHDOG.$BotName"

function Get-SessionID { "$BotName.Session_$(Get-Date -Format yyyyMMdd)" }
$SessionID = Get-SessionID
function Get-NowUtc { (Get-Date).ToUniversalTime() }
function Is-Process-Alive($procId) { try { Get-Process -Id $procId -ErrorAction Stop | Out-Null; return $true } catch { return $false } }

# --- Lock helpers ---
function Acquire-Lock {
    if (Test-Path $lockFile) {
        try {
            $content = Get-Content $lockFile -ErrorAction Stop | ConvertFrom-Json
            if ($content.pid -and (Is-Process-Alive -procId $content.pid)) {
                Write-Warning "⛔ Instance active (PID=$($content.pid), session=$($content.session_id))"
                return $false
            }
            Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        } catch { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }
    }
    $payload = @{ pid = $PID; session_id = $SessionID; ts = (Get-NowUtc).ToString("o"); bot = $BotName }
    $payload | ConvertTo-Json | Set-Content -Path $lockFile -Force
    return $true
}
function Release-Lock {
    if (Test-Path $lockFile) {
        try {
            $content = Get-Content $lockFile | ConvertFrom-Json
            if ($content.pid -eq $PID) { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }
        } catch { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }
    }
}

# --- Notifications ---
function Send-Notification($message, $retry=3) {
    if ($NotificationWebhook -ne "") {
        for ($i=0; $i -lt $retry; $i++) {
            try {
                $payload = @{ text = $message } | ConvertTo-Json
                Invoke-RestMethod -Uri $NotificationWebhook -Method Post -Body $payload -ContentType "application/json"
                return
            } catch { Start-Sleep -Seconds 2 }
        }
        Write-Warning "⚠️ Notification échouée après $retry tentatives: $message"
    }
}

# --- Relance log ---
function Log-Relance($reason) {
    $entry = @{ ts = (Get-NowUtc).ToString("o"); reason = $reason; bot = $BotName; session_id = $SessionID }
    $entry | ConvertTo-Json | Add-Content -Path $relanceLog
    if ((Get-Content $relanceLog | Measure-Object).Count -gt 1000) {
        Move-Item -Path $relanceLog -Destination "$relanceLog.old" -Force
        New-Item -Path $relanceLog -ItemType File | Out-Null
    }
}
function Get-RelanceCountLastHour {
    if (-not (Test-Path $relanceLog)) { return 0 }
    $cutoff = (Get-NowUtc).AddHours(-1)
    Get-Content $relanceLog -ReadCount 100 | ForEach-Object {
        $_ | ForEach-Object {
            if ($_ -ne "") {
                $obj = $_ | ConvertFrom-Json
                if ([datetime]$obj.ts -ge $cutoff) { 1 } else { 0 }
            }
        }
    } | Measure-Object -Sum | Select-Object -ExpandProperty Sum
}

# --- Run logs ---
function New-RunLogs {
    $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $logOut = Join-Path $artifactsDir "production_${BotName}_run_${ts}.out.log"
    $logErr = Join-Path $artifactsDir "production_${BotName}_run_${ts}.err.log"
    return @{out=$logOut; err=$logErr; ts=$ts}
}

# --- Save state ---
function Save-State($hasRun, $extra) {
    $obj = @{
        last_run = (Get-Date).ToString("o")
        ran = $hasRun
        extra = $extra
        relance_count_last_hour = Get-RelanceCountLastHour
        pids = if (Test-Path $pidFile) { Get-Content $pidFile } else { @() }
    }
    $obj | ConvertTo-Json | Set-Content -Path $stateFile -Force
}

# --- Acquire lock ---
if (-not (Acquire-Lock)) {
    Send-Notification "⛔ Watchdog $BotName déjà en cours. Session=$SessionID"
    exit 1
}
$PID | Out-File -FilePath $pidFile -Encoding ascii -Force

# === Variables d'environnement adaptées au moteur (sans invention) ===
# Ajustements strictement basés sur les fonctionnalités existantes du moteur
# Vars engine (pas de conflit avec les règles internes du moteur)
$env:PNL_UPDATE_EVERY_CYCLES = "5"
$env:ADAPT_PRUDENT = "1"
$env:WINRATE_MIN_SYMBOL = "0.45"
$env:EXPECTANCY_MIN_SYMBOL = "0.00"
$env:DAILY_LOSS_LIMIT_PCT = "0.03"
# Paramètres trading déjà utilisés
$env:TRADE_INTERVAL_SECONDS = "930"
$env:AUTO_CLOSE_MINUTES = "31"
$env:MAX_OPEN_POSITIONS = "6"
$env:MTF_TIMEFRAME = "M15"
$env:BACKTEST_YEARS = "7"

try {
    while (-not (Test-Path $stopFlag)) {
        $nowUtc = Get-NowUtc
        # Gating ouverture/fermeture marché (évite entrées proches de la clôture)
        try {
            # Prochaine fermeture vendredi 22:00 UTC (approx) et jours fériés non gérés ici
            $dow = [int]$nowUtc.DayOfWeek
            $isWeekend = ($dow -eq [int][DayOfWeek]::Saturday) -or ($dow -eq [int][DayOfWeek]::Sunday)
            if ($isWeekend) {
                $env:ALLOW_NEW_ENTRIES = "0"
            } else {
                # Fenêtre d’arrêt des nouvelles entrées si proche de la clôture (30 min)
                $utc22 = [datetime]::SpecifyKind($nowUtc.Date.AddHours(22), [System.DateTimeKind]::Utc)
                if ($nowUtc -gt $utc22) { $utc22 = $utc22.AddDays(1) }
                $minsToClose = [int](($utc22 - $nowUtc).TotalMinutes)
                $env:ALLOW_NEW_ENTRIES = if ($minsToClose -le 30) { "0" } else { "1" }
            }
        } catch { $env:ALLOW_NEW_ENTRIES = "1" }
        $running = 0
        if (Test-Path $pidFile) {
            Get-Content $pidFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" } | Select-Object -Unique | ForEach-Object {
                if (Is-Process-Alive -procId $_) { $running++ }
            }
        }
        if ($running -eq 0) {
            $relances = Get-RelanceCountLastHour
            if ($relances -ge $MaxRelanceParHeure) {
                Write-Warning "Trop de relances en 1h ($relances). Cooldown $RelanceCooldownMinutes min."
                Send-Notification "⚠️ Trop de relances ($relances) pour $BotName. Cooldown activé."
                Log-Relance "cooldown_trigger"
                for ($i=0; $i -lt $RelanceCooldownMinutes*60; $i+=10) {
                    if (Test-Path $stopFlag) { break }
                    Start-Sleep -Seconds 10
                }
                continue
            }
            $logs = New-RunLogs
            Write-Output "🚀 Démarrage du bot $BotName (session $SessionID)"
            $scriptPs = Join-Path $BaseDir "tools/run_production.ps1"
            if (Test-Path $scriptPs) {
                try {
                    $env:PYTHONPATH = ".;$BaseDir"
                    $env:SYMBOLS = "BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash"
                    # Respecter sécurité run_production.ps1 : n'activer l'envoi live que si CONFIRM_PRODUCTION exact
                    if ($env:CONFIRM_PRODUCTION -eq "I_CONFIRM_ALLOW_MT5_SEND") {
                        if (-not $env:ALLOW_MT5_SEND) { $env:ALLOW_MT5_SEND = "1" }
                    } else {
                        $env:ALLOW_MT5_SEND = "0"
                    }
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
                    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$scriptPs) -NoNewWindow -RedirectStandardOutput $logs.out -RedirectStandardError $logs.err -PassThru
                    $proc.Id | Out-File -FilePath $pidFile -Append -Encoding ascii
                    Write-Output "💡 Process PID=$($proc.Id) lancé"
                    Send-Notification "🚀 Bot $BotName lancé. PID=$($proc.Id), session=$SessionID"
                    Log-Relance "manual_launch"
                } catch {
                    Write-Warning "⚠️ Échec lancement: $_"
                    Send-Notification ("❌ Échec lancement bot {0}: {1}" -f $BotName, $_)
                    Log-Relance "launch_fail"
                }
            } else {
                Write-Warning "⚠️ Script run_production.ps1 introuvable."
                Send-Notification "⚠️ Aucun script run_production.ps1 pour $BotName"
            }
        }
        Start-Sleep -Seconds $WatchIntervalSeconds
    }
} finally {
    Release-Lock
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
    Write-Output "🧾 Watchdog [$BotName, session $SessionID] terminé proprement."
    Send-Notification "🧾 Watchdog $BotName terminé proprement. Session=$SessionID"
}
