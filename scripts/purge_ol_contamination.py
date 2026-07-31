#!/usr/bin/env python3
"""Purger la contamination OnlineLearner (burst/replay) des fichiers d'état.

Contexte (FIX 31 Juillet 2026):
- EURUSD, GBPUSD, USDCHF ont chacun 200 trades "enregistrés" en 10 minutes
  avec 96% des gaps < 1s → signature d'un replay (cache MT5 vide → timeout
  transforme toutes les positions en "fermées" → centaines de faux trades).
- Ces trades synthétiques ont alimenté l'OnlineLearner ET ses adapted_params
  (ex: EURUSD risk_mult=0.538 dérivé de bruit pur).
- Un guard anti-burst a été ajouté dans adaptive_intelligence.py (_load_state
  + _load_calibration) qui purge automatiquement au chargement. Ce script
  nettoie AUSSI les fichiers sur disque (défense en profondeur).

Usage:
    python scripts/purge_ol_contamination.py              # Appliquer
    python scripts/purge_ol_contamination.py --dry-run    # Simulation seule
"""

import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / "runtime" / "ol_state.json",
    ROOT / "runtime" / "calibration_state.json",
]

# Seuils du guard (identiques à adaptive_intelligence._is_burst_history)
MIN_TRADES = 15
BURST_RATIO = 0.5


def is_burst_history(hist: list) -> bool:
    if len(hist) < MIN_TRADES:
        return False
    timestamps = []
    for h in hist:
        t = h.get("time")
        if isinstance(t, str):
            try:
                timestamps.append(datetime.fromisoformat(t))
            except (ValueError, TypeError):
                continue
    if len(timestamps) < MIN_TRADES:
        return False
    timestamps.sort()
    gaps = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
    if not gaps:
        return False
    sub_1s = sum(1 for g in gaps if g < 1.0)
    return (sub_1s / len(gaps)) >= BURST_RATIO


def purge_file(path: Path, dry_run: bool) -> int:
    if not path.exists():
        print(f"  {path.name}: absent — skip")
        return 0
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    purged = 0
    # 1. Purge history contaminée (online_history / history)
    for hist_key in ("online_history", "history"):
        hist = data.get(hist_key)
        if isinstance(hist, dict):
            for sym in list(hist.keys()):
                if is_burst_history(list(hist[sym])):
                    print(f"  {path.name}: PURGE {sym} history ({len(hist[sym])} trades contaminés)")
                    if not dry_run:
                        del hist[sym]
                    purged += 1

    # 2. Purge adapted_params des symboles sans history valide
    hist = data.get("online_history", data.get("history", {}))
    adapted = data.get("adapted_params")
    if isinstance(adapted, dict):
        for sym in list(adapted.keys()):
            if sym not in hist:
                print(f"  {path.name}: PURGE {sym} adapted_params (history absente/contaminée)")
                if not dry_run:
                    del adapted[sym]
                purged += 1

    if dry_run:
        print(f"  {path.name}: {purged} entrées à purger (dry-run)")
        return purged

    # Backup avant écriture
    backup = path.with_suffix(".json.bak_contam")
    shutil.copy2(path, backup)
    print(f"  {path.name}: backup → {backup.name}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  {path.name}: {purged} entrées purgées")
    return purged


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    print("=== Purge contamination OnlineLearner ===")
    print(f"Seuils: min_trades={MIN_TRADES}, burst_ratio={BURST_RATIO}")
    total = 0
    for fp in FILES:
        total += purge_file(fp, dry_run)
    print(f"\nTotal: {total} entrées {'à purger (dry-run)' if dry_run else 'purgées'}")
    print("⚠️  Le robot doit être ARRÊTÉ avant de purger (sinon il réécrit les données contaminées en mémoire).")
