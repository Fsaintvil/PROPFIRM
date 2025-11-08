#!/usr/bin/env python3
"""Validation légère de `compute_7_tech_indicators` via `get_technical_indicators`.

Le script crée une série OHLCV synthétique courte, appelle la fonction et vérifie
que les colonnes attendues sont présentes et non vides. Retourne 0 si OK, 2 sinon.
"""

import sys
from datetime import datetime, timedelta

import pandas as pd

# Ensure project root is importable (so `from src.pipeline...` works without PYTHONPATH)
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_sample_df(n=60):
    now = datetime.utcnow()
    idx = [now - timedelta(minutes=15 * (n - i - 1)) for i in range(n)]
    idx = pd.to_datetime(idx)
    # build simple trending prices
    price = pd.Series([100.0 + 0.01 * i for i in range(n)], index=idx)
    df = pd.DataFrame(index=idx)
    df["open"] = price.shift(1).fillna(price.iloc[0])
    df["high"] = price + 0.02
    df["low"] = price - 0.02
    df["close"] = price
    df["volume"] = 100 + (pd.Series(range(n), index=idx) % 5) * 10
    return df


def main():
    try:
        from src.pipeline.indicators import get_technical_indicators
    except Exception:
        # try direct compute_7_tech_indicators
        try:
            from src.pipeline.mtf_features import (
                compute_7_tech_indicators as get_technical_indicators,
            )
        except Exception:
            print(
                "MISSING: neither indicators.get_technical_indicators nor "
                "mtf_features.compute_7_tech_indicators importable",
                file=sys.stderr,
            )
            return 2

    df = make_sample_df()
    try:
        out = get_technical_indicators(df)
    except Exception as e:
        print("ERROR when calling function:", e, file=sys.stderr)
        return 2

    # expected suffixes
    expected_suffixes = [
        "_rsi14",
        "_macd",
        "_macd_signal",
        "_macd_hist",
        "_ema20",
        "_bb_high",
        "_bb_low",
        "_atr14",
    ]
    # check at least one column with each suffix
    cols = list(out.columns)
    for s in expected_suffixes:
        if not any(c.endswith(s) for c in cols):
            print(f"MISSING_SUFFIX: {s}", file=sys.stderr)
            return 2

    # check there is at least one non-zero value
    if (out.fillna(0) != 0).any().any():
        print("OK")
        return 0
    else:
        print("ALL_ZERO_OUTPUT", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
