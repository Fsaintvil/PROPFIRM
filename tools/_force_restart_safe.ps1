#!/usr/bin/env pwsh
## Script temporaire: force restart safe
## - Arrête les processus listés dans artifacts/live_trading/*.pid
## - Supprime les fichiers .pid
## - Relance tools/run_production.ps1 avec ALLOW_MT5_SEND=0

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
    $baseDir = Resolve-Path (Join-Path $scriptPath '..')
    $baseDir = $baseDir.Path
    Write-Output "BaseDir = $baseDir"

    $artifacts = Join-Path $baseDir 'artifacts\live_trading'
    New-Item -Path $artifacts -ItemType Directory -Force | Out-Null

    $pidFiles = Get-ChildItem -Path $artifacts -Filter '*.pid' -File -ErrorAction SilentlyContinue
    if ($pidFiles -and $pidFiles.Count -gt 0) {
        Write-Output "Found $($pidFiles.Count) .pid file(s) - attempting to stop referenced processes"
        foreach ($f in $pidFiles) {
            Write-Output "Processing $($f.FullName)"
            try {
                $content = Get-Content $f.FullName -ErrorAction Stop
                foreach ($line in $content) {
                    if ($line -match '\\d+') {
                        $pidVal = [int]$Matches[0]
                        $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
                        if ($proc) {
                            Write-Output "Stopping PID=$pidVal (Name=$($proc.ProcessName))"
                            Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
                            Start-Sleep -Milliseconds 200
                        } else {
                            Write-Output "PID $pidVal not running"
                        }
                    }
                }
            } catch {
                Write-Warning "Failed to read or process $($f.FullName): $_"
            }
            try {
                Remove-Item -Path $f.FullName -Force -ErrorAction SilentlyContinue
                Write-Output "Removed pid file $($f.Name)"
            } catch {
                Write-Warning "Could not remove $($f.FullName): $_"
            }
        }
    } else {
        Write-Output 'No .pid files found'
    }

    # Start production in SAFE mode (no MT5 sends)
    $startCmd = "`$env:ALLOW_MT5_SEND='0'; & '$baseDir\\tools\\run_production.ps1'"
    Write-Output "Starting production (SAFE) with command: $startCmd"
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',$startCmd -WorkingDirectory $baseDir -PassThru
    if ($proc) {
        $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
        $pidFile = Join-Path $artifacts "production_run_$ts.$($proc.Id).pid"
        try {
            $proc.Id | Out-File -FilePath $pidFile -Encoding ascii -Force
            Write-Output "Started process PID=$($proc.Id), wrote pid file $pidFile"
        } catch {
            Write-Warning "Started PID=$($proc.Id) but failed to write pid file: $_"
        }
    } else {
        throw 'Failed to start new production process'
    }

} catch {
    Write-Error "Force restart SAFE failed: $_"
    exit 1
}

exit 0
