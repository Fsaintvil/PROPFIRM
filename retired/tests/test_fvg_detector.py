import numpy as np
import pytest

from engine_simple.fvg_detector import (
    detect_fvg,
    detect_liquidity_sweep,
    filter_active_fvgs,
    fvg_score,
    is_price_in_fvg,
)


@pytest.fixture
def bullish_fvg_data():
    h = [1.1000, 1.1020, 1.1015, 1.1040, 1.1030, 1.1060]
    l = [1.0990, 1.1010, 1.1005, 1.1030, 1.1020, 1.1050]
    return np.array(h, dtype=float), np.array(l, dtype=float)


@pytest.fixture
def bearish_fvg_data():
    h = [1.1080, 1.1070, 1.1090, 1.1060, 1.1075, 1.1050]
    l = [1.1070, 1.1055, 1.1080, 1.1045, 1.1060, 1.1040]
    return np.array(h, dtype=float), np.array(l, dtype=float)


class TestDetectFVG:

    def test_no_fvg_on_continuous_data(self):
        h = np.arange(1.1000, 1.1100, 0.001, dtype=float)
        l = h - 0.001
        result = detect_fvg(h, l, lookback=len(h))
        assert len(result) == 0

    def test_detects_bullish_fvg(self, bullish_fvg_data):
        h, l = bullish_fvg_data
        result = detect_fvg(h, l, lookback=6)
        assert len(result) >= 1
        bullish = [f for f in result if f["type"] == "BULL"]
        assert len(bullish) >= 1
        assert bullish[0]["bottom"] < bullish[0]["top"]

    def test_detects_bearish_fvg(self, bearish_fvg_data):
        h, l = bearish_fvg_data
        result = detect_fvg(h, l, lookback=6)
        assert len(result) >= 1
        bearish = [f for f in result if f["type"] == "BEAR"]
        assert len(bearish) >= 1

    def test_returns_empty_for_short_data(self):
        h = np.array([1.0, 1.01])
        l = np.array([0.99, 1.005])
        result = detect_fvg(h, l)
        assert result == []

    def test_age_field_increases_with_distance(self):
        h = np.array([1.0, 1.02, 1.01, 1.03, 1.015, 1.04], dtype=float)
        l = np.array([0.99, 1.01, 1.00, 1.02, 1.005, 1.03], dtype=float)
        result = detect_fvg(h, l, lookback=6)
        if result:
            for f in result:
                assert f["age"] >= 1


class TestFilterActiveFvgs:

    def test_keeps_active_bullish_fvg(self):
        fvgs = [{"type": "BULL", "top": 1.1050, "bottom": 1.1020}]
        active = filter_active_fvgs(fvgs, 1.1040, 1.1010)
        assert len(active) == 1

    def test_removes_filled_gap(self):
        fvgs = [{"type": "BULL", "top": 1.1050, "bottom": 1.1020}]
        active = filter_active_fvgs(fvgs, 1.1000, 1.0990)
        assert len(active) == 0

    def test_removes_gap_when_price_above(self):
        fvgs = [{"type": "BULL", "top": 1.1050, "bottom": 1.1020}]
        active = filter_active_fvgs(fvgs, 1.1100, 1.1080)
        assert len(active) == 0

    def test_handles_multiple_fvgs(self):
        fvgs = [
            {"type": "BULL", "top": 1.1050, "bottom": 1.1020},
            {"type": "BEAR", "top": 1.1080, "bottom": 1.1070},
        ]
        active = filter_active_fvgs(fvgs, 1.1040, 1.1010)
        assert len(active) == 1
        assert active[0]["type"] == "BULL"

    def test_returns_empty_for_empty_input(self):
        assert filter_active_fvgs([], 1.10, 1.09) == []


class TestIsPriceInFvg:

    def test_price_inside(self):
        fvg = {"bottom": 1.1020, "top": 1.1050}
        assert is_price_in_fvg(1.1035, fvg) is True

    def test_price_at_boundary(self):
        fvg = {"bottom": 1.1020, "top": 1.1050}
        assert is_price_in_fvg(1.1020, fvg) is True
        assert is_price_in_fvg(1.1050, fvg) is True

    def test_price_outside(self):
        fvg = {"bottom": 1.1020, "top": 1.1050}
        assert is_price_in_fvg(1.1010, fvg) is False
        assert is_price_in_fvg(1.1060, fvg) is False


class TestFVGScore:

    def test_bullish_aligned_buy(self):
        fvgs = [{"type": "BULL", "top": 1.10, "bottom": 1.09}]
        assert fvg_score(fvgs, "BUY") == 0.10

    def test_bearish_aligned_sell(self):
        fvgs = [{"type": "BEAR", "top": 1.10, "bottom": 1.09}]
        assert fvg_score(fvgs, "SELL") == 0.10

    def test_bullish_opposes_sell(self):
        fvgs = [{"type": "BULL", "top": 1.10, "bottom": 1.09}]
        assert fvg_score(fvgs, "SELL") == -0.15

    def test_bearish_opposes_buy(self):
        fvgs = [{"type": "BEAR", "top": 1.10, "bottom": 1.09}]
        assert fvg_score(fvgs, "BUY") == -0.15

    def test_caps_at_plus_20(self):
        fvgs = [
            {"type": "BULL", "top": 1.10, "bottom": 1.09},
            {"type": "BULL", "top": 1.12, "bottom": 1.11},
            {"type": "BULL", "top": 1.14, "bottom": 1.13},
        ]
        assert fvg_score(fvgs, "BUY") == 0.20

    def test_caps_at_minus_20(self):
        fvgs = [
            {"type": "BEAR", "top": 1.10, "bottom": 1.09},
            {"type": "BEAR", "top": 1.12, "bottom": 1.11},
            {"type": "BEAR", "top": 1.14, "bottom": 1.13},
        ]
        assert fvg_score(fvgs, "BUY") == -0.20

    def test_empty_fvgs(self):
        assert fvg_score([], "BUY") == 0.0
        assert fvg_score([], "SELL") == 0.0

    def test_mixed_fvgs(self):
        fvgs = [
            {"type": "BULL", "top": 1.10, "bottom": 1.09},
            {"type": "BEAR", "top": 1.12, "bottom": 1.11},
        ]
        score = fvg_score(fvgs, "BUY")
        assert score == -0.05


class TestDetectLiquiditySweep:

    def test_sweep_high_detected(self):
        h4h = np.array([1.10] * 15 + [1.12, 1.11, 1.10], dtype=float)
        h4l = h4h - 0.01
        h1h = np.array([1.10] * 8 + [1.13, 1.12, 1.11], dtype=float)
        h1l = h1h - 0.01
        h1c = np.array([1.10] * 8 + [1.09, 1.08, 1.07], dtype=float)
        sweep_type, level = detect_liquidity_sweep(h4h, h4l, h1h, h1l, h1c)
        assert sweep_type == "SWEEP_HIGH"
        assert level is not None

    def test_sweep_low_detected(self):
        h4h = np.array([1.12] * 15 + [1.10, 1.11, 1.12], dtype=float)
        h4l = np.array([1.11] * 15 + [1.09, 1.10, 1.11], dtype=float)
        h1h = np.array([1.12] * 8 + [1.10, 1.11, 1.12], dtype=float)
        h1l = np.array([1.11] * 8 + [1.08, 1.09, 1.10], dtype=float)
        h1c = np.array([1.12] * 8 + [1.09, 1.10, 1.11], dtype=float)
        sweep_type, level = detect_liquidity_sweep(h4h, h4l, h1h, h1l, h1c)
        assert sweep_type == "SWEEP_LOW"
        assert level is not None

    def test_no_sweep_on_trend(self):
        h4h = np.linspace(1.10, 1.15, 20, dtype=float)
        h4l = h4h - 0.01
        h1h = np.linspace(1.14, 1.16, 15, dtype=float)
        h1l = h1h - 0.01
        h1c = np.linspace(1.14, 1.16, 15, dtype=float)
        result = detect_liquidity_sweep(h4h, h4l, h1h, h1l, h1c)
        assert result == (None, None)

    def test_no_sweep_on_short_data(self):
        h4h = np.array([1.10, 1.11])
        h4l = np.array([1.09, 1.10])
        h1h = np.array([1.10, 1.11])
        h1l = np.array([1.09, 1.10])
        h1c = np.array([1.10, 1.11])
        result = detect_liquidity_sweep(h4h, h4l, h1h, h1l, h1c)
        assert result == (None, None)
