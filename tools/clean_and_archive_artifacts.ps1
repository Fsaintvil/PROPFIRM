<#
Archive tmp/ and .bak artifacts that contain machine-specific absolute paths.
This helper moves the full `tmp/` folder and nearby .bak files into `archive/` with a timestamped folder.
It is non-destructive: files are moved (not deleted) and a small placeholder file is left in place describing the move.
Run locally in the repo root (tools/clean_and_archive_artifacts.ps1).
#>

param(
    [string]$RepoRoot,
    [switch]$WhatIf
)

# Determine repository root as the parent of the tools/ directory when not passed explicitly
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if ([string]::IsNullOrEmpty($RepoRoot)) {
    $RepoRoot = Resolve-Path (Join-Path $scriptDir '..') | Select-Object -ExpandProperty Path
}
Set-Location $RepoRoot

$timestamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$targetRoot = Join-Path -Path $RepoRoot -ChildPath ("archive/archived_artifacts_$timestamp")

Write-Host "Archive target directory: $targetRoot"

if ($WhatIf) {
    Write-Host "WhatIf: No changes will be made. Preview only."; return
}

# Create archive folder
New-Item -Path $targetRoot -ItemType Directory -Force | Out-Null

# Move tmp/ if exists
$tmpDir = Join-Path -Path $RepoRoot -ChildPath 'tmp'
if (Test-Path $tmpDir) {
    $dest = Join-Path -Path $targetRoot -ChildPath 'tmp'
    Write-Host "Moving tmp/ -> $dest"
    try {
        Move-Item -Path $tmpDir -Destination $dest -Force
        # leave a placeholder
        $placeholder = Join-Path -Path (Split-Path -Parent $dest) -ChildPath 'README_ARCHIVED_tmp.txt'
        "tmp/ archived to: $dest on $(Get-Date)" | Out-File -FilePath $placeholder -Encoding utf8
    } catch {
        Write-Warning "Failed to move tmp/: $_"
    }
} else {
    Write-Host "No tmp/ folder found; skipping."
}

# Move .bak files recursively under the repository into archive/baks_recursive
$bakFiles = Get-ChildItem -Path $RepoRoot -Filter '*.bak' -File -Recurse -ErrorAction SilentlyContinue
if ($bakFiles) {
    $bakDest = Join-Path -Path $targetRoot -ChildPath 'baks_recursive'
    New-Item -Path $bakDest -ItemType Directory -Force | Out-Null
    foreach ($f in $bakFiles) {
        try {
            # Preserve subfolder structure under the bakDest
            $relative = $f.FullName.Substring($RepoRoot.Length).TrimStart('\')
            $relativeDir = Split-Path $relative -Parent
            $destFolder = if ($relativeDir) { Join-Path $bakDest $relativeDir } else { $bakDest }
            New-Item -Path $destFolder -ItemType Directory -Force | Out-Null
            Move-Item -Path $f.FullName -Destination (Join-Path $destFolder $f.Name) -Force
        } catch {
            Write-Warning "Failed to move $($f.FullName): $_"
        }
    }
    "Moved $($bakFiles.Count) .bak files (recursive) to: $bakDest" | Write-Host
} else {
    Write-Host "No .bak files found under repository; skipping."
}

Write-Host "Archive complete. Review $targetRoot and commit changes if desired."
