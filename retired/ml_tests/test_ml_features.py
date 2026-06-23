"""Tests fonctionnels pour ml_features.py — FeatureEngine + compute_features"""
import numpy as np
import pytest

from engine_simple.ml_features import FULL_FEATURE_NAMES, FeatureEngine, compute_features


class TestFeatureNames:
    def test_full_feature_count(self):
        assert len(FULL_FEATURE_NAMES) >= 30

    def test_all_names_are_strings(self):
        assert all(isinstance(n, str) for n in FULL_FEATURE_NAMES)

    def test_no_duplicates(self):
        assert len(FULL_FEATURE_NAMES) == len(set(FULL_FEATURE_NAMES))


class TestComputeFeatures:
    @pytest.fixture
    def sample_data(self):
        n = 100
        close = np.linspace(1.0, 1.1, n) + np.random.normal(0, 0.005, n)
        high = close + np.random.uniform(0, 0.01, n)
        low = close - np.random.uniform(0, 0.01, n)
        volume = np.ones(n) * 1000
        return high, low, close, volume

    def test_returns_all_features(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        for name in FULL_FEATURE_NAMES:
            assert name in result, f"Missing feature: {name}"

    def test_no_nan_in_output(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        for name in FULL_FEATURE_NAMES:
            val = result[name]
            assert not (isinstance(val, float) and np.isnan(val)), f"NaN in {name}"

    def test_short_data_returns_defaults(self):
        close = np.array([1.0, 1.01])
        result = compute_features(
            close, close, close, np.ones(2), np.ones(2)
        )
        assert all(result[n] == 0.5 for n in FULL_FEATURE_NAMES)

    def test_returns_are_nonzero_with_trend(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        assert result["return_1"] != 0 or result["return_20"] != 0

    def test_bb_position_in_range(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        assert 0 <= result["bb_position"] <= 1

    def test_rsi_in_range(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        assert 0 <= result["rsi"] <= 100

    def test_atr_pct_non_negative(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        assert result["atr_pct"] >= 0

    def test_confluence_score_clipped(self, sample_data):
        h, lo, c, v = sample_data
        result = compute_features(h, lo, c, v, v)
        assert -1 <= result["confluence_score"] <= 1


class TestFeatureEngine:
    @pytest.fixture
    def rates(self):
        n = 100
        base = 1.1
        r = []
        for i in range(n):
            o = base + (i * 0.001) + np.random.normal(0, 0.002)
            h = o + np.random.uniform(0, 0.005)
            lo = o - np.random.uniform(0, 0.005)
            c = np.random.uniform(lo, h)
            r.append((i, o, h, lo, c, 1000, 10, 5000))
        return r

    def test_compute_features_returns_dict(self, rates):
        fe = FeatureEngine()
        result = fe.compute_features(rates)
        assert isinstance(result, dict)

    def test_compute_features_all_keys(self, rates):
        fe = FeatureEngine()
        result = fe.compute_features(rates)
        assert len(result) >= 30

    def test_short_rates_returns_defaults(self):
        fe = FeatureEngine()
        short = [(i, 1.0, 1.01, 0.99, 1.0, 100, 5, 500) for i in range(5)]
        result = fe.compute_features(short)
        assert all(v == 0.5 for v in result.values())

    def test_none_rates_safe(self):
        fe = FeatureEngine()
        result = fe.compute_features(None)
        assert isinstance(result, dict)

    def test_empty_rates_safe(self):
        fe = FeatureEngine()
        result = fe.compute_features([])
        assert isinstance(result, dict)
