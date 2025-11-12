"""Script utilitaire pour initialiser les systèmes AI sans démarrer la boucle de trading.

Usage:
  python tools/initialize_ai_systems.py

Le script instancie `LiveTradingEngine`, appelle `initialize_ai_systems()` et
écrit un résumé dans `artifacts/live_trading/ai_init_result.json`.
"""
import os
import json
from pathlib import Path

def main():
    # Charger le moteur depuis scripts
    try:
        from scripts.live_trading_engine import LiveTradingEngine
    except Exception:
        from live_trading_engine import LiveTradingEngine

    symbols = os.getenv("SYMBOLS", "BTCUSD,ETHUSD,XAUUSD,USDCAD,AUDNZD,EURJPY,GBPCHF,NZDJPY,EURUSD,EURAUD,US500.cash,JP225.cash")
    symbols = [s.strip() for s in symbols.split(",") if s.strip()]

    engine = LiveTradingEngine(symbols=symbols)

    Path("artifacts/live_trading").mkdir(parents=True, exist_ok=True)
    out = {"timestamp": None, "initialized": False, "components": {}, "errors": []}

    try:
        out["timestamp"] = __import__('datetime').datetime.utcnow().isoformat()
        ok = engine.initialize_ai_systems()
        out["initialized"] = bool(ok)
        out["components"]["meta_learning"] = bool(getattr(engine, 'meta_learning', None))
        out["components"]["rl_agent"] = bool(getattr(engine, 'rl_agent', None))
        out["components"]["portfolio_optimizer"] = bool(getattr(engine, 'portfolio_optimizer', None))
        out["components"]["regime_detector"] = bool(getattr(engine, 'regime_detector', None))
    except Exception as e:
        out["errors"].append(str(e))

    fn = Path("artifacts/live_trading/ai_init_result.json")
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("AI init result written to", str(fn))

if __name__ == '__main__':
    main()
