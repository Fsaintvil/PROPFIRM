"""Download H1 MT5 data for all trade symbols (Jan-May 2026 overlap)."""
import os
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

SYMBOLS = ['AUDUSD', 'BTCUSD', 'ETHUSD', 'EURJPY', 'EURUSD', 'GBPJPY', 'GBPUSD',
           'JP225.cash', 'NZDUSD', 'US500.cash', 'USDCAD', 'USDCHF', 'USDJPY',
           'USOIL.cash', 'XAUUSD']

FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 5, 25, tzinfo=timezone.utc)
OUT = 'runtime/market_h1_2026'

if not mt5.initialize():
    print('MT5 initialize failed')
    exit()

try:
    os.makedirs(OUT, exist_ok=True)

    for sym in SYMBOLS:
        fpath = os.path.join(OUT, f'{sym}_H1.csv')
        if os.path.exists(fpath):
            sz = os.path.getsize(fpath)
            print(f'{sym}: already exists ({sz//1024}KB)')
            continue

        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, FROM, TO)
        if rates is None or len(rates) == 0:
            print(f'{sym}: no data')
            continue

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df[['time','open','high','low','close','tick_volume','spread','real_volume']]
        df.to_csv(fpath, index=False)
        print(f'{sym}: {len(df)} bars ({df["time"].min()} to {df["time"].max()})')

    # Summary
    print(f'\nDownloaded to {OUT}')
    for f in sorted(os.listdir(OUT)):
        sz = os.path.getsize(os.path.join(OUT, f))
        print(f'  {f}: {sz//1024}KB')
finally:
    mt5.shutdown()
