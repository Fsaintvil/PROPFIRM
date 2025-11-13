<#
Generate a Task Scheduler XML for Watchdog SF_IA.7 and write as UTF-16 (Unicode).
Usage: run from repo root: pwsh -NoProfile -File tools\generate_watchdog_xml.ps1
#>
$repoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) '..')).Path
$toolsDir = Join-Path $repoRoot 'tools'
$watchdogScript = Join-Path $repoRoot 'tools\watchdog_sf_ia7.ps1'
$outXml = Join-Path $toolsDir 'Watchdog_SF_IA7_task.xml'

if (-not (Test-Path $watchdogScript)) { Write-Error "watchdog script not found: $watchdogScript"; exit 2 }

$ts = (Get-Date).ToString('s')
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>$ts</Date>
    <Author>PROPFIRM</Author>
    <Description>Watchdog SF_IA.7 - generated task</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>P1D</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -File "$watchdogScript"</Arguments>
      <WorkingDirectory>$toolsDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Write as UTF-16 to match Task Scheduler import expectations
$xml | Out-File -FilePath $outXml -Encoding Unicode -Force
Write-Output "WROTE_XML: $outXml"
