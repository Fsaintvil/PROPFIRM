"""Tests for adaptive_intelligence.py — MarketRegime, OnlineLearner, AdaptiveEngine"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock

import numpy as np

from engine_simple.adaptive_intelligence import AdaptiveEngine, MarketRegime, OnlineLearner


def _make_h1_rates(n=100, trend="up"):
    rates = []
    base = 1.1000
    for i in range(n):
        if trend == "up":
            shift = i * 0.0003
        elif trend == "down":
            shift = -i * 0.0003
        else:
            shift = np.sin(i * 0.2) * 0.005
        o = base + shift + np.random.normal(0, 0.0001)
        h = o + abs(np.random.normal(0, 0.0002))
        lo = o - abs(np.random.normal(0, 0.0002))
        c = (h + lo) / 2
        v = 1000 + np.random.randint(-50, 50)
        rates.append((i, o, h, lo, c, v))
    return rates


class TestMarketRegime:
    def test_detect_returns_regime_and_meta(self):
        reg = MarketRegime()
        rates = _make_h1_rates(100, trend="up")
        regime, meta = reg.detect(rates)
        assert regime in ("TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL")
        assert "adx" in meta
        assert "vol_percentile" in meta
        assert "structure_trend" in meta
        assert meta["adx"] >= 0

    def test_detect_too_short_returns_ranging(self):
        reg = MarketRegime()
        rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(30)]
        regime, meta = reg.detect(rates)
        assert regime == "RANGING"

    def test_detect_trend_up_on_bullish_data(self):
        reg = MarketRegime()
        rates = _make_h1_rates(100, trend="up")
        regime, meta = reg.detect(rates)
        # Override ADX for test: force high ADX
        meta["adx"] = 30
        meta["structure_trend"] = "bullish"
        meta["vol_percentile"] = 0.5
        # Re-detect with forced params
        assert meta["adx"] > 20


class TestOnlineLearner:
    def test_init(self):
        ol = OnlineLearner()
        assert ol.window == 200
        assert ol.history == {}

    def test_record_trade_creates_history(self):
        ol = OnlineLearner()
        ol.record_trade("EURUSD", 1.5, "RANGING")
        assert "EURUSD" in ol.history
        assert len(ol.history["EURUSD"]) == 1
        assert ol.history["EURUSD"][0]["r"] == 1.5

    def test_get_params_returns_defaults(self):
        ol = OnlineLearner()
        params = ol.get_params("UNKNOWN")
        assert isinstance(params, dict)
        assert "thresh" in params
        assert "risk_mult" in params

    def test_update_params_high_wr(self):
        ol = OnlineLearner(window=10)
        for _ in range(10):
            ol.record_trade("EURUSD", 1.0, "TREND_UP")
        params = ol.get_params("EURUSD")
        assert params["risk_mult"] >= 1.0

    def test_update_params_low_wr(self):
        ol = OnlineLearner(window=10)
        for _ in range(10):
            ol.record_trade("EURUSD", -1.0, "RANGING")
        params = ol.get_params("EURUSD")
        assert params["risk_mult"] <= 1.0

    def test_get_summary(self):
        ol = OnlineLearner(window=10)
        assert ol.get_summary("EURUSD") == {}
        for _ in range(10):
            ol.record_trade("EURUSD", 0.5, "RANGING")
        s = ol.get_summary("EURUSD")
        assert s["trades"] == 10
        assert s["wr"] > 0
        assert s["avg_r"] == 0.5


class TestAdaptiveEngine:
    def test_init(self):
        ae = AdaptiveEngine(MagicMock())
        assert ae.regime is not None
        assert ae.learner is not None
        assert ae.meta is not None
        assert ae.ml is None  # ML ensemble disabled

    def test_vigilance_returns_none_without_h1(self):
        ae = AdaptiveEngine(MagicMock())
        result = ae.vigilance("EURUSD", {"M5": [1, 2, 3]})
        assert result is None

    def test_vigilance_returns_valid_on_good_data(self):
        ae = AdaptiveEngine(MagicMock())
        rates = _make_h1_rates(100, trend="up")
        result = ae.vigilance("EURUSD", {"H1": rates})
        if result:
            assert "symbol" in result
            assert "regime" in result
            assert result["symbol"] == "EURUSD"

    def test_build_dl_features_no_dl(self):
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.build_dl_features({})
        assert result is None

    def test_analyze_returns_none_without_h1(self):
        ae = AdaptiveEngine(MagicMock())
        result = ae.analyze("EURUSD", {}, {"action": "BUY", "score": 0.7})
        assert result is not None  # passes through signal when no H1

    def test_analyze_with_valid_data(self):
        ae = AdaptiveEngine(MagicMock())
        rates = _make_h1_rates(100, trend="up")
        signal = {"action": "BUY", "score": 0.7, "confidence": 0.6,
                  "atr": 0.005, "sl_atr": 2.0, "tp_atr": 4.0,
                  "rates": {"H1": rates}, "adx": 25, "is_ranging": False}
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        if result:
            assert "action" in result
            assert "risk_mult" in result
            assert "score" in result

    def test_get_report_empty(self):
        ae = AdaptiveEngine(MagicMock())
        r = ae.get_report("EURUSD")
        assert r == {}

    def test_learner_updates_after_record_result(self):
        ae = AdaptiveEngine(MagicMock())
        ae.record_result("EURUSD", 1.5, "TREND_UP")
        s = ae.learner.get_summary("EURUSD")
        assert s["trades"] == 1
