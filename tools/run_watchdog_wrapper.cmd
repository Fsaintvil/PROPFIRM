@echo off
REM Wrapper to start the watchdog in a detached window at user logon.
REM This wrapper intentionally does not require admin rights and starts PowerShell 7.

SETLOCAL
set SCRIPT=%~dp0watchdog_sf_ia7.ps1
set LOGDIR=%~dp0..\artifacts\live_trading
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Start PowerShell 7 (pwsh) in hidden window detached from this console
start "Watchdog_SF_IA7" pwsh -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT%"
