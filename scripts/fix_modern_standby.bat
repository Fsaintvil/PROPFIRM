@echo off
echo ==============================================
echo   FIX MODERN STANDBY - Veille moderne S0
echo ==============================================
echo.
echo  Ce script doit tourner en ADMINISTRATEUR.
echo  Si une fenetre UAC apparait, cliquez sur OUI.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0fix_modern_standby.ps1' -Verb RunAs"
echo.
echo  Script lance. Verifiez le resultat dans:
echo  logs\platformaao_setup.log
echo.
pause
