@echo off
REM Central production environment defaults for .cmd wrappers
REM Usage: call "%~dp0production_env.cmd" from scripts in tools\

REM SCRIPT_DIR is folder of this file
set "SCRIPT_DIR=%~dp0"
REM REPO_ROOT points to parent of tools
set "REPO_ROOT=%SCRIPT_DIR%..\"

REM Defaults (safe: ALLOW_MT5_SEND=0)
set "ALLOW_MT5_SEND=0"
set "AI_AUTOMATE=1"
set "AI_VOLUME=0.01"
set "LIVE_ENGINE_LIGHT_MODE=0"
set "CONFIRME_DEPLACEMENT=YES_I_CONFIRM"
set "AUTO_APPLY=1"
set "AUTO_DEPLOY=1"
set "AUTO_LEARN=1"
set "AUTO_ADAPT=1"
set "AUTO_ENRICH=1"
set "SYMBOLS=BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash"

REM Export PYTHONPATH as repo root (append existing if present)
if defined PYTHONPATH (
    set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%REPO_ROOT%"
)
REM Default python executable (override by setting PYTHON before calling)
if not defined PYTHON (
    set "PYTHON=python"
)
