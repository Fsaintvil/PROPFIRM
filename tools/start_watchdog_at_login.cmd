@echo off
REM Wrapper to start the watchdog at user logon without requiring scheduled tasks.
REM It launches PowerShell (pwsh) to run the watchdog script in background.

SET "REPOROOT=C:\Users\saint\Documents\PROPFIRM"
SET "WATCHDOG=%REPOROOT%\tools\watchdog_sf_ia7.ps1"

REM Use pwsh (PowerShell 7+) if available, otherwise fallback to powershell.exe
where pwsh >nul 2>nul
if %errorlevel%==0 (
    set "PS_EXE=pwsh"
) else (
    set "PS_EXE=powershell.exe"
)

REM Start the watchdog detached and hidden
%PS_EXE% -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Process -FilePath %PS_EXE% -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','""%WATCHDOG%""' -WindowStyle Hidden"

exit /b 0
