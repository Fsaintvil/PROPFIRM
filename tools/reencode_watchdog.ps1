<# Re-encode watchdog file to UTF8 with BOM safely #>
param(
    [string]$Path = "tools\watchdog_sf_ia7.ps1"
)

if (-not (Test-Path $Path)) { Write-Error "File not found: $Path"; exit 2 }
$full = Resolve-Path -Path $Path
$txt = Get-Content -Path $full -Raw
[System.IO.File]::WriteAllText($full.Path, $txt, [System.Text.Encoding]::UTF8)
Write-Output "REENCODE_OK: $full"
