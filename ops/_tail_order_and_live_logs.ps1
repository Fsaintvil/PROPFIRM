param(
    [int]$Minutes = 20,
    [int]$PollSeconds = 2
)

$base = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $base 'logs'
$order = Join-Path $logs 'order_audit.csv'
$live = Join-Path $logs 'live_enable_audit.csv'

Write-Output "Tailing: $order and $live for $Minutes minutes"

function TailFile($path, [ref]$pos) {
    if (-not (Test-Path $path)) { return }
    $all = Get-Content -Path $path -ErrorAction SilentlyContinue
    if ($all.Length -gt $pos.Value) {
        $new = $all[$pos.Value..($all.Length - 1)]
        foreach ($l in $new) { Write-Output "$([IO.Path]::GetFileName($path)): $l" }
        $pos.Value = $all.Length
    }
}

$posOrder = 0
$posLive = 0
if (Test-Path $order) { $posOrder = (Get-Content $order).Length }
if (Test-Path $live) { $posLive = (Get-Content $live).Length }

$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalMinutes -lt $Minutes) {
    TailFile $order ([ref]$posOrder)
    TailFile $live ([ref]$posLive)
    Start-Sleep -Seconds $PollSeconds
}
Write-Output "Done tailing files."
