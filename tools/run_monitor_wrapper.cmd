@echo off
REM Wrapper to run the Python monitor script and capture stdout/stderr to a log
REM Source central env defaults (tools\production_env.cmd)
call "%~dp0production_env.cmd"

REM Use %PYTHON% and %REPO_ROOT% from production_env.cmd
"%PYTHON%" "%REPO_ROOT%tools\monitor_parse_and_enrich.py" >> "%REPO_ROOT%artifacts\live_trading\monitor_wrapper.log" 2>&1