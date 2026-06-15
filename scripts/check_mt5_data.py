"""Vérifie la disponibilité des données MT5 pour 5 symboles."""
import MetaTrader5 as mt5
from datetime import datetime as dt

if not mt5.initialize():
    print("MT5 non connecté:", mt5.last_error())
    exit(1)

print("MT5 connecté")
ti = mt5.terminal_info()
print(f"Terminal: {ti.name if ti else 'N/A'} (build {ti.build if ti else 'N/A'})")
acc = mt5.account_info()
print(f"Compte: {acc.login if acc else 'N/A'}")

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]

now = dt.now()
print("\n=== Dernières données disponibles ===")
for sym in SYMBOLS:
    rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_H1, now, 10)
    if rates is not None and len(rates) > 0:
        print(f"  {sym}: OK (dernière bougie: {dt.fromtimestamp(rates[-1][0])})")
    else:
        print(f"  {sym}: PAS DE DONNÉES")

print("\n=== Profondeur historique H1 ===")
for sym in SYMBOLS:
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 100000)
    if rates is not None and len(rates) > 0:
        from_date = dt.fromtimestamp(rates[0][0])
        to_date = dt.fromtimestamp(rates[-1][0])
        days = (to_date - from_date).days
        years = days / 365.25
        print(f"  {sym}: {len(rates)} bougies, du {from_date.date()} au {to_date.date()} ({days}j, {years:.1f} ans)")
    else:
        print(f"  {sym}: PAS DE DONNÉES DISPO")

mt5.shutdown()
