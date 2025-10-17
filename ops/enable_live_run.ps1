<#
.SYNOPSIS
  Prepare a pending, auditable manual activation token for live sends.

DESCRIPTION
  This script generates a secure activation token and writes a pending
  activation file under `control/`. It appends a human-auditable line to
  `logs/live_enable_audit.csv` including git/python/sklearn metadata.

  IMPORTANT: This script DOES NOT remove or modify `control/kill_switch`.
  An operator must review the pending activation and then run the executor
  with the token to enable live sends.

USAGE:
  pwsh .\ops\enable_live_run.ps1 [-Note "reason"] [-Force]
#>

param(
    [string]$Note = "Manual activation request",
    [switch]$Force
)

Set-StrictMode -Version Latest

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$Control = Join-Path $ProjectRoot 'control'
$Logs = Join-Path $ProjectRoot 'logs'

If (-not (Test-Path $Control)) { New-Item -ItemType Directory -Path $Control | Out-Null }
If (-not (Test-Path $Logs)) { New-Item -ItemType Directory -Path $Logs | Out-Null }

# Generate secure 32-hex token using cryptographic RNG
$bytes = New-Object 'System.Byte[]' 16
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''

$user = $env:USERNAME
if (-not $user) { $user = (whoami) }
$ts = (Get-Date).ToUniversalTime().ToString('o')

if (-not $Force) {
    Write-Host "About to create pending live activation token for user=$user at $ts"
    $ans = Read-Host "Type YES to proceed"
    if ($ans -ne 'YES') {
        Write-Host "Aborted by operator"
        exit 1
    }
}

$pending = Join-Path $Control ("pending_live_activation_{0}.json" -f $token)
$payload = @{ token = $token; user = $user; timestamp = $ts; note = $Note } | ConvertTo-Json -Depth 4
$payload | Out-File -FilePath $pending -Encoding UTF8

# Ensure audit CSV exists with header
$auditFile = Join-Path $Logs 'live_enable_audit.csv'
if (-not (Test-Path $auditFile)) {
    'timestamp,action,token,user,note,git_sha,python,sklearn' | Out-File -FilePath $auditFile -Encoding UTF8
}

# Best-effort metadata
try {
    $git_sha = (& git rev-parse --short HEAD) -as [string]
    if (-not $git_sha) { $git_sha = 'NA' }
} catch { $git_sha = 'NA' }

try {
    $python = (& python -c "import platform; print(platform.python_version())") -as [string]
    if (-not $python) { $python = 'NA' }
} catch { $python = 'NA' }

try {
    $sk = (& python -c "import sklearn; print(getattr(sklearn, '__version__', 'not-installed'))") -as [string]
    if (-not $sk) { $sk = 'not-installed' }
} catch { $sk = 'not-installed' }

# Escape double quotes in note
$note_escaped = $Note.Replace('"', '""')

$line = '{0},{1},{2},{3},"{4}",{5},{6},{7}' -f $ts, 'live_requested', $token, $user, $note_escaped, $git_sha, $python, $sk
Add-Content -Path $auditFile -Value $line -Encoding UTF8

Write-Host "Pending live activation file created: $pending"
Write-Host "Audit line appended to: $auditFile"
Write-Host "Token: $token"
Write-Host "To perform live sends later, run the executor with the token exactly as printed (use --simulate first):"
Write-Host "python -m MT5_FTMO_IA.scripts._execute_recommendations_live --auth-token $token --simulate"

Write-Host "IMPORTANT: This script did NOT remove or modify control/kill_switch."
Write-Host "Review the pending activation file and audit before running without --simulate."

exit 0
