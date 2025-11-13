# Search production_* logs for candidate markers indicating LIVE start or MT5 sends
$patterns = @('Starting LIVE production','CONFIRM_PRODUCTION token valid','ALLOW_MT5_SEND=1','Envoi MT5 effectué','Send order','Bot .*lanc','lanc.*LIVE','LIVE start','LIVE started')
$logDir = Join-Path $PSScriptRoot '..\artifacts\live_trading' | Resolve-Path -ErrorAction SilentlyContinue
if (-not $logDir) { $logDir = Join-Path (Get-Location) 'artifacts\live_trading' }
Write-Output "Searching logs in: $logDir"
$files = Get-ChildItem -Path $logDir -Filter '*.out.log' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 200
foreach ($f in $files) {
    foreach ($p in $patterns) {
        $found = Select-String -Path $f.FullName -Pattern $p -ErrorAction SilentlyContinue
        if ($found) {
            foreach ($m in $found) {
                Write-Output "[$($f.LastWriteTime.ToString('o'))] $($f.Name): $($m.Line.Trim())"
            }
        }
    }
}
