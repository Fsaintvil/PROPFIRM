@echo off
REM Backtest 6 symboles avec suivi progressif
REM Chaque symbole prend ~5min, total ~30min

set LOGFILE=backtest\results\progress.log
echo %DATE% %TIME% - Debut backtest portfolio 6 symboles > %LOGFILE%

for %%s in (EURUSD USDCAD EURJPY GBPJPY XAUUSD BTCUSD) do (
    echo. >> %LOGFILE%
    echo ===== %%s ===== >> %LOGFILE%
    echo %DATE% %TIME% - Debut %%s >> %LOGFILE%
    python scripts/bt_full_portfolio.py --symbol %%s 2>&1 | tee -a %LOGFILE%
    echo %DATE% %TIME% - Fin %%s >> %LOGFILE%
)
