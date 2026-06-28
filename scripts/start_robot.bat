@echo off
cd /d "C:\Users\saint\Documents\MT5_FTMO_IA.7"
start "" pythonw.exe main.py
timeout /t 20 /nobreak >nul
start "" python.exe scripts\agent_daemon.py
exit 0
