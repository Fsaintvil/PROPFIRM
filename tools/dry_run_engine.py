"""Script non invasif pour exécuter un dry-run du LiveTradingEngine.
Il instancie le moteur en mode simulation (MT5 désactivé si indisponible),
exécute les checks, initialise les systèmes AI (fallback autorisé),
récupère les signaux pour chaque symbole et sauvegarde un rapport JSON
sous `artifacts/live_dry_run/` sans envoyer d'ordres.

Utilisation: python tools/dry_run_engine.py
"""

import os
import json
from datetime import datetime
from pathlib import Path
import sys

# Ajouter le repo au path si nécessaire (avant imports du module local)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_dry_run(symbols=None, count=200):
    # Import local effectué ici pour éviter des effets secondaires au niveau module
    from scripts.live_trading_engine import LiveTradingEngine
    os.makedirs("artifacts/live_dry_run", exist_ok=True)
    engine = LiveTradingEngine(symbols=symbols)

    report = {
        "timestamp": datetime.now().isoformat(),
        "symbols": engine.symbols,
        "mt5_available": False,
        "health_check": None,
        "signals": {},
        "notes": []
    }

    # Check MT5 availability from module variable
    try:
        report["mt5_available"] = globals().get('MT5_AVAILABLE', None)
    except Exception:
        pass

    # Run production health check (non-fatal)
    try:
        report["health_check"] = engine.production_health_check()
    except Exception as e:
        report["notes"].append(f"health_check_error: {e}")

    # Initialize AI (allow fallback)
    try:
        engine.initialize_ai_systems()
    except Exception as e:
        report["notes"].append(f"init_ai_error: {e}")

    # Get simulated live data
    try:
        data = engine.get_live_data(None, count)
    except Exception as e:
        data = {}
        report["notes"].append(f"get_live_data_error: {e}")

    # For each symbol, compute signals but DO NOT execute trades
    for symbol in engine.symbols:
        try:
            symbol_df = None
            if isinstance(data, dict) and symbol in data:
                symbol_df = data[symbol]
            elif not isinstance(data, dict) and hasattr(data, 'head'):
                symbol_df = data

            if symbol_df is None or len(symbol_df) == 0:
                report["signals"][symbol] = {"error": "no_data"}
                continue

            signals = engine.get_ai_signals(symbol_df)
            report["signals"][symbol] = {
                "combined_signal": signals.get("combined_signal"),
                "confidence": signals.get("confidence"),
                "meta_learning": signals.get("meta_learning"),
                "regime_detection": signals.get("regime_detection"),
            }

        except Exception as e:
            report["signals"][symbol] = {"error": str(e)}

    # Save report
    filename = f"artifacts/live_dry_run/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, default=str)

    print(f"Dry-run report saved: {filename}")
    return filename


if __name__ == '__main__':
    run_dry_run()
