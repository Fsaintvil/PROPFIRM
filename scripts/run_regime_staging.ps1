# Helper PowerShell pour lancer une exécution de staging du détecteur de régimes
# Usage (depuis la racine du repo) :
#   .\scripts\run_regime_staging.ps1
# Ce script active des options opt-in non invasives :
# - REGIME_VALIDATE_INPUT=1 : active la validation d'entrée
# - REGIME_VALIDATE_DUMP=1  : écrit un rapport JSON en cas d'échec
# - REGIME_SAFE_CLEAN=1    : applique un nettoyage conservateur des features

param()

Set-Location -Path "$PSScriptRoot\.."

# Définit les variables d'environnement pour ce processus
Set-Item -Path Env:PYTHONPATH -Value (Get-Location).Path
Set-Item -Path Env:REGIME_VALIDATE_INPUT -Value '1'
Set-Item -Path Env:REGIME_VALIDATE_DUMP -Value '1'
Set-Item -Path Env:REGIME_SAFE_CLEAN -Value '1'

Write-Host "Lancement staging: REGIME_VALIDATE_INPUT=1, REGIME_VALIDATE_DUMP=1, REGIME_SAFE_CLEAN=1"

& 'C:/Users/saint/AppData/Local/Programs/Python/Python313/python.exe' -c "import importlib; m = importlib.import_module('scripts.market_regime_detection'); m.main()"

Write-Host "Run staging terminé. Vérifiez artifacts/diagnostics pour les rapports (si présents)."
