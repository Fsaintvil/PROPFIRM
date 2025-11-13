Param()
$ErrorActionPreference = 'Stop'

Write-Output "[STOP] Création du marqueur emergency_stop"
New-Item -ItemType File -Path control\emergency_stop -Force | Out-Null

Write-Output "[STOP] Recherche des processus start_production.py / live_run_controller.py"
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'start_production.py' -or $_.CommandLine -match 'live_run_controller.py') }
if (-not $procs) {
  Write-Output "[STOP] Aucun processus de production détecté"
} else {
  foreach ($p in $procs) {
    Write-Output "[STOP] Tentative d'arrêt PID=$($p.ProcessId) Name=$($p.Name)"
    try { Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue } catch {}
  }
}

Write-Output "[STOP] Nettoyage des lockfiles"
Get-ChildItem control -Filter '*production*.lock' -ErrorAction SilentlyContinue | ForEach-Object { try { Remove-Item $_.FullName -Force } catch {} }

Write-Output "[STOP] Fin stop_production.ps1"