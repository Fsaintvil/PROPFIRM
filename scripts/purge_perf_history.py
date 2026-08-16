"""Purge du state performance_history.json pollué (FIX 14 Août 2026).

Avant: recent_trades = 500 trades dont 494 IMPORT (6 semaines), 218 SELL bannis,
XAGUSD (trou noir), ancienne config. Cela faussait WR rolling (31-45%) et PF (<1.0).

Après: recent_trades limité aux trades LIVE réels + imports valides (BUY, symboles
actifs, 7 derniers jours). Rolling windows recalculés proprement.

Ce script s'exécute UNE FOIS pour corriger le state existant.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 🐛 FIX 16 Août 2026 (Audit C2): charger explicitement .env — avant, si le
# script était lancé sans .env chargé, active_symbols était vide et le filtre
# `if active_symbols and ...` était SILENCIEUSEMENT désactivé → SELL/symboles
# désactivés NON filtrés, tout en prétendant l'avoir fait.
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

RUNTIME_DIR = Path(__file__).parent.parent / "runtime"
HISTORY_FILE = RUNTIME_DIR / "performance_history.json"

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


def main(file_path: str | None = None) -> None:
    # Chemin optionnel (test / dry-run) : python purge_perf_history.py [CHEMIN]
    history_file = Path(file_path) if file_path else HISTORY_FILE
    if not history_file.exists():
        print("Fichier historique absent — rien à faire.")
        return

    with open(history_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    recent = data.get("recent_trades", [])
    before = len(recent)

    # 🔧 Déduplication AVANT purge temporelle (FIX M-S4) : les doubles écritures
    # non atomiques passées ont laissé des entrées strictement identiques
    # (même symbole + même timestamp). Clé naturelle = (symbole, ts) — un trade
    # réel est unique par (symbole, timestamp de fermeture).
    seen = set()
    deduped = []
    for t in recent:
        k = (t.get("symbol", ""), t.get("ts", ""))
        if k not in seen:
            seen.add(k)
            deduped.append(t)
    n_dupes = len(recent) - len(deduped)
    recent = deduped

    cutoff_str = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    kept = []
    removed_sell = removed_old = removed_disabled = 0
    for t in recent:
        symbol = t.get("symbol", "")
        direction = t.get("direction", "BUY")
        ts = t.get("ts", "")
        # Filtrer les SELL (bannis) en PREMIER — même les trades récents rejoués
        if direction == "SELL":
            removed_sell += 1
            continue
        # Filtrer les symboles désactivés
        if active_symbols and symbol not in active_symbols:
            removed_disabled += 1
            continue
        # Garder les trades LIVE réels (regime != IMPORT) — mais déjà filtrés SELL/désactivés
        if t.get("regime") != "IMPORT":
            kept.append(t)
            continue
        # Imports: appliquer le filtre temps
        if ts and ts[:10] < cutoff_str:
            removed_old += 1
            continue
        kept.append(t)

    data["recent_trades"] = kept

    # 🔧 Déduplication: le robot a rejoué les mêmes trades fermés en boucle
    # (bug replay: 3 trades écrits 83× le 14/08 à 17:13). Clé = symbole+direction+pnl.
    dedup = {}
    dedup_order = []
    for t in kept:
        k = (t.get("symbol", ""), t.get("direction", "BUY"), round(t.get("profit", 0), 2))
        if k not in dedup:
            dedup[k] = t
            dedup_order.append(k)
    data["recent_trades"] = [dedup[k] for k in dedup_order]
    n_dedup = len(data["recent_trades"])
    print(f"  dédupliqués: {len(kept)} → {n_dedup}")

    # Recalculer les rolling windows à partir de recent_trades (dédupliqué)
    cleaned = data["recent_trades"]
    rolling = {}
    n = len(cleaned)
    for w in [20, 50, 100, 200]:
        if n < w:
            continue
        subset = cleaned[-w:]
        wins = sum(1 for t in subset if t.get("profit", 0) > 0)
        pnl = sum(t.get("profit", 0) for t in subset)
        rolling[f"last_{w}"] = {
            "trades": w,
            "wins": wins,
            "losses": w - wins,
            "pnl": round(pnl, 2),
            "wr": round(wins / w * 100, 1) if w > 0 else 0,
            "avg": round(pnl / w, 2) if w > 0 else 0,
        }
    data["rolling"] = rolling

    # Recalculer les stats symboles (réinitialiser proprement)
    symbols = {}
    for t in cleaned:
        s = t.get("symbol", "")
        p = t.get("profit", 0)
        sd = symbols.setdefault(
            s,
            {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
             "gross_profit": 0.0, "gross_loss": 0.0, "regime_stats": {},
             "direction_stats": {}},
        )
        sd["trades"] += 1
        sd["pnl"] = round(sd.get("pnl", 0.0) + p, 2)
        if p > 0:
            sd["wins"] += 1
            sd["gross_profit"] = round(sd.get("gross_profit", 0.0) + p, 2)
        else:
            sd["losses"] += 1
            sd["gross_loss"] = round(sd.get("gross_loss", 0.0) + abs(p), 2)
    data["symbols"] = symbols

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"Purge terminée:")
    print(f"  recent_trades: {before} → {len(kept)}")
    print(f"  doublons (symbole+ts) retirés: {n_dupes}")
    print(f"  SELL retirés: {removed_sell}")
    print(f"  désactivés retirés: {removed_disabled}")
    print(f"  anciens (>7j) retirés: {removed_old}")
    print(f"  rolling: {json.dumps(rolling, indent=2)}")
    print(f"  symboles: {json.dumps(symbols, indent=2)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
