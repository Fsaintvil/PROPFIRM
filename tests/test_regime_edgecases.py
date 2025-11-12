import numpy as np
import pandas as pd

import scripts.market_regime_detection as mrd


def _force_kmeans():
    # Ensure tests do not depend on hmmlearn availability
    try:
        mrd.HMM_AVAILABLE = False
    except Exception:
        pass


def test_detect_regimes_empty_dataframe():
    _force_kmeans()
    det = mrd.MarketRegimeDetector(n_regimes=3)
    # empty dataframe with a known price column
    df = pd.DataFrame({"close": []}, index=pd.DatetimeIndex([]))
    # current implementation now returns a safe empty result
    res = det.detect_regimes(df)
    probs = res.get("probabilities")
    features = res.get("features")
    # probabilities should be an array with zero rows and n_regimes columns
    import numpy as np

    assert probs is not None
    probs = np.asarray(probs)
    assert probs.shape[0] == 0
    assert probs.shape[1] == 3
    # features should be a DataFrame with zero rows
    assert features is not None and features.shape[0] == 0
    # current regime should be None when no data
    assert res.get("current_regime") is None


def test_detect_regimes_nan_only_columns():
    _force_kmeans()
    det = mrd.MarketRegimeDetector(n_regimes=3)
    idx = pd.date_range("2025-01-01", periods=10, freq="D")
    df = pd.DataFrame({"close": [np.nan] * 10}, index=idx)
    res = det.detect_regimes(df)
    assert "features" in res and isinstance(res["features"], pd.DataFrame)
    # features may be all-NaN; ensure no exception and index length preserved
    assert res["features"].shape[0] == 10


def test_clustering_probabilities_sum_to_one(monkeypatch):
    _force_kmeans()
    # enable safe clean to trigger clipping/robust scaler in pipeline
    monkeypatch.setenv("REGIME_SAFE_CLEAN", "1")
    det = mrd.MarketRegimeDetector(n_regimes=3)
    idx = pd.date_range("2025-01-01", periods=7, freq="D")
    # introduce a large outlier spike
    close = [1, 1, 1, 1, 1_000_000.0, 1, 1]
    df = pd.DataFrame({"close": close}, index=idx)
    res = det.detect_regimes(df)
    probs = np.asarray(res["probabilities"])
    assert probs.shape[0] == 7
    # each row of probabilities should sum to ~1.0
    row_sums = probs.sum(axis=1)
    assert np.allclose(row_sums, np.ones_like(row_sums), atol=1e-6)
