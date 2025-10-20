#!/usr/bin/env python3
"""
Outil de diagnostic rapide pour BTCUSD :
- Affiche les dernières lignes de logs contenant 'BTCUSD'
- Affiche les derniers deals BTCUSD (24h)
- Affiche les positions ouvertes BTCUSD
- Affiche le tick actuel et info symbole

Usage: python tools/monitor_btc_signal.py
"""
import os
import glob
import re
from datetime import datetime, timedelta
import sys
import threading

try:
    import MetaTrader5 as mt5
except Exception as e:
    mt5 = None

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')


def tail_btc_logs(num_lines=80):
    pattern = os.path.join(LOG_DIR, 'live_trading_*.log')
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        print('Aucun fichier de log live_trading trouvé')
        return

    latest = files[0]
    print(f"--- Dernier fichier log: {latest}\n")

    with open(latest, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    # Filtrer les lignes contenant BTCUSD
    btc_lines = [ln for ln in lines if 'BTCUSD' in ln]
    if not btc_lines:
        print('Aucune ligne BTCUSD dans le dernier fichier de log')
        return

    print(f"--- Dernières {min(len(btc_lines), num_lines)} lignes BTCUSD dans le log:\n")
    for ln in btc_lines[-num_lines:]:
        print(ln.rstrip())


def mt5_status():
    # Run MT5 queries in a short-lived thread to avoid blocking the monitor
    if mt5 is None:
        print('\nMT5 non disponible dans l\'environnement Python actuel.\n')
        return

    def worker():
        try:
            if not mt5.initialize():
                print('Échec initialisation MT5:', mt5.last_error())
                return

            account = mt5.account_info()
            print(f"\nCompte MT5: {getattr(account, 'login', 'N/A')} | Balance: {getattr(account, 'balance', 'N/A')} | Equity: {getattr(account, 'equity', 'N/A')}")

            # Tick
            tick = mt5.symbol_info_tick('BTCUSD')
            if tick is None:
                print('Impossible d\'obtenir tick BTCUSD')
            else:
                print(f"Tick BTCUSD - Bid: {tick.bid}, Ask: {tick.ask}, Spread: {tick.ask - tick.bid:.2f}")

            # Symbol info
            info = mt5.symbol_info('BTCUSD')
            if info is None:
                print('Symbol info BTCUSD indisponible')
            else:
                print(f"Symbol visible: {info.visible} | Trade mode: {getattr(info, 'trade_mode', 'N/A')} | Session: {getattr(info, 'session_open', 'N/A')}")

            # Positions
            positions = mt5.positions_get(symbol='BTCUSD')
            print(f"\nPositions ouvertes BTCUSD: {len(positions) if positions is not None else 0}")
            if positions:
                for pos in positions:
                    print(f"- ticket:{pos.ticket} type:{pos.type} vol:{pos.volume} open:{pos.price_open} profit:{pos.profit}")

            # Deals récents (24h)
            now = datetime.now()
            yesterday = now - timedelta(days=1)

            deals = mt5.history_deals_get(yesterday, now, symbol='BTCUSD')
            print(f"\nDeals BTCUSD dernières 24h: {len(deals) if deals is not None else 0}")
            if deals:
                for d in deals[-10:]:
                    t = datetime.fromtimestamp(d.time)
                    print(f"- {t} | type:{d.type} vol:{d.volume} price:{d.price} profit:{d.profit}")

            mt5.shutdown()

        except Exception as e:
            print('Erreur interrogating MT5:', e)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(10)
    if thread.is_alive():
        print(f"MT5 interrogation dépassé après 10s, abandon pour éviter blocage.")


if __name__ == '__main__':
    tail_btc_logs(80)
    mt5_status()
