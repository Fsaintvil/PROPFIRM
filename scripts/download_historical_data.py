"""
Download H1/H4/D1 historical data for 15 symbols via MT5.
Saves to data/historical/ as Parquet files.
Reuses existing data from data/raw/ when available.

Usage:
    python scripts/download_historical_data.py
    python scripts/download_historical_data.py --force
    python scripts/download_historical_data.py --symbols EURUSD,GBPUSD
    python scripts/download_historical_data.py --symbols EURUSD --max-candles 500000
    python scripts/download_historical_data.py --symbols EURUSD --max-candles 500000 --force
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
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

ALL_SYMBOLS = [
    # Commodities
    "XAUUSD",
    "XAGUSD",
    "USOIL.cash",
    # Crypto
    "BTCUSD",
    "ETHUSD",
    # Indices
    "US500.cash",
    "NAS100.cash",
    "JP225.cash",
    # Forex — Majors
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "USDCHF",
    # Forex — Crosses
    "EURJPY",
    "GBPJPY",
]

# Max candles par timeframe par défaut
DEFAULT_MAX_CANDLES = {"M15": 500000, "H1": 300000, "H4": 200000, "D1": 10000}

# Max candles personnalisés par symbole (optionnel, ex: récupérer plus de données forex)
SYMBOL_MAX_CANDLES = {
    # Forex pairs — max data pour les symboles actifs
    "EURUSD": {"H1": 500000},
    "GBPUSD": {"H1": 500000},
    "USDJPY": {"H1": 500000},
    "USDCAD": {"H1": 500000},
    "AUDUSD": {"H1": 500000},
    "NZDUSD": {"H1": 500000},
    "USDCHF": {"H1": 500000},
}

BATCH_SIZE = 50000
SLEEP_BETWEEN = 0.3

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/historical")


def download_tf(symbol, tf_name, force=False, max_candles_override=None):
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
    # max_candles: d'abord override CLI, puis per-symbol, puis défaut
    if max_candles_override is not None:
        max_candles = max_candles_override
    elif symbol in SYMBOL_MAX_CANDLES and tf_name in SYMBOL_MAX_CANDLES[symbol]:
        max_candles = SYMBOL_MAX_CANDLES[symbol][tf_name]
    else:
        max_candles = DEFAULT_MAX_CANDLES.get(tf_name, 10000)

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
        result.append(
            {
                "timestamp": datetime.fromtimestamp(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": int(r[5]),
                "spread": int(r[6]),
                "symbol": symbol,
            }
        )

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

    parser = argparse.ArgumentParser(description="Télécharge les données MT5 vers data/historical/")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Symboles séparés par des virgules (défaut: tous)",
    )
    parser.add_argument(
        "--max-candles",
        type=int,
        default=None,
        help="Max candles par timeframe (défaut: M15=500K, H1=300K, H4=200K, D1=10K). "
        "Utile après avoir augmenté maxbars dans MT5: --max-candles 500000",
    )
    args = parser.parse_args()

    symbols_to_download = ALL_SYMBOLS
    if args.symbols:
        symbols_to_download = [s.strip() for s in args.symbols.split(",")]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    print(f"MT5 connected: {mt5.terminal_info().name}")

    timeframes = ["M15", "H1", "H4", "D1"]

    for symbol in symbols_to_download:
        for tf_name in timeframes:
            print(f"  {symbol}_{tf_name}... ", end="", flush=True)
            start = time.time()
            (data, error) = download_tf(symbol, tf_name, force=args.force, max_candles_override=args.max_candles)
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
