Write-Host "Testing live_monitor.ps1 syntax..."
$errors = $null
$tokens = $null
$content = Get-Content "C:\Users\saint\Documents\MT5_FTMO_IA.7\scripts\live_monitor.ps1" -Raw -ErrorAction Stop
$parsed = [System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
    Write-Host "SYNTAX ERRORS:" -ForegroundColor Red
    foreach ($e in $errors) {
        Write-Host "  $($e.Message) at line $($e.Extent.StartLineNumber)" -ForegroundColor Red
    }
    exit 1
} else {
    Write-Host "SYNTAX OK - no errors" -ForegroundColor Green
    exit 0
}
