# Check market open/close using the project's auto_start_production logic
Write-Output "Running check_market_open.ps1"
$tz = [System.TimeZoneInfo]::Local
$now = Get-Date
$todayLocal = $now.Date
$marketOpenLocal = $todayLocal.AddHours(22).AddMinutes(5)
$marketCloseLocal = $todayLocal.AddDays(5).AddHours(21).AddMinutes(30)
$marketOpenUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($marketOpenLocal, $tz)
$marketCloseUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($marketCloseLocal, $tz)
Write-Output ("System timezone: {0}" -f $tz.Id)
Write-Output ("now (local): {0}" -f $now.ToString('o'))
Write-Output ("marketOpen (local): {0}" -f $marketOpenLocal.ToString('o'))
Write-Output ("marketOpen (UTC): {0}" -f $marketOpenUtc.ToString('o'))
Write-Output ("marketClose (local): {0}" -f $marketCloseLocal.ToString('o'))
Write-Output ("marketClose (UTC): {0}" -f $marketCloseUtc.ToString('o'))
$tOpen = [math]::Round(($marketOpenLocal - $now).TotalMinutes,2)
$tClose = [math]::Round(($marketCloseLocal - $now).TotalMinutes,2)
Write-Output ("timeToOpenMin: {0}" -f $tOpen)
Write-Output ("timeToCloseMin: {0}" -f $tClose)
if ($now -ge $marketOpenLocal -and $now -lt $marketCloseLocal) {
    Write-Output 'MARKET_OPEN'
} else {
    Write-Output 'MARKET_CLOSED'
}
