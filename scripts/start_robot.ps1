param(
    [switch]$NoMonitor  # Lance sans moniteur de surveillance
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$LOG_DIR = "$BASE\logs"
$LOG = "$LOG_DIR\simple_robot.log"
$RUNTIME = "$BASE\runtime"
$PID_FILE = "$RUNTIME\robot.pid"

# S'assurer que les répertoires existent
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }

function Log { param($msg) Write-Host "$(Get-Date -Format 'HH:mm:ss') - $msg" }

# 1. Vérifier le jour (weekend = pas de trading)
$dayOfWeek = (Get-Date).DayOfWeek
if ($dayOfWeek -eq [DayOfWeek]::Saturday -or $dayOfWeek -eq [DayOfWeek]::Sunday) {
    Log "⚠️  Weekend ($dayOfWeek) — FTMO bloque les trades. Démarrage possible mais aucun trade ne sera émis."
}

# 2. Nettoyer les PID zombies
if (Test-Path $PID_FILE) {
    $oldPid = Get-Content $PID_FILE -Raw | ForEach-Object { $_.Trim() }
    if ($oldPid -match '^\d+$') {
        $running = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($running) {
            Log "⚠️  Ancien robot PID $oldPid toujours actif — arrêt..."
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }
    Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue
    Log "🧹 PID lock nettoyé"
}

# 3. Nettoyer les fichiers watchdog obsolètes
$watchdogFiles = @("ai_manager.pid", "watchdog_heartbeat.txt", "watchdog_snapshot.json")
foreach ($f in $watchdogFiles) {
    $path = Join-Path $RUNTIME $f
    if (Test-Path $path) { Remove-Item $path -Force -ErrorAction SilentlyContinue }
}

# 4. Vérifier que les écrans MT5 sont prêts
$mt5Process = Get-Process -Name "metatrader*" -ErrorAction SilentlyContinue
if (-not $mt5Process) {
    Log "⚠️  MetaTrader 5 n'est pas lancé ! Lancez-le d'abord."
    Log "   Chemin: C:\Program Files\MetaTrader 5\terminal64.exe"
} else {
    Log "✅ MetaTrader 5 détecté (PID $($mt5Process[0].Id))"
}

# 5. Vérifier que le venv est accessible
$python = "python.exe"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
    Log "❌ python.exe introuvable dans le PATH"
    exit 1
}

# 6. Vérifier les dépendances principales
$hasMT5 = python -c "import MetaTrader5" 2>$null
if ($LASTEXITCODE -ne 0) {
    Log "⚠️  MetaTrader5 non installé — exécutez: pip install MetaTrader5"
}

# 7. Démarrer le robot
Log "🚀 Démarrage du robot MOM20x3..."
$process = Start-Process -FilePath "python.exe" -ArgumentList "main.py" -WorkingDirectory $BASE -NoNewWindow -PassThru -RedirectStandardOutput "$LOG" -RedirectStandardError "$LOG"

Start-Sleep -Seconds 5

# 8. Vérifier le démarrage
$pid = $process.Id
if ($pid -and (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
    Log "✅ Robot démarré avec PID $pid"
    
    # Vérifier les premières lignes de log
    if (Test-Path $LOG) {
        $startupLogs = Get-Content $LOG -Tail 10
        foreach ($line in $startupLogs) {
            if ($line -match "ERROR|CRITICAL|TRACE") {
                Log "   ⚠️  $line"
            }
        }
        # Vérifier connexion MT5
        $mt5ok = $startupLogs -match "MT5.*[Cc]onnect|[Bb]alance"
        if ($mt5ok) {
            Log "✅ Connexion MT5 détectée dans les logs"
        } else {
            Log "⏳ Attente connexion MT5..."
        }
    }
    
    # 9. Démarrer le moniteur (sauf si --NoMonitor)
    if (-not $NoMonitor) {
        Log "📊 Démarrage du moniteur..."
        Start-Process -FilePath "python.exe" -ArgumentList "scripts\monitor.py" -WorkingDirectory $BASE -NoNewWindow -PassThru -RedirectStandardOutput "$LOG_DIR\monitor.log" -RedirectStandardError "$LOG_DIR\monitor.log" | Out-Null
        Start-Sleep -Seconds 2
        Log "✅ Moniteur démarré"
    }
    
    Log ""
    Log "=== RÉSUMÉ ==="
    Log "  PID: $pid"
    Log "  Logs: $LOG"
    Log "  Commandes:"
    Log "    .\scripts\robot.ps1 -Status    → Voir l'état"
    Log "    .\scripts\robot.ps1 -Logs      → Voir les logs"
    Log "    .\scripts\robot.ps1 -Stop      → Arrêter le robot"
    Log "    taskkill /F /IM python.exe     → Arrêt forcé"
} else {
    Log "❌ ÉCHEC au démarrage du robot"
    Log "   Vérifiez les logs: $LOG"
    exit 1
}
