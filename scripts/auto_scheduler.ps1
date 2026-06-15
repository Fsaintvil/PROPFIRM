param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status
)

$BASE = "C:\Users\saint\Documents\MT5_FTMO_IA.7"
$PYTHON = "python.exe"
$TASK_START = "MT5-Robot-Start"
$TASK_STOP = "MT5-Robot-Stop"

function Log { param($msg) Write-Host "$(Get-Date -Format 'HH:mm:ss') - $msg" }

# Vérifier Admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and ($Install -or $Remove)) {
    Log "⚠️  Installation/Suppression nécessite ADMIN. Relance en admin..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Install"
    exit
}

if ($Status) {
    Write-Host "=== TACHES PLANIFIEES ==="
    schtasks /Query /TN "$TASK_START" /FO LIST /V 2>$null | Select-String "TaskName|Next Run|Status"
    if (-not $?) { Write-Host "  $TASK_START : ABSENTE" }
    schtasks /Query /TN "$TASK_STOP" /FO LIST /V 2>$null | Select-String "TaskName|Next Run|Status"
    if (-not $?) { Write-Host "  $TASK_STOP : ABSENTE" }
    exit
}

if ($Remove) {
    Log "Suppression des taches planifiees..."
    schtasks /Delete /TN "$TASK_START" /F 2>$null
    schtasks /Delete /TN "$TASK_STOP" /F 2>$null
    Log "Taches supprimees."
    exit
}

if ($Install) {
    Log "Installation des taches planifiees..."

    # Tâche START : Lun-Ven 00:00 UTC (02:00 heure Paris)
    # Utilise pythonw.exe (sans console) + start_robot.ps1
    schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI `
        /TN "$TASK_START" `
        /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \`"${BASE}\scripts\start_robot.ps1\`"" `
        /ST 02:00 `
        /SD "01/01/2026" `
        /RL HIGHEST `
        /F

    if ($?) {
        Log "✅ $TASK_START creee : Lun-Ven 02:00 (00:00 UTC) → start_robot.ps1"
    } else {
        Log "❌ Erreur creation $TASK_START"
    }

    # Tâche STOP : Ven 23:00 UTC (Sam 01:00 Paris) — arrêt weekend
    schtasks /Create /SC WEEKLY /D SAT `
        /TN "$TASK_STOP" `
        /TR "taskkill /F /IM python.exe" `
        /ST 01:00 `
        /SD "01/01/2026" `
        /RL HIGHEST `
        /F

    if ($?) {
        Log "✅ $TASK_STOP creee : Sam 01:00 (Ven 23:00 UTC) → taskkill python.exe"
    } else {
        Log "❌ Erreur creation $TASK_STOP"
    }

    Log ""
    Log "Resume:"
    schtasks /Query /TN "$TASK_START" /FO LIST /V 2>$null | Select-String "TaskName|Next Run|Status|Schedule"
    schtasks /Query /TN "$TASK_STOP" /FO LIST /V 2>$null | Select-String "TaskName|Next Run|Status|Schedule"
    exit
}

Write-Host "Usage:"
Write-Host "  .\scripts\auto_scheduler.ps1 -Install   → Installer les taches (admin requis)"
Write-Host "  .\scripts\auto_scheduler.ps1 -Remove    → Supprimer les taches (admin requis)"
Write-Host "  .\scripts\auto_scheduler.ps1 -Status    → Voir l'etat"
