<#
Auto Threshold Monitor (PowerShell)
- Affiche le performance_summary si présent
- Ajuste BASE_CONFIDENCE_THRESHOLD si AUTO_THRESHOLD_MODE=1 (en session)
- Arrête les jobs PowerShell dont la durée dépasse MaxHours:ExtraMinutes
#>

[CmdletBinding()]
param(
    [int] $MaxHours = 12,
    [int] $ExtraMinutes = 1,
    [string] $PerfFile = (Join-Path (Join-Path 'artifacts' 'live_trading') 'performance_summary.json')
)

$ErrorActionPreference = 'Stop'

# --- Garde-fou singleton: éviter chevauchement de monitor ---
try {
    $selfName = 'auto_threshold_monitor.ps1'
    $running = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match [regex]::Escape($selfName)) }
    if ($null -ne $running) {
        # Si plus d'une instance (celle-ci incluse), se retirer pour préserver un seul monitor.
        if (($running | Measure-Object).Count -gt 1) {
            Write-Warning "[ATM] Une autre instance est détectée, fin immédiate pour préserver le singleton."
            return
        }
    }
} catch {}

# --- Lecture des performances (supporte plusieurs schémas de clés) ---
$perf = $null
if (Test-Path $PerfFile) {
    try {
        $perf = Get-Content $PerfFile -Raw | ConvertFrom-Json
    } catch {
        Write-Warning ("Impossible de lire {0}: {1}" -f $PerfFile, $_)
    }
} else {
    Write-Warning ("Fichier non trouvé : {0}" -f $PerfFile)
}

$winratePct = $null
$sharpe = $null
$avgRR = $null
if ($perf) {
    # Essaye win_rate (0.0-1.0) puis winrate (% ou 0.0-1.0) et rr/sharpe
    if ($perf.PSObject.Properties.Name -contains 'win_rate') {
        try { $winratePct = [double]$perf.win_rate * 100 } catch {}
    } elseif ($perf.PSObject.Properties.Name -contains 'winrate') {
        try {
            $winratePct = [double]$perf.winrate
            if ($winratePct -le 1) { $winratePct = $winratePct * 100 }
        } catch {}
    }
    if ($perf.PSObject.Properties.Name -contains 'sharpe') {
        try { $sharpe = [double]$perf.sharpe } catch {}
    }
    if ($perf.PSObject.Properties.Name -contains 'avg_rr') {
        try { $avgRR = [double]$perf.avg_rr } catch {}
    }
}

# --- Lecture du seuil courant ---
$baseThreshold = 0.55
try {
    if ($env:BASE_CONFIDENCE_THRESHOLD) { $baseThreshold = [double]$env:BASE_CONFIDENCE_THRESHOLD }
} catch {}
$upperBound = 0.85

if ($null -ne $winratePct) { $wrText = [string]([math]::Round($winratePct,2)) } else { $wrText = 'n/a' }
if ($null -ne $avgRR) { $rrText = [string]([math]::Round($avgRR,2)) } else { $rrText = 'n/a' }
if ($null -ne $sharpe) { $shText = [string]([math]::Round($sharpe,2)) } else { $shText = 'n/a' }
Write-Output ("[perf] winrate={0}% rr={1} sharpe={2} base={3}" -f $wrText, $rrText, $shText, $baseThreshold)

# --- Mode adaptatif automatique ---
if ($env:AUTO_THRESHOLD_MODE -eq '1') {
    $newThreshold = $baseThreshold
    # Règle 1: bons perfs (schema 1)
    if ($null -ne $winratePct -and $null -ne $sharpe) {
        if ($winratePct -ge 55 -and $sharpe -ge 0.8) { $newThreshold = [math]::Min($baseThreshold + 0.10, $upperBound) }
    }
    # Règle 2: bons perfs (schema 2)
    if ($null -ne $winratePct -and $null -ne $avgRR) {
        if ($winratePct -gt 60 -and $avgRR -gt 1.2) { $newThreshold = [math]::Min($baseThreshold + 0.05, $upperBound) }
        elseif ($winratePct -lt 40) { $newThreshold = [math]::Max($baseThreshold - 0.05, 0.55) }
    }

    if ($newThreshold -ne $baseThreshold) {
        $rounded = [math]::Round($newThreshold, 2)
        $env:BASE_CONFIDENCE_THRESHOLD = [string]$rounded
        Write-Output "[threshold] BASE_CONFIDENCE_THRESHOLD=$($env:BASE_CONFIDENCE_THRESHOLD)"
        # Persistance légère côté projet
        try {
            $ctrl = 'control'
            if (-not (Test-Path $ctrl)) { New-Item -ItemType Directory -Path $ctrl -Force | Out-Null }
            $file = Join-Path -Path $ctrl -ChildPath 'base_confidence_threshold.txt'
            Set-Content -Path $file -Value $env:BASE_CONFIDENCE_THRESHOLD -Encoding UTF8
        } catch {}
    }
}

# --- Mettre à jour le statut du monitor (incluant le dernier seuil) ---
try {
    $statusFile = Join-Path (Join-Path 'artifacts' 'live_trading') 'monitor_status.json'
    $now = Get-Date
    $thrNow = $env:BASE_CONFIDENCE_THRESHOLD
    if (-not $thrNow) { $thrNow = [string]$baseThreshold }

    $status = $null
    if (Test-Path $statusFile) {
        try { $status = Get-Content $statusFile -Raw | ConvertFrom-Json } catch {}
    }
    if (-not $status) { $status = [pscustomobject]@{} }

    # Conserver les champs existants et mettre à jour/ajouter des clés clés
    $status | Add-Member -NotePropertyName 'timestamp' -NotePropertyValue $now.ToString('o') -Force
    $status | Add-Member -NotePropertyName 'threshold_last' -NotePropertyValue $thrNow -Force
    $status | Add-Member -NotePropertyName 'monitor_heartbeat' -NotePropertyValue $now.ToString('o') -Force

    $json = $status | ConvertTo-Json -Depth 5
    Set-Content -Path $statusFile -Value $json -Encoding UTF8
} catch {}

# --- Arrêter les jobs PowerShell au-delà de MaxHours:ExtraMinutes ---
$maxRun = New-TimeSpan -Hours $MaxHours -Minutes $ExtraMinutes
Get-Job -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $start = $_.StartTime
        if ($null -ne $start) {
            $elapsed = (Get-Date) - $start
            if ($elapsed -gt $maxRun) {
                Write-Output "[cleanup] stopping job Id=$($_.Id) Name=$($_.Name) Elapsed=$elapsed"
                Stop-Job -Id $_.Id -ErrorAction SilentlyContinue
                Receive-Job -Id $_.Id -ErrorAction SilentlyContinue | Out-Null
                Remove-Job -Id $_.Id -ErrorAction SilentlyContinue
            }
        }
    } catch {}
}

Write-Output "auto_threshold_monitor.ps1 done."
