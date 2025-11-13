$bkroot = Join-Path (Get-Location).Path 'artifacts\backups'
if (-not (Test-Path $bkroot)) { Write-Output "NO_BACKUPS_DIR: $bkroot"; exit 2 }
$entries = Get-ChildItem $bkroot -Directory | ForEach-Object {
    $mf = Join-Path $_.FullName 'manifest.json'
    if (Test-Path $mf) {
        [PSCustomObject]@{
            backup = $_.Name
            path = $_.FullName
            manifestPath = $mf
            lastwrite = $_.LastWriteTimeUtc.ToString('o')
        }
    }
}
$outPath = Join-Path $bkroot 'manifest_master.json'
$entries | ConvertTo-Json -Depth 6 | Out-File -FilePath $outPath -Encoding utf8
Write-Output "MANIFEST_MASTER_WRITTEN: $outPath"