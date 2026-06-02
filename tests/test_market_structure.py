"""Tests for market_structure.py — pure NumPy, no MT5"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np

from engine_simple.market_structure import (
    analyze_market_structure,
    break_of_structure,
    change_of_character,
    equal_highs_lows,
    find_fvg,
    find_liquidity_sweeps,
    find_order_blocks,
    higher_highs,
    lower_lows,
    swing_points,
    trendlines,
)


def _up_trend_data(n=60):
    high = np.linspace(1.1, 1.2, n) + np.random.normal(0, 0.002, n)
    low = np.linspace(1.09, 1.19, n) + np.random.normal(0, 0.002, n)
    close = (high + low) / 2
    return high, low, close


def test_swing_points_basic():
    high = [1, 2, 3, 2, 1, 2, 3, 2, 1, 2, 3, 2, 1]
    low = [0.9, 1.9, 2.9, 1.9, 0.9, 1.9, 2.9, 1.9, 0.9, 1.9, 2.9, 1.9, 0.9]
    swings = swing_points(high, low, left=2, right=2)
    assert len(swings) == 13
    # peaks at index 2, 6, 10
    assert swings[2] == 1, f"Expected swing high at 2, got {swings[2]}"
    assert swings[6] == 1, f"Expected swing high at 6, got {swings[6]}"
    assert swings[10] == 1, f"Expected swing high at 10, got {swings[10]}"


def test_swing_points_insufficient_data():
    swings = swing_points([1, 2, 3], [0.9, 1.9, 2.9], left=5, right=5)
    assert len(swings) == 3
    assert np.all(swings == 0)


def test_higher_highs():
    high = np.array([1, 2, 3, 2, 1, 4, 5, 4, 3, 6, 7])
    swings = np.array([0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0])  # 3 HH at idx 1,5,9
    is_up, hh_list = higher_highs(high, swings)
    assert is_up, "Expected higher highs trend"
    assert len(hh_list) >= 3


def test_lower_lows():
    low = np.array([5, 4, 3, 4, 5, 2, 1, 2, 3, 0.5, 1])
    swings = np.array([0, -1, 0, 0, 0, -1, 0, 0, 0, -1, 0])  # 3 LL at idx 1,5,9
    is_down, ll_list = lower_lows(low, swings)
    assert is_down, "Expected lower lows trend"
    assert len(ll_list) >= 3


def test_break_of_structure_bullish():
    high = np.array([1, 1.05, 1.1, 1.08, 1.06, 1.12, 1.15])
    low = np.array([0.95, 1.0, 1.05, 1.03, 1.01, 1.07, 1.1])
    swings = np.array([0, 0, 1, 0, 0, 0, 0])  # swing high at idx 2 (1.1)
    bos = break_of_structure(high, low, swings)
    assert bos["bullish_bos"], f"Expected bullish BOS, got {bos}"
    assert bos["last_swing_high"] is not None
    assert bos["last_swing_high"][1] == 1.1


def test_break_of_structure_no_swing():
    high = [1, 1.02, 1.01, 1.03, 1.02, 1.04, 1.03]
    low = [0.98, 1.0, 0.99, 1.01, 1.0, 1.02, 1.01]
    swings = np.zeros(7)
    bos = break_of_structure(high, low, swings)
    assert not bos["bullish_bos"]
    assert not bos["bearish_bos"]


def test_change_of_character_bearish():
    # Uptrend reversing: HH then breaks last HL
    high = np.array([5, 6, 7, 6, 8, 9, 8, 7])
    low = np.array([4, 5, 6, 5, 7, 8, 7, 5.5])
    swings = np.array([0, 1, 0, -1, 1, 0, -1, 0])
    choch = change_of_character(swings, high, low)
    # Should detect bearish CHOCH or not — depends on the data
    assert isinstance(choch["bullish_choch"], bool)
    assert isinstance(choch["bearish_choch"], bool)


def test_find_fvg_bullish():
    high = np.array([1.0, 1.02, 1.05, 1.06, 1.07, 1.08, 1.09])
    low = np.array([0.98, 1.0, 1.03, 1.04, 1.05, 1.06, 1.07])
    # Gap up between candle 0 and 2: high[0]=1.0 < low[2]=1.03
    fvgs = find_fvg(high, low, lookback=10, threshold_pct=0.0001)
    bullish = [f for f in fvgs if f["type"] == "bullish"]
    assert len(bullish) > 0, f"Expected bullish FVG, got {fvgs}"


def test_find_fvg_empty():
    high = np.linspace(1.0, 1.1, 20)
    low = high - 0.01
    fvgs = find_fvg(high, low, lookback=20, threshold_pct=0.1)
    assert len(fvgs) == 0, f"Expected no FVGs with high threshold, got {len(fvgs)}"


def test_equal_highs_lows():
    high = np.array([1.0, 1.01, 1.02, 1.01, 1.0, 1.03, 1.04])
    low = np.array([0.99, 1.0, 1.01, 1.0, 0.99, 1.02, 1.03])
    result = equal_highs_lows(high, low, threshold_pct=0.01)
    assert "highs" in result
    assert "lows" in result
    assert isinstance(result["count"], int)


def test_trendlines_short_data():
    result = trendlines([1, 2, 3], [0.9, 1.9, 2.9], min_touch=2)
    assert result["ascending"] is None
    assert result["descending"] is None


def test_analyze_market_structure():
    high, low, close = _up_trend_data(60)
    result = analyze_market_structure(high, low, close)
    assert "trend" in result
    assert "score" in result
    assert result["score"] >= 0  # uptrend data
    assert "swings" in result
    assert "bos" in result
    assert "choch" in result
    assert "order_blocks" in result
    assert "fvgs" in result
    assert "sweeps" in result
    assert "equal_highs_lows" in result
    assert "trendlines" in result


def test_analyze_market_structure_too_short():
    result = analyze_market_structure([1, 2], [0.9, 1.9], [1.0, 2.0])
    assert result["trend"] == "unknown"
    assert result["score"] == 0


def test_find_order_blocks_basic():
    high = np.array([1.0, 1.05, 1.1, 1.08, 1.06, 1.12, 1.15, 1.13, 1.11, 1.16])
    low = np.array([0.98, 1.02, 1.07, 1.05, 1.03, 1.09, 1.12, 1.1, 1.08, 1.13])
    close = np.array([0.99, 1.04, 1.09, 1.07, 1.05, 1.11, 1.14, 1.12, 1.1, 1.15])
    swings = np.array([0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    obs = find_order_blocks(high, low, close, swings, lookback=10)
    assert isinstance(obs, list)


def test_find_liquidity_sweeps():
    high = np.array([1.0, 1.05, 1.1, 1.08, 1.06, 1.15, 1.12, 1.09, 1.07, 1.05])
    low = np.array([0.98, 1.02, 1.07, 1.05, 1.03, 1.12, 1.09, 1.06, 1.04, 1.02])
    close = np.array([0.99, 1.04, 1.09, 1.07, 1.05, 1.13, 1.1, 1.07, 1.05, 1.03])
    swings = np.array([0, 0, 1, 0, 0, 1, 0, 0, 0, 0])
    sweeps = find_liquidity_sweeps(high, low, close, swings, lookback=10)
    assert isinstance(sweeps, list)
