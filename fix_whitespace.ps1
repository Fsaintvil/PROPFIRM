# Script pour corriger les erreurs d'espaces dans les fichiers
param([string]$FilePath)

$content = Get-Content $FilePath -Raw
# Supprimer les espaces en fin de ligne
$content = $content -replace '[ \t]+\r?\n', "`n"
# Supprimer les espaces dans les lignes vides  
$content = $content -replace '\r?\n[ \t]+\r?\n', "`n`n"
# S'assurer qu'il y a un newline final
if (-not $content.EndsWith("`n")) {
    $content += "`n"
}
Set-Content $FilePath -Value $content -NoNewline