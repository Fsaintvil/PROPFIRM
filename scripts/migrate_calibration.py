#!/usr/bin/env python3
"""Migration one-shot : pickle → JSON pour calibration_state.

Étape 1 : Lire calibration_state.pkl (pickle) — dernière fois.
Étape 2 : Lire online_learner_state.json (déjà JSON).
Étape 3 : Fusionner dans calibration_state.json (nouveau format JSON).
Étape 4 : Renommer .pkl → .pkl.migrated (archive).
Étape 5 : Initialiser MetaLearner depuis l'historique.
"""

import json
import logging
import os
import sys
from collections import deque
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")

BASE = Path(__file__).resolve().parent.parent
RUNTIME = BASE / "runtime"

CAL_PKL = RUNTIME / "calibration_state.pkl"
CAL_JSON = RUNTIME / "calibration_state.json"
OL_JSON = RUNTIME / "online_learner_state.json"
META_JSON = RUNTIME / "meta_learner.json"


def migrate():
    # --- Étape 1 : Lire l'ancien pickle ---
    state = {}
    if CAL_PKL.exists():
        try:
            import joblib
            old = joblib.load(CAL_PKL)
            logger.info(f"📖 Lu calibration_state.pkl ({CAL_PKL.stat().st_size} bytes)")
            state = old or {}
            # Renommer pour archive
            CAL_PKL.rename(CAL_PKL.with_suffix(".pkl.migrated"))
            logger.info(f"📦 Archivé → {CAL_PKL.name}.migrated")
        except Exception as e:
            logger.warning(f"⚠️  Impossible de lire {CAL_PKL.name}: {e}")
            logger.warning("   Démarrage avec un état vierge")
    else:
        logger.info("ℹ️  calibration_state.pkl non trouvé — démarrage vierge")

    # --- Étape 2 : Lire online_learner_state.json (déjà fonctionnel) ---
    ol_data = {}
    if OL_JSON.exists():
        try:
            with open(OL_JSON) as f:
                ol_data = json.load(f)
            total = sum(len(v) for v in ol_data.get("history", {}).values())
            logger.info(f"📖 Lu online_learner_state.json: {total} trades "
                        f"dans {len(ol_data.get('history', {}))} symboles")
        except Exception as e:
            logger.warning(f"⚠️  Impossible de lire {OL_JSON.name}: {e}")

    # --- Étape 3 : Construire le nouvel état JSON ---
    # Extraire les meta trackers du vieux pickle si existant
    mc = state.get("meta_calibration", {})
    mc_calibrated = mc.get("meta_calibration", mc)

    # Construire le nouveau calibration_state
    new_state = {
        "meta_calibration": {
            "meta_calibration": {
                "meta_trackers": mc_calibrated.get("meta_trackers", {
                    "DL_LSTM": {"regime_stats": {}, "global_stats": {"wins": 0, "losses": 0, "total": 0},
                                "symbol_stats": {}},
                    "MOM20x3": {"regime_stats": {}, "global_stats": {"wins": 0, "losses": 0, "total": 0},
                                "symbol_stats": {}},
                    "LGB": {"regime_stats": {}, "global_stats": {"wins": 0, "losses": 0, "total": 0},
                            "symbol_stats": {}},
                }),
                "meta_regime_performance": mc_calibrated.get("meta_regime_performance", {}),
                "meta_regime_penalty": mc_calibrated.get("meta_regime_penalty", {
                    "DL_LSTM": {}, "MOM20x3": {}, "LGB": {}
                }),
                "meta_trades_since_recal": mc_calibrated.get("meta_trades_since_recal", 0),
            }
        },
        "online_history": ol_data.get("history", {}),
    }

    # --- Étape 4 : Écrire calibration_state.json ---
    try:
        with open(CAL_JSON, "w") as f:
            json.dump(new_state, f, indent=2, default=str)
        logger.info(f"💾 Écrit calibration_state.json (size: {CAL_JSON.stat().st_size} bytes)")
    except Exception as e:
        logger.error(f"❌ Erreur écriture calibration_state.json: {e}")
        return False

    # --- Étape 5 : Vérifier la cohérence ---
    try:
        with open(CAL_JSON) as f:
            verify = json.load(f)
        mc_check = verify.get("meta_calibration", {}).get("meta_calibration", {})
        n_trackers = len(mc_check.get("meta_trackers", {}))
        n_hist = sum(len(v) for v in verify.get("online_history", {}).values())
        logger.info(f"✅ Migration réussie : {n_trackers} trackers, {n_hist} trades historiques")
    except Exception as e:
        logger.error(f"❌ Vérification échouée: {e}")
        return False

    return True


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("  MIGRATION calibration_state.pkl → calibration_state.json")
    logger.info("=" * 50)
    success = migrate()
    sys.exit(0 if success else 1)
