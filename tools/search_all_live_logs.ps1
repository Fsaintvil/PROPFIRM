# tools/search_all_live_logs.ps1
$dir = Resolve-Path -Path "artifacts\\live_trading" -ErrorAction SilentlyContinue
if (-not $dir) { Write-Output "Directory not found: artifacts\\live_trading"; exit 0 }
Write-Output "Searching all .log files in $($dir.Path) for ENV patterns..."
$patterns = @('ALLOW_MT5_SEND','ALLOW_MT5_SEND=','CONFIRM_PRODUCTION','BASE_CONFIDENCE_THRESHOLD','AUTO_EXECUTION','AUTO_APPLY')
foreach ($f in Get-ChildItem -Path $dir.Path -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*.log' -or $_.Name -like '*.out.log' }) {
    foreach ($p in $patterns) {
        $res = Select-String -Path $f.FullName -Pattern $p -SimpleMatch -AllMatches -ErrorAction SilentlyContinue
        if ($res) {
            foreach ($m in $res) {
                Write-Output ("$($f.Name):$($m.LineNumber): $($m.Line)")
            }
        }
    }
}
Write-Output "Search complete."
