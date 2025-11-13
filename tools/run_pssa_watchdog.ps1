try {
    Import-Module PSScriptAnalyzer -ErrorAction Stop
} catch {
    Write-Output "PSScriptAnalyzer_NOT_AVAILABLE: $($_.Exception.Message)"
    exit 2
}

$reportPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'pssa_watchdog_report.json'
$res = Invoke-ScriptAnalyzer -Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'watchdog_sf_ia7.ps1') -Recurse -Severity Error,Warning
if ($res -and $res.Count -gt 0) {
    $res | Select-Object RuleName,Severity,ScriptName,Line,Message | ConvertTo-Json -Depth 6 | Out-File -FilePath $reportPath -Encoding utf8
    Write-Output "PSScriptAnalyzer_RESULTS_WRITTEN"
} else {
    Write-Output "PSScriptAnalyzer_OK"
}
