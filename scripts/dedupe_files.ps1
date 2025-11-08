<#
Script: dedupe_files.ps1
But: repérer les fichiers dupliqués (même contenu) dans un répertoire, lister et optionnellement
  - remplacer les doublons par des hardlinks (si sur le même volume),
  - ou déplacer les doublons vers un dossier d'archive,
  - ou supprimer les doublons.

Usage examples:
  # mode lecture seule (par défaut)
  .\scripts\dedupe_files.ps1 -RootPath .

  # appliquer et remplacer les doublons par des hardlinks quand possible
  .\scripts\dedupe_files.ps1 -RootPath . -Apply -HardLink

  # appliquer et déplacer les doublons dans archive/duplicates
  .\scripts\dedupe_files.ps1 -RootPath . -Apply -MoveToArchive "archive/duplicates"

Notes de sécurité:
  - Sans le switch -Apply le script se contente d'afficher un rapport.
  - Testez d'abord sur un petit dossier. Les hardlinks sont sûrs mais le script supprime le fichier dupliqué
    avant de créer le lien; en cas d'échec de création de hardlink, le fichier original sera restauré si possible.
#>

param(
    [string]$RootPath = ".",
    [switch]$Apply,
    [switch]$HardLink,
    [string]$MoveToArchive = '',
    [switch]$Verbose
)

Write-Host "[dedupe] Racine : $RootPath"

# Directories à exclure par défaut
$defaultExcludes = @('.git','node_modules','__pycache__','.venv','env','venv')

function ShouldExcludePath([string]$path){
    foreach($ex in $defaultExcludes){
        if($path -like "*\$ex\*" -or $path -like "*\$ex") { return $true }
    }
    return $false
}

if($MoveToArchive -and -not (Test-Path $MoveToArchive)){
    if($Apply){
        New-Item -ItemType Directory -Path $MoveToArchive -Force | Out-Null
    }
}

# Collecte et hachage des fichiers
$hashMap = @{}

Write-Host "[dedupe] Scan des fichiers (cela peut prendre du temps)..."
$files = Get-ChildItem -Path $RootPath -File -Recurse -Force -ErrorAction SilentlyContinue

foreach($f in $files){
    if(ShouldExcludePath($f.FullName)){ continue }
    try{
        $h = (Get-FileHash -Algorithm SHA256 -Path $f.FullName).Hash
    } catch {
        Write-Warning "Impossible de hasher $($f.FullName) : $_"
        continue
    }
    if(-not $hashMap.ContainsKey($h)){
        $hashMap[$h] = New-Object System.Collections.ArrayList
    }
    [void]$hashMap[$h].Add($f.FullName)
}

# Résumé
$dupeGroups = $hashMap.GetEnumerator() | Where-Object { $_.Value.Count -gt 1 }
if($dupeGroups.Count -eq 0){
    Write-Host "[dedupe] Aucun fichier dupliqué trouvé." -ForegroundColor Green
    exit 0
}

Write-Host "[dedupe] Groupes de fichiers dupliqués trouvés : $($dupeGroups.Count)" -ForegroundColor Yellow

foreach($g in $dupeGroups){
    $filesList = $g.Value
    Write-Host "\n=== Hash: $($g.Key) - $($filesList.Count) fichiers ===" -ForegroundColor Cyan
    $idx = 0
    foreach($p in $filesList){
        Write-Host "[$idx] $p"
        $idx++
    }
    $canonical = $filesList[0]
    Write-Host "Canonical choisi : $canonical"

    for($i=1; $i -lt $filesList.Count; $i++){
        $dup = $filesList[$i]
        if(-not $Apply){
            Write-Host "  -> Dupliqué : $dup (action proposée : remplacer par hardlink ou déplacer)"
            continue
        }

        # Si demandé, tenter hardlink
        if($HardLink){
            $rootCan = [System.IO.Path]::GetPathRoot($canonical)
            $rootDup = [System.IO.Path]::GetPathRoot($dup)
            if($rootCan -ne $rootDup){
                Write-Warning "  Hardlink impossible (volumes différents) : $dup"
            } else {
                Write-Host "  Remplacement par hardlink : $dup -> $canonical"
                try{
                    $tmpBackup = "$dup.tmp.dedupe"
                    Rename-Item -Path $dup -NewName $tmpBackup -Force
                    New-Item -ItemType HardLink -Path $dup -Target $canonical | Out-Null
                    Remove-Item -Path (Join-Path (Split-Path $dup -Parent) $tmpBackup) -Force -ErrorAction SilentlyContinue
                } catch {
                    Write-Warning "    Erreur création hardlink pour $dup : $_"
                    # tenter restaurer
                    if(Test-Path (Join-Path (Split-Path $dup -Parent) $tmpBackup)){
                        Rename-Item -Path (Join-Path (Split-Path $dup -Parent) $tmpBackup) -NewName (Split-Path $dup -Leaf) -Force
                    }
                }
                continue
            }
        }

        if($MoveToArchive){
            $rel = [System.IO.Path]::GetRelativePath((Resolve-Path $RootPath).Path, (Resolve-Path $dup).Path)
            $dest = Join-Path (Resolve-Path $MoveToArchive).Path $rel
            $destDir = Split-Path $dest -Parent
            if(-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
            Write-Host "  Déplacement vers archive : $dup -> $dest"
            try{ Move-Item -Path $dup -Destination $dest -Force } catch { Write-Warning "    Erreur déplacement : $_" }
            continue
        }

        # Sinon suppression
        Write-Host "  Suppression : $dup"
        try{ Remove-Item -Path $dup -Force } catch { Write-Warning "    Erreur suppression : $_" }
    }
}

Write-Host "\n[dedupe] Opérations terminées." -ForegroundColor Green
