import numpy as np
import pandas as pd

import scripts.market_regime_detection as mrd


def _force_kmeans():
    try:
        mrd.HMM_AVAILABLE = False
    except Exception:
        pass


def test_n_regimes_one():
    _force_kmeans()
    det = mrd.MarketRegimeDetector(n_regimes=1)
    idx = pd.date_range("2025-01-01", periods=5, freq="D")
    close = [1.0, 1.1, 1.05, 1.02, 1.03]
    df = pd.DataFrame({"close": close}, index=idx)
    res = det.detect_regimes(df)
    probs = np.asarray(res["probabilities"])
    # With one regime, probabilities must have shape (n_rows, 1) and sum to 1
    assert probs.shape == (5, 1)
    assert np.allclose(probs.sum(axis=1), np.ones(5))


def test_features_columns_presence():
    _force_kmeans()
    det = mrd.MarketRegimeDetector(n_regimes=2)
    idx = pd.date_range("2025-01-01", periods=8, freq="D")
    close = [1, 1.02, 1.01, 1.03, 1.04, 1.02, 1.01, 1.05]
    df = pd.DataFrame({"close": close}, index=idx)
    res = det.detect_regimes(df)
    features = res["features"]
    expected_cols = [
        "returns",
        "volatility",
        "sma_5",
        "sma_20",
        "sma_50",
        "regime_momentum",
    ]
    for c in expected_cols:
        assert c in features.columns
