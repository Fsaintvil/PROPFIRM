# Script helper to create the scheduled task from XML, start it and report results.
# Usage: run this PowerShell script in an elevated (Run as Administrator) PowerShell.
# It will attempt several creation methods and write a short report to artifacts\live_trading\install_task_result.txt

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Use repo-relative paths
$repoRoot = (Resolve-Path (Join-Path $root '..')).Path
$xml = Join-Path -Path $repoRoot -ChildPath 'tools\PROPFIRM_MonitorParseEnrich_task.xml'
$taskName = 'PROPFIRM_MonitorParseEnrich'
$reportFile = Join-Path -Path $repoRoot -ChildPath 'artifacts\live_trading\install_task_result.txt'

function Write-Report($line){
    $ts = (Get-Date).ToString('s')
    "$ts - $line" | Tee-Object -FilePath $reportFile -Append
}

# Start fresh report
"Install run at: $(Get-Date -Format s)" | Out-File -FilePath $reportFile -Encoding utf8

# Check elevation
$IsAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Report "IsAdmin: $IsAdmin"
if (-not $IsAdmin) {
    Write-Host "This script must be run as Administrator. Please re-run PowerShell as Administrator and call this script again."
    Write-Report "Not elevated - aborting."
    exit 2
}

# Try Register-ScheduledTask if available (PowerShell cmdlets)
if (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue) {
    try {
        Write-Report "Trying Register-ScheduledTask path (will create from XML via schtasks fallback if needed)."
        # Register-ScheduledTask does not import XML directly, so use schtasks /Create /XML
    } catch {
        Write-Report "Register-ScheduledTask present but will use schtasks for XML import."
    }
}

# Attempt 1: schtasks /Create /XML
try {
    # Ensure generated XMLs are up to date (generate from repo-relative templates)
    $genScript = Join-Path -Path $repoRoot -ChildPath 'tools\generate_task_xmls.ps1'
    if (Test-Path $genScript) {
        Write-Report "Generating task XMLs via $genScript"
        & powershell -NoProfile -ExecutionPolicy Bypass -File $genScript
    } else {
        Write-Report "Generator script not found: $genScript - proceeding with existing XML: $xml"
    }
    Write-Report "Running schtasks /Create /XML ..."
    $proc = Start-Process -FilePath cmd.exe -ArgumentList '/c', "schtasks /Create /TN \"$taskName\" /XML \"$xml\" /F" -NoNewWindow -Wait -PassThru -ErrorAction Stop
    Write-Report "schtasks ExitCode: $($proc.ExitCode)"
} catch {
    Write-Report "schtasks create failed: $($_.Exception.Message)"
}

# If the task appears, proceed to start and verify
$exists = $false
try{
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($t) { $exists = $true }
} catch {
    $exists = $false
}
Write-Report "Task exists after schtasks attempt: $exists"

if (-not $exists) {
    # Fallback: try Register-ScheduledTask by building an Action/Trigger
    Write-Report "Attempting fallback: import via UI-style Register-ScheduledTask not possible from XML. Will report failure."
    Write-Host "Task not created. Please import the XML via Task Scheduler UI as Admin or run the schtasks command manually in an elevated console. See $reportFile for details."
    exit 1
}

# Start the task
try{
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Write-Report "Start-ScheduledTask: started"
} catch {
    Write-Report "Start-ScheduledTask failed: $($_.Exception.Message)"
}

# Wait a few seconds and list outputs
Start-Sleep -Seconds 6
Write-Report "Listing recent audit files:"
Get-ChildItem (Join-Path $repoRoot 'artifacts\live_trading') -Filter 'orders_audit_*' | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,LastWriteTime | ForEach-Object { Write-Report $_.Name + ' | ' + $_.LastWriteTime }

Write-Host "Done. See report: $reportFile"
