<#
.SYNOPSIS
    Market Watch — Surveillance automatique du robot MT5
.DESCRIPTION
    Tourne en arrière-plan, analyse le marché en live, écrit dans runtime/dashboard.txt
    Toutes les 3 minutes : positions, régimes, ADX, DL, FTMO, signaux, alertes.
    Utilisation : powershell -WindowStyle Hidden -File scripts\market-watch.ps1
.PARAMETER Stop
    Arrête le market-watch (via PID file)
.PARAMETER Status
    Affiche le dernier dashboard enregistré
#>
param([switch]$Stop,[switch]$Status)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$PID_FILE = "$BASE\runtime\market_watch.pid"
$DASHBOARD = "$BASE\runtime\dashboard.txt"
$LOG = "$BASE\logs\market_watch.log"

function Log { param($msg) $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts - $msg" | Out-File -FilePath $LOG -Append -Encoding UTF8 }

if ($Stop) {
    if (Test-Path $PID_FILE) {
        $oldPid = Get-Content $PID_FILE -Raw
        try { Stop-Process -Id $oldPid -Force -ErrorAction Stop; Log "Market-Watch PID $oldPid arrete" } catch {}
        Remove-Item $PID_FILE -Force
    }
    Write-Host "Market-Watch arrete" -ForegroundColor Green; exit
}

if ($Status) {
    if (Test-Path $DASHBOARD) { Get-Content $DASHBOARD } else { Write-Host "Pas de dashboard disponible" -ForegroundColor Yellow }
    exit
}

# Éviter les doublons
if (Test-Path $PID_FILE) {
    $oldPid = Get-Content $PID_FILE -Raw
    $p = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($p) { Write-Host "⚠️ Market-Watch deja actif (PID $oldPid). Utilise -Stop puis relance." -ForegroundColor Yellow; exit }
    else { Remove-Item $PID_FILE -Force }
}

$script:watchPid = (Get-Process -Id $pid).Id
$script:watchPid | Out-File $PID_FILE -Force
Log "=== Market-Watch demarre (PID $script:watchPid) ==="

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $output = @()
    $output += "╔══════════════════════════════════════════════════════╗"
    $output += "║     📊 MARKET WATCH — $($ts)     ║"
    $output += "╚══════════════════════════════════════════════════════╝"
    
    # ── 1. VÉRIFICATION PROCESSUS ──
    $robotProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "main\.py" }
    if ($robotProc) {
        $memMB = [math]::Round($robotProc.WorkingSetSize / 1MB)
        $output += "✅ Robot: PID $($robotProc.ProcessId) | $memMB MB RAM"
    } else {
        $output += "❌ Robot: ARRÊTÉ"
    }
    
    # ── 2. FTMO REPORT ──
    $report = Get-Content "$BASE\runtime\ftmo_report.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    if ($report) {
        $output += "🏆 FTMO: $($report.status) | Eq=`$$($report.equity) | Prog=$($report.profit_progress) | DD=$($report.dd_from_peak)"
        $output += "   Balance=`$$($report.balance) | PnL=`$$($report.pnl) | Trades=$($report.total_trades) | WR=$($report.win_rate)"
        $output += "   Jours=$($report.trading_days)/$($report.days_remaining) restants | Pertes consec=$($report.consecutive_losses)"
    }
    
    # ── 3. LOGS : Positions, Régimes, Signaux ──
    $logFile = "$BASE\logs\simple_robot.log"
    $lines = @()
    if (Test-Path $logFile) { $lines = Get-Content $logFile -Tail 2000 }
    
    # Dernier cycle
    $cycleLine = $lines | Select-String "\[Cycle \d+\]" | Select-Object -Last 1
    if ($cycleLine) { $output += "📊 $($cycleLine.Line.Trim())" }
    
    # Dernières positions
    $posLine = $lines | Select-String "Positions:" | Select-Object -Last 1
    if ($posLine) { $output += "📈 $($posLine.Line.Trim())" }
    
    # Régimes par symbole (dernier connu)
    $regimes = @{}
    $adxVals = @{}
    $dlVals = @{}
    foreach ($l in $lines) {
        if ($l -match "\[VIGIL\] (\w+): regime=(\w+) DL=\w+.*?score=([\d.]+).*?ADX=(\d+)") {
            $regimes[$matches[1]] = $matches[2]
            $adxVals[$matches[1]] = $matches[4]
            $dlVals[$matches[1]] = $matches[3]
        }
    }
    
    if ($regimes.Count -gt 0) {
        $output += "🌍 Régimes:"
        foreach ($s in ($regimes.Keys | Sort-Object)) {
            $r = $regimes[$s]
            $a = $adxVals[$s]
            $d = $dlVals[$s]
            $icon = switch ($r) { "TREND_UP" { "🟢" } "TREND_DOWN" { "🔴" } "RANGING" { "🟡" } "HIGH_VOL" { "🟠" } "LOW_VOL" { "🔵" } default { "⚪" } }
            $adxIcon = if ([int]$a -ge 25) { "⚡" } elseif ([int]$a -ge 20) { "·" } else { "↓" }
            $dlIcon = if ([double]$d -ge 0.60) { "🧠" } else { "·" }
            $output += "   $icon $s ${adxIcon}ADX=$a $dlIcon DL=$d $r"
        }
    }
    
    # Signaux ICT
    $signals = $lines | Select-String "\[ICT\] \w+: \w+ \| score="
    if ($signals) {
        $highSigs = @()
        foreach ($s in $signals) {
            if ($s -match "\[ICT\] (\w+): (\w+) \| score=([\d.]+)") {
                $sc = [double]$matches[3]
                if ($sc -ge 0.80) { $highSigs += "$($matches[1]) $($matches[2]) score=$sc ⚡" }
            }
        }
        if ($highSigs.Count -gt 0) {
            $output += "⚡ Signaux forts (≥0.80):"
            foreach ($h in $highSigs[-3..-1]) { $output += "   $h" }
        }
    }
    
    # Ordres récents
    $orders = $lines | Select-String "PlaceOrder OK" | Select-Object -Last 3
    if ($orders) {
        $output += "✅ Ordres recents:"
        foreach ($o in $orders) { $output += "   $($o.Line.Trim())" }
    }
    
    # Trades fermés
    $closed = $lines | Select-String "TRADE CLOSED" | Select-Object -Last 2
    if ($closed) {
        $output += "🔒 Trades fermes:"
        foreach ($c in $closed) { $output += "   $($c.Line.Trim())" }
    }
    
    # Erreurs récentes
    $errors = $lines | Select-String "ERROR" | Select-Object -Last 3
    if ($errors) { $output += "⚠️ Dernieres erreurs: $($errors.Count)" }
    else { $output += "✅ Aucune erreur" }
    
    # ── 4. ANALYSE MARCHÉ ──
    $output += "📋 Analyse marche:"
    
    # Compter les régimes
    $rCount = @{}
    foreach ($r in $regimes.Values) { 
        if (-not $rCount.ContainsKey($r)) { $rCount[$r] = 0 }
        $rCount[$r]++
    }
    $rSummary = ($rCount.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ", "
    $output += "   Regimes: $rSummary"
    
    # ADX moyen
    $adxList = $adxVals.Values | ForEach-Object { [int]$_ }
    if ($adxList.Count -gt 0) {
        $avgAdx = [math]::Round(($adxList | Measure-Object -Average).Average, 0)
        $trending = ($adxList | Where-Object { $_ -ge 25 }).Count
        $ranging = ($adxList | Where-Object { $_ -lt 25 }).Count
        $output += "   ADX: moyen=$avgAdx | trending=$trending | ranging=$ranging"
    }
    
    # DL moyen
    $dlList = $dlVals.Values | ForEach-Object { [double]$_ }
    if ($dlList.Count -gt 0) {
        $avgDl = [math]::Round(($dlList | Measure-Object -Average).Average, 2)
        $greyZone = ($dlList | Where-Object { $_ -ge 0.50 -and $_ -lt 0.60 }).Count
        $safeZone = ($dlList | Where-Object { $_ -ge 0.60 }).Count
        $output += "   DL: moyen=$avgDl | grey=$greyZone | safe=$safeZone"
    }
    
    # Progression
    if ($report) {
        $progStr = $report.profit_progress -replace '%',''
        try { $progVal = [double]$progStr } catch { $progVal = -99 }
        $trendIcon = if ($progVal -ge 0) { "🟢" } elseif ($progVal -ge -3) { "🟡" } else { "🔴" }
        $output += "   $trendIcon FTMO Progress: $($report.profit_progress)"
    }
    
    $output += "╚══════════════════════════════════════════════════════╝"
    $output += ""
    
    # Écrire le dashboard
    $output -join "`n" | Out-File $DASHBOARD -Force -Encoding UTF8
    
    Log "Dashboard mis a jour ($($regimes.Count) symboles, $($orders.Count) ordres)"
    
    # Rotation si > 1MB
    if ((Get-Item $LOG -ErrorAction SilentlyContinue).Length -gt 1MB) {
        Remove-Item $LOG -Force; Log "Rotation log"
    }
    
    Start-Sleep -Seconds 180
}
