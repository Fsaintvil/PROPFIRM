#!/usr/bin/env python3
"""
Orchestre: export MT5 7y 15m -> build dataset MTF -> backtest baseline.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]):
    print("$", " ".join(cmd))
    res = subprocess.run(cmd, check=True)
    return res.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="EURUSD,XAUUSD,BTCUSD")
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # 1) Export MT5 7y 15m (run as module to ensure package imports from project root)
    run([sys.executable, "-m", "scripts.export_mt5_ohlcv_7y", "--symbols", ",".join(symbols)])

    # 2) Build dataset
    for sym in symbols:
        run([sys.executable, "-m", "scripts.build_mtf_dataset", sym])

    # 3) Backtest
    for sym in symbols:
        dataset = Path(f"artifacts/datasets/{sym}_mtf_15m.parquet")
        if not dataset.exists():
            # fallback CSV
            dataset = Path(f"artifacts/datasets/{sym}_mtf_15m.csv")
    run([sys.executable, "-m", "scripts.backtest_mtf_7y", str(dataset)])


if __name__ == "__main__":
    main()
