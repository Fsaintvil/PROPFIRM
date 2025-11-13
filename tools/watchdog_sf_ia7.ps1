<#
.WATCHDOG
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
$artifactsDir = Join-Path $BaseDir "artifacts\live_trading"
New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null
$lockFile = Join-Path $artifactsDir "$BotName.run.lock"
$pidFile = Join-Path $artifactsDir "$BotName.active.pid"
$stateFile = Join-Path $artifactsDir "$BotName.session_state.json"
$relanceLog = Join-Path $artifactsDir "$BotName.relaunch.log"
$stopFlag = Join-Path $BaseDir "STOP_WATCHDOG.$BotName"

# --- Session ID ---
function Get-SessionID { "$BotName.Session_$(Get-Date -Format yyyyMMdd)" }
$SessionID = Get-SessionID

# --- Helpers ---
function Get-NowUtc { (Get-Date).ToUniversalTime() }
function Is-Process-Alive($pid) { try { Get-Process -Id $pid -ErrorAction Stop | Out-Null; return $true } catch { return $false } }

# --- Lock helpers ---
function Acquire-Lock {
    if (Test-Path $lockFile) {
        try {
            $content = Get-Content $lockFile -ErrorAction Stop | ConvertFrom-Json
            if ($content.pid -and (Is-Process-Alive -pid $content.pid)) {
                Write-Warning "⛔ Une instance est déjà active (PID=$($content.pid), session=$($content.session_id))"
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

# --- Main loop (sans blocage ouverture/fermeture marché) ---
try {
    while (-not (Test-Path $stopFlag)) {
        $nowUtc = Get-NowUtc

        # NOTE: suppression des règles liées à l'ouverture/fermeture du marché
        # La variable d'environnement ALLOW_NEW_ENTRIES n'est plus modifiée en fonction du temps restant

        $running = 0
        if (Test-Path $pidFile) {
            Get-Content $pidFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" } | Select-Object -Unique | ForEach-Object {
                if (Is-Process-Alive -pid $_) { $running++ }
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

            # --- run_production.ps1 avec intégration complète ---
            $scriptPs = Join-Path $BaseDir "tools\run_production.ps1"
            if (Test-Path $scriptPs) {
                try {
                    $env:PYTHONPATH = ".;$BaseDir"
                    $env:SYMBOLS = "BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash"
                    $env:ALLOW_MT5_SEND = "1"
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
                    $env:TRADE_INTERVAL_SECONDS = "930"
                    $env:AUTO_CLOSE_MINUTES = "31"
                    $env:MAX_OPEN_POSITIONS = "6"
                    $env:MTF_TIMEFRAME = "M15"
                    $env:BACKTEST_YEARS = "7"

                    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$scriptPs) -NoNewWindow -RedirectStandardOutput $logs.out -RedirectStandardError $logs.err -PassThru
                    $proc.Id | Out-File -FilePath $pidFile -Append -Encoding ascii
                    Write-Output "💡 Process PID=$($proc.Id) lancé"
                    Send-Notification "🚀 Bot $BotName lancé. PID=$($proc.Id), session=$SessionID"
                    Log-Relance "manual_launch"
                } catch {
                    Write-Warning "⚠️ Échec lancement: $_"
                    Send-Notification "❌ Échec lancement bot $BotName: $_"
                    Log-Relance "launch_fail"
                }
            } else {
                Write-Warning "⚠️ Aucun script de lancement trouvé."
                Send-Notification "⚠️ Aucun script run_production.ps1 trouvé pour $BotName"
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
<#
.WATCHDOG
Watchdog SF_IA.7 ultime + améliorations : notifications multi-canal, anti-duplication PID robuste, logs optimisés, cooldown adaptatif, lockfile multi-bot, surveillance avancée des processus IA et performance
#>

[CmdletBinding()]
param (
    [string]$BaseDir = "C:\Users\saint\Documents\PROPFIRM",
    [int]$MaxRelanceParHeure = 6,
    [int]$WatchIntervalSeconds = 60,
    [int]$RelanceCooldownMinutes = 30,
    [string]$NotificationWebhook = "",
    [string]$FallbackEmail = "",
    [string]$BotName = "SF_IA.7"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Use the parameters in a benign way so static analysis treats them as referenced
if ($NotificationWebhook -ne '') { $null = $NotificationWebhook }
if ($FallbackEmail -ne '') { $null = $FallbackEmail }

# --- Paths ---
$artifactsDir = Join-Path $BaseDir "artifacts\live_trading"
New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null
$lockFile = Join-Path $artifactsDir "$BotName.run.lock"
$pidFile = Join-Path $artifactsDir "$BotName.active.pid"
$stateFile = Join-Path $artifactsDir "$BotName.session_state.json"
$relanceLog = Join-Path $artifactsDir "$BotName.relaunch.log"
$stopFlag = Join-Path $BaseDir "STOP_WATCHDOG.$BotName"

# --- Session ID ---
function Get-SessionID { "$BotName.Session_$(Get-Date -Format yyyyMMdd)" }
$SessionID = Get-SessionID

# --- Helpers ---
function Get-NowUtc { (Get-Date).ToUniversalTime() }

# Use approved verb 'Test' and avoid shadowing automatic variables
function Test-ProcessAlive {
    param([Alias('Pid')][int]$ProcessId)
    try { Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null; return $true } catch { return $false }
}

function Test-ProcessActive {
    param(
        [Alias('Pid')][int]$ProcessId,
        [string]$LogFile,
        [int]$TimeoutSec = 180
    )
    if (-not (Test-ProcessAlive -ProcessId $ProcessId)) { return $false }
    if (Test-Path $LogFile) {
        $lastWrite = (Get-Item $LogFile).LastWriteTimeUtc
        if ((Get-NowUtc) - $lastWrite -gt [timespan]::FromSeconds($TimeoutSec)) { return $false }
    }
    return $true
}

# --- Lock helpers (multi-bot) ---
function New-LockFile {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param()
    $maxAttempts = 5
    for ($i=0; $i -lt $maxAttempts; $i++) {
        if (-not (Test-Path $lockFile)) { break }
        try {
            $content = Get-Content $lockFile -ErrorAction Stop | ConvertFrom-Json
            if ($content -and $content.pids -and $content.pids.Count -gt 0) {
                $alivePids = @()
                foreach ($p in $content.pids) { if (Test-ProcessAlive -ProcessId $p) { $alivePids += $p } }
                if ($alivePids.Count -gt 0) {
                    Write-Warning "⛔ Instance(s) active(s) PID=$($alivePids -join ', ')"
                    return $false
                }
            }
            Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        } catch { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 200
    }
    $payload = @{ pids = @($PID); session_id = $SessionID; ts = (Get-NowUtc).ToString("o"); bot = $BotName }
    if ($PSCmdlet.ShouldProcess($lockFile, 'Create lock file')) {
        $payload | ConvertTo-Json | Set-Content -Path $lockFile -Force
    }
    return $true
}

function Remove-LockFile {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param()
    if (Test-Path $lockFile) {
        try {
            $content = Get-Content $lockFile | ConvertFrom-Json
            if ($content.pids -contains $PID) {
                if ($PSCmdlet.ShouldProcess($lockFile, 'Remove lock file')) {
                    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
                }
            }
        } catch { if ($PSCmdlet.ShouldProcess($lockFile, 'Remove lock file (catch)')) { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } }
    }
}

# --- Notifications multi-canal ---
function Send-Notification($message, $retry=5) {
    $sent = $false
    if ($NotificationWebhook -ne "") {
        for ($i=0; $i -lt $retry; $i++) {
            try { 
                $payload = @{ text = $message } | ConvertTo-Json
                Invoke-RestMethod -Uri $NotificationWebhook -Method Post -Body $payload -ContentType "application/json"
                $sent = $true; break
            } catch { Start-Sleep -Seconds 2 }
        }
    }
    if (-not $sent -and $FallbackEmail -ne "") {
        Send-MailMessage -To $FallbackEmail -From "$BotName@localhost" -Subject "Watchdog $BotName" -Body $message -SmtpServer "localhost"
    }
    if (-not $sent) { Write-Warning "⚠️ Notification échouée: $message" }
}

# --- Relance log avec rotation adaptative ---
function Add-RelanceLog($reason) {
    $entry = @{ ts = (Get-NowUtc).ToString("o"); reason = $reason; bot = $BotName; session_id = $SessionID }
    $entry | ConvertTo-Json | Add-Content -Path $relanceLog
    $count = (Get-Content $relanceLog | Measure-Object).Count
    if ($count -gt 2000) {
        Move-Item -Path $relanceLog -Destination "$relanceLog.old" -Force
        New-Item -Path $relanceLog -ItemType File | Out-Null
    }
}

function Get-RelanceCountLastHour {
    if (-not (Test-Path $relanceLog)) { return 0 }
    $cutoff = (Get-NowUtc).AddHours(-1)
    Get-Content $relanceLog | ForEach-Object {
        if ($_ -ne "") {
            $obj = $_ | ConvertFrom-Json
            if ([datetime]$obj.ts -ge $cutoff) { 1 } else { 0 }
        }
    } | Measure-Object -Sum | Select-Object -ExpandProperty Sum
}

# --- Run logs ---
function New-RunLog {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param()
    $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $logOut = Join-Path $artifactsDir "production_${BotName}_run_${ts}.out.log"
    $logErr = Join-Path $artifactsDir "production_${BotName}_run_${ts}.err.log"
    if ($PSCmdlet.ShouldProcess($logOut, 'Create run log files')) {
        New-Item -Path $logOut -ItemType File -Force | Out-Null
        New-Item -Path $logErr -ItemType File -Force | Out-Null
    }
    return @{out=$logOut; err=$logErr; ts=$ts}
}

# --- Market open/close (idem SF_IA.7) ---
$MarketHolidaysUtc = @([datetime]::Parse("2025-01-01T00:00:00Z"), [datetime]::Parse("2025-12-25T00:00:00Z"))
function Test-Holiday($dt) { return $MarketHolidaysUtc -contains $dt.Date }
function Get-NextMarketOpenUtc { ... } # identique
function Get-NextMarketCloseUtc { ... } # identique

# --- Save state avancé ---
function Save-State($hasRun, $extra) {
    $obj = @{
        last_run = (Get-Date).ToString("o")
        ran = $hasRun
        extra = $extra
        relance_count_last_hour = Get-RelanceCountLastHour
        pids = if (Test-Path $pidFile) { Get-Content $pidFile } else { @() }
        heartbeat = (Get-NowUtc).ToString("o")
    }
    $obj | ConvertTo-Json | Set-Content -Path $stateFile -Force
}

# --- Acquire lock ---
if (-not (New-LockFile)) {
    Send-Notification "⛔ Watchdog $BotName déjà en cours. Session=$SessionID"
    exit 1
}
$PID | Out-File -FilePath $pidFile -Encoding ascii -Force

# --- Main loop avancé ---
try {
    $failureCount = 0
    while (-not (Test-Path $stopFlag)) {
        $nowUtc = Get-NowUtc
        $nextClose = Get-NextMarketCloseUtc
        $timeToCloseMin = [int](($nextClose - $nowUtc).TotalMinutes)
        $env:ALLOW_NEW_ENTRIES = if ($timeToCloseMin -le 30) { "0" } else { "1" }

        if ($nowUtc -ge $nextClose) {
            Write-Output "✅ Marché fermé (UTC). Arrêt contrôlé."
            Send-Notification "✅ Marché fermé. Watchdog $BotName session=$SessionID terminé."
            if (Test-Path $pidFile) {
                Get-Content $pidFile | ForEach-Object { if (Test-ProcessAlive -ProcessId $_) { Stop-Process -Id $_ -ErrorAction SilentlyContinue } }
            }
            Add-RelanceLog "market_close"
            Save-State $false @{ reason="market_close" }
            break
        }

        # --- Vérifie les PIDs actifs ---
        $running = 0
        if (Test-Path $pidFile) {
                Get-Content $pidFile | ForEach-Object {
                    if (Test-ProcessActive -ProcessId $_ -LogFile $artifactsDir -TimeoutSec 180) { $running++ }
                }
        }

        if ($running -eq 0) {
            $relances = Get-RelanceCountLastHour
                if ($relances -ge $MaxRelanceParHeure) {
                Write-Warning "⚠️ Trop de relances en 1h ($relances). Cooldown $RelanceCooldownMinutes min."
                Send-Notification "⚠️ Trop de relances ($relances). Cooldown activé."
                Add-RelanceLog "cooldown_trigger"
                Start-Sleep -Seconds ($RelanceCooldownMinutes*60)
                continue
            }

            $logs = New-RunLog
            $scriptPs = Join-Path $BaseDir "tools\run_production.ps1"
            if (Test-Path $scriptPs) {
                try {
                    $fileHash = (Get-FileHash $scriptPs -Algorithm SHA256).Hash
                    Write-Output "💡 Script SHA256=$fileHash"
                    # Environnement complet
                    $env:PYTHONPATH = ".;$BaseDir"
                    $env:SYMBOLS = "BTCUSD,ETHUSD,XAUUSD,USDCAD,..."
                    $env:ALLOW_MT5_SEND = "1"
                    $env:TRADE_INTERVAL_SECONDS = "930"
                    $env:AUTO_CLOSE_MINUTES = "31"
                    $env:MAX_OPEN_POSITIONS = "6"
                    $env:MTF_TIMEFRAME = "M15"
                    $env:BACKTEST_YEARS = "7"
                    
                    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$scriptPs) -NoNewWindow -RedirectStandardOutput $logs.out -RedirectStandardError $logs.err -PassThru
                    $proc.Id | Out-File -FilePath $pidFile -Append -Encoding ascii
                    Write-Output "💡 Process PID=$($proc.Id) lancé"
                    Send-Notification "🚀 Bot $BotName lancé. PID=$($proc.Id), session=$SessionID"
                    Add-RelanceLog "manual_launch"
                    $failureCount = 0
                } catch {
                    Write-Warning "⚠️ Échec lancement: $_"
                    # Use formatted string to avoid variable parsing issues
                    Send-Notification -message ("❌ Échec lancement bot {0}: {1}" -f $BotName, $_)
                    Add-RelanceLog "launch_fail"
                    $failureCount++
                    Start-Sleep -Seconds (30 * $failureCount) # cooldown progressif
                }
            } else {
                Write-Warning "⚠️ Aucun script de lancement trouvé."
                Send-Notification "⚠️ Aucun script run_production.ps1 trouvé pour $BotName"
            }
        }

        Start-Sleep -Seconds $WatchIntervalSeconds
    }

} finally {
    Remove-LockFile
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
    Write-Output "🧾 Watchdog [$BotName, session $SessionID] terminé proprement."
    Send-Notification "🧾 Watchdog $BotName terminé proprement. Session=$SessionID"
}
