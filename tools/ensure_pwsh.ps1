<#
Ensure the current shell is PowerShell (pwsh or Windows PowerShell).
Exit code 0 = ok, 1 = not PowerShell.

Usage: .\tools\ensure_pwsh.ps1 ; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
#>

try {
    if ($null -eq $PSVersionTable) {
        Write-Error "PSVersionTable missing — not running inside PowerShell"
        exit 1
    }
} catch {
    Write-Error "Not running inside PowerShell (PSVersionTable not available)"
    exit 1
}

# Optional: detect edition (Core vs Desktop)
$edition = $PSVersionTable.PSEdition -as [string]
Write-Host "PowerShell detected: Edition=$edition; Version=$($PSVersionTable.PSVersion)"
exit 0
