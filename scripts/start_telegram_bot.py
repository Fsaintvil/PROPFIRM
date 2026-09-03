#!/usr/bin/env python3
"""Script pour démarrer le bot Telegram en arrière-plan.

Usage:
    python scripts/start_telegram_bot.py

Ce script démarre le bot Telegram et reste en écoute pour les commandes.
"""

import os
import sys
import signal
import time
from pathlib import Path

# Ajouter le répertoire parent au path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine_simple.telegram_bot import get_telegram_bot


def main():
    """Point d'entrée principal."""
    print("=" * 60)
    print("DÉMARRAGE BOT TELEGRAM - MT5 FTMO")
    print("=" * 60)
    
    # Charger le bot
    bot = get_telegram_bot()
    
    if not bot._enabled:
        print("\n❌ ERREUR: Le bot n'est pas activé!")
        print("\nPour configurer:")
        print("1. Créez un bot via @BotFather sur Telegram")
        print("2. Ajoutez dans .env:")
        print("   TELEGRAM_BOT_TOKEN=votre_token")
        print("   TELEGRAM_CHAT_ID=votre_chat_id")
        return 1
    
    print(f"\n✅ Bot configuré:")
    print(f"   Token: {bot.token[:10]}...")
    print(f"   Chat ID: {bot.chat_id}")
    print(f"   Chat IDs autorisés: {len(bot.authorized_chat_ids)}")
    
    # Gestionnaire d'arrêt propre
    def signal_handler(signum, frame):
        print("\n\n🛑 Arrêt du bot...")
        bot.stop()
        print("✅ Bot arrêté proprement")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Démarrer le bot
    print("\n🚀 Démarrage du bot...")
    bot.start()
    
    print("\n✅ Bot démarré et en écoute!")
    print("\nCommandes disponibles:")
    print("   /start    - Affiche l'aide")
    print("   /status   - État du robot")
    print("   /positions - Positions ouvertes")
    print("   /help     - Aide détaillée")
    print("\nAppuyez sur Ctrl+C pour arrêter")
    
    # Boucle principale
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    
    return 0


if __name__ == "__main__":
    sys.exit(main())