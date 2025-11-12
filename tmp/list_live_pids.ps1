$pidDir='C:\Users\saint\Documents\PROPFIRM\artifacts\live_trading'
if (-not (Test-Path $pidDir)) { Write-Output 'PID_DIR_MISSING'; exit 2 }
Get-ChildItem -Path $pidDir -Filter '*.pid' -File | ForEach-Object {
    $pfile = $_.FullName
    $ptext = (Get-Content $pfile -Raw).Trim()
    Write-Output "FILE:$pfile"
    Write-Output "PID_IN_FILE:$ptext"
    if ($ptext -match '^[0-9]+$') {
        try {
            $filter = "ProcessId=$ptext"
            $proc = Get-CimInstance Win32_Process -Filter $filter -ErrorAction Stop
            if ($proc) {
                Write-Output "PROC_FOUND: $($proc.ProcessId) $($proc.Name)"
                $cl = $proc.CommandLine
                if ($cl) { Write-Output "CommandLine: $cl" } else { Write-Output 'CommandLine: <EMPTY_OR_UNAVAILABLE>' }
            } else { Write-Output 'PROC_NOT_FOUND' }
        } catch {
            Write-Output 'PROC_QUERY_ERROR'
            Write-Output $_.Exception.Message
        }
    } else { Write-Output 'PID_IN_FILE_INVALID' }
}
