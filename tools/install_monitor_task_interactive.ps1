<#
Interactive helper to create the scheduled task using user credentials.
Run this script in an elevated PowerShell (Run as Administrator).
It will prompt for the account (username/password) under which the task should run.
#>
param()

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Source central env defaults if present
$envFile = Join-Path -Path $root -ChildPath "production_env.ps1"
if (Test-Path $envFile) { . $envFile }

# python executable: prefer central $env:PYTHON, fallback to a PATH-resolvable 'python'
# Avoid hard-coded per-user install paths; set $env:PYTHON in tools/production_env.ps1 if needed.
$python = $env:PYTHON
if ([string]::IsNullOrEmpty($python)) { $python = 'python' }
$script = Join-Path -Path $root -ChildPath 'monitor_parse_and_enrich.py'
$xmlPath = Join-Path -Path $root -ChildPath 'PROPFIRM_MonitorParseEnrich_task.xml'
$taskName = 'PROPFIRM_MonitorParseEnrich'

Write-Host "This helper will create a scheduled task named: $taskName"
Write-Host "It will run: $python $script every 15 minutes for 365 days."

# Prompt for credential
$cred = Get-Credential -Message "Enter the account (DOMAIN\\User or .\\User) and password to run the scheduled task"
if (-not $cred) {
    Write-Host "No credentials provided; aborting."; exit 1
}

# Convert SecureString password to plain text for schtasks (local machine only)
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($cred.Password)
$passwordPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)

Write-Host "Creating scheduled task (this may require administrative rights)..."

# Prefer importing from XML (avoids quoting issues). Use schtasks /Create /TN <name> /XML <file> /F
$argList = @('/Create', '/TN', $taskName, '/XML', $xmlPath, '/F')
$proc = Start-Process -FilePath schtasks -ArgumentList $argList -NoNewWindow -Wait -PassThru -ErrorAction SilentlyContinue
if ($proc -and $proc.ExitCode -eq 0) {
    Write-Host "Scheduled task created successfully."
} else {
    if ($proc) { Write-Host "schtasks returned exit code: $($proc.ExitCode)" }
    else { Write-Host "Initial schtasks call failed; attempting fallback via cmd.exe..." }

    # Fallback: try creating from XML via cmd.exe (this is robust and avoids quoting issues)
    $cmd = 'schtasks /Create /TN "' + $taskName + '" /XML "' + $xmlPath + '" /F'
    Write-Host "Fallback command: $cmd"
    $proc2 = Start-Process -FilePath cmd.exe -ArgumentList '/c', $cmd -NoNewWindow -Wait -PassThru -ErrorAction SilentlyContinue
    if ($proc2 -and $proc2.ExitCode -eq 0) {
        Write-Host "Scheduled task created successfully (fallback)."
    } else {
        if ($proc2) { Write-Host "Fallback schtasks returned exit code: $($proc2.ExitCode)" }
        else { Write-Host "Fallback failed to start. Try running this script in an elevated PowerShell and ensure schtasks is available." }
    }
}

# Clear plaintext password variable
$passwordPlain = $null

Write-Host "Done. Verify the task in Task Scheduler UI and run it manually to test."
