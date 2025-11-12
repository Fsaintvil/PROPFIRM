# Inspect production status: processes, lock files, logs, audit files
try {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
} catch {
    if ($PSScriptRoot) { $scriptRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path } else { $scriptRoot = (Get-Location).Path }
}
Set-Location $scriptRoot
Write-Output "=== Running inspection at: $(Get-Date) ==="

Write-Output "\n=== Processes matching repository scripts ==="
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'start_production.py' -or $_.CommandLine -match 'live_run_controller.py' -or $_.CommandLine -match 'run_active_monitor.ps1' -or $_.CommandLine -match 'run_live_controller_wrapper.cmd' -or $_.CommandLine -match 'start_production') }
if ($procs) { $procs | Select-Object ProcessId,CommandLine,CreationDate | Format-List } else { Write-Output 'No matching processes found' }

Write-Output "\n=== Python processes with PROPFIRM in commandline ==="
$py = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'PROPFIRM' -and $_.CommandLine -match 'python' }
if ($py) { $py | Select-Object ProcessId,CommandLine,CreationDate | Format-List } else { Write-Output 'No python processes with PROPFIRM found' }

Write-Output "\n=== Lock files ==="
$locks = @('control\\production.lock','control\\ai_sending.lock','control\\apply_live.confirm','control\\apply_live.auto.confirm')
foreach ($l in $locks) {
    if (Test-Path $l) {
        Write-Output "-- $l exists --"
        try { Write-Output (Get-Content $l -Raw) } catch { Write-Output "(failed to read $l)" }
    } else {
        Write-Output "-- $l not found"
    }
}

Write-Output "\n=== Recent live logs (tail 200 from newest) ==="
$live = Get-ChildItem tools\\logs -Filter 'live_trading_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($live) { Write-Output "Live log: $($live.FullName)"; Get-Content $live.FullName -Tail 200 -ErrorAction SilentlyContinue } else { Write-Output 'No live logs found' }

Write-Output "\n=== Controller log tail ==="
$ctrl = Join-Path $scriptRoot 'artifacts\\live_trading\\live_run_controller.log'
if (Test-Path $ctrl) { Write-Output "Ctrl log: $ctrl"; Get-Content $ctrl -Tail 200 -ErrorAction SilentlyContinue } else { Write-Output 'No controller log' }

Write-Output "\n=== Recent orders_audit files ==="
$audits = Get-ChildItem artifacts\\live_trading -Filter 'orders_audit_*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,LastWriteTime
if ($audits) { $audits | Format-Table -AutoSize } else { Write-Output 'No orders_audit files found' }

Write-Output "\n=== End of inspection ==="
