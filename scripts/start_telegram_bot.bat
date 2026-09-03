@echo off
REM Script pour démarrer le bot Telegram en arrière-plan
REM Usage: double-cliquez sur ce fichier ou exécutez: scripts\start_telegram_bot.bat

echo.
echo ========================================
echo BOT TELEGRAM - MT5 FTMO
echo ========================================
echo.

REM Vérifier la configuration
if "%TELEGRAM_BOT_TOKEN%"=="" (
    echo ❌ TELEGRAM_BOT_TOKEN non configuré!
    echo.
    echo Pour configurer:
    echo 1. Créez un bot via @BotFather sur Telegram
    echo 2. Ajoutez dans .env:
    echo    TELEGRAM_BOT_TOKEN=votre_token
    echo    TELEGRAM_CHAT_ID=votre_chat_id
    echo.
    pause
    exit /b 1
)

if "%TELEGRAM_CHAT_ID%"=="" (
    echo ❌ TELEGRAM_CHAT_ID non configuré!
    echo.
    echo Pour configurer:
    echo 1. Ouvrez Telegram et recherchez @userinfobot
    echo 2. Envoyez /start pour obtenir votre chat_id
    echo 3. Ajoutez dans .env:
    echo    TELEGRAM_CHAT_ID=votre_chat_id
    echo.
    pause
    exit /b 1
)

echo ✅ Configuration vérifiée
echo.

REM Démarrer le bot
echo 🚀 Démarrage du bot Telegram...
echo.
python scripts\start_telegram_bot.py

pause