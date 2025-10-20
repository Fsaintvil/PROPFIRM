param(
    [string]$LogDir = "C:\\Users\\saint\\Documents\\PROPFIRM\\logs",
    [int]$Minutes = 20,
    [int]$PollSeconds = 2
)

Write-Output "Looking for periodic_live_*.log in $LogDir"
$deadline = (Get-Date).AddSeconds(90)
$log = $null
while ((Get-Date) -lt $deadline -and -not $log) {
    $log = Get-ChildItem -Path $LogDir -Filter 'periodic_live_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Desc | Select-Object -First 1
    if (-not $log) { Start-Sleep -Seconds 2 }
}

if (-not $log) {
    Write-Output "No periodic_live log file found after waiting; aborting."
    exit 2
}

$path = $log.FullName
Write-Output "Tailing log: $path for $Minutes minutes"

# initialize position
$lines = Get-Content -Path $path -ErrorAction SilentlyContinue
$pos = $lines.Length
Write-Output "Starting at line index $pos"

$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalMinutes -lt $Minutes) {
    $all = Get-Content -Path $path -ErrorAction SilentlyContinue
    if ($all.Length -gt $pos) {
        $new = $all[$pos..($all.Length - 1)]
        foreach ($l in $new) { Write-Output $l }
        $pos = $all.Length
    }
    Start-Sleep -Seconds $PollSeconds
}
Write-Output "Finished tailing (duration reached)."
