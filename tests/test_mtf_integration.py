import pandas as pd
import numpy as np

from MT5_FTMO_IA.scripts.mtf_helpers import resample_ohlcv, align_features
from MT5_FTMO_IA.scripts.indicators_mtf_integration import compute_mtf_features


def make_sample_df(n=60, freq="min"):
    idx = pd.date_range("2020-01-01", periods=n, freq=freq)
    prices = pd.Series(np.linspace(10, 11, n), index=idx)
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.1,
            "low": prices - 0.1,
            "close": prices,
            "volume": 1,
        },
        index=idx,
    )
    return df


def test_resample_and_align():
    df = make_sample_df(60)
    r = resample_ohlcv(df, "5min")
    assert not r.empty
    aligned = align_features(df, [r], method="ffill")
    assert "aux1_close" in aligned.columns


def test_compute_mtf_features():
    df = make_sample_df(120)
    merged = compute_mtf_features(df, "15min")
    # base columns
    assert "rsi" in merged.columns
    assert "macd_hist" in merged.columns
    # higher timeframe prefixed columns should exist
    assert "aux1_macd_hist" in merged.columns
