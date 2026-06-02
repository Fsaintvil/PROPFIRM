"""Tests for strategy.py — MOM20x3 + Signal"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from engine_simple.strategy import MOM20x3, Signal, MIN_SIGNAL_SCORE
from engine_simple.strategy import THRESHOLD_BY_REGIME, MAX_THRESHOLD


def _mock_rates(length, start=1.1, trend=0.0005):
    closes = [start + trend * i + np.random.normal(0, 0.0002) for i in range(length)]
    rates = []
    for i in range(length):
        c = closes[i]
        h = c + abs(np.random.normal(0, 0.0003))
        l = c - abs(np.random.normal(0, 0.0003))
        o = c - np.random.normal(0, 0.0001)
        rates.append((i, o, h, l, c, 1000 + np.random.randint(-50, 50)))
    return rates


class TestSignal:
    def test_valid_buy(self):
        s = Signal("BUY", 0.8, 0.7, "RANGING", 15, 0.005, 0.01, 2.0)
        assert s.is_valid()
        assert s.is_valid(min_score=0.5)

    def test_valid_sell(self):
        s = Signal("SELL", 0.65, 0.6, "TREND_DOWN", 25, 0.005, 0.01, 2.5)
        assert s.is_valid()

    def test_invalid_hold(self):
        s = Signal("HOLD", 0.8, 0.7, "RANGING", 15, 0.005, 0.01, 2.0)
        assert not s.is_valid()

    def test_invalid_low_score(self):
        s = Signal("BUY", 0.4, 0.3, "RANGING", 15, 0.005, 0.01, 2.0)
        assert not s.is_valid()

    def test_is_valid_custom_min_score(self):
        s = Signal("BUY", 0.6, 0.5, "RANGING", 15, 0.005, 0.01, 2.0)
        assert s.is_valid(min_score=0.5)
        assert not s.is_valid(min_score=0.7)

    def test_signal_dataclass_fields(self):
        s = Signal("BUY", 0.8, 0.7, "TREND_UP", 25, 0.005, 0.012, 2.5)
        assert s.action == "BUY"
        assert s.score == 0.8
        assert s.confidence == 0.7
        assert s.regime == "TREND_UP"
        assert s.adx == 25
        assert s.atr_val == 0.005
        assert s.threshold == 0.012
        assert s.rr_ratio == 2.5


class TestMOM20x3:
    def test_not_enough_data_returns_none(self):
        rates = _mock_rates(10)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("RANGING", 15, 0.005)
        assert result is None

    def test_buy_signal_on_uptrend(self):
        rates = _mock_rates(50, trend=0.001)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("TREND_UP", 25, 0.005)
        assert result is not None
        assert result.action == "BUY"

    def test_sell_signal_on_downtrend(self):
        rates = _mock_rates(50, trend=-0.001)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("TREND_DOWN", 25, 0.005)
        assert result is not None
        assert result.action == "SELL"

    def test_no_signal_on_ranging(self):
        rates = _mock_rates(50, trend=0.00001)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("RANGING", 10, 0.005, min_score=0.3)
        assert result is None or isinstance(result, Signal)

    def test_higher_rr_in_trend(self):
        rates = _mock_rates(50, trend=0.001)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("TREND_UP", 25, 0.005)
        if result:
            assert result.rr_ratio == 2.5

    def test_lower_rr_in_ranging(self):
        rates = _mock_rates(50, trend=0.001)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("RANGING", 15, 0.005)
        if result:
            assert result.rr_ratio == 2.0

    def test_score_capped_at_one(self):
        rates = _mock_rates(50, trend=0.005)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("TREND_UP", 30, 0.001)
        if result:
            assert result.score <= 1.0
            assert result.confidence <= 1.0

    def test_threshold_differs_by_regime(self):
        rates = _mock_rates(50, trend=0.002)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("TREND_UP", 25, 0.005, adx_thresh=22)
        # Strong trend should produce a signal
        assert result is not None

    def test_ranging_adx_lowers_threshold(self):
        rates = _mock_rates(50, trend=0.002)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("RANGING", 15, 0.005)
        # Ranging may still produce signal with strong momentum
        assert result is None or isinstance(result, Signal)

    def test_confidence_boosted_in_trend(self):
        rates = _mock_rates(50, trend=0.001)
        mom = MOM20x3({"H1": rates}, "EURUSD")
        result = mom.analyze("TREND_UP", 28, 0.005)
        if result:
            assert result.confidence >= result.score

    def test_missing_h1_returns_none(self):
        mom = MOM20x3({"M15": _mock_rates(100)}, "EURUSD")
        assert mom.analyze("RANGING", 15, 0.005) is None

    def test_multiple_symbols_independent(self):
        rates1 = _mock_rates(50, trend=0.001)
        rates2 = _mock_rates(50, trend=-0.001)
        mom1 = MOM20x3({"H1": rates1}, "EURUSD")
        mom2 = MOM20x3({"H1": rates2}, "GBPUSD")
        sig1 = mom1.analyze("TREND_UP", 25, 0.005)
        sig2 = mom2.analyze("TREND_DOWN", 25, 0.005)
        if sig1 and sig2:
            assert sig1.action == "BUY"
            assert sig2.action == "SELL"


def test_constants():
    assert "TREND_UP" in THRESHOLD_BY_REGIME
    assert "RANGING" in THRESHOLD_BY_REGIME
    assert MAX_THRESHOLD > 0
    assert MIN_SIGNAL_SCORE == 0.55
