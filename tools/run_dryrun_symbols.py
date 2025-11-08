#!/usr/bin/env python3
"""Run dry-run (light mode) for a list of symbols non-invasively.

This script sets LIVE_ENGINE_LIGHT_MODE=1 and iterates over provided symbols,
running a single-cycle dry-run for each and printing a compact summary.
"""
import os
import json
import sys
from pathlib import Path
import importlib.util


SYMBOLS = [
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "USDCAD",
    "AUDNZD",
    "EURJPY",
    "GBPCHF",
    "NZDJPY",
    "EURUSD",
    "EURAUD",
    "US500.cash",
    "JP225.cash",
]


def load_engine_module():
    ROOT = Path(__file__).resolve().parents[1]
    MODULE_PATH = ROOT / "scripts" / "live_trading_engine.py"
    spec = importlib.util.spec_from_file_location("live_trading_engine", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_trading_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_for_symbol(engine_cls, symbol):
    try:
        eng = engine_cls(symbols=[symbol])
        # Ensure light mode and single cycle
        eng.max_cycles = 1
        # Try to fetch live data
        df = eng.get_live_data(symbol, count=100)
        eng.live_data[symbol] = df
        signals = eng.get_ai_signals(df, symbol)
        summary = {
            "symbol": symbol,
            "combined_signal": signals.get("combined_signal") if isinstance(signals, dict) else str(signals),
            "confidence": signals.get("confidence") if isinstance(signals, dict) else None,
        }
        print(json.dumps(summary, default=str))
        return True, summary
    except Exception as e:
        print(f"[error] symbol={symbol} -> {e}")
        return False, str(e)


def main():
    os.environ["LIVE_ENGINE_LIGHT_MODE"] = "1"

    mod = load_engine_module()
    LiveTradingEngine = getattr(mod, "LiveTradingEngine")

    results = {}
    for sym in SYMBOLS:
        print(f"--- Running dry-run for {sym} ---")
        ok, res = run_for_symbol(LiveTradingEngine, sym)
        results[sym] = {"ok": ok, "result": res}

    print("=== Summary ===")
    print(json.dumps(results, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
