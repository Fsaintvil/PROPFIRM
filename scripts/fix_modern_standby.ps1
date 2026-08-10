# Fix Modern Standby (S0) — à exécuter en ADMINISTRATEUR
# Désactive la veille moderne qui fige le robot de trading
$ErrorActionPreference = 'Continue'
$log = "C:\Users\saint\Documents\MT5_FTMO_IA.7\logs\platformaao_setup.log"
$logLines = @()

# 1. powercfg : tous les timeouts à 0
powercfg /change standby-timeout-ac 0 2>&1 | Out-Null
powercfg /change standby-timeout-dc 0 2>&1 | Out-Null
powercfg /change hibernate-timeout-ac 0 2>&1 | Out-Null
powercfg /change hibernate-timeout-dc 0 2>&1 | Out-Null
powercfg /change monitor-timeout-ac 0 2>&1 | Out-Null
powercfg /change monitor-timeout-dc 0 2>&1 | Out-Null
$logLines += "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] powercfg: timeouts a 0"

# 2. Registre : désactiver Modern Standby S0
$keyPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Power'
try {
    if (-not (Get-Item -Path "$keyPath\PlatformAoAcOverride" -ErrorAction SilentlyContinue)) {
        New-Item -Path $keyPath -Name 'PlatformAoAcOverride' -Force | Out-Null
    }
    Set-ItemProperty -Path $keyPath -Name 'PlatformAoAcOverride' -Value 0
    $v = (Get-ItemProperty -Path $keyPath).PlatformAoAcOverride
    $logLines += "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] OK PlatformAoAcOverride = $v (0 = Modern Standby OFF)"
} catch {
    $logLines += "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERREUR registre: $($_.Exception.Message)"
}

$logLines | Add-Content -Path $log -Encoding UTF8
# Afficher un récap visible à l'écran
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  FIX MODERN STANDBY APPLIQUE" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
$logLines | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "  Un REDEMARRAGE est recommande pour que" -ForegroundColor Yellow
Write-Host "  la desactivation prenne plein effet." -ForegroundColor Yellow
Write-Host "==============================================" -ForegroundColor Green
Start-Sleep -Seconds 5
