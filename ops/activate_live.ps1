Param(
    [string]$Token,
    [switch]$Execute
)

# This script will validate a pending token file in control/ and then
# (optionally) enable live by moving/removing the control/kill_switch.
# By default it only performs checks and writes an audit line. Use -Execute
# to actually modify the kill_switch (dangerous).

$control = Join-Path $PSScriptRoot '..\control' | Resolve-Path -Relative
$pending = Join-Path $control "pending_live_activation_$Token.json"
if (-not (Test-Path $pending)) {
    Write-Host "Pending token not found:" $pending
    exit 1
}

$json = Get-Content $pending -Raw | ConvertFrom-Json
Write-Host "Found pending activation:" $json.token "user:" $json.user "note:" $json.note

# Append audit line
$log = Join-Path (Join-Path $PSScriptRoot '..') 'logs\live_enable_audit.csv'
$now = (Get-Date).ToString('o')
$line = "$now,activated_attempt,$($json.token),$($json.user),$($json.note)"
Add-Content -Path $log -Value $line
Write-Host "Audit appended to" $log

if ($Execute) {
    # Backup and remove kill_switch
    $ks = Join-Path $control 'kill_switch'
    if (Test-Path $ks) {
        $bak = $ks + ".bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item -Path $ks -Destination $bak -Force
        Remove-Item -Path $ks -Force
        Write-Host "kill_switch backed up to" $bak "and removed -> live enabled"
    } else {
        Write-Host "kill_switch not present; live appears already enabled or managed elsewhere"
    }
} else {
    Write-Host "Dry run: use -Execute to actually modify kill_switch (dangerous)."
}
