#!/usr/bin/env pwsh
<#
Exemple de hook pre-commit PowerShell pour repérer rapidement les gros répertoires de backups ou fichiers .bak avant commit.
Ce script est volontairement conservateur : il émet des warnings et empêche le commit seulement si --fail-on-warning est utilisé.
#>
param(
    [switch]$FailOnWarning
)

Write-Host "[pre-commit] Vérification rapide des fichiers de backup et duplicatas potentiels..."

# Patterns à surveiller (gitwildmatch)
$patterns = @('*.bak','*~','*_backup_*','artifacts*','backups/*')

$found = @()
foreach($p in $patterns){
    $matches = git ls-files --others --cached --exclude-standard | Where-Object { $_ -like $p }
    if($matches){ $found += $matches }
}

if($found.Count -gt 0){
    Write-Host "[pre-commit] Attention : fichiers correspondant aux patterns de backup trouvés:" -ForegroundColor Yellow
    $found | ForEach-Object { Write-Host "  $_" }
    if($FailOnWarning){
        Write-Host "[pre-commit] Echec du commit à cause de fichiers de backup détectés." -ForegroundColor Red
        exit 1
    }
}

Write-Host "[pre-commit] Vérification terminée." -ForegroundColor Green
exit 0
