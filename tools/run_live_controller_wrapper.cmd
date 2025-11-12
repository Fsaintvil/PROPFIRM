@echo off
REM Wrapper to launch the live run controller in production mode (enables live sends)
REM Sets required environment variables for full automated production
SETLOCAL
REM Source central batch env defaults (tools\production_env.cmd)
call "%~dp0production_env.cmd"

REM Enable live sends for this wrapper
set "ALLOW_MT5_SEND=1"

REM Timestamp header for each run (use REPO_ROOT from production_env.cmd)
echo ==== %DATE% %TIME% ==== >> "%REPO_ROOT%artifacts\live_trading\live_run_controller.log"

REM Launch Python controller and append logs (use central PYTHON variable)
"%PYTHON%" "%REPO_ROOT%tools\live_run_controller.py" >> "%REPO_ROOT%artifacts\live_trading\live_run_controller.log" 2>&1

ENDLOCAL