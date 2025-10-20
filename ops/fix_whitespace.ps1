# Script PowerShell pour nettoyer les espaces en fin de ligne et lignes vides avec espaces
$files = @(
    "scripts\production_monitor.py",
    "scripts\quick_decision.py"
)

foreach ($file in $files) {
    if (Test-Path $file) {
        Write-Host "Nettoyage de $file..."
        $content = Get-Content $file -Raw
        # Supprime les espaces en fin de ligne
        $content = $content -replace '\s+$', '' -split "`n" | ForEach-Object { $_.TrimEnd() }
        # Rejoint avec des sauts de ligne Unix puis convertit en Windows
        $content = ($content -join "`n").TrimEnd() + "`n"
        Set-Content $file $content -NoNewline
        Write-Host "✓ $file nettoyé"
    }
}
Write-Host "Nettoyage des espaces terminé."