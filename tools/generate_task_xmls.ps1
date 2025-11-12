<#
Generate task XML files with repo-absolute paths for Task Scheduler imports.
Usage: run this script from the repo (or it will resolve repo root from its location).
This will overwrite the two XML files used by the installer helper: 
 - PROPFIRM_MonitorParseEnrich_task.xml
 - run_active_monitor_task.xml
#>
param(
    [string]$OutDir = $(Join-Path -Path (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) '..')).Path -ChildPath 'tools')
)

# Resolve repo root and tools folder
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
$toolsDir = Join-Path $repoRoot 'tools'
if (-not (Test-Path $toolsDir)) { New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null }

# Paths
$monitorWrapper = Join-Path $repoRoot 'tools\run_monitor_wrapper.cmd'
$monitorXml = Join-Path $toolsDir 'PROPFIRM_MonitorParseEnrich_task.xml'
$activeMonitorScript = Join-Path $repoRoot 'tools\run_active_monitor.ps1'
$activeXml = Join-Path $toolsDir 'run_active_monitor_task.xml'

Write-Output "Generating task XMLs into: $toolsDir"

# Monitor task XML (no principal so importer supplies credentials)
$monitorXmlContent = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$(Get-Date -Format s)</Date>
    <Author>PROPFIRM</Author>
    <Description>Moniteur: parse logs + enrich orders (toutes les 15 minutes) - generated</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>$(Get-Date -Format s)</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT15M</Interval>
        <Duration>P365D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>P3D</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$monitorWrapper</Command>
      <Arguments></Arguments>
    </Exec>
  </Actions>
</Task>
"@

# Active monitor task XML (runs the run_active_monitor.ps1 via powershell.exe)
$activeXmlContent = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$(Get-Date -Format s)</Date>
    <Author>PROPFIRM</Author>
    <Description>Run the PROPFIRM active monitor (generated).</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$(Get-Date -Format s)</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
      <Repetition>
        <Interval>PT15M30S</Interval>
        <Duration>PT24H</Duration>
      </Repetition>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$activeMonitorScript"</Arguments>
      <WorkingDirectory>$toolsDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Write files as UTF-16 (Unicode) to match Task Scheduler expectations
$monitorXmlContent | Out-File -FilePath $monitorXml -Encoding Unicode -Force
Write-Output "Wrote: $monitorXml"
$activeXmlContent | Out-File -FilePath $activeXml -Encoding Unicode -Force
Write-Output "Wrote: $activeXml"

Write-Output "Task XML generation complete. To import, run the installer helper or schtasks with the generated XMLs as admin."
