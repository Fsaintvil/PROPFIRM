import pandas as pd
import numpy as np

from src.pipeline.mtf_features import build_mtf_technical
from src.pipeline.fundamentals import build_7_fundamentals


def test_mtf_alignment_and_no_nans():
    # Fabrique un index 15m court
    idx = pd.date_range("2020-01-01", periods=200, freq="15min")
    df = pd.DataFrame({
        "open": np.random.rand(len(idx)) * 100 + 1000,
        "high": np.random.rand(len(idx)) * 100 + 1000,
        "low": np.random.rand(len(idx)) * 100 + 1000,
        "close": np.random.rand(len(idx)) * 100 + 1000,
        "volume": np.random.randint(1, 1000, size=len(idx)),
        "time": idx,
    }).set_index(idx)

    tech = build_mtf_technical(df)
    assert tech.index.equals(df.index)
    # autorise quelques NaN en tête, mais pas sur toute la série
    assert tech.notna().mean().mean() > 0.9


def test_fundamentals_merge():
    idx = pd.date_range("2020-01-01", periods=100, freq="15min")
    # Série mensuelle simulée
    dates = pd.date_range("2019-12-01", periods=4, freq="MS")
    fundas = {
        "inflation_yoy": pd.Series([2.0, 2.1, 2.0, 1.9], index=dates),
        "gdp_growth_qoq": pd.Series([0.5, 0.6, 0.4, 0.3], index=dates),
        "unemployment_rate": pd.Series([5.0, 5.1, 5.2, 5.1], index=dates),
        "interest_rate": pd.Series([1.0, 1.0, 1.25, 1.5], index=dates),
        "m2_growth_yoy": pd.Series([6.0, 6.5, 6.2, 6.1], index=dates),
        "cpi_core_yoy": pd.Series([1.7, 1.8, 1.8, 1.9], index=dates),
        "sentiment_index": pd.Series([50, 52, 49, 51], index=dates),
    }

    funda = build_7_fundamentals(idx, fundas)
    assert funda.index.equals(idx)
    assert funda.isna().sum().sum() == 0
