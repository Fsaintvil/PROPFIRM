#!/usr/bin/env python3
"""Smoke test for JP225 SL override.

This script enables LIVE_ENGINE_LIGHT_MODE=1 to avoid heavy AI imports,
creates synthetic live_data for 'JP225.cash' and calls
LiveTradingEngine.calculate_dynamic_stop_loss to verify ATR*3 override.
"""
import os
os.environ["LIVE_ENGINE_LIGHT_MODE"] = "1"

from pathlib import Path
import importlib.util
import sys
import pandas as pd
import numpy as np


# Load the live_trading_engine module by file path to avoid package import issues
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "live_trading_engine.py"
spec = importlib.util.spec_from_file_location("live_trading_engine", str(MODULE_PATH))
live_mod = importlib.util.module_from_spec(spec)
sys.modules["live_trading_engine"] = live_mod
spec.loader.exec_module(live_mod)
LiveTradingEngine = getattr(live_mod, "LiveTradingEngine")


def main():
    eng = LiveTradingEngine(symbols=["JP225.cash"])

    # Build synthetic live_data with high/low/close and returns
    periods = 30
    np.random.seed(42)
    base = 36000.0
    moves = np.random.normal(0, 20, periods)
    closes = base + np.cumsum(moves)
    highs = closes + np.abs(np.random.normal(0, 8, periods))
    lows = closes - np.abs(np.random.normal(0, 8, periods))
    returns = np.concatenate([[0.0], np.diff(closes) / closes[:-1]])

    df = pd.DataFrame({"high": highs, "low": lows, "close": closes, "returns": returns})
    eng.live_data["JP225.cash"] = df

    entry = float(df["close"].iloc[-1])
    sl = eng.calculate_dynamic_stop_loss("JP225.cash", "buy", entry)

    print("entry", entry)
    print("calculated_sl", sl)
    print("distance", entry - sl)


if __name__ == "__main__":
    main()
