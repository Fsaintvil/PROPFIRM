import pandas as pd
import numpy as np

from scripts.signal_mtf import generate_signals
from scripts.realtime_backtester import SimpleRealtimeBacktester


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


def test_generate_signals_and_backtest():
    df = make_sample_df(120)
    sig = generate_signals(df, higher_tf="15min")
    assert "signal" in sig.columns
    br = SimpleRealtimeBacktester()
    _ = list(br.run_stream(df.iloc[i] for i in range(len(df))))
    # backtester should have final cash attribute
    assert hasattr(br, "cash")
