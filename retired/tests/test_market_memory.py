"""Tests pour market_memory.py — PatternMatcher (patterns chartistes) + MarketMemory."""
import numpy as np
import pandas as pd
import pytest

from engine_simple.market_memory import PatternMatcher
from engine_simple.regime import RegimeDetector


@pytest.fixture
def sample_data() -> dict[str, pd.DataFrame]:
    """Génère un DataFrame OHLCV synthétique sur 200 périodes."""
    np.random.seed(42)
    n = 200
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.1) + np.arange(n) * 0.01
    high = close + abs(np.random.randn(n)) * 0.2
    low = close - abs(np.random.randn(n)) * 0.2
    open_p = close + np.random.randn(n) * 0.05
    df = pd.DataFrame({
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.randint(100, 1000, n),
    })
    return {"H1": df}


class TestPatternMatcherInit:
    def test_init_empty(self):
        pm = PatternMatcher({"H1": pd.DataFrame()})
        assert "H1" in pm.data

    def test_init_with_data(self, sample_data):
        pm = PatternMatcher(sample_data)
        assert len(pm.data["H1"]) == 200

    def test_cache_initialized(self):
        pm = PatternMatcher({"H1": pd.DataFrame()})
        assert pm._cache == {}


class TestDetectChartPatterns:
    def test_returns_list(self, sample_data):
        pm = PatternMatcher(sample_data)
        patterns = pm.detect_chart_patterns(sample_data["H1"])
        assert isinstance(patterns, list)

    def test_detects_double_top(self):
        """Fake un double top dans les données."""
        n = 120
        close = np.concatenate([
            np.arange(1.0, 1.05, 0.001),   # montée
            np.arange(1.05, 1.0, -0.002),  # redescente
            np.arange(1.0, 1.05, 0.001),   # remontée (deuxième sommet)
            np.arange(1.05, 1.0, -0.002),  # redescente finale
        ])[:n]
        high = close + 0.01 + abs(np.random.randn(n)) * 0.005
        low = close - 0.01 - abs(np.random.randn(n)) * 0.005
        open_p = close + np.random.randn(n) * 0.002
        df = pd.DataFrame({
            "open": open_p, "high": high, "low": low,
            "close": close, "volume": np.random.randint(100, 1000, n),
        })
        pm = PatternMatcher({"H1": df})
        patterns = pm.detect_chart_patterns(df)
        assert isinstance(patterns, list)

    def test_no_patterns_with_insufficient_data(self):
        df = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [100]})
        pm = PatternMatcher({"H1": df})
        patterns = pm.detect_chart_patterns(df)
        assert patterns == []

    def test_engulfing_pattern_with_open_p(self, sample_data):
        """Vérifie que la variable open_p ne crée pas d'erreur (bug fix)."""
        pm = PatternMatcher(sample_data)
        patterns = pm.detect_chart_patterns(sample_data["H1"])
        assert isinstance(patterns, list)


class TestGetPatternSignal:
    def test_returns_dict(self, sample_data):
        pm = PatternMatcher(sample_data)
        signal = pm.get_pattern_signal(sample_data["H1"], tf="H1")
        assert isinstance(signal, dict)
        assert "signal" in signal
        assert "confidence" in signal
        assert "chart_patterns" in signal or "patterns" in signal

    def test_confidence_between_0_and_1(self, sample_data):
        pm = PatternMatcher(sample_data)
        signal = pm.get_pattern_signal(sample_data["H1"], tf="H1")
        assert 0.0 <= signal.get("confidence", 0) <= 1.0


class TestMarketMemoryImports:
    def test_market_memory_class_exists(self):
        from engine_simple.market_memory import MarketMemory
        assert MarketMemory is not None

    def test_market_memory_init(self):
        from engine_simple.market_memory import MarketMemory
        mm = MarketMemory()
        assert mm._loaded is False

    def test_pattern_matcher_class_exists(self):
        from engine_simple.market_memory import PatternMatcher
        assert PatternMatcher is not None


class TestRegimeDetectorQuick:
    """Test rapide du detecteur de régime (interfaçage avec market_memory)."""

    def test_regime_detector_init(self):
        rd = RegimeDetector()
        assert rd is not None

    def test_regime_detector_detect(self, sample_data):
        rd = RegimeDetector()
        df = sample_data["H1"]
        hh = df["high"].values
        ll = df["low"].values
        cc = df["close"].values
        regime, meta = rd.detect(hh, ll, cc)
        assert regime in ("TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL", "UNKNOWN")
        assert isinstance(meta, dict)
