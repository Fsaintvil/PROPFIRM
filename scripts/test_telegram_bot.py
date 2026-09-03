#!/usr/bin/env python3
"""Script de test pour le bot Telegram.

Usage:
    python scripts/test_telegram_bot.py

Ce script teste la connexion au bot Telegram et envoie un message de test.
"""

import os
import sys
import json
from pathlib import Path

# Ajouter le répertoire parent au path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine_simple.telegram_bot import TelegramBot, get_telegram_bot


def test_telegram_connection():
    """Teste la connexion au bot Telegram."""
    print("=" * 60)
    print("TEST BOT TELEGRAM - MT5 FTMO")
    print("=" * 60)
    
    # Charger le bot
    bot = get_telegram_bot()
    
    print(f"\n1. Configuration:")
    print(f"   Token configuré: {'✅' if bot.token else '❌'}")
    print(f"   Chat ID configuré: {'✅' if bot.chat_id else '❌'}")
    print(f"   Bot activé: {'✅' if bot._enabled else '❌'}")
    
    if not bot._enabled:
        print("\n❌ ERREUR: Le bot n'est pas activé!")
        print("\nPour configurer:")
        print("1. Créez un bot via @BotFather sur Telegram")
        print("2. Ajoutez dans .env:")
        print("   TELEGRAM_BOT_TOKEN=votre_token")
        print("   TELEGRAM_CHAT_ID=votre_chat_id")
        return False
    
    print(f"\n2. Chat IDs autorisés: {len(bot.authorized_chat_ids)}")
    for cid in bot.authorized_chat_ids:
        print(f"   - {cid}")
    
    # Test d'envoi
    print("\n3. Test d'envoi de message...")
    try:
        success = bot.send(
            "🧪 <b>Test Bot Telegram</b>\n\n"
            "Ce message est un test du bot MT5 FTMO.\n\n"
            "Timestamp: " + __import__('datetime').datetime.now().isoformat()
        )
        
        if success:
            print("   ✅ Message envoyé avec succès!")
            print(f"   → Vérifiez Telegram sur le chat {bot.chat_id}")
        else:
            print("   ❌ Échec de l'envoi")
            return False
            
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        return False
    
    # Test des commandes
    print("\n4. Test des commandes disponibles:")
    commands = [
        ("/start", "Affiche l'aide"),
        ("/status", "État du robot"),
        ("/positions", "Positions ouvertes"),
        ("/help", "Aide détaillée"),
    ]
    
    for cmd, desc in commands:
        print(f"   {cmd} - {desc}")
    
    print("\n" + "=" * 60)
    print("✅ TEST TERMINÉ AVEC SUCCÈS")
    print("=" * 60)
    
    print("\nProchaines étapes:")
    print("1. Ouvrez Telegram sur votre téléphone")
    print("2. Recherchez votre bot (ex: @robot_mt5_ftmo_bot)")
    print("3. Envoyez /start pour commencer")
    print("4. Testez /status pour voir l'état du robot")
    
    return True


def test_commands():
    """Teste les commandes du bot."""
    print("\n" + "=" * 60)
    print("TEST DES COMMANDES")
    print("=" * 60)
    
    bot = get_telegram_bot()
    
    if not bot._enabled:
        print("❌ Bot non activé - test ignoré")
        return
    
    # Simuler des commandes
    test_chat_id = bot.chat_id
    
    print(f"\nTest avec chat_id: {test_chat_id}")
    
    # Test /help
    print("\n1. Test /help...")
    bot._cmd_help(test_chat_id)
    
    # Test /status (nécessite dashboard.json)
    print("\n2. Test /status...")
    if Path("runtime/dashboard.json").exists():
        bot._cmd_status(test_chat_id)
    else:
        print("   ⚠️ dashboard.json non trouvé - test ignoré")
    
    # Test /positions
    print("\n3. Test /positions...")
    if Path("runtime/dashboard.json").exists():
        bot._cmd_positions(test_chat_id)
    else:
        print("   ⚠️ dashboard.json non trouvé - test ignoré")
    
    print("\n✅ Tests de commandes terminés")


if __name__ == "__main__":
    success = test_telegram_connection()
    
    if success:
        test_commands()
    
    input("\nAppuyez sur Entrée pour continuer...")