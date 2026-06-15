"""
Download H1/H4/D1 historical data for 15 symbols via MT5.
Saves to data/historical/ as Parquet files.
Reuses existing data from data/raw/ when available.

Usage:
    python scripts/download_historical_data.py
    python scripts/download_historical_data.py --force
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5

TF_MAP = {
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

ALL_SYMBOLS = [
    "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD",
    "NZDUSD", "USDCAD", "USDCHF", "USDJPY", "XAUUSD",
    "USOIL.cash", "US500.cash", "BTCUSD", "ETHUSD", "JP225.cash",
]

BATCH_SIZE = 50000
SLEEP_BETWEEN = 0.3

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/historical")


def download_tf(symbol, tf_name, force=False):
    """Download one timeframe for one symbol."""
    out_path = OUT_DIR / f"{symbol}_{tf_name}.parquet"

    if not force and out_path.exists():
        df = pd.read_parquet(out_path)
        return (df.to_dict("records"), None)

    raw_path = RAW_DIR / f"{symbol}_{tf_name}_raw.parquet"
    if not force and raw_path.exists():
        df = pd.read_parquet(raw_path)
        df.to_parquet(out_path)
        return (df.to_dict("records"), None)

    tf = TF_MAP[tf_name]
    all_rates = []
    offset = 0
    max_candles = 300000 if tf_name == "H1" else 200000 if tf_name == "H4" else 10000

    while offset < max_candles:
        rates = mt5.copy_rates_from_pos(symbol, tf, offset, BATCH_SIZE)
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        n = len(rates)
        offset += n
        if n < BATCH_SIZE:
            break
        time.sleep(0.2)

    if len(all_rates) == 0:
        return ([], f"Aucune donnee pour {symbol} {tf_name}")

    result = []
    for r in all_rates:
        result.append({
            "timestamp": datetime.fromtimestamp(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": int(r[5]),
            "spread": int(r[6]),
            "symbol": symbol,
        })

    seen = set()
    deduped = []
    for r in result:
        ts = r["timestamp"]
        if ts in seen:
            continue
        seen.add(ts)
        deduped.append(r)

    df = pd.DataFrame(deduped)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(out_path, index=False)
    return (df.to_dict("records"), None)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    print(f"MT5 connected: {mt5.terminal_info().name}")

    timeframes = ["H1", "H4", "D1"]

    for symbol in ALL_SYMBOLS:
        for tf_name in timeframes:
            print(f"  {symbol}_{tf_name}... ", end="", flush=True)
            start = time.time()
            (data, error) = download_tf(symbol, tf_name, force=args.force)
            elapsed = time.time() - start

            if error:
                print(f"  {error}")
            elif len(data) == 0:
                print(f"  0 barres")
            else:
                times = [r["timestamp"] for r in data]
                t_min = str(min(times))[:10]
                t_max = str(max(times))[:10]
                print(f"  {len(data):>6d} barres | {t_min} -> {t_max} | {elapsed:.0f}s")

            time.sleep(SLEEP_BETWEEN)

    mt5.shutdown()
    print(f"\nDone. Files in {OUT_DIR}")


if __name__ == "__main__":
    main()
