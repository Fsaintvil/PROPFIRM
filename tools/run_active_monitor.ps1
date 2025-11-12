Param()

# Ignore Ctrl+C / CancelKeyPress so the monitor won't be interrupted by accidental SIGINT
try {
    $cb = [ConsoleCancelEventHandler]{ param($sender, $e) $e.Cancel = $true; Write-Output "[MON] CancelKeyPress suppressed at $(Get-Date)" }
    [Console]::add_CancelKeyPress($cb)
} catch {
    Write-Warning "Could not register CancelKeyPress handler: $_"
}
# Robust active monitor: restart live_run_controller wrapper and collect logs for 930s
$ErrorActionPreference = 'Stop'
try {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
} catch {
    # Fallback to script folder parent via PSScriptRoot for more reliable repo-relative paths
    if ($PSScriptRoot) { $scriptRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path } else { $scriptRoot = (Get-Location).Path }
}
$art = Join-Path $scriptRoot 'artifacts\live_trading'
New-Item -ItemType Directory -Path $art -Force | Out-Null

function Kill-LiveControllerProcesses {
    try {
        $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'live_run_controller.py' }
        if ($procs) {
            foreach ($p in $procs) {
                Write-Output "Stopping PID=$($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        } else {
            Write-Output 'No live_run_controller processes found'
        }
    } catch {
        Write-Warning "Kill error: $_"
    }
}

function Start-Wrapper {
    $wrapper = Join-Path $scriptRoot 'tools\run_live_controller_wrapper.cmd'
    if (-not (Test-Path $wrapper)) { throw "Wrapper not found: $wrapper" }
    Start-Process -FilePath 'C:\Windows\System32\cmd.exe' -ArgumentList '/c', "start `"PROPFIRM LiveRunController`" /B `"$wrapper`"" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

function Collect-Snapshot {
    param($liveLog, $ctrlLog, $ordersAuditBefore)
    $sample = [ordered]@{time = Get-Date; trades = 0; new_audits = @(); errors = @(); lock_exists = Test-Path (Join-Path $scriptRoot 'control\ai_sending.lock')}
    $lines = @()
    if ($liveLog -and (Test-Path $liveLog)) { $lines += Get-Content $liveLog -Tail 200 -ErrorAction SilentlyContinue }
    if (Test-Path $ctrlLog) { $lines += Get-Content $ctrlLog -Tail 200 -ErrorAction SilentlyContinue }
    foreach ($line in $lines) {
        if ($line -match 'Ordre ex|ordre ex|Ordre exécut|execute_trade|Ordre ex' ) { $sample.trades += 1 }
        if ($line -match 'ERR|ERROR|Exception|Traceback|retcode') { $sample.errors += $line }
    }
    $auditsNow = Get-ChildItem $art -Filter 'orders_audit_*' -ErrorAction SilentlyContinue | Select-Object Name,LastWriteTime
    $new = @()
    foreach ($a in $auditsNow) {
        if (-not ($ordersAuditBefore | Where-Object { $_.Name -eq $a.Name })) { $new += $a }
    }
    $sample.new_audits = $new
    return @{ sample = $sample; auditsNow = $auditsNow }
}

# Main
try {
    Write-Output "[MON] Kill existing controller processes"
    Kill-LiveControllerProcesses

    Write-Output "[MON] Start wrapper"
    Start-Wrapper

    $liveLog = (Get-ChildItem (Join-Path $scriptRoot 'tools\logs') -Filter 'live_trading_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    $ctrlLog = Join-Path $art 'live_run_controller.log'

    Write-Output "[MON] Live log: $liveLog"
    Write-Output "[MON] Ctrl log: $ctrlLog"

    $start = Get-Date
    $end = $start.AddSeconds(930)
    $samples = @()
    $tradeEvents = @()
    $ordersAuditBefore = Get-ChildItem $art -Filter 'orders_audit_*' -ErrorAction SilentlyContinue | Select-Object Name,LastWriteTime

    while ((Get-Date) -lt $end) {
        $res = Collect-Snapshot -liveLog $liveLog -ctrlLog $ctrlLog -ordersAuditBefore $ordersAuditBefore
        $sample = $res.sample
        $ordersAuditBefore = $res.auditsNow
        $samples += $sample
        Start-Sleep -Seconds 15
    }

} catch {
    Write-Warning "Monitoring error: $_"
} finally {
    # Summarize and write report regardless
    try {
        $summary = [ordered]@{}
        $summary.start = $start
        $summary.end = Get-Date
        $summary.duration = ($summary.end - $summary.start).TotalSeconds
        $summary.total_samples = $samples.Count
        # Compute total trades defensively (some samples may not have trades property)
        $totalTrades = 0
        foreach ($s in $samples) {
            try {
                if ($s -is [System.Collections.Hashtable] -and $s.ContainsKey('trades')) { $totalTrades += [int]$s.trades }
            } catch { }
        }
        $summary.total_trades = $totalTrades
        $summary.trade_events = @()
        $summary.new_audits = ($samples | ForEach-Object { $_.new_audits }) | Where-Object { $_ } | Select-Object -Unique Name,LastWriteTime
        $summary.errors = ($samples | ForEach-Object { $_.errors }) | Where-Object { $_ } | Select-Object -Unique -First 200
        $summary.lock_present = Test-Path (Join-Path $scriptRoot 'control\ai_sending.lock')

        $ts = $start.ToString('yyyyMMddTHHmmssZ')
        $reportFile = Join-Path $art ("active_monitor_report_$ts.txt")
        $sb = New-Object System.Text.StringBuilder
        $sb.AppendLine('---- ACTIVE MONITOR REPORT ----') | Out-Null
        $sb.AppendLine(("Start: " + $summary.start)) | Out-Null
        $sb.AppendLine(("End:   " + $summary.end)) | Out-Null
        $sb.AppendLine(("Duration seconds: " + $summary.duration)) | Out-Null
        $sb.AppendLine(("Total samples: " + $summary.total_samples)) | Out-Null
        $sb.AppendLine(("Total trade events seen (approx): " + $summary.total_trades)) | Out-Null
        $sb.AppendLine('New audit files created during monitoring:') | Out-Null
        if ($summary.new_audits) { foreach ($a in $summary.new_audits) { $sb.AppendLine(("  " + $a.Name + "  " + $a.LastWriteTime)) | Out-Null } } else { $sb.AppendLine('  (none)') | Out-Null }
        $sb.AppendLine('') | Out-Null
        $sb.AppendLine('Errors / Exceptions seen in logs (first 200):') | Out-Null
        if ($summary.errors) { foreach ($e in $summary.errors) { $sb.AppendLine($e) | Out-Null } } else { $sb.AppendLine('  (none)') | Out-Null }
        $sb.AppendLine('') | Out-Null
        $sb.AppendLine(("Lock file present at end: " + $summary.lock_present)) | Out-Null
        $sb.AppendLine('---- END REPORT ----') | Out-Null
        $sb.ToString() | Tee-Object -FilePath $reportFile
        Write-Output "Report written to $reportFile"
        # Attempt to extract orders from controller log and run enrichment for traceability
        try {
            $extractScript = Join-Path $scriptRoot 'tools\extract_orders_from_controller_log.py'
            if (Test-Path $extractScript) {
                Write-Output "[MON] Running extractor: $extractScript"
                            $pythonExec = $env:PYTHON
                            if ([string]::IsNullOrEmpty($pythonExec)) { $pythonExec = 'python' }
                            $extractRaw = & $pythonExec $extractScript 2>&1 | Out-String
                Write-Output $extractRaw
                try {
                    $extractObj = $extractRaw | ConvertFrom-Json
                } catch {
                    $extractObj = $null
                }
                if ($extractObj -and $extractObj.status -eq 'done') {
                    $tmpFile = $extractObj.out
                    Write-Output "[MON] Extracted tmp orders: $tmpFile"
                    $enrichScript = Join-Path $scriptRoot 'tools\enrich_orders_with_mt5.py'
                    if (Test-Path $enrichScript) {
                        Write-Output "[MON] Running enrichment: $enrichScript on $tmpFile"
                        $enrichRaw = & $pythonExec $enrichScript $tmpFile 2>&1 | Out-String
                        Write-Output $enrichRaw
                        try { $enrichObj = $enrichRaw | ConvertFrom-Json } catch { $enrichObj = $null }
                        if ($enrichObj -and $enrichObj.status -eq 'done') {
                            Add-Content -Path $reportFile -Value "`n--- ENRICHMENT ---"
                            Add-Content -Path $reportFile -Value ("enrich_out_json: " + $enrichObj.out_json)
                            Add-Content -Path $reportFile -Value ("enrich_out_csv: " + $enrichObj.out_csv)
                            Add-Content -Path $reportFile -Value ("enrich_count: " + $enrichObj.count)
                        } else {
                            Add-Content -Path $reportFile -Value "`n--- ENRICHMENT FAILED OR NO OUTPUT ---"
                            Add-Content -Path $reportFile -Value $enrichRaw
                        }
                    }
                } else {
                    Add-Content -Path $reportFile -Value "`n--- EXTRACTOR: no orders extracted or extractor failed ---"
                    Add-Content -Path $reportFile -Value $extractRaw
                }
            } else {
                Write-Warning "Extractor not found: $extractScript"
            }
        } catch {
            Write-Warning "Extraction/Enrichment failed: $_"
            Add-Content -Path $reportFile -Value ("Extraction/Enrichment failed: " + $_)
        }
    } catch {
        Write-Warning "Failed to write report: $_"
    }
}
