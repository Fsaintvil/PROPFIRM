#!/usr/bin/env python3
"""Runner non-invasif : preflight + dry-run léger (1 cycle).

Ce script :
- force LIVE_ENGINE_LIGHT_MODE=1 pour éviter imports ML lourds
- instancie `LiveTradingEngine`
- appelle `production_health_check()` et affiche le résultat
- récupère des données live simulées ou via MT5 (mode simulation possible)
- appelle `get_ai_signals` pour 1 symbole et affiche un résumé du résultat

Ne réalise aucun envoi d'ordres ni modification d'état externe.
"""
import os
import json
from pathlib import Path
import importlib.util
import sys


def load_engine_module():
    ROOT = Path(__file__).resolve().parents[1]
    MODULE_PATH = ROOT / "scripts" / "live_trading_engine.py"
    spec = importlib.util.spec_from_file_location("live_trading_engine", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_trading_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    # Environment: light mode, single cycle
    os.environ["LIVE_ENGINE_LIGHT_MODE"] = "1"
    os.environ["ENGINE_MAX_CYCLES"] = "1"

    print("[runner] LIVE_ENGINE_LIGHT_MODE=1, ENGINE_MAX_CYCLES=1")

    mod = load_engine_module()
    LiveTradingEngine = getattr(mod, "LiveTradingEngine")

    # Instantiate engine with default symbols
    eng = LiveTradingEngine()

    print("[runner] Running production_health_check()...")
    try:
        ok = eng.production_health_check()
        print(f"[runner] production_health_check -> {ok}")
    except Exception as e:
        print("[runner] production_health_check raised:", e)

    # Dry-run: get live data for first symbol and compute signals
    symbol = eng.symbols[0] if eng.symbols else None
    if not symbol:
        print("[runner] No symbols configured, exiting")
        return 1

    print(f"[runner] Performing dry-run for symbol: {symbol}")

    try:
        # Use engine's get_live_data (it may generate simulation data)
        df = eng.get_live_data(symbol, count=100)
        if df is None:
            print("[runner] get_live_data returned None")
            return 2
        # Ensure engine.live_data updated
        eng.live_data[symbol] = df

        # Call get_ai_signals (non-invasive) to compute signals
        signals = eng.get_ai_signals(df, symbol)
        # Print a compact summary
        summary = {
            "symbol": symbol,
            "combined_signal": signals.get("combined_signal") if isinstance(signals, dict) else str(signals),
            "confidence": signals.get("confidence") if isinstance(signals, dict) else None,
        }
        print("[runner] signals summary:", json.dumps(summary, default=str))
    except Exception as e:
        print("[runner] Dry-run failed:", e)
        return 3

    print("[runner] Dry-run completed successfully")
    return 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)
