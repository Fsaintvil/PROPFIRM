"""Tests for mtf_confirm.py — MultiTimeframeConfirmer"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from engine_simple.mtf_confirm import (
    MultiTimeframeConfirmer,
    confirm,
    confirm_multi,
)


@pytest.fixture
def confirmer():
    return MultiTimeframeConfirmer()


@pytest.fixture
def confirmer_custom():
    return MultiTimeframeConfirmer(ema_fast=10, ema_slow=30, confirmation_threshold=0.7)


def _make_df(n, trend=0, vol=0.0002, base=1.1, seed=42):
    """Génère un DataFrame synthétique avec close/high/low."""
    rng = np.random.RandomState(seed)
    closes = np.array([base + trend * i + rng.normal(0, vol) for i in range(n)], dtype=float)
    highs = np.array([c + abs(rng.normal(0, vol * 2)) for c in closes], dtype=float)
    lows = np.array([c - abs(rng.normal(0, vol * 2)) for c in closes], dtype=float)
    return pd.DataFrame({"close": closes, "high": highs, "low": lows})


def _make_rising_df(n, base=100.0, step=0.5):
    """Crée un DataFrame avec prix en hausse continue."""
    closes = np.array([base + i * step for i in range(n)], dtype=float)
    highs = closes + 1.0
    lows = closes - 1.0
    return pd.DataFrame({"close": closes, "high": highs, "low": lows})


def _make_falling_df(n, base=110.0, step=0.5):
    """Crée un DataFrame avec prix en baisse continue."""
    closes = np.array([base - i * step for i in range(n)], dtype=float)
    highs = closes + 1.0
    lows = closes - 1.0
    return pd.DataFrame({"close": closes, "high": highs, "low": lows})


def _make_flat_df(n, base=100.0):
    """Crée un DataFrame avec tous les prix identiques (ATR=0)."""
    closes = np.full(n, base, dtype=float)
    highs = np.full(n, base, dtype=float)
    lows = np.full(n, base, dtype=float)
    return pd.DataFrame({"close": closes, "high": highs, "low": lows})


def _make_bullish_adx_df(n, base=100.0):
    """Crée un DataFrame où +DI domine (highs montent, lows flat+)."""
    closes = np.array([base + i * 0.3 for i in range(n)], dtype=float)
    highs = np.array([base + 1.0 + i * 0.5 for i in range(n)], dtype=float)
    lows = np.array([base - 1.0 + i * 0.2 for i in range(n)], dtype=float)
    return pd.DataFrame({"close": closes, "high": highs, "low": lows})


def _make_bearish_adx_df(n, base=100.0):
    """Crée un DataFrame où -DI domine (lows descendent, highs flat-)."""
    closes = np.array([base - i * 0.3 for i in range(n)], dtype=float)
    highs = np.array([base + 1.0 - i * 0.2 for i in range(n)], dtype=float)
    lows = np.array([base - 1.0 - i * 0.5 for i in range(n)], dtype=float)
    return pd.DataFrame({"close": closes, "high": highs, "low": lows})


class TestMultiTimeframeConfirmerInit:
    def test_default_params(self, confirmer):
        assert confirmer.ema_fast == 20
        assert confirmer.ema_slow == 50
        assert confirmer.confirmation_threshold == 0.6

    def test_custom_params(self, confirmer_custom):
        assert confirmer_custom.ema_fast == 10
        assert confirmer_custom.ema_slow == 30
        assert confirmer_custom.confirmation_threshold == 0.7


class TestEma:
    def test_ema_rising_prices(self, confirmer):
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
        result = confirmer._ema(data, 3)
        assert len(result) == 5
        assert result[0] == 1.0
        assert result[-1] > result[0]

    def test_ema_constant_prices(self, confirmer):
        data = np.array([5.0, 5.0, 5.0, 5.0, 5.0], dtype=float)
        result = confirmer._ema(data, 3)
        assert np.allclose(result, 5.0)

    def test_ema_single_element(self, confirmer):
        data = np.array([42.0], dtype=float)
        result = confirmer._ema(data, 3)
        assert result[0] == 42.0


class TestGetTrend:
    def test_none_returns_neutral(self, confirmer):
        assert confirmer._get_trend(None) == "NEUTRAL"

    def test_short_data_returns_neutral(self, confirmer):
        df = _make_df(30)
        assert confirmer._get_trend(df) == "NEUTRAL"

    def test_bullish_trend(self, confirmer):
        df = _make_rising_df(100)
        assert confirmer._get_trend(df) == "BULLISH"

    def test_bearish_trend(self, confirmer):
        df = _make_falling_df(100)
        assert confirmer._get_trend(df) == "BEARISH"

    def test_edge_ema_equal(self, confirmer):
        """Quand ema_fast == ema_slow exactement → NEUTRAL."""
        data = np.full(100, 10.0, dtype=float)
        df = pd.DataFrame({"close": data, "high": data + 1, "low": data - 1})
        trend = confirmer._get_trend(df)
        assert trend in ("NEUTRAL", "BULLISH", "BEARISH")


class TestGetAdxDirection:
    def test_none_returns_neutral(self, confirmer):
        assert confirmer._get_adx_direction(None) == "NEUTRAL"

    def test_short_data_returns_neutral(self, confirmer):
        df = _make_df(20)
        assert confirmer._get_adx_direction(df) == "NEUTRAL"

    def test_bullish_adx(self, confirmer):
        df = _make_bullish_adx_df(60)
        assert confirmer._get_adx_direction(df) == "BULLISH"

    def test_bearish_adx(self, confirmer):
        df = _make_bearish_adx_df(60)
        assert confirmer._get_adx_direction(df) == "BEARISH"

    def test_flat_prices_atr_zero_returns_neutral(self, confirmer):
        df = _make_flat_df(60)
        assert confirmer._get_adx_direction(df) == "NEUTRAL"

    def test_exactly_30_bars(self, confirmer):
        """30 bars est le minimum, doit passer le check len>=30."""
        df = _make_bullish_adx_df(30)
        result = confirmer._get_adx_direction(df)
        assert result in ("BULLISH", "BEARISH", "NEUTRAL")

    def test_29_bars_returns_neutral(self, confirmer):
        df = _make_bullish_adx_df(29)
        assert confirmer._get_adx_direction(df) == "NEUTRAL"


class TestConfirm:
    def test_none_higher_returns_passthrough(self, confirmer):
        df_signal = _make_rising_df(60)
        confirmed, factor = confirmer.confirm(df_signal, None, "BUY")
        assert confirmed is True
        assert factor == 1.0

    def test_short_higher_returns_passthrough(self, confirmer):
        df_signal = _make_rising_df(60)
        df_short = _make_df(30)
        confirmed, factor = confirmer.confirm(df_signal, df_short, "BUY")
        assert confirmed is True
        assert factor == 1.0

    def test_buy_aligned_bullish(self, confirmer):
        df_signal = _make_rising_df(60)
        df_higher = _make_rising_df(100)
        confirmed, factor = confirmer.confirm(df_signal, df_higher, "BUY")
        assert confirmed is True
        assert factor == 1.1

    def test_buy_opposite_bearish(self, confirmer):
        df_signal = _make_rising_df(60)
        df_higher = _make_bearish_adx_df(100)
        confirmed, factor = confirmer.confirm(df_signal, df_higher, "BUY")
        assert confirmed is False
        assert factor == 0.7

    def test_buy_neutral(self, confirmer):
        df_signal = _make_rising_df(60)
        df_higher = _make_df(100)
        with (
            patch.object(confirmer, "_get_trend", return_value="BEARISH"),
            patch.object(confirmer, "_get_adx_direction", return_value="BULLISH"),
        ):
            confirmed, factor = confirmer.confirm(df_signal, df_higher, "BUY")
        assert confirmed is True
        assert factor == 1.0

    def test_sell_aligned_bearish(self, confirmer):
        df_signal = _make_falling_df(60)
        df_higher = _make_falling_df(100)
        confirmed, factor = confirmer.confirm(df_signal, df_higher, "SELL")
        assert confirmed is True
        assert factor == 1.1

    def test_sell_opposite_bullish(self, confirmer):
        df_signal = _make_falling_df(60)
        df_higher = _make_bullish_adx_df(100)
        confirmed, factor = confirmer.confirm(df_signal, df_higher, "SELL")
        assert confirmed is False
        assert factor == 0.7

    def test_sell_neutral(self, confirmer):
        df_signal = _make_falling_df(60)
        df_higher = _make_df(100)
        with (
            patch.object(confirmer, "_get_trend", return_value="BULLISH"),
            patch.object(confirmer, "_get_adx_direction", return_value="BEARISH"),
        ):
            confirmed, factor = confirmer.confirm(df_signal, df_higher, "SELL")
        assert confirmed is True
        assert factor == 1.0


class TestConfirmMulti:
    def test_empty_list_returns_passthrough(self, confirmer):
        df_signal = _make_rising_df(60)
        confirmed, factor, details = confirmer.confirm_multi(df_signal, [], "BUY")
        assert confirmed is True
        assert factor == 1.0
        assert details == {}

    def test_single_tf(self, confirmer):
        df_signal = _make_rising_df(60)
        df_h1 = _make_rising_df(100)
        confirmed, factor, details = confirmer.confirm_multi(df_signal, [df_h1], "BUY")
        assert len(details) == 1
        assert "TF_1" in details
        assert details["TF_1"]["confirmed"] is True

    def test_multiple_tf_all_aligned(self, confirmer):
        df_signal = _make_rising_df(60)
        df_h4 = _make_rising_df(100)
        df_d1 = _make_rising_df(200)
        confirmed, factor, details = confirmer.confirm_multi(df_signal, [df_h4, df_d1], "BUY")
        assert len(details) == 2
        assert factor == pytest.approx(1.1 * 1.1)
        assert confirmed is True

    def test_multiple_tf_with_opposite(self, confirmer):
        df_signal = _make_rising_df(60)
        df_aligned = _make_rising_df(100)
        df_opposite = _make_bearish_adx_df(100)
        confirmed, factor, details = confirmer.confirm_multi(df_signal, [df_aligned, df_opposite], "BUY")
        assert len(details) == 2
        assert factor == pytest.approx(1.1 * 0.7)
        assert confirmed is True  # 0.77 >= 0.6

    def test_below_threshold(self, confirmer_custom):
        """Avec seuil 0.7 et facteur 0.49 < 0.7 → non confirmé."""
        df_signal = _make_rising_df(60)
        df_opposite = _make_bearish_adx_df(100)
        confirmed, factor, details = confirmer_custom.confirm_multi(df_signal, [df_opposite, df_opposite], "BUY")
        assert factor == pytest.approx(0.49)
        assert confirmed is False

    def test_details_contain_trend_info(self, confirmer):
        df_signal = _make_rising_df(60)
        df_higher = _make_rising_df(100)
        _, _, details = confirmer.confirm_multi(df_signal, [df_higher], "BUY")
        assert "trend" in details["TF_1"]
        assert "factor" in details["TF_1"]
        assert "confirmed" in details["TF_1"]


class TestConvenienceFunctions:
    def test_confirm_convenience(self):
        df_signal = _make_rising_df(60)
        df_higher = _make_rising_df(100)
        confirmed, factor = confirm(df_signal, df_higher, "BUY")
        assert confirmed is True
        assert factor == 1.1

    def test_confirm_multi_convenience(self):
        df_signal = _make_rising_df(60)
        df_h4 = _make_rising_df(100)
        df_d1 = _make_rising_df(200)
        confirmed, factor, details = confirm_multi(df_signal, [df_h4, df_d1], "BUY")
        assert confirmed is True
        assert factor == pytest.approx(1.21)
        assert len(details) == 2

    def test_confirm_convenience_opposite(self):
        df_signal = _make_rising_df(60)
        df_opposite = _make_bearish_adx_df(100)
        confirmed, factor = confirm(df_signal, df_opposite, "BUY")
        assert confirmed is False
        assert factor == 0.7
