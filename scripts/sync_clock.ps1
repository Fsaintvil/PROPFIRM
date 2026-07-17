<#
.SYNOPSIS
    Synchronise l'horloge Windows avec un serveur NTP.
    À exécuter au démarrage du robot pour éviter le décalage de 5h observé le 9 Juillet 2026.

.DESCRIPTION
    Vérifie le décalage avec time.windows.com et le corrige si > 1 seconde.
    Log dans runtime/clock_sync.log.

    Usage: .\scripts\sync_clock.ps1
#>

$logFile = Join-Path $PSScriptRoot "..\runtime\clock_sync.log"
$ntpServer = "time.windows.com"
$maxOffsetSec = 1.0

# Timestamp actuel
$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Vérifier le temps NTP
try {
    $client = New-Object System.Net.Sockets.UdpClient("$ntpServer", 123)
    $client.Client.ReceiveTimeout = 5000  # 5s timeout

    # Paquet NTP (RFC 4330 — SNTP v4)
    $bytes = New-Object byte[] 48
    $bytes[0] = 0x1B  # Mode client, version 4

    [void]$client.Send($bytes, 48)
    $serverBytes = $client.Receive([ref]$null)
    $client.Close()

    # Extraire le timestamp de référence (octets 40-43)
    $intPart = [bigint]0
    for ($i = 40; $i -le 43; $i++) {
        $intPart = ($intPart -shl 8) -bor $serverBytes[$i]
    }
    $fracPart = [bigint]0
    for ($i = 44; $i -le 47; $i++) {
        $fracPart = ($fracPart -shl 8) -bor $serverBytes[$i]
    }
    $ntpTime = [double]($intPart) + ([double]($fracPart) / [math]::Pow(2, 32))
    # Convertir NTP epoch (1/1/1900) en Unix epoch (1/1/1970)
    $unixTime = $ntpTime - 2208988800.0
    $ntpDate = [DateTimeOffset]::FromUnixTimeSeconds([long]$unixTime).LocalDateTime
}
catch {
    "$now | ERREUR: Impossible de joindre $ntpServer — $_" | Out-File $logFile -Append
    Write-Warning "NTP sync failed: $_"
    exit 1
}

# Calculer le décalage
$localTime = Get-Date
$offset = ($localTime - $ntpDate).TotalSeconds

if ([math]::Abs($offset) -le $maxOffsetSec) {
    "$now | OK: décalage $([math]::Round($offset, 2))s (seuil ${maxOffsetSec}s) — aucun ajustement nécessaire" | Out-File $logFile -Append
    exit 0
}

# Corriger l'horloge
try {
    # Nécessite admin — sinon juste warning
    w32tm /resync /nowait 2>$null
    if ($LASTEXITCODE -eq 0) {
        "$now | CORRIGÉ: horloge décalée de $([math]::Round($offset, 1))s → resync effectué" | Out-File $logFile -Append
        Write-Host "Clock synced: offset was $([math]::Round($offset, 1))s"
    } else {
        "$now | WARNING: décalage de $([math]::Round($offset, 1))s mais w32tm /resync a échoué (code $LASTEXITCODE)" | Out-File $logFile -Append
        Write-Warning "Clock offset: $([math]::Round($offset, 1))s (w32tm resync failed)"
    }
}
catch {
    "$now | WARNING: décalage de $([math]::Round($offset, 1))s mais correction impossible: $_" | Out-File $logFile -Append
    Write-Warning "Clock offset: $([math]::Round($offset, 1))s (cannot fix: $_)"
}
