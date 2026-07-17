"""Test MT5 historical data access - multiple methods."""

import MetaTrader5 as mt5
from datetime import datetime as dt, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError


def _mt5_call(func, timeout=60, *args, **kwargs):
    """Execute an MT5 call with a timeout."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except _TimeoutError:
            raise TimeoutError(f"MT5 call timed out after {timeout}s: {func.__name__}")


if not _mt5_call(mt5.initialize, 30):
    print("MT5 non connecté:", mt5.last_error())
    exit(1)

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]

print(f"\n=== Test copy_rates_from_pos (petit count) ===")
for sym in SYMBOLS:
    rates = _mt5_call(mt5.copy_rates_from_pos, 30, sym, mt5.TIMEFRAME_H1, 0, 100)
    if rates is not None:
        print(f"  {sym}: {len(rates)} bougies, de {dt.fromtimestamp(rates[0][0])} à {dt.fromtimestamp(rates[-1][0])}")
    else:
        err = mt5.last_error()
        print(f"  {sym}: ÉCHEC (err={err})")

print(f"\n=== Test copy_rates_from (1 an en arrière) ===")
one_year_ago = dt.now() - timedelta(days=365)
for sym in SYMBOLS:
    rates = _mt5_call(mt5.copy_rates_from, 60, sym, mt5.TIMEFRAME_H1, one_year_ago, 50000)
    if rates is not None and len(rates) > 0:
        print(f"  {sym}: {len(rates)} bougies, de {dt.fromtimestamp(rates[0][0])} à {dt.fromtimestamp(rates[-1][0])}")
    else:
        err = mt5.last_error()
        print(f"  {sym}: ÉCHEC (err={err})")

print(f"\n=== Test copy_rates_from (toute l'histoire disponible) ===")
# Try going back 10 years
ten_years_ago = dt.now() - timedelta(days=3650)
for sym in SYMBOLS:
    rates = _mt5_call(mt5.copy_rates_from, 120, sym, mt5.TIMEFRAME_H1, ten_years_ago, 100000)
    if rates is not None and len(rates) > 0:
        print(f"  {sym}: {len(rates)} bougies, de {dt.fromtimestamp(rates[0][0])} à {dt.fromtimestamp(rates[-1][0])}")
    else:
        print(f"  {sym}: ÉCHEC (peut-être pas assez de données dispo)")

        # Fallback: try incremental approach to find how much data exists
        for years_back in [5, 3, 2, 1]:
            d = dt.now() - timedelta(days=years_back * 365)
            rates = _mt5_call(mt5.copy_rates_from, 60, sym, mt5.TIMEFRAME_H1, d, 100)
            if rates is not None and len(rates) > 0:
                print(f"    -> {years_back}an: {len(rates)} bougies, de {dt.fromtimestamp(rates[0][0])}")
                break
        else:
            print(f"    -> Aucune donnée historique trouvée")

mt5.shutdown()
