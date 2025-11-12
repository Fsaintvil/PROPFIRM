#
.EXAMPLE
    .\tools\list_propfirm_pids.ps1
    .\tools\list_propfirm_pids.ps1 -Path "$PSScriptRoot\..\"   # example: search the repository root
.DESCRIPTION
    Utilise Win32_Process pour récupérer la CommandLine des processus et filtre
    sur le chemin fourni (par défaut le répertoire courant). Affiche ProcessId,
    Name et CommandLine. Pratique pour repérer les processus Python/powershell
    démarrés depuis le dépôt.

.PARAMETER Path
    Chemin à rechercher dans la ligne de commande des processus.

.DESCRIPTION
    Utilise Win32_Process pour récupérer la CommandLine des processus et filtre
    sur le chemin fourni (par défaut le répertoire courant). Affiche ProcessId,
    Name et CommandLine. Pratique pour repérer les processus Python/powershell
    démarrés depuis le dépôt.

.PARAMETER Path
    Chemin à rechercher dans la ligne de commande des processus.

.EXAMPLE
    .\tools\list_propfirm_pids.ps1
    .\tools\list_propfirm_pids.ps1 -Path "$PSScriptRoot\..\"   # example: search the repository root
#>

param(
    [string]
    $Path = (Get-Location).Path,
    [switch]
    $VerboseMatch
)

try {
    $escaped = [regex]::Escape($Path)
    $procs = Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $escaped) }

    if (-not $procs) {
        Write-Output "Aucun processus trouvé dont la ligne de commande contient: $Path"
        exit 0
    }

    $procs |
        Select-Object @{Name='PID';Expression={$_.ProcessId}},@{Name='Name';Expression={$_.Name}},@{Name='CommandLine';Expression={$_.CommandLine}} |
        Sort-Object PID |
        Format-Table -AutoSize

    if ($VerboseMatch) {
        Write-Output "\nNombre de processus trouvés: $($procs.Count)"
    }
}
catch {
    Write-Error "Erreur lors de l'interrogation des processus: $_"
    exit 2
}
