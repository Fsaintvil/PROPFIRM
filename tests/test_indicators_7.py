import pandas as pd
import numpy as np

from MT5_FTMO_IA.scripts import indicators_7 as ind


def make_sample(n=100):
    idx = pd.date_range("2020-01-01", periods=n, freq="min")
    close = np.linspace(1.0, 2.0, n) + np.random.normal(0, 0.001, size=n)
    high = close + 0.0005
    low = close - 0.0005
    open_ = close + np.random.normal(0, 0.0002, size=n)
    vol = np.random.randint(1, 100, size=n).astype(float)
    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        },
        index=idx,
    )
    return df


def test_basic_indicators():
    df = make_sample(200)
    s = df["close"]
    sma5 = ind.sma(s, 5)
    assert len(sma5) == len(s)
    ema10 = ind.ema(s, 10)
    assert not ema10.isna().all()
    r = ind.rsi(s)
    assert ((r >= 0) & (r <= 100)).all()
    a = ind.atr(df, 14)
    assert (a >= 0).all()
    v = ind.vwap(df, period=20)
    assert v.isna().sum() < len(v)
    g = ind.fvg_gaps(df, threshold=0.0)
    assert g.dtype == bool
    edges, vb = ind.market_profile_summary(df, buckets=5)
    assert len(edges) == 6
    assert vb.sum() == df["volume"].sum()
