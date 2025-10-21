import pandas as pd
import numpy as np

from MT5_FTMO_IA.scripts.indicators_extra import (
    rsi,
    macd,
    atr,
    vwap,
    resample_ohlcv,
)
from MT5_FTMO_IA.scripts.realtime_backtester import (
    SimpleRealtimeBacktester,
)


def test_rsi_basic():
    data = pd.Series([1, 2, 1.5, 2.5, 2, 3, 2.5])
    out = rsi(data, period=3)
    assert isinstance(out, pd.Series)
    assert not out.isna().all()


def test_macd_basic():
    data = pd.Series(np.linspace(1, 10, 20))
    m, s, h = macd(data)
    assert len(m) == len(data)
    assert len(s) == len(data)


def test_atr_vwap_resample():
    idx = pd.date_range("2020-01-01", periods=10, freq="min")
    df = pd.DataFrame(
        {
            "open": range(10),
            "high": range(1, 11),
            "low": range(0, 10),
            "close": range(10),
            "volume": [1] * 10,
        },
        index=idx,
    )
    a = atr(df, period=3)
    assert len(a) == 10
    v = vwap(df)
    # For volume=1, VWAP equals the mean of the close series
    expected = float(df["close"].mean())
    assert abs(float(v.iloc[-1]) - expected) < 1e-12
    r = resample_ohlcv(df, "5min")
    assert "open" in r.columns


def test_realtime_backtester_run():
    idx = pd.date_range("2020-01-01", periods=30, freq="min")
    prices = pd.Series(np.sin(range(30)) + np.linspace(10, 11, 30), index=idx)
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
    br = SimpleRealtimeBacktester()
    bars = (df.iloc[i] for i in range(len(df)))
    results = list(br.run_stream(bars))
    assert isinstance(results, list)
