<#
.SYNOPSIS
Démarrage automatique et autonome du robot Forex institutionnel avec alerte 15 min si aucun signal d'envoi.
.DESCRIPTION
Attente ouverture marché, lancement robot, SL/TP par symbole, contrôle anti-double PID,
interdiction nouvelles entrées avant fermeture, et alerte si aucun signal MT5 15 min après ouverture.
#>

# === CONFIGURATION GLOBALE ===

$ErrorActionPreference = "Stop"
$baseDir = "C:\Users\saint\Documents\PROPFIRM"
Set-Location $baseDir

# === VARIABLES D'ENVIRONNEMENT ===

$env:PYTHONPATH = "."
$env:AI_AUTOMATE = "1"
$env:META_LEARNING_TRADING_SYSTEM = "1"
$env:REINFORCEMENT_LEARNING_TRADING_SYSTEM = "1"
$env:MULTI_ASSET_PORTFOLIO = "1"
$env:LIVE_ENGINE_LIGHT_MODE = "0"
$env:ALLOW_MT5_SEND = "1"

# Stop-loss et Take-profit par instrument

$env:PER_SYMBOL_SL_JSON = '{"BTCUSD":300,"ETHUSD":20,"XAUUSD":50,"USDCAD":25,"AUDNZD":20,"EURJPY":30,"GBPCHF":35,"NZDJPY":25,"EURUSD":25,"EURAUD":25,"US500.cash":15,"JP225.cash":25}'
$env:PER_SYMBOL_TP_JSON = '{"BTCUSD":600,"ETHUSD":40,"XAUUSD":100,"USDCAD":50,"AUDNZD":40,"EURJPY":60,"GBPCHF":70,"NZDJPY":50,"EURUSD":50,"EURAUD":50,"US500.cash":30,"JP225.cash":50}'

# === HORAIRES FOREX (timezone-aware) ===
# Use the system local timezone explicitly and show UTC equivalents.
$tz = [System.TimeZoneInfo]::Local
$now = Get-Date
# Build local dates from today's date in the local timezone (midnight local)
$todayLocal = $now.Date
# Market open/close times defined in local clock (project convention)
$marketOpenLocal = $todayLocal.AddHours(22).AddMinutes(5)
$marketCloseLocal = $todayLocal.AddDays(5).AddHours(21).AddMinutes(30)
# Convert to UTC for clarity and any components requiring UTC
$marketOpenUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($marketOpenLocal, $tz)
$marketCloseUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($marketCloseLocal, $tz)
$timeToOpen = ($marketOpenLocal - $now).TotalMinutes
$timeToClose = ($marketCloseLocal - $now).TotalMinutes

Write-Output ("System timezone: {0}" -f $tz.Id)
Write-Output ("now (local): {0}" -f $now.ToString('o'))
Write-Output ("marketOpen (local): {0}" -f $marketOpenLocal.ToString('o'))
Write-Output ("marketOpen (UTC): {0}" -f $marketOpenUtc.ToString('o'))
Write-Output ("marketClose (local): {0}" -f $marketCloseLocal.ToString('o'))
Write-Output ("marketClose (UTC): {0}" -f $marketCloseUtc.ToString('o'))

# === ATTENTE OUVERTURE ===

if ($timeToOpen -gt 0) {
    Write-Output "🕒 Marché fermé. Attente jusqu'à ouverture à $marketOpenLocal ..."
    # Compute seconds to sleep safely and clamp to a sane maximum to avoid Int32 overflow
    $diffSeconds = ($marketOpenLocal - $now).TotalSeconds
    if ($diffSeconds -lt 0) { $sleepSec = 0 } else { $sleepSec = [math]::Min([math]::Max($diffSeconds,0), 7*24*3600) }
    # Start-Sleep expects an Int32, ensure safe cast
    Start-Sleep -Seconds ([int]$sleepSec)
}

# === CONTRÔLE FERMETURE PROCHAINE ===

if ($timeToClose -lt 30) {
    Write-Output "⚠️ Marché proche de la fermeture ($([int]$timeToClose) min restantes)."
    $env:ALLOW_NEW_ENTRIES = "0"
} else {
    $env:ALLOW_NEW_ENTRIES = "1"
}

# === ANTI-DOUBLE LANCEMENT ===

$pidFiles = Get-ChildItem "$baseDir\artifacts\live_trading" -Filter "production_run_*.pid" -ErrorAction SilentlyContinue
if ($pidFiles -and $pidFiles.Count -gt 0) {
    Write-Warning "🚫 Processus de production déjà actif (PID détecté). Abandon du lancement."
    exit 1
}

# === LANCEMENT DU ROBOT ===

Write-Output "🚀 Lancement automatique de la production Forex..."
Start-Process -FilePath "powershell.exe" -ArgumentList "-File", "$baseDir\tools\run_production.ps1", "-Detached" -NoNewWindow

# === ALERTE SI PAS DE SIGNAL APRES 15 MINUTES ===

$alertDelayMinutes = 15
$signalDetected = $false
$alertCheckTime = (Get-Date).AddMinutes($alertDelayMinutes)
Write-Output "⏱ Surveillance des signaux : alerte si aucun envoi après $alertDelayMinutes minutes..."

while ((Get-Date) -lt $alertCheckTime) {
    # Vérification logs de production pour signal MT5
    $logFiles = Get-ChildItem "$baseDir\artifacts\live_trading" -Filter "production_run_*.out.log" | Sort-Object LastWriteTime -Descending
    if ($logFiles.Count -gt 0) {
        $lastLog = Get-Content $logFiles[0].FullName -Tail 50
        foreach ($line in $lastLog) {
            if ($line -match 'Envoi MT5 effectué|Send order') {
                $signalDetected = $true
                break
            }
        }
    }
    if ($signalDetected) { break }
    Start-Sleep -Seconds 30
}

if (-not $signalDetected) {
    Write-Warning "⚠️ Aucun signal d'envoi détecté 15 minutes après l'ouverture du marché Forex!"
    # Optionnel : webhook/email
    # if ($env:NOTIFY_ON_CRITICAL) { Invoke-RestMethod -Uri $env:NOTIFY_ON_CRITICAL -Method Post -Body (ConvertTo-Json @{ message = 'Aucun signal MT5 15min après ouverture' }) }
}

# === SURVEILLANCE CONTINUE ET ARRÊT DYNAMIQUE ===

while ($true) {
    $now = Get-Date
    # Use the timezone-aware local market close variable
    $timeToClose = ($marketCloseLocal - $now).TotalMinutes

    if ($timeToClose -le 30) {
        Write-Output "🛑 Fermeture imminente du marché. Arrêt contrôlé des nouvelles positions."
        $env:ALLOW_NEW_ENTRIES = "0"
    }

    if ($timeToClose -le 0) {
        Write-Output "✅ Marché Forex fermé. Extinction complète du système."
        Stop-Process -Name "python" -Force -ErrorAction SilentlyContinue
        break
    }

    Start-Sleep -Seconds 300
}
