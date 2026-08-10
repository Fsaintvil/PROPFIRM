@echo off
REM Lance le fix Modern Standby en admin (UAC prompt)
powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File \"C:\Users\saint\Documents\MT5_FTMO_IA.7\scripts\fix_modern_standby.ps1\"' -Wait"
