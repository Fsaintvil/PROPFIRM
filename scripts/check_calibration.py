#!/usr/bin/env python3
"""Vérification complète de la chaîne de persistance calibration."""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RUNTIME = BASE / "runtime"

errors = []
ok = []

def check(label, condition, detail=""):
    if condition:
        ok.append(f"  ✅ {label}{' — ' + detail if detail else ''}")
    else:
        errors.append(f"  ❌ {label}{' — ' + detail if detail else ''}")

print("=" * 55)
print("  VÉRIFICATION COMPLÈTE DE LA PERSISTANCE CALIBRATION")
print("=" * 55)

# 1. calibration_state.json
cal = RUNTIME / "calibration_state.json"
check("calibration_state.json existe", cal.exists())
if cal.exists():
    try:
        data = json.loads(cal.read_text())
        mc = data.get("meta_calibration", {}).get("meta_calibration", {})
        trackers = mc.get("meta_trackers", {})
        check("Format JSON valide", True)
        check("3 trackers MetaLearner (DL_LSTM, MOM20x3, LGB)",
              len(trackers) == 3, f"trouvé: {list(trackers.keys())}")
        total_hist = sum(len(v) for v in data.get("online_history", {}).values())
        check("Trades historiques > 0", total_hist > 0, f"{total_hist} trades")
        check("Meta trades_since_recal initialisé",
              mc.get("meta_trades_since_recal", -1) >= 0)
        # Vérifier la structure des trackers
        for name, tdata in trackers.items():
            gs = tdata.get("global_stats", {})
            check(f"Tracker {name}: global_stats présent",
                  "wins" in gs and "losses" in gs and "total" in gs)
        # Vérifier online_history
        hist = data.get("online_history", {})
        check(f"Online history: {len(hist)} symboles", len(hist) > 0)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        check("calibration_state.json lisible", False, str(e))

# 2. online_learner_state.json
ol = RUNTIME / "online_learner_state.json"
check("online_learner_state.json existe", ol.exists())
if ol.exists():
    try:
        oldata = json.loads(ol.read_text())
        check("Format JSON valide", True)
        total = sum(len(v) for v in oldata.get("history", {}).values())
        check("Trades online_learner > 0", total > 0, f"{total} trades")
        n_adapted = len(oldata.get("adapted_params", {}))
        check("adapted_params présents", n_adapted > 0, f"{n_adapted} symboles")
        if total > 0:
            # Vérifier structure d'un trade
            for sym, hist in oldata.get("history", {}).items():
                if hist:
                    sample = hist[0]
                    check("Structure trade: keys ['r', 'regime']",
                          isinstance(sample, dict) and "r" in sample and "regime" in sample)
                    break
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        check("online_learner_state.json lisible", False, str(e))

# 3. Ancien pickle migré
pkl_migrated = RUNTIME / "calibration_state.pkl.migrated"
check("Ancien pickle archivé (.pkl.migrated)", pkl_migrated.exists())
if pkl_migrated.exists():
    check("Taille archive > 0", pkl_migrated.stat().st_size > 0)

# 4. Vérifier AdaptiveEngine (chargement simulation)
print("\n--- Test chargement AdaptiveEngine (simulation restart) ---")
try:
    sys.path.insert(0, str(BASE))
    from engine_simple.adaptive_intelligence import AdaptiveEngine
    ae = AdaptiveEngine(None)
    # Vérifier que l'OnlineLearner a des données
    n_records = sum(len(v) for v in ae.learner.history.values())
    check("OnlineLearner chargé depuis JSON", n_records > 0, f"{n_records} records")
    if n_records > 0:
        check("OnlineLearner.history non vide", True, f"{len(ae.learner.history)} symboles")
        # Vérifier existence de meta_learner.json (créé par load_state)
        meta_json = RUNTIME / "meta_learner.json"
        if meta_json.exists():
            meta_data = json.loads(meta_json.read_text())
            check("meta_learner.json chargé", True)
            trackers_loaded = list(meta_data.keys())
            check("MetaLearner trackers restaurés",
                  len(trackers_loaded) > 0, str(trackers_loaded))
except Exception as e:
    check("Simulation restart", False, str(e))

print("\n" + "=" * 55)
if errors:
    print(f"  {len(errors)} erreur(s), {len(ok)} succès")
    for e in errors:
        print(e)
    sys.exit(1)
else:
    print(f"  ✅ {len(ok)}/{len(ok)} tests PASSÉS — Persistance complète")
    print("=" * 55)
