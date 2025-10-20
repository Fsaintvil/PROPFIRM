param(
    [int]$Id = 1
)

Write-Output "Jobs:"
Get-Job | Select-Object Id,Name,State,HasMoreData,HasMoreData | Format-List

Write-Output "\nReceive-Job output (if any):"
try {
    $out = Receive-Job -Id $Id -Keep -ErrorAction Stop
    if ($out) { $out | ForEach-Object { Write-Output $_ } }
    else { Write-Output "(no output)" }
}
catch {
    Write-Output "Receive-Job failed: $_"
}

Write-Output "\nChild jobs (if any):"
Get-Job -IncludeChildJob | Select-Object Id,Name,State | Format-List
