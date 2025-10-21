from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd
import numpy as np


@dataclass
class FundamentalsConfig:
    base_tf: str = "15min"
    required_series: List[str] = (
        "inflation_yoy",
        "gdp_growth_qoq",
        "unemployment_rate",
        "interest_rate",
        "m2_growth_yoy",
        "cpi_core_yoy",
        "sentiment_index",
    )


def load_fundamentals_csv(folder: str | Path) -> Dict[str, pd.Series]:
    folder = Path(folder)
    series: Dict[str, pd.Series] = {}
    for csv in folder.glob("*.csv"):
        name = csv.stem
        df = pd.read_csv(csv)
        if "date" in df.columns and "value" in df.columns:
            s = pd.Series(df["value"].values, index=pd.to_datetime(df["date"]))
            s = s.sort_index()
            series[name] = s
    return series


def build_7_fundamentals(
    base_index: pd.DatetimeIndex,
    fundas: Dict[str, pd.Series],
    cfg: FundamentalsConfig | None = None,
) -> pd.DataFrame:
    cfg = cfg or FundamentalsConfig()
    out = pd.DataFrame(index=base_index)
    for name in cfg.required_series:
        s = fundas.get(name)
        if s is None:
            # colonne vide si manquante
            out[f"fund_{name}"] = pd.Series(index=base_index, dtype=float)
        else:
            # aligner par forward-fill sur l’index 15m
            out[f"fund_{name}"] = s.reindex(base_index, method="pad")
    # Optionnel: normalisation simple
    out = out.ffill().bfill()
    return out


def compute_fundamental_confluence(
    funda_15m: pd.DataFrame,
    min_window_days: int = 60,
    max_window_days: int = 180,
) -> Dict[str, object]:
    """Calcule un score de confluence fondamentale et un biais global.

    Approche prudente et générique:
    - Aligne les 7 séries fondamentales sur une fenêtre (60..180 jours) en 15m
    - Calcule un z-score simple (valeur vs moyenne/écart-type fenêtre)
    - Agrège les signes pour obtenir un biais bull/bear/neutre
    - Calcule un score [0..1] proportionnel à l'intensité moyenne |z|

    Remarques:
    - Ne dépend pas du symbole (effet macro générique)
    - Si données insuffisantes ou constantes, retourne neutre/0.0
    """
    result = {
        "bias": "neutral",
        "score": 0.0,
        "components": {}
    }

    try:
        if funda_15m is None or len(funda_15m) == 0:
            return result

        # Fenêtre en points (15 min): approx Jours * 24*4
        def _to_points(days: int) -> int:
            return max(int(days * 24 * 4), 1)
        win_min = _to_points(min_window_days)
        win_max = _to_points(max_window_days)

        df = funda_15m.copy()
        cols = [c for c in df.columns if c.startswith("fund_")]
        if not cols:
            return result

    # Index courant non utilisé pour l'instant; focus sur fenêtre récente

        # Choisir une fenêtre disponible (pref win_max, fallback win_min)
        window_points = min(len(df), win_max)
        if window_points < win_min:
            # Données insuffisantes
            return result

        window = df.iloc[-window_points:]

        component_scores = {}
        z_values = []
        sign_votes = []

        for c in cols:
            series = window[c].astype(float)
            if series.isna().all():
                continue

            mu = series.mean()
            sigma = float(series.std(ddof=0))
            val = float(series.iloc[-1])

            if not np.isfinite(sigma) or sigma == 0:
                # Série quasi-constante
                z = 0.0
            else:
                z = (val - mu) / sigma

            # Z borné pour robustesse
            z = float(np.clip(z, -3.0, 3.0))
            z_values.append(z)
            component_scores[c] = z
            sign_votes.append(np.sign(z))

        if not z_values:
            return result

        # Intensité moyenne
        avg_intensity = float(np.mean(np.abs(z_values)))  # 0..~3
        # Normaliser vers [0..1]
        score = float(np.clip(avg_intensity / 3.0, 0.0, 1.0))

        # Biais par vote de signe
        vote_sum = float(np.sum(sign_votes))
        if vote_sum > 1:
            bias = "bull"
        elif vote_sum < -1:
            bias = "bear"
        else:
            bias = "neutral"

        result["bias"] = bias
        result["score"] = score
        result["components"] = component_scores
        return result

    except Exception:
        # En cas d'erreur, rester neutre
        return result
