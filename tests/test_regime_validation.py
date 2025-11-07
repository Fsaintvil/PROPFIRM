import pandas as pd
from scripts.market_regime_detection import MarketRegimeDetector



def test_validate_regime_input_detects_extreme_returns():
    # Build a small DataFrame with a huge jump to simulate corrupted ingestion
    df = pd.DataFrame({
        "close": [100.0, 101.0, 100000.0, 100001.0]
    })
    detector = MarketRegimeDetector(n_regimes=3)
    features = detector.extract_regime_features(df)

    ok, report = detector._validate_regime_input(df, features)
    assert ok is False
    assert "reason" in report or "exception" in report
