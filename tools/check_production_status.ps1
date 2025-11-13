# tools/check_production_status.ps1
# Vérifie l'état de la production: PID, processus start_production.py, fichiers artifacts, kill-switch, derniers logs

Write-Output "=== Check production status started ==="

$checkPid = 34156
Write-Output "Checking PID $checkPid..."
$proc = Get-Process -Id $checkPid -ErrorAction SilentlyContinue
if ($proc) {
    Write-Output "PID $checkPid FOUND:"
    $proc | Select-Object Id, ProcessName, @{Name='StartTime';Expression={$_.StartTime}} | Format-List
} else {
    Write-Output "PID $checkPid not found"
}

Write-Output "\n=== Processes containing 'start_production.py' in their command line ==="
try {
    $matches = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'start_production.py') }
    if ($matches) {
        foreach ($m in $matches) {
            Write-Output "--- ProcessId: $($m.ProcessId) ---"
            Write-Output "CreationDate: $($m.CreationDate)"
            Write-Output "CommandLine: $($m.CommandLine)"
            Write-Output ""
        }
    } else {
        Write-Output "No processes found with start_production.py in command line."
    }
} catch {
    Write-Warning "Failed to enumerate Win32_Process: $_"
}

Write-Output "\n=== artifacts\\live_trading recent files ==="
$art = Join-Path -Path (Resolve-Path ".") -ChildPath 'artifacts\\live_trading'
if (Test-Path $art) {
    Get-ChildItem -Path $art -File | Sort-Object LastWriteTime -Descending | Select-Object Name, LastWriteTime, Length -First 40 | Format-Table -AutoSize
} else {
    Write-Output "$art not found"
}

Write-Output "\n=== .pid files and contents ==="
if (Test-Path $art) {
    $pids = Get-ChildItem -Path $art -Filter '*.pid' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if ($pids) {
        foreach ($f in $pids) {
            Write-Output "--- PID file: $($f.FullName) (LastWrite: $($f.LastWriteTime)) ---"
            try { Get-Content $f.FullName -ErrorAction SilentlyContinue } catch { Write-Warning "Could not read $($f.FullName): $_" }
        }
    } else {
        Write-Output "No .pid files found in $art"
    }
}

Write-Output "\n=== Kill-switch files ==="
Write-Output "control\\disable_trading present: $(Test-Path 'control\\disable_trading')"
Write-Output "control\\emergency_stop present: $(Test-Path 'control\\emergency_stop')"

Write-Output "\n=== Latest *out.log tail (last 200 lines) ==="
if (Test-Path $art) {
    $latest = Get-ChildItem -Path $art -Filter '*out.log' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        Write-Output "Latest out log: $($latest.FullName) (LastWrite: $($latest.LastWriteTime))"
        Write-Output "---- tail (last 200 lines) ----"
        try { Get-Content $latest.FullName -Tail 200 -ErrorAction SilentlyContinue } catch { Write-Warning "Failed to read $($latest.FullName): $_" }
    } else {
        Write-Output "No *out.log files found in $art"
    }
}

Write-Output "\n=== Environment variables of interest (from process env) ==="
Write-Output "ALLOW_MT5_SEND=$env:ALLOW_MT5_SEND"
Write-Output "CONFIRM_PRODUCTION=$env:CONFIRM_PRODUCTION"
Write-Output "AUTO_EXECUTION=$env:AUTO_EXECUTION"
Write-Output "BASE_CONFIDENCE_THRESHOLD=$env:BASE_CONFIDENCE_THRESHOLD"
Write-Output "PYTHONPATH=$env:PYTHONPATH"

Write-Output "=== Check production status finished ==="

Write-Output "\n=== Searching for key variable occurrences inside .log files ==="
if (Test-Path $art) {
    $patterns = 'ALLOW_MT5_SEND','CONFIRM_PRODUCTION','BASE_CONFIDENCE_THRESHOLD','PID\W*29884'
    foreach ($f in Get-ChildItem -Path $art -Filter '*.log' -File -ErrorAction SilentlyContinue) {
        Write-Output "--- Scanning: $($f.Name) ---"
        foreach ($p in $patterns) {
            $matches = Select-String -Path $f.FullName -Pattern $p -SimpleMatch -ErrorAction SilentlyContinue
            if ($matches) {
                foreach ($m in $matches) {
                    Write-Output "$($f.Name):$($m.LineNumber): $($m.Line)"
                }
            }
        }
    }
} else {
    Write-Output "No log directory to scan"
}
