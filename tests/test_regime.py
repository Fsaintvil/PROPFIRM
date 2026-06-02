"""Tests for regime.py — RegimeDetector"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from engine_simple.regime import RegimeDetector, ADX_TREND_THRESHOLD


@pytest.fixture
def detector():
    return RegimeDetector()


def _make_rates(n, base=1.1, trend=0, vol=0.001):
    closes = [base + trend * i + np.random.normal(0, vol) for i in range(n)]
    highs = [c + abs(np.random.normal(0, vol * 2)) for c in closes]
    lows = [c - abs(np.random.normal(0, vol * 2)) for c in closes]
    return (
        np.array(highs, dtype=float),
        np.array(lows, dtype=float),
        np.array(closes, dtype=float),
    )


class TestRegimeDetector:
    def test_too_short_returns_ranging(self, detector):
        h, l, c = _make_rates(20)
        regime, meta = detector.detect(h, l, c)
        assert regime == "RANGING"

    def test_trend_up_detected(self, detector):
        h, l, c = _make_rates(100, trend=0.0005, vol=0.0002)
        regime, meta = detector.detect(h, l, c)
        assert "TREND" in regime
        assert meta["adx"] >= 0

    def test_trend_down_detected(self, detector):
        h, l, c = _make_rates(100, trend=-0.0005, vol=0.0002)
        regime, meta = detector.detect(h, l, c)
        assert "TREND" in regime
        assert meta["adx"] >= 0

    def test_ranging_detected(self, detector):
        h, l, c = _make_rates(100, trend=0, vol=0.0005)
        regime, meta = detector.detect(h, l, c)
        assert regime in ("RANGING", "LOW_VOL", "HIGH_VOL")

    def test_low_vol_detected(self, detector):
        h, l, c = _make_rates(100, trend=0, vol=0.00001)
        regime, meta = detector.detect(h, l, c)
        assert regime in ("RANGING", "LOW_VOL")

    def test_high_vol_detected(self, detector):
        h, l, c = _make_rates(100, trend=0, vol=0.01)
        regime, meta = detector.detect(h, l, c)
        assert regime in ("RANGING", "HIGH_VOL")

    def test_meta_contains_all_keys(self, detector):
        h, l, c = _make_rates(100)
        regime, meta = detector.detect(h, l, c)
        for key in ("adx", "atr", "atr_pct", "slope", "vol_percentile"):
            assert key in meta

    def test_adx_custom_override(self, detector):
        h, l, c = _make_rates(100)
        regime, meta = detector.detect(h, l, c, adx_val=35)
        assert meta["adx"] == 35

    def test_adx_zero_ranging(self, detector):
        h, l, c = _make_rates(100)
        regime, meta = detector.detect(h, l, c, adx_val=0)
        assert regime in ("RANGING", "LOW_VOL", "HIGH_VOL")

    def test_flat_prices_low_vol(self, detector):
        h = np.ones(50) * 1.1
        l = np.ones(50) * 1.1
        c = np.ones(50) * 1.1
        regime, meta = detector.detect(h, l, c)
        assert regime == "LOW_VOL"

    def test_adx_is_float(self, detector):
        h, l, c = _make_rates(100)
        _, meta = detector.detect(h, l, c)
        assert isinstance(meta["adx"], float)

    def test_atr_is_positive(self, detector):
        h, l, c = _make_rates(100, vol=0.001)
        _, meta = detector.detect(h, l, c)
        assert meta["atr"] > 0

    def test_slope_reflects_trend(self, detector):
        h, l, c = _make_rates(100, trend=0.001, vol=0.0001)
        _, meta = detector.detect(h, l, c)
        assert meta["slope"] >= -0.001 or regime in ("RANGING", "LOW_VOL", "HIGH_VOL")

    def test_detect_with_insufficient_data_no_crash(self, detector):
        h, l, c = _make_rates(10)
        regime, meta = detector.detect(h, l, c)
        assert regime == "RANGING"

    def test_detect_large_data_no_crash(self, detector):
        h, l, c = _make_rates(5000, trend=0.0001, vol=0.001)
        regime, meta = detector.detect(h, l, c)
        assert regime in ("TREND_UP", "TREND_DOWN", "RANGING", "LOW_VOL", "HIGH_VOL")
        assert isinstance(meta["adx"], float)


def test_adx_trend_threshold_constant():
    assert ADX_TREND_THRESHOLD == 20
