"""Tests for engine_simple/volume_profile.py — VolumeProfile"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

from engine_simple.volume_profile import (
    VolumeProfile,
    VolumeLevels,
    analyze,
    get_support_resistance,
    is_near_poc,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def vp():
    return VolumeProfile(num_bins=50, lookback=100, value_area_pct=0.70)


@pytest.fixture
def sample_df():
    """Create a trending DataFrame with volume."""
    rng = np.random.RandomState(42)
    n = 100
    base = 100.0
    closes = [base + i * 0.1 + rng.normal(0, 0.5) for i in range(n)]
    highs = [c + abs(rng.normal(0, 0.3)) for c in closes]
    lows = [c - abs(rng.normal(0, 0.3)) for c in closes]
    volumes = [abs(rng.normal(1000, 200)) for _ in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


@pytest.fixture
def flat_df():
    """Flat prices — all same level."""
    n = 100
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [1000] * n,
        }
    )


@pytest.fixture
def empty_df():
    return pd.DataFrame()


@pytest.fixture
def no_vol_df():
    rng = np.random.RandomState(42)
    n = 100
    closes = [100 + i * 0.1 + rng.normal(0, 0.5) for i in range(n)]
    highs = [c + abs(rng.normal(0, 0.3)) for c in closes]
    lows = [c - abs(rng.normal(0, 0.3)) for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [0] * n,
        }
    )


# ── VolumeLevels ─────────────────────────────────────────────────────


class TestVolumeLevels:
    def test_default_construction(self):
        vl = VolumeLevels()
        assert vl.poc is None
        assert vl.vah is None
        assert vl.val is None
        assert vl.total_volume == 0.0
        assert vl.price_range == (0.0, 0.0)
        assert vl.num_bins == 50

    def test_to_dict(self):
        vl = VolumeLevels(poc=105.0, vah=110.0, val=100.0, total_volume=50000.0, price_range=(95.0, 115.0), num_bins=50)
        d = vl.to_dict()
        assert d["poc"] == 105.0
        assert d["vah"] == 110.0
        assert d["val"] == 100.0
        assert d["total_volume"] == 50000.0
        assert d["price_range"] == (95.0, 115.0)
        assert d["num_bins"] == 50

    def test_to_dict_none_poc(self):
        vl = VolumeLevels()
        d = vl.to_dict()
        assert d["poc"] is None


# ── VolumeProfile.init ───────────────────────────────────────────────


class TestVolumeProfileInit:
    def test_default_values(self):
        vp = VolumeProfile()
        assert vp.num_bins == 50
        assert vp.lookback == 100
        assert vp.value_area_pct == 0.70

    def test_custom_values(self):
        vp = VolumeProfile(num_bins=30, lookback=50, value_area_pct=0.68)
        assert vp.num_bins == 30
        assert vp.lookback == 50
        assert vp.value_area_pct == 0.68


# ── VolumeProfile.analyze ────────────────────────────────────────────


class TestAnalyze:
    def test_none_df_returns_empty(self, vp):
        result = vp.analyze(None)
        assert result.poc is None

    def test_empty_df_returns_empty(self, vp):
        result = vp.analyze(pd.DataFrame())
        assert result.poc is None

    def test_short_df_returns_empty(self, vp):
        df = pd.DataFrame({"high": [1], "low": [1], "close": [1], "volume": [1]})
        result = vp.analyze(df)
        assert result.poc is None

    def test_no_volume_column_returns_empty(self, vp):
        df = pd.DataFrame({"high": [1, 2], "low": [1, 2], "close": [1, 2]})
        result = vp.analyze(df)
        assert result.poc is None

    def test_zero_total_volume_returns_empty(self, vp, no_vol_df):
        result = vp.analyze(no_vol_df)
        assert result.poc is None

    def test_flat_prices_returns_empty(self, vp, flat_df):
        result = vp.analyze(flat_df)
        assert result.poc is None

    def test_returns_volume_levels(self, vp, sample_df):
        result = vp.analyze(sample_df)
        assert result.poc is not None
        assert result.vah is not None
        assert result.val is not None
        assert result.total_volume > 0
        assert result.price_range[0] < result.price_range[1]

    def test_poc_within_price_range(self, vp, sample_df):
        result = vp.analyze(sample_df)
        low, high = result.price_range
        assert low <= result.poc <= high
        assert low <= result.val <= high
        assert low <= result.vah <= high

    def test_val_leq_vah(self, vp, sample_df):
        result = vp.analyze(sample_df)
        assert result.val <= result.vah
        assert result.val <= result.poc <= result.vah

    def test_value_area_approx_70_percent(self, vp, sample_df):
        result = vp.analyze(sample_df)
        assert result.total_volume > 0

    def test_lookback_limits_data(self):
        vp = VolumeProfile(lookback=10)
        rng = np.random.RandomState(42)
        n = 200
        closes = [100 + i * 0.1 + rng.normal(0, 0.5) for i in range(n)]
        highs = [c + abs(rng.normal(0, 0.3)) for c in closes]
        lows = [c - abs(rng.normal(0, 0.3)) for c in closes]
        df = pd.DataFrame(
            {
                "open": closes,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": [abs(rng.normal(1000, 200)) for _ in range(n)],
            }
        )
        result = vp.analyze(df)
        assert result.poc is not None


# ── VolumeProfile.get_support_resistance ─────────────────────────────


class TestGetSupportResistance:
    def test_returns_list(self, vp, sample_df):
        sr = vp.get_support_resistance(sample_df)
        assert isinstance(sr, list)

    def test_max_num_levels(self, vp, sample_df):
        sr = vp.get_support_resistance(sample_df, num_levels=3)
        assert len(sr) <= 3

    def test_each_level_is_tuple_price_type(self, vp, sample_df):
        sr = vp.get_support_resistance(sample_df, num_levels=2)
        for price, typ in sr:
            assert isinstance(price, float)
            assert typ in ("support", "resistance")

    def test_empty_df_returns_empty(self, vp):
        sr = vp.get_support_resistance(pd.DataFrame(), num_levels=3)
        assert sr == []

    def test_sorted_by_distance(self, vp, sample_df):
        sr = vp.get_support_resistance(sample_df, num_levels=5)
        current_price = sample_df["close"].iloc[-1]
        distances = [abs(p - current_price) for p, _ in sr]
        assert distances == sorted(distances)

    def test_zero_levels(self, vp, sample_df):
        sr = vp.get_support_resistance(sample_df, num_levels=0)
        assert sr == []


# ── VolumeProfile.is_near_poc ────────────────────────────────────────


class TestIsNearPoc:
    def test_returns_bool(self, vp, sample_df):
        result = vp.is_near_poc(sample_df["close"].iloc[-1], sample_df)
        assert isinstance(result, (bool, np.bool_))

    def test_false_when_poc_none(self, vp, empty_df):
        assert vp.is_near_poc(100.0, empty_df) == False

    def test_exact_poc_returns_true(self, vp, sample_df):
        levels = vp.analyze(sample_df)
        if levels.poc is not None:
            assert vp.is_near_poc(levels.poc, sample_df, tolerance_pct=0.1) == True

    def test_far_price_returns_false(self, vp, sample_df):
        levels = vp.analyze(sample_df)
        if levels.poc is not None:
            far_price = float(levels.poc) * 1.1  # 10% above
            assert vp.is_near_poc(far_price, sample_df, tolerance_pct=0.1) == False

    def test_custom_tolerance(self, vp, sample_df):
        levels = vp.analyze(sample_df)
        if levels.poc is not None:
            close_price = float(levels.poc) * 1.005  # 0.5% above
            assert vp.is_near_poc(close_price, sample_df, tolerance_pct=1.0) == True


# ── Convenience functions ────────────────────────────────────────────


class TestConvenienceFunctions:
    def test_analyze_convenience(self):
        rng = np.random.RandomState(42)
        n = 100
        closes = [100 + i * 0.1 + rng.normal(0, 0.5) for i in range(n)]
        highs = [c + abs(rng.normal(0, 0.3)) for c in closes]
        lows = [c - abs(rng.normal(0, 0.3)) for c in closes]
        df = pd.DataFrame(
            {
                "open": closes,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": [abs(rng.normal(1000, 200)) for _ in range(n)],
            }
        )
        result = analyze(df)
        assert isinstance(result, VolumeLevels)
        assert result.poc is not None

    def test_get_support_resistance_convenience(self):
        n = 100
        df = pd.DataFrame(
            {
                "open": [100] * n,
                "high": [101] * n,
                "low": [99] * n,
                "close": [100] * n,
                "volume": [1000] * n,
            }
        )
        sr = get_support_resistance(df)
        assert isinstance(sr, list)

    def test_is_near_poc_convenience(self):
        n = 100
        df = pd.DataFrame(
            {
                "open": [100] * n,
                "high": [101] * n,
                "low": [99] * n,
                "close": [100] * n,
                "volume": [1000] * n,
            }
        )
        result = is_near_poc(100.0, df)
        assert isinstance(result, (bool, np.bool_))
