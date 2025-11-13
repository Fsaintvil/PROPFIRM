# tools/inspect_production_log_start.ps1
$log = Resolve-Path -Path "artifacts\\live_trading\\production_live_20251112_234907.out.log" -ErrorAction SilentlyContinue
if (-not $log) {
    Write-Output "Log file not found: artifacts\\live_trading\\production_live_20251112_234907.out.log"
    exit 0
}
Write-Output "--- First 400 lines of $($log.Path) ---"
Get-Content -Path $log.Path -TotalCount 400 -ErrorAction SilentlyContinue | ForEach-Object { Write-Output $_ }
Write-Output "--- Search results for ALLOW_MT5_SEND / CONFIRM_PRODUCTION / BASE_CONFIDENCE_THRESHOLD ---"
$patterns = @('ALLOW_MT5_SEND=','ALLOW_MT5_SEND','CONFIRM_PRODUCTION=','CONFIRM_PRODUCTION','BASE_CONFIDENCE_THRESHOLD=')
foreach ($p in $patterns) {
    $res = Select-String -Path $log.Path -Pattern $p -SimpleMatch -AllMatches -ErrorAction SilentlyContinue
    if ($res) {
        foreach ($m in $res) {
            Write-Output ("$($m.Path):$($m.LineNumber): $($m.Line)")
        }
    }
}
Write-Output "--- End of inspection ---"
