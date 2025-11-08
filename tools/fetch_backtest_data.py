#!/usr/bin/env python3
"""Fetch or assemble backtest 15-min data for given symbols.

This helper attempts to ensure `years` years of 15-minute bars exist for each
symbol by checking local CSVs, then attempting to fetch from MT5 (if available)
or from a crypto exchange via CCXT for crypto symbols (BTC, ETH, etc.).

Usage:
  python tools/fetch_backtest_data.py --symbols BTCUSD,EURUSD --years 7

Notes:
 - For MT5 retrieval, the MT5 connector must be available and credentials set.
 - For CCXT retrieval, the package `ccxt` should be installed; this script will
   attempt to use Binance as default exchange for crypto ticks.
 - The script writes CSV files under `data/<SYMBOL>_15min.csv` when successful.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def bars_needed_for_15min(years: int) -> int:
    return int(years * 365 * 96)


def check_local_csv(symbol: str, years: int) -> tuple[bool, int, Path | None]:
    candidates = [
        Path("data") / f"{symbol}_15min.csv",
        Path("data") / f"{symbol}.csv",
    ]
    for p in candidates:
        if p.exists():
            try:
                import pandas as pd

                df = pd.read_csv(p)
                ln = len(df)
                return (ln >= bars_needed_for_15min(years), ln, p)
            except Exception:
                continue
    return (False, 0, None)


def try_mt5_fetch(symbol: str, years: int) -> tuple[bool, int, Path | None]:
    try:
        # lazy import of local mt5 connector
        from src.utils import mt5_connector as mt5_connector

        api = mt5_connector.get_mt5()
        if api is None:
            return (False, 0, None)
        timeframe = getattr(mt5_connector, "TIMEFRAME_M15", getattr(api, "TIMEFRAME_M15", 15))
        needed = bars_needed_for_15min(years)
        # Cap fetch size to a safe number
        fetch_n = int(min(300_000, needed))
        if hasattr(api, "copy_rates_from_pos"):
            arr = api.copy_rates_from_pos(symbol, timeframe, 0, fetch_n)
        else:
            if hasattr(api, "time_current"):
                arr = api.copy_rates_from(symbol, api.time_current(), fetch_n)
            else:
                arr = None
        if not arr:
            return (False, 0, None)

        # Convert to CSV
        try:
            import pandas as pd

            df = pd.DataFrame(arr)
            # time -> iso
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], unit="s")
            out = Path("data")
            out.mkdir(parents=True, exist_ok=True)
            file = out / f"{symbol}_15min.csv"
            df.to_csv(file, index=False)
            return (len(arr) >= needed, len(arr), file)
        except Exception:
            return (len(arr) >= needed, len(arr), None)
    except Exception:
        return (False, 0, None)


def try_ccxt_fetch(symbol: str, years: int) -> tuple[bool, int, Path | None]:
    # Only attempt for crypto-like symbols (BTCUSD, ETHUSD)
    s = symbol.upper()
    if not any(x in s for x in ("BTC", "ETH", "LTC", "XRP")):
        return (False, 0, None)
    try:
        import ccxt
        import pandas as pd
        import time

        ex = ccxt.binance()
        # map symbol to exchange pair (assume USDT quotes)
        if s.endswith("USD") and not s.endswith("USDT"):
            pair = s.replace("USD", "/USDT")
        elif "/" in s:
            pair = s
        else:
            pair = s.replace("USD", "/USDT")

        timeframe = "15m"
        needed = bars_needed_for_15min(years)
        limit = min(1000, needed)
        all_ohlcv = []
        since = None
        while len(all_ohlcv) < needed:
            batch = ex.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            if not batch:
                break
            all_ohlcv.extend(batch)
            since = batch[-1][0] + 1
            time.sleep(ex.rateLimit / 1000.0)
            if len(batch) < limit:
                break

        if not all_ohlcv:
            return (False, 0, None)

        df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
        out = Path("data")
        out.mkdir(parents=True, exist_ok=True)
        file = out / f"{symbol}_15min.csv"
        df.to_csv(file, index=False)
        return (len(df) >= needed, len(df), file)
    except Exception as e:
        return (False, 0, None)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default=",".join(["EURUSD", "BTCUSD"]))
    p.add_argument("--years", type=int, default=7)
    args = p.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    for s in syms:
        print(f"Checking {s}...")
        local_ok, local_count, path = check_local_csv(s, args.years)
        if local_ok:
            print(f"  Local CSV ok: {path} ({local_count} bars)")
            continue

        print(f"  Local CSV missing ({local_count} bars). Trying MT5...")
        mt5_ok, mt5_count, mt5_file = try_mt5_fetch(s, args.years)
        if mt5_ok:
            print(f"  MT5 fetch ok -> {mt5_file} ({mt5_count} bars)")
            continue
        if mt5_count > 0:
            print(f"  MT5 fetch insufficient: {mt5_count} bars")

        print("  Trying CCXT (crypto) fallback...")
        ccxt_ok, ccxt_count, ccxt_file = try_ccxt_fetch(s, args.years)
        if ccxt_ok:
            print(f"  CCXT fetch ok -> {ccxt_file} ({ccxt_count} bars)")
            continue
        if ccxt_count > 0:
            print(f"  CCXT fetch insufficient: {ccxt_count} bars")

        print(f"  Could not fetch sufficient 15min data for {s}. Please provide CSV under data/ or enable MT5/CCXT credentials.")


if __name__ == "__main__":
    main()
