$archiveRoot = 'C:\Users\saint\Documents\PROPFIRM\archive'
$latest = Get-ChildItem -Path $archiveRoot -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) {
    Write-Output 'NO_ARCHIVES_FOUND'
    exit 0
}
Write-Output ("LATEST_ARCHIVE:$($latest.FullName)")
Get-ChildItem -Path $latest.FullName -Recurse | Select-Object FullName,@{Name='SizeKB';Expression={[math]::Round(($_.Length)/1KB,2)}} | Format-Table -AutoSize
