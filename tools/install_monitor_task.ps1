# Installe une tâche planifiée qui exécute le moniteur de parsing+enrichissement toutes les 15 minutes.
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Source central env defaults if present
$envFile = Join-Path -Path $root -ChildPath "production_env.ps1"
if (Test-Path $envFile) { . $envFile }

# python executable: prefer central $env:PYTHON, fallback to a PATH-resolvable 'python'
# This avoids hard-coding user-specific absolute paths. Ensure $env:PYTHON is set in
# `tools/production_env.ps1` when you want to target a specific interpreter.
$python = $env:PYTHON
if ([string]::IsNullOrEmpty($python)) { $python = 'python' }
$script = Join-Path -Path $root -ChildPath 'monitor_parse_and_enrich.py'
$taskName = 'PROPFIRM_MonitorParseEnrich'

# construire la chaîne TR correctement entre guillemets
$tr = "`"$python`" `"$script`""

Write-Host "Creating scheduled task $taskName to run: $tr"

# create task every 15 minutes (run as current user)
 $argList = @('/Create', '/TN', $taskName, '/TR', $tr, '/SC', 'MINUTE', '/MO', '15', '/RL', 'HIGHEST', '/F')

try {
    # Prefer Register-ScheduledTask (more robust quoting) when available
    if (Get-Command -Name Register-ScheduledTask -ErrorAction SilentlyContinue) {
        $actionObj = New-ScheduledTaskAction -Execute $python -Argument $script
        # create trigger that repeats every 15 minutes for 1 year
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 365)
        Register-ScheduledTask -TaskName $taskName -Action $actionObj -Trigger $trigger -RunLevel Highest -User $env:USERNAME -Force
        Write-Host "Scheduled task registered via Register-ScheduledTask."
    } else {
        Start-Process -FilePath schtasks -ArgumentList $argList -NoNewWindow -Wait -ErrorAction Stop
        Write-Host "Scheduled task created (or updated) via schtasks."
    }
} catch {
    Write-Host "Failed to create scheduled task:" $_.Exception.Message
    exit 1
}
