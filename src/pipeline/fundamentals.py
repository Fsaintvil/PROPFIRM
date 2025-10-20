from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd


@dataclass
class FundamentalsConfig:
    base_tf: str = "15T"
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
