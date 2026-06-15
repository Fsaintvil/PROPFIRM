@echo off
title MT5 Market Watch
cls
echo.
echo ═══════════════════════════════════════
echo    MT5 FTMO — Market Watch Dashboard
echo    Actualise toutes les 3 minutes
echo    Ferme la fenetre pour arreter
echo ═══════════════════════════════════════
echo.

:loop
cls
type "C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\dashboard.txt"
echo.
echo ─── Derniere mise a jour: %date% %time% ───
echo (Appuyez sur Ctrl+C pour arreter, ou fermez la fenetre)
timeout /t 60 /nobreak >nul
goto loop
