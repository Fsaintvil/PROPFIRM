while ($true) {
    Write-Host "=== periodic summary: " (Get-Date)
    if (Test-Path "logs\order_audit.csv") {
        Get-Content -Path "logs\order_audit.csv" -Tail 5 | ForEach-Object { Write-Host "[order] $_" }
    } else { Write-Host "No order_audit.csv" }
    if (Test-Path "logs\live_enable_audit.csv") {
        Get-Content -Path "logs\live_enable_audit.csv" -Tail 5 | ForEach-Object { Write-Host "[live] $_" }
    } else { Write-Host "No live_enable_audit.csv" }
    Start-Sleep -Seconds 300
}
