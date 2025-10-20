param(
    [Parameter(Mandatory=$true)][string]$token,
    [int]$minutes = 5
)

$scriptPath = Join-Path $PSScriptRoot 'start_periodic_live.ps1'
$job = Start-Job -FilePath $scriptPath -ArgumentList $token, $minutes
Write-Output "Started job id=$($job.Id) state=$($job.State)"
Write-Output "Use Get-Job -Id $($job.Id) to inspect. Logs will be under .\logs\periodic_live_*.log"
