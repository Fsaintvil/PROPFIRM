"""Régénération complète du performance_history.json (FIX 14 Août 2026).

Le fichier était corrompu par un bug de replay: le robot a réécrit les mêmes
trades fermés en boucle (83× pour certains) pendant ~2h le 14/08, gonflant
le daily à 506 trades (au lieu de 8 réels) et les rolling windows à WR 31-45%.

Cette régénération reconstruit performance_history.json à partir de
trades_log.csv (la source fiable), avec les filtres du fix:
- SELL exclus (bannis depuis le 06/08, WR 34% = -2 925$)
- Symboles désactivés exclus
- 7 derniers jours uniquement
- Déduplication par (symbole, direction, pnl)
- Reconstruit les rolling windows + stats symboles propres
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# 🐛 FIX 16 Août 2026 (Audit C2): charger explicitement .env — le filtre des
# symboles désactivés était silencieusement désactivé si SYMBOLS absent.
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

RUNTIME_DIR = Path(__file__).parent.parent / "runtime"
HISTORY_FILE = RUNTIME_DIR / "performance_history.json"
CSV_FILE = RUNTIME_DIR / "trades_log.csv"

# Symboles actifs (source .env, comme dans _import_from_csv)
_env_syms = os.environ.get("SYMBOLS", "").strip()
active_symbols = {s.strip() for s in _env_syms.split(",") if s.strip()}
if not active_symbols:
    print(
        "❌ ERREUR: variable SYMBOLS absente/vide — le filtre des symboles "
        "désactivés serait inopérant. Lancez ce script depuis la racine du "
        "projet avec .env chargé (ou définissez SYMBOLS)."
    )
    sys.exit(1)


def build_recent_from_csv() -> list:
    cutoff_str = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = []
    seen = set()
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        import csv as csv_mod
        reader = csv_mod.DictReader(f)
        for row in reader:
            symbol = row.get("symbol", "")
            try:
                pnl = float(row.get("pnl", 0))
            except (TypeError, ValueError):
                continue
            direction = row.get("direction", "BUY")
            ts = row.get("timestamp", "")
            if pnl == 0 or not symbol or not ts:
                continue
            if direction == "SELL":
                continue
            if active_symbols and symbol not in active_symbols:
                continue
            if ts[:10] < cutoff_str:
                continue
            k = (symbol, direction, round(pnl, 2))
            if k in seen:
                continue
            seen.add(k)
            recent.append({
                "profit": pnl,
                "symbol": symbol,
                "regime": "IMPORT",
                "direction": direction,
                "ts": ts,
            })
    # Trier par timestamp (les plus récents en dernier, comme record_trade)
    recent.sort(key=lambda t: t["ts"])
    return recent


def main() -> None:
    if not CSV_FILE.exists():
        print("trades_log.csv absent — rien à faire.")
        return

    recent = build_recent_from_csv()
    print(f"CSV import propre: {len(recent)} trades uniques (BUY, actifs, 7 jours)")

    # Reconstruire daily à partir du CSV (derniers 7 jours)
    daily = {}
    for t in recent:
        day = t["ts"][:10]
        d = daily.setdefault(day, {
            "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "symbols": {},
        })
        d["trades"] += 1
        p = t["profit"]
        d["pnl"] = round(d["pnl"] + p, 2)
        if p > 0:
            d["wins"] += 1
            d["gross_profit"] = round(d["gross_profit"] + p, 2)
        else:
            d["losses"] += 1
            d["gross_loss"] = round(d["gross_loss"] + abs(p), 2)
        s = d["symbols"].setdefault(t["symbol"], {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        s["trades"] += 1
        s["pnl"] = round(s["pnl"] + p, 2)
        if p > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1

    # Rolling windows
    rolling = {}
    n = len(recent)
    for w in [20, 50, 100, 200]:
        if n < w:
            continue
        subset = recent[-w:]
        wins = sum(1 for t in subset if t["profit"] > 0)
        pnl = sum(t["profit"] for t in subset)
        rolling[f"last_{w}"] = {
            "trades": w, "wins": wins, "losses": w - wins,
            "pnl": round(pnl, 2),
            "wr": round(wins / w * 100, 1) if w > 0 else 0,
            "avg": round(pnl / w, 2) if w > 0 else 0,
        }

    # Stats symboles
    symbols = {}
    for t in recent:
        s = symbols.setdefault(t["symbol"], {
            "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "regime_stats": {},
            "direction_stats": {},
        })
        s["trades"] += 1
        p = t["profit"]
        s["pnl"] = round(s.get("pnl", 0.0) + p, 2)
        if p > 0:
            s["wins"] += 1
            s["gross_profit"] = round(s.get("gross_profit", 0.0) + p, 2)
        else:
            s["losses"] += 1
            s["gross_loss"] = round(s.get("gross_loss", 0.0) + abs(p), 2)

    data = {
        "daily": daily,
        "rolling": rolling,
        "symbols": symbols,
        "alerts": [],
        "challenge": {},
        "recent_trades": recent,
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"Régénération terminée → {HISTORY_FILE}")
    print(f"  daily: {json.dumps(daily, indent=2)}")
    print(f"  rolling: {json.dumps(rolling, indent=2)}")
    print(f"  symbols: {json.dumps(symbols, indent=2)}")


if __name__ == "__main__":
    main()