# Analyze latest mt5_apply_retry_aggressive_*.json and print counts + samples
$pattern = 'artifacts\live_trading\mt5_apply_retry_aggressive_*.json'
$files = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
if (-not $files) {
    Write-Output "No retry file found matching $pattern"
    exit 1
}
$path = $files[-1].FullName
Write-Output "Analyzing: $path"
try {
    $jsonRaw = Get-Content -Path $path -Raw
    $json = $jsonRaw | ConvertFrom-Json
} catch {
    Write-Output "Failed to read/parse JSON: $_"
    exit 2
}
$results = $json.results
$counts = @{}
$samples = @()
foreach ($r in $results) {
    # normalize retcode / error
    if ($null -ne $r.result) {
        $rc = $r.result.retcode
        $comment = $r.result.comment
    } elseif ($r.PSObject.Properties['error']) {
        $rc = 'error'
        # collect error message(s) if present
        $errVals = $r.PSObject.Properties['error'] | ForEach-Object { $_.Value }
        $comment = ($errVals -join ', ')
    } else {
        $rc = 'unknown'
        $comment = $null
    }

    # count
    if ($counts.ContainsKey($rc)) { $counts[$rc] += 1 } else { $counts[$rc] = 1 }

    # record a small sample
    if ($samples.Count -lt 8) {
        $samples += [pscustomobject]@{
            ticket = $r.ticket
            symbol = $r.symbol
            retcode = $rc
            comment = $comment
        }
    }
}
Write-Output "Counts:" 
$counts.GetEnumerator() | Sort-Object Name | ForEach-Object { Write-Output ("  {0} = {1}" -f $_.Name, $_.Value) }
Write-Output "\nExamples (up to 8):"
$samples | Format-Table -AutoSize
Write-Output "\nReport file: $path"
exit 0
