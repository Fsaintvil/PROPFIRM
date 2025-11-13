Param(
    [int] $Cycles = 6,
    [int] $IntervalMinutes = 31
)
$ErrorActionPreference = 'Stop'

# --- Garde-fou singleton: éviter plusieurs schedulers concurrents ---
try {
    $selfName = 'schedule_auto_threshold_monitor.ps1'
    $running = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match [regex]::Escape($selfName)) }
    if ($null -ne $running -and ($running | Measure-Object).Count -gt 1) {
        Write-Warning "[SCHED] Une autre instance du scheduler est détectée. Arrêt pour préserver le singleton."
        return
    }
} catch {}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
$monitorScript = Join-Path $scriptRoot 'tools\auto_threshold_monitor.ps1'
if (-not (Test-Path $monitorScript)) { Write-Error "auto_threshold_monitor.ps1 introuvable: $monitorScript"; exit 2 }

# --- Activer le mode adaptatif par défaut pour le monitor ---
try {
    if (-not $env:AUTO_THRESHOLD_MODE) { $env:AUTO_THRESHOLD_MODE = '1' }
} catch {}

Write-Output "[SCHED] Lancement scheduler auto-threshold ($Cycles cycles / $IntervalMinutes min)"
for ($i = 0; $i -lt $Cycles; $i++) {
    Write-Output "[SCHED] Cycle $($i+1)/$Cycles -> execution monitor"
    try {
        # Charger le seuil persistant s'il existe pour alimenter la session du monitor
        $ctrlFile = Join-Path $scriptRoot 'control\base_confidence_threshold.txt'
        if (Test-Path $ctrlFile) {
            try {
                $val = (Get-Content -Path $ctrlFile -Raw).Trim()
                if ($val) { $env:BASE_CONFIDENCE_THRESHOLD = $val }
            } catch {}
        }

        pwsh -NoProfile -ExecutionPolicy Bypass -File $monitorScript | ForEach-Object { Write-Output "[ATM] $_" }
    } catch {
        Write-Warning ("Erreur execution cycle {0}: {1}" -f $i, $_)
    }
    if ($i -lt ($Cycles - 1)) { Start-Sleep -Minutes $IntervalMinutes }
}
Write-Output "[SCHED] Terminé (auto-stop)."