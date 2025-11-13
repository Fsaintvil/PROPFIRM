<#
.SYNOPSIS
    Écrit les variables définies dans `docs/production_env_defaults.json` en tant que variables
    d'environnement MACHINE (persistantes).

.DESCRIPTION
    Ce script lit `docs/production_env_defaults.json` et applique chaque clé/valeur
    via [Environment]::SetEnvironmentVariable(name, value, 'Machine').
    Nécessite des droits administrateur pour écrire dans la portée MACHINE.

    Usage (dry-run):
      .\set_production_env.ps1 -WhatIf

    Pour appliquer réellement : lancer PowerShell en administrateur et exécuter sans -WhatIf.
#>

<# NOTE: param parsing caused parser issues in some environments. Use simple assignments. #>
$DefaultsJson = 'docs\\production_env_defaults.json'
$WhatIf = $false
# Accept simple args '-WhatIf' passed positionally
if ($args -and ($args -contains '-WhatIf')) { $WhatIf = $true }

Set-StrictMode -Version Latest

$repoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..')
$jsonPath = Join-Path $repoRoot $DefaultsJson
if (-not (Test-Path $jsonPath)) { Write-Error "Defaults JSON not found: $jsonPath"; exit 2 }

Write-Output "Loading defaults from $jsonPath"
$txt = Get-Content -Path $jsonPath -Raw
try {
    $data = $txt | ConvertFrom-Json
} catch {
    Write-Error "Failed to parse JSON: $_"; exit 3
}

foreach ($p in $data.PSObject.Properties) {
    $k = $p.Name
    $v = [string]$p.Value
    Write-Output "Will set MACHINE env: $k=$v"
    if ($WhatIf) { continue }
    try {
        [System.Environment]::SetEnvironmentVariable($k, $v, 'Machine')
        Write-Output "Set $k"
    } catch {
        Write-Warning ("Failed to set {0}: {1}" -f $k, $_)
    }
}

Write-Output "Done. If you updated MACHINE variables, processes may need restart to pick them up (or user logoff/logon)."
