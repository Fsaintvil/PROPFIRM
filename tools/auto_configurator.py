#!/usr/bin/env python3
"""
Auto-configurateur de production - propose une configuration sûre
en fonction de l’environnement (MT5, artefacts, fondamentaux).

Retourne un dict prêt à être utilisé par le lanceur.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Dict, Any, List


def _read_selected_threshold() -> float | None:
    try:
        p = Path("artifacts/auto_improve/optimization/selected_threshold.json")
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            val = data.get("selected_threshold")
            return float(val) if val is not None else None
    except Exception:
        return None
    return None


def _mt5_available() -> bool:
    # Détection légère: évite d'importer si non installé
    try:
        import importlib.util as _ilu  # type: ignore
        return _ilu.find_spec("MetaTrader5") is not None
    except Exception:
        return False


def _probe_mt5_symbols(candidates: List[str]) -> List[str]:
    """Filtre les symboles disponibles si MT5 est accessible; sinon retourne candidats."""
    try:
        import MetaTrader5 as mt5  # type: ignore
        if not mt5.initialize():
            return candidates
        available = []
        for sym in candidates:
            info = mt5.symbol_info(sym)
            if info is not None:
                # Essayer de sélectionner pour s'assurer de la dispo
                mt5.symbol_select(sym, True)
                available.append(sym)
        mt5.shutdown()
        return available or candidates
    except Exception:
        return candidates


def _default_lots(symbols: List[str]) -> Dict[str, float]:
    # Valeurs sûres par défaut
    return {s: 0.01 for s in symbols}


def suggest_production_config() -> Dict[str, Any]:
    """Construit une configuration de production sûre et exploitable.

    Contenu retourné:
    - symbols: List[str]
    - lot_sizes: Dict[str, float]
    - risk_per_trade: float
    - threshold: float
    - flags: Dict[str, Any] (USE_MTF, USE_FUNDAMENTAL, USE_EXT_MTF_TECH)
    - interval_seconds: int
    """
    # 1) Symboles candidats
    candidates = ["EURUSD", "XAUUSD", "BTCUSD"]
    if _mt5_available():
        symbols = _probe_mt5_symbols(candidates)
    else:
        symbols = candidates

    # 2) Lots par défaut
    lots = _default_lots(symbols)

    # 3) Seuil de confiance
    threshold = _read_selected_threshold() or 0.68

    # 4) Intervalle recommandé (défaut 10 min)
    interval_seconds = int(os.getenv("TRADING_INTERVAL", "600"))

    # 5) Flags suggérés
    flags = {
        "USE_MTF": True,
        "USE_FUNDAMENTAL": True,
        "USE_EXT_MTF_TECH": True,  # prudente mais utile
    }

    # 6) Risque par trade (par défaut 2%)
    risk_per_trade = 0.02

    return {
        "symbols": symbols,
        "lot_sizes": lots,
        "risk_per_trade": risk_per_trade,
        "threshold": threshold,
        "flags": flags,
        "interval_seconds": interval_seconds,
    }


def apply_env_flags(flags: Dict[str, Any]) -> None:
    """Applique les flags côté environnement (process courant)."""
    try:
        os.environ["USE_MTF"] = "1" if flags.get("USE_MTF", True) else "0"
        os.environ["USE_FUNDAMENTAL"] = (
            "1" if flags.get("USE_FUNDAMENTAL", True) else "0"
        )
        os.environ["USE_EXT_MTF_TECH"] = (
            "1" if flags.get("USE_EXT_MTF_TECH", False) else "0"
        )
    except Exception:
        pass


if __name__ == "__main__":
    cfg = suggest_production_config()
    print(json.dumps(cfg, indent=2))
