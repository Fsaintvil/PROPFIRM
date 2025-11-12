#!/usr/bin/env python3
"""
🔍 MONITEUR DE PRODUCTION EN TEMPS RÉEL
Surveillance continue du système de trading IA
"""

import time
import json
import os
from datetime import datetime
import subprocess
from pathlib import Path


def check_trading_status():
    """Vérifier si le trading live est actif"""
    try:
        # Vérifier les processus Python actifs
        cmd = ("Get-Process python | Where-Object {$_.MainWindowTitle "
               "-like '*live_trading*' -or $_.CommandLine -like "
               "'*live_trading_engine*'}")
        result = subprocess.run(
            ['powershell', '-Command', cmd],
            capture_output=True, text=True, shell=True
        )

        if result.stdout.strip():
            return True

        # Vérifier alternative avec wmic
        result = subprocess.run(
            ['wmic', 'process', 'where', 'name="python.exe"', 'get',
             'commandline'],
            capture_output=True, text=True
        )

        return 'live_trading_engine' in result.stdout

    except Exception as e:
        print(f"⚠️ Erreur vérification processus: {e}")
        return False


def get_latest_trades():
    """Récupérer les derniers trades"""
    try:
        # Determine repository root relative to this script and use repo-relative logs
        project_root = Path(__file__).resolve().parents[1]
        logs_dir = project_root / 'logs'
        trades_file = logs_dir / 'trades.json'
        if not trades_file.exists():
            return []

        with open(trades_file, 'r') as f:
            lines = f.readlines()

        trades = []
        for line in lines[-10:]:  # Derniers 10 trades
            if line.strip():
                try:
                    trade = json.loads(line)
                    trades.append(trade)
                except json.JSONDecodeError:
                    continue

        return trades

    except Exception as e:
        print(f"⚠️ Erreur lecture trades: {e}")
        return []


def get_latest_log_entries():
    """Récupérer les dernières entrées de log"""
    try:
        # Use repo-relative logs directory
        project_root = Path(__file__).resolve().parents[1]
        logs_dir = project_root / 'logs'
        if not logs_dir.exists():
            return []

        log_files = [
            f for f in os.listdir(logs_dir)
            if f.startswith('live_trading_202510')
        ]

        if not log_files:
            return []

        latest_log = logs_dir / sorted(log_files)[-1]

        with open(latest_log, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        return lines[-20:]  # Dernières 20 lignes

    except Exception as e:
        print(f"⚠️ Erreur lecture logs: {e}")
        return []


def display_production_status():
    """Afficher le statut de production"""
    os.system('cls' if os.name == 'nt' else 'clear')

    print("🚀 " + "="*60)
    print("    MONITEUR PRODUCTION TRADING IA - TEMPS RÉEL")
    print("="*64)
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Vérifier statut système
    is_active = check_trading_status()
    status_icon = "🟢" if is_active else "🔴"
    status_text = "ACTIF" if is_active else "INACTIF"

    print(f"📊 STATUT SYSTÈME: {status_icon} {status_text}")
    print()

    if is_active:
        print("🎯 CONFIGURATION PRODUCTION:")
        print("   📈 Instruments: EURUSD, XAUUSD, BTCUSD")
        try:
            from config.trading_config import TradingConfig as _TC
            _interval = getattr(_TC, 'TRADING_INTERVAL_SECONDS', 600)
        except Exception:
            _interval = 600
        print(f"   ⏱️  Intervalle: {_interval} secondes")
        print("   🎯 Seuil confiance: 68%")
        print("   💰 Lot size: 0.01")
        print("   🛡️  Risque max: 2%")
        print()

    # Derniers trades
    print("💰 DERNIERS TRADES:")
    trades = get_latest_trades()
    if trades:
        for trade in trades[-5:]:  # 5 derniers
            timestamp = trade.get('timestamp', 'N/A')
            symbol = trade.get('symbol', 'N/A')
            side = trade.get('side', 'N/A')
            lot = trade.get('lot', 'N/A')
            price = trade.get('price', 'N/A')

            if isinstance(timestamp, str) and '2025-10-20' in timestamp:
                trade_info = (f"   ✅ {timestamp[:19]} | {symbol} "
                              f"{side.upper()} {lot} @ {price}")
                print(trade_info)
    else:
        print("   ⏸️ Aucun trade récent détecté")

    print()

    # Dernières activités
    print("📋 DERNIÈRES ACTIVITÉS:")
    log_entries = get_latest_log_entries()
    if log_entries:
        for entry in log_entries[-8:]:  # 8 dernières
            keywords = ['Trade', 'TRADE', 'Ordre', 'Cycle']
            if any(keyword in entry for keyword in keywords):
                clean_entry = entry.strip()
                if len(clean_entry) > 80:
                    clean_entry = clean_entry[:77] + "..."
                print(f"   📝 {clean_entry}")
    else:
        print("   ⏸️ Aucune activité récente")

    print()
    print("="*64)
    print("🔄 Actualisation toutes les 10 secondes - Ctrl+C pour arrêter")


def main():
    """Boucle principale de monitoring"""
    print("🚀 Démarrage du moniteur de production...")
    print("⏰ Surveillance en temps réel du trading IA")
    time.sleep(2)

    try:
        while True:
            display_production_status()
            time.sleep(10)  # Actualisation toutes les 10 secondes

    except KeyboardInterrupt:
        print("\n\n🛑 Moniteur arrêté par l'utilisateur")
        print("✅ Surveillance terminée")

    except Exception as e:
        print(f"\n❌ Erreur moniteur: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
