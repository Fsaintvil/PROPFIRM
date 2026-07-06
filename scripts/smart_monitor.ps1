<#
.SYNOPSIS
    Smart Monitor - surveillance automatisee 24/7 du robot MT5 FTMO.
    Remplace les verifications manuelles avec alertes temps reel.

.DESCRIPTION
    Verifie toutes les 60s : PID, heartbeat, logs, metriques, DD, daily loss, WR, tendances.
    Genere runtime/monitor_report.json pour lecture par l'IA et notifications toast Windows.

    Utilisation:
        .\scripts\smart_monitor.ps1                     # One-shot
        .\scripts\smart_monitor.ps1 -Daemon              # Boucle continue
        .\scripts\smart_monitor.ps1 -Daemon -Interval 30 # Boucle toutes les 30s
        .\scripts\smart_monitor.ps1 -Install             # Planifier dans Task Scheduler

    Demarrage auto avec le robot:
        .\scripts\robot.ps1                              # Lance robot + moniteur
#>

param(
    [switch]$Daemon,
    [switch]$Install,
    [switch]$Remove,
    [int]$Interval = 60,
    [int]$HeartbeatTimeout = 180,
    [string]$LogTailPath = ""
)

# --- Configuration ---
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$LogDir     = Join-Path $ProjectRoot "logs"

# Fichiers
$PidFile       = Join-Path $RuntimeDir "robot.pid"
$HeartbeatFile = Join-Path $RuntimeDir "heartbeat.txt"
$DashboardFile = Join-Path $RuntimeDir "dashboard.json"
$FtmoFile      = Join-Path $RuntimeDir "ftmo_report.json"
$ReportFile    = Join-Path $RuntimeDir "monitor_report.json"
$MonitorLog    = Join-Path $LogDir "smart_monitor.log"
$RobotLog      = if ($LogTailPath -and (Test-Path $LogTailPath)) { $LogTailPath } else { Join-Path $LogDir "simple_robot.log" }
$MainScript    = Join-Path $ProjectRoot "main.py"
$PythonExe     = "C:\Users\saint\AppData\Local\Programs\Python\Python310\pythonw.exe"

# Seuils d'alerte
$Limits = @{
    DD_WARN        = 0.03   # 3% -> INFO jaune
    DD_ALERT       = 0.05   # 5% -> WARNING orange
    DD_CRITICAL    = 0.07   # 7% -> CRITIQUE rouge
    DAILY_LOSS_PCT = 0.80   # 80% de la limite journaliere -> WARNING
    WR_WARN        = 0.50   # 50% -> INFO
    WR_ALERT       = 0.45   # 45% -> WARNING
    PF_ALERT       = 1.0    # PF < 1.0 -> WARNING
    POSITION_MAX_DURATION = 480  # 8h -> INFO position trop longue
    POSITION_PNL_ALERT    = -200 # -$200 -> WARNING
    CONSECUTIVE_LOSSES    = 5    # 5 pertes consecutives -> WARNING
    MIN_HEARTBEAT_FRESH   = 120  # 120s sans heartbeat -> WARNING
}

# --- Logger ---
function Write-MonitorLog {
    param([string]$Msg, [string]$Level = "INFO")
    $Line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Msg"
    Add-Content -Path $MonitorLog -Value $Line -Encoding UTF8
    switch ($Level) {
        "CRITICAL" { Write-Host $Line -ForegroundColor Red }
        "WARNING"  { Write-Host $Line -ForegroundColor Yellow }
        "INFO"     { Write-Host $Line -ForegroundColor Gray }
        default    { Write-Host $Line }
    }
}

# --- Utilitaires ---
function Get-FileAge {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $LastWrite = (Get-Item $Path).LastWriteTimeUtc
    return [int]([DateTime]::UtcNow - $LastWrite).TotalSeconds
}

function Get-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        $content = Get-Content $Path -Raw -Encoding UTF8
        return $content | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Send-ToastNotification {
    param([string]$Title, [string]$Message, [string]$Level = "INFO")
    try {
        $popup = New-Object -ComObject Wscript.Shell
        $icon = if ($Level -eq "CRITICAL") { 16 } else { 48 }
        $popup.Popup($Message, 10, "[MT5 Robot] $Title", $icon) | Out-Null
    } catch {
        # Silently fail
    }
}

# --- Checks ---
function Check-Process {
    $result = @{ alive = $false; pid = $null; heartbeat_age = $null; ram_mb = $null }

    if (Test-Path $PidFile) {
        $pid_val = (Get-Content $PidFile -Raw).Trim()
        if ($pid_val) {
            $proc = Get-Process -Id $pid_val -ErrorAction SilentlyContinue
            if ($proc) {
                $result.alive = $true
                $result.pid = $pid_val
                $result.ram_mb = [math]::Round($proc.WorkingSet64 / 1MB, 0)
            }
        }
    }

    $hb_age = Get-FileAge -Path $HeartbeatFile
    $result.heartbeat_age = $hb_age

    return $result
}

function Check-Metrics {
    $result = @{
        balance = $null; equity = $null; dd = $null
        daily_pnl = $null; daily_limit = $null
        wr = $null; pf = $null; trades = $null
        open_positions = 0; positions = @(); alerts = @()
        ftmo_status = $null; profit_progress = $null; profit_remaining = $null
        trading_days = $null; days_remaining = $null; consecutive_losses = $null
    }

    $dash = Get-JsonFile -Path $DashboardFile
    if ($dash) {
        $result.balance = $dash.balance
        $result.equity = $dash.equity
        $result.dd = $dash.current_dd
        $result.daily_pnl = $dash.daily_pnl
        $result.daily_limit = $dash.daily_loss_limit
        $result.wr = $dash.win_rate
        $result.pf = $dash.profit_factor
        $result.trades = $dash.total_trades
        $result.open_positions = $dash.open_positions
        if ($dash.positions) { $result.positions = $dash.positions }
        if ($dash.alerts) { $result.alerts = $dash.alerts }
    }

    # Helper: convertit une valeur potentiellement formatée (ex: "1.0%") en nombre
    function Convert-NumericField {
        param($Value)
        if ($null -eq $Value) { return $null }
        if ($Value -is [string]) {
            $clean = $Value -replace '%','' -replace ',','.'
            try { return [double]$clean } catch { return $null }
        }
        return [double]$Value
    }

    $ftmo = Get-JsonFile -Path $FtmoFile
    if ($ftmo) {
        $result.ftmo_status = $ftmo.status
        $result.profit_progress = $ftmo.profit_progress
        $result.profit_remaining = $ftmo.profit_remaining
        $result.trading_days = $ftmo.trading_days
        $result.days_remaining = $ftmo.days_remaining
        $result.consecutive_losses = $ftmo.consecutive_losses
        if (-not $result.balance) { $result.balance = Convert-NumericField $ftmo.balance }
        if (-not $result.equity)  { $result.equity = Convert-NumericField $ftmo.equity }
        if (-not $result.dd)      {
            $dd_val = Convert-NumericField $ftmo.dd_from_peak
            $result.dd = if ($dd_val) { $dd_val / 100 } else { $null }
        }
        if (-not $result.wr)      {
            $wr_val = Convert-NumericField $ftmo.win_rate
            $result.wr = if ($wr_val) { $wr_val / 100 } else { $null }
        }
        if (-not $result.trades)  { $result.trades = [int](Convert-NumericField $ftmo.total_trades) }
    }

    return $result
}

function Check-Logs {
    $result = @{ errors = @(); last_error = $null; error_count = 0; warnings = @{ mt5_disconnect = 0; order_rejected = 0; spread_high = 0; dd_high = 0 }; log_tail = @() }

    if (-not (Test-Path $RobotLog)) { return $result }

    try {
        $lines = Get-Content $RobotLog -Tail 200 -ErrorAction SilentlyContinue
        $result.log_tail = [string[]]($lines | Select-Object -Last 10)

        $errors = $lines | Where-Object { $_ -match '\bERROR\b' -or $_ -match '\bCRITICAL\b' }
        $result.error_count = $errors.Count
        $result.errors = $errors | Select-Object -Last 5
        $result.last_error = [string]($errors | Select-Object -Last 1)

        $result.warnings.mt5_disconnect = ($lines | Where-Object { $_ -match 'MT5.*deconnect|connection.*lost|login.*fail' } | Measure-Object).Count
        $result.warnings.order_rejected = ($lines | Where-Object { $_ -match 'order.*rejet|rejected|invalid.*ticket' } | Measure-Object).Count
        $result.warnings.spread_high = ($lines | Where-Object { $_ -match 'spread.*trop.*elev|spread.*too.*high' } | Measure-Object).Count
        $result.warnings.dd_high = ($lines | Where-Object { $_ -match 'drawdown.*critique|dd.*critical' } | Measure-Object).Count
    } catch {
        $result.errors = @("Erreur lecture logs: $_")
    }

    return $result
}

function Check-Trends {
    $result = @{
        wr_change = $null; pnl_change = $null
        dd_change = $null; trades_since_last = $null
        wr_trend = "stable"; pnl_trend = "stable"
    }

    $prev = Get-JsonFile -Path $ReportFile
    if (-not $prev) { return $result }

    $current = Get-JsonFile -Path $DashboardFile
    if (-not $current) { return $result }

    $prev_wr = $prev.metrics.wr
    $curr_wr = $current.win_rate
    if ($prev_wr -and $curr_wr) {
        $result.wr_change = [math]::Round(($curr_wr - $prev_wr) * 100, 1)
        $result.wr_trend = if ($result.wr_change -gt 2) { "hausse" } elseif ($result.wr_change -lt -2) { "baisse" } else { "stable" }
    }

    $prev_equity = $prev.metrics.equity
    $curr_equity = $current.equity
    if ($prev_equity -and $curr_equity) {
        $result.pnl_change = [math]::Round($curr_equity - $prev_equity, 2)
        $result.pnl_trend = if ($result.pnl_change -gt 50) { "hausse" } elseif ($result.pnl_change -lt -50) { "baisse" } else { "stable" }
    }

    $prev_dd = $prev.metrics.dd
    $curr_dd = $current.current_dd
    if ($prev_dd -and $curr_dd) {
        $result.dd_change = [math]::Round(($curr_dd - $prev_dd) * 100, 2)
    }

    $prev_trades = $prev.metrics.trades
    $curr_trades = $current.total_trades
    if ($prev_trades -and $curr_trades) {
        $result.trades_since_last = $curr_trades - $prev_trades
    }

    return $result
}

function Get-HealthScore {
    param($process, $metrics, $logs, $trends)

    $score = 100

    if (-not $process.alive) { $score -= 30 }
    elseif ($process.heartbeat_age -gt $HeartbeatTimeout) { $score -= 15 }
    elseif ($process.heartbeat_age -gt 60) { $score -= 5 }

    if ($metrics) {
        if ($metrics.dd -gt 0.07) { $score -= 25 }
        elseif ($metrics.dd -gt 0.05) { $score -= 15 }
        elseif ($metrics.dd -gt 0.03) { $score -= 5 }

        if ($metrics.wr -lt 0.40) { $score -= 15 }
        elseif ($metrics.wr -lt 0.45) { $score -= 10 }
        elseif ($metrics.wr -lt 0.50) { $score -= 5 }

        if ($metrics.pf -lt 1.0 -and $metrics.pf -gt 0) { $score -= 10 }
        if ($metrics.pf -lt 0.5 -and $metrics.pf -gt 0) { $score -= 15 }

        if ($metrics.consecutive_losses -ge 5) { $score -= 10 }
    }

    if ($logs) {
        $score -= [math]::Min($logs.error_count * 2, 15)
        if ($logs.warnings.mt5_disconnect -gt 3) { $score -= 10 }
    }

    if ($trends) {
        if ($trends.wr_change -lt -5) { $score -= 10 }
        elseif ($trends.wr_change -lt -2) { $score -= 5 }
        if ($trends.pnl_change -lt -200) { $score -= 10 }
        elseif ($trends.pnl_change -lt -100) { $score -= 5 }
    }

    return [math]::Max(0, [math]::Min(100, $score))
}

function Get-StatusLabel {
    param([int]$Score)
    if ($Score -ge 80) { return "OK" }
    if ($Score -ge 60) { return "SURVEILLANCE" }
    if ($Score -ge 40) { return "ATTENTION" }
    return "CRITIQUE"
}

# --- Generation des alertes ---
function New-Alerts {
    param($process, $metrics, $logs)

    $alerts = @()

    # Process
    if (-not $process.alive) {
        $alerts += @{ level = "CRITICAL"; source = "process"; message = "Robot ARRETE (PID introuvable)"; action = "REDEMARRER" }
    } elseif ($process.heartbeat_age -gt $HeartbeatTimeout) {
        $alerts += @{ level = "WARNING"; source = "process"; message = "Heartbeat stale ($($process.heartbeat_age)s)"; action = "VERIFIER" }
    }

    # Drawdown
    if ($metrics.dd -gt $Limits.DD_CRITICAL) {
        $alerts += @{ level = "CRITICAL"; source = "risk"; message = "DD $([math]::Round($metrics.dd * 100, 1))% > 7% - RISQUE MAX"; action = "KILL SWITCH" }
    } elseif ($metrics.dd -gt $Limits.DD_ALERT) {
        $alerts += @{ level = "WARNING"; source = "risk"; message = "DD $([math]::Round($metrics.dd * 100, 1))% > 5% - SURVEILLANCE"; action = "REDUIRE RISQUE" }
    } elseif ($metrics.dd -gt $Limits.DD_WARN) {
        $alerts += @{ level = "INFO"; source = "risk"; message = "DD $([math]::Round($metrics.dd * 100, 1))% > 3%"; action = "SURVEILLER" }
    }

    # Daily Loss
    if ($metrics.daily_limit -and $metrics.daily_limit -gt 0) {
        $daily_pct = [math]::Abs($metrics.daily_pnl) / $metrics.daily_limit
        if ($daily_pct -gt $Limits.DAILY_LOSS_PCT) {
            $alerts += @{ level = "WARNING"; source = "risk"; message = "Daily loss $([math]::Round($daily_pct * 100, 0))% de la limite"; action = "REDUIRE RISQUE" }
        }
    }

    # Win Rate
    if ($metrics.wr -lt $Limits.WR_ALERT -and $metrics.trades -gt 20) {
        $alerts += @{ level = "WARNING"; source = "performance"; message = "WR $([math]::Round($metrics.wr * 100, 1))% < 45%"; action = "REVOIR STRATEGIE" }
    } elseif ($metrics.wr -lt $Limits.WR_WARN -and $metrics.trades -gt 20) {
        $alerts += @{ level = "INFO"; source = "performance"; message = "WR $([math]::Round($metrics.wr * 100, 1))% < 50%"; action = "SURVEILLER" }
    }

    # Profit Factor
    if ($metrics.pf -and $metrics.pf -lt $Limits.PF_ALERT -and $metrics.pf -gt 0) {
        $alerts += @{ level = "WARNING"; source = "performance"; message = "PF $([math]::Round($metrics.pf, 2)) < 1.0"; action = "REVOIR STRATEGIE" }
    }

    # Positions longues
    foreach ($pos in $metrics.positions) {
        if ($pos.duration_min -gt $Limits.POSITION_MAX_DURATION) {
            $alerts += @{ level = "INFO"; source = "position"; message = "$($pos.symbol) ticket=$($pos.ticket) ouvert depuis $([math]::Round($pos.duration_min, 0))min"; action = "VERIFIER TRAILING" }
        }
        if ($pos.pnl -lt $Limits.POSITION_PNL_ALERT) {
            $alerts += @{ level = "WARNING"; source = "position"; message = "$($pos.symbol) PnL $($pos.pnl)$"; action = "SURVEILLER" }
        }
    }

    # Erreurs logs
    if ($logs.error_count -gt 0) {
        $alerts += @{ level = "WARNING"; source = "logs"; message = "$($logs.error_count) erreur(s) dans les logs"; action = "ANALYSER" }
        foreach ($err in $logs.errors) {
            $alerts += @{ level = "INFO"; source = "logs"; message = "Derniere: $err"; action = "ANALYSER" }
        }
    }

    # Erreurs MT5
    if ($logs.warnings.mt5_disconnect -gt 0) {
        $alerts += @{ level = "WARNING"; source = "mt5"; message = "$($logs.warnings.mt5_disconnect) deconnexion(s) MT5"; action = "VERIFIER CONNEXION" }
    }

    # Ordres rejectes
    if ($logs.warnings.order_rejected -gt 3) {
        $alerts += @{ level = "WARNING"; source = "execution"; message = "$($logs.warnings.order_rejected) ordres rejectes"; action = "VERIFIER EXECUTION" }
    }

    # Pertes consecutives
    if ($metrics.consecutive_losses -ge $Limits.CONSECUTIVE_LOSSES) {
        $alerts += @{ level = "WARNING"; source = "risk"; message = "$($metrics.consecutive_losses) pertes consecutives - pause"; action = "COOLDOWN" }
    }

    # Spreads eleves
    if ($logs.warnings.spread_high -gt 10) {
        $alerts += @{ level = "INFO"; source = "market"; message = "$($logs.warnings.spread_high) spreads eleves"; action = "SURVEILLER" }
    }

    if ($alerts.Count -eq 0) {
        $alerts += @{ level = "INFO"; source = "system"; message = "Tout est OK"; action = "AUCUNE" }
    }

    return $alerts
}

# --- Rapport consolide ---
function New-MonitorReport {
    param($process, $metrics, $logs, $trends, $alerts)

    $health_score = Get-HealthScore -process $process -metrics $metrics -logs $logs -trends $trends
    $status_label = Get-StatusLabel -Score $health_score

    $critical_count = ($alerts | Where-Object { $_.level -eq "CRITICAL" } | Measure-Object).Count
    $warning_count  = ($alerts | Where-Object { $_.level -eq "WARNING" } | Measure-Object).Count
    $info_count     = ($alerts | Where-Object { $_.level -eq "INFO" } | Measure-Object).Count

    # Uptime calculation with safety
    $uptime = $null
    if ($process.pid) {
        try {
            $procInfo = Get-Process -Id $process.pid -ErrorAction SilentlyContinue
            if ($procInfo) {
                $uptime = [math]::Round(((Get-Date) - ($procInfo.StartTime)).TotalSeconds)
            }
        } catch { $uptime = $null }
    }

    $positions_summary = @()
    foreach ($pos in $metrics.positions) {
        $positions_summary += @{
            symbol = $pos.symbol
            direction = $pos.direction
            pnl = [math]::Round($pos.pnl, 2)
            duration_min = [math]::Round($pos.duration_min, 0)
            volume = $pos.volume
        }
    }

    $alerts_summary = @()
    foreach ($a in $alerts) {
        $alerts_summary += @{
            level = $a.level
            source = $a.source
            message = $a.message
            action = $a.action
        }
    }

    $report = @{
        timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        uptime_seconds = $uptime
        health = @{
            score = $health_score
            label = $status_label
        }
        alerts = @{
            total = $alerts.Count
            critical = $critical_count
            warning = $warning_count
            info = $info_count
            items = $alerts_summary
        }
        process = @{
            alive = $process.alive
            pid = $process.pid
            ram_mb = $process.ram_mb
            heartbeat_age_s = $process.heartbeat_age
        }
        metrics = @{
            balance = $metrics.balance
            equity = $metrics.equity
            dd_pct = if ($metrics.dd) { [math]::Round($metrics.dd * 100, 2) } else { $null }
            daily_pnl = $metrics.daily_pnl
            daily_limit = $metrics.daily_limit
            wr_pct = if ($metrics.wr) { [math]::Round($metrics.wr * 100, 1) } else { $null }
            pf = if ($metrics.pf) { [math]::Round($metrics.pf, 2) } else { $null }
            trades = $metrics.trades
            open_positions = $metrics.open_positions
            consecutive_losses = $metrics.consecutive_losses
        }
        ftmo = @{
            status = $metrics.ftmo_status
            profit_progress = $metrics.profit_progress
            profit_remaining = $metrics.profit_remaining
            trading_days = $metrics.trading_days
            days_remaining = $metrics.days_remaining
        }
        positions = $positions_summary
        trends = @{
            wr_change_pct = $trends.wr_change
            wr_trend = $trends.wr_trend
            pnl_change = $trends.pnl_change
            pnl_trend = $trends.pnl_trend
            dd_change_pct = $trends.dd_change
            trades_since_last = $trends.trades_since_last
        }
        logs = @{
            error_count = $logs.error_count
            last_error = $logs.last_error
            mt5_disconnects = $logs.warnings.mt5_disconnect
            order_rejects = $logs.warnings.order_rejected
            spread_warnings = $logs.warnings.spread_high
            last_lines = $logs.log_tail
        }
    }

    return $report
}

# --- Notification push ---
function Push-CriticalAlerts {
    param($alerts)

    $criticals = $alerts | Where-Object { $_.level -in @("CRITICAL", "WARNING") }
    foreach ($a in $criticals) {
        $title = "[$($a.level)] $($a.source)"
        $msg = "$($a.message) - Action: $($a.action)"
        Send-ToastNotification -Title $title -Message $msg -Level $a.level
    }
}

# --- Action automatique ---
function Auto-Restart {
    Write-MonitorLog "REDEMARRAGE AUTOMATIQUE du robot..." -Level "WARNING"
    try {
        Get-Process -Name "pythonw", "python" -ErrorAction SilentlyContinue |
            Stop-Process -Force -ErrorAction SilentlyContinue
    } catch {}
    Start-Sleep -Seconds 3
    try {
        Start-Process -FilePath $PythonExe -ArgumentList $MainScript -WindowStyle Hidden
        Write-MonitorLog "Robot relance (main.py)" -Level "INFO"
        Send-ToastNotification -Title "ROBOT REDEMARRE" -Message "Le robot a ete relance automatiquement" -Level "WARNING"
    } catch {
        Write-MonitorLog "ECHEC redemarrage: $_" -Level "CRITICAL"
    }
}

# --- Run-Check ---
function Run-Check {
    Write-MonitorLog "=== CYCLE DE VERIFICATION ===" -Level "INFO"

    Write-MonitorLog "Verification processus..." -Level "INFO"
    $process = Check-Process

    Write-MonitorLog "Analyse metriques..." -Level "INFO"
    $metrics = Check-Metrics

    Write-MonitorLog "Analyse logs..." -Level "INFO"
    $logs = Check-Logs

    Write-MonitorLog "Analyse tendances..." -Level "INFO"
    $trends = Check-Trends

    Write-MonitorLog "Generation alertes..." -Level "INFO"
    $alerts = New-Alerts -process $process -metrics $metrics -logs $logs

    Write-MonitorLog "Creation rapport consolide..." -Level "INFO"
    $report = New-MonitorReport -process $process -metrics $metrics -logs $logs -trends $trends -alerts $alerts

    Write-MonitorLog "Sauvegarde du rapport..." -Level "INFO"
    $reportJson = $report | ConvertTo-Json -Depth 3
    # UTF-8 sans BOM pour compatibilité Python (json.load attend UTF-8 pur)
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ReportFile, $reportJson, $utf8NoBom)
    Write-MonitorLog "Rapport sauvegarde: $ReportFile" -Level "INFO"

    $critical = ($alerts | Where-Object { $_.level -eq "CRITICAL" } | Measure-Object).Count
    $warning  = ($alerts | Where-Object { $_.level -eq "WARNING" } | Measure-Object).Count
    if ($critical -gt 0 -or $warning -gt 0) {
        Push-CriticalAlerts -alerts $alerts
    }

    if (-not $process.alive) {
        Write-MonitorLog "ROBOT MORT - tentative de redemarrage" -Level "CRITICAL"
        Auto-Restart
    }

    $score = $report.health.score
    $label = $report.health.label
    Write-MonitorLog "RESUME: $label Score=$score/100 | PID=$($process.pid) | DD=$($report.metrics.dd_pct)% | WR=$($report.metrics.wr_pct)% | Alertes: $critical critiques, $warning warnings" -Level "INFO"

    return $report
}

# --- Tache planifiee ---
function Install-TaskScheduler {
    $TaskName = "MT5_Robot_SmartMonitor"
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Daemon -Interval 60"
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Principal $Principal -Description "MT5 FTMO Smart Monitor - verifications automatiques 24/7" -Force
    Write-Host "Tache planifiee '$TaskName' installee (demarrage au boot)" -ForegroundColor Green
}

function Remove-TaskScheduler {
    $TaskName = "MT5_Robot_SmartMonitor"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tache planifiee '$TaskName' retiree" -ForegroundColor Yellow
}

# --- MAIN ---
if (-not (Test-Path $LogDir))    { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
if (-not (Test-Path $RuntimeDir)) { New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null }

Write-MonitorLog "=== SMART MONITOR DEMARRE ===" -Level "INFO"
Write-MonitorLog "Projet: $ProjectRoot" -Level "INFO"
Write-MonitorLog "Intervalle: ${Interval}s" -Level "INFO"

if ($Install) { Install-TaskScheduler; return }
if ($Remove)  { Remove-TaskScheduler; return }

if ($Daemon) {
    Write-MonitorLog "Mode daemon active - verification toutes les ${Interval}s" -Level "INFO"
    while ($true) {
        try {
            Run-Check
        } catch {
            Write-MonitorLog "ERREUR dans Run-Check: $_" -Level "ERROR"
        }
        Start-Sleep -Seconds $Interval
    }
} else {
    $report = Run-Check
    $report | ConvertTo-Json -Depth 3
}
