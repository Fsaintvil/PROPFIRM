"""Tests for session_analyzer.py — pure logic, no MT5"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np

from engine_simple.session_analyzer import (
    analyze_sessions,
    get_current_session,
    is_session_active,
    session_highs_lows,
    session_proximity_weight,
)


def test_get_current_session():
    session = get_current_session()
    assert session in ("asia", "london_open", "london", "ny_open",
                       "new_york", "london_ny", "off_hours")


def test_is_session_active():
    assert is_session_active("asia", current_hour=3)
    assert not is_session_active("asia", current_hour=12)
    assert is_session_active("london_ny", current_hour=13)
    assert not is_session_active("london_ny", current_hour=10)


def test_session_proximity_weight_during_overlap():
    w = session_proximity_weight(current_hour=14)
    assert abs(w - 1.0) < 0.001


def test_session_proximity_weight_far():
    w = session_proximity_weight(current_hour=2)
    assert w < 1.0


def test_session_proximity_weight_off_hours():
    w = session_proximity_weight(current_hour=22)
    assert 0 <= w <= 1.0


def test_session_highs_lows_no_data():
    result = session_highs_lows([], [], [], lookback_hours=24)
    assert result == {}


def test_session_highs_lows_basic():
    high = np.array([1.1, 1.12, 1.11, 1.13, 1.1])
    low = np.array([1.09, 1.11, 1.1, 1.12, 1.09])
    np.array([1.095, 1.115, 1.105, 1.125, 1.095])
    import time
    timestamps = np.array([time.time() - i * 3600 for i in range(5)])
    result = session_highs_lows(high, low, timestamps, lookback_hours=24)
    assert "session_high" in result
    assert "session_low" in result
    assert "session_range" in result
    assert abs(result["session_high"] - 1.13) < 0.001
    assert abs(result["session_low"] - 1.09) < 0.001


def test_analyze_sessions():
    high = np.array([1.1, 1.12, 1.11, 1.13, 1.1])
    low = np.array([1.09, 1.11, 1.1, 1.12, 1.09])
    close = np.array([1.095, 1.115, 1.105, 1.125, 1.095])
    import time
    timestamps = np.array([time.time() - i * 3600 for i in range(5)])
    result = analyze_sessions(high, low, close, timestamps)
    assert "current_session" in result
    assert "session_weight" in result
    assert "session_high" in result
    assert "session_bias" in result
    assert result["session_bias"] in ("support", "resistance", "neutral", "premium", "discount")


def test_session_bias_resistance():
    high = np.array([1.1, 1.12, 1.11, 1.13, 1.14])
    low = np.array([1.09, 1.11, 1.1, 1.12, 1.13])
    close = np.array([1.095, 1.115, 1.105, 1.125, 1.135])
    import time
    timestamps = np.array([time.time() - i * 3600 for i in range(5)])
    result = analyze_sessions(high, low, close, timestamps)
    # close=1.135 near high=1.14 → resistance
    # ICT: close near session high = premium zone
    assert result["session_bias"] in ("resistance", "premium")


def test_session_bias_support():
    high = np.array([1.12, 1.11, 1.1, 1.09, 1.08])
    low = np.array([1.11, 1.1, 1.09, 1.08, 1.07])
    close = np.array([1.111, 1.101, 1.091, 1.081, 1.075])
    import time
    timestamps = np.array([time.time() - i * 3600 for i in range(5)])
    result = analyze_sessions(high, low, close, timestamps)
    # ICT: close near session low = discount zone
    assert result["session_bias"] in ("support", "discount")


def test_is_session_active_off_hours():
    assert not is_session_active("london", current_hour=22)
    assert not is_session_active("new_york", current_hour=8)
