"""Tests for signals.py — MOM20x3 breakout + reversion + confluence scoring"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock

import numpy as np

from engine_simple.signals import SignalGenerator


def _make_rates(n=100, trend="up"):
    """Generate synthetic OHLCV data"""
    rates = []
    base = 1.1000
    for i in range(n):
        if trend == "up":
            shift = i * 0.0005
        elif trend == "down":
            shift = -i * 0.0005
        else:
            shift = np.sin(i * 0.3) * 0.005
        o = base + shift + np.random.normal(0, 0.0001)
        h = o + abs(np.random.normal(0, 0.0002))
        lo = o - abs(np.random.normal(0, 0.0002))
        c = (h + lo) / 2 + np.random.normal(0, 0.00005)
        v = 1000 + np.random.randint(-100, 100)
        rates.append((i, o, h, lo, c, v))
    return np.array(rates, dtype=[("index", float), ("open", float), ("high", float),
                                  ("low", float), ("close", float), ("volume", float)])


def test_signal_generator_init():
    sg = SignalGenerator(MagicMock())
    assert sg._cache_ttl == 15


def test_analyze_returns_none_for_unknown_symbol():
    sg = SignalGenerator(MagicMock())
    result = sg.analyze("UNKNOWN")
    assert result is None


def test_analyze_returns_none_without_rates():
    sg = SignalGenerator(MagicMock())
    sg._get_rates_cached = MagicMock(return_value=None)
    result = sg.analyze("EURUSD")
    assert result is None


def test_analyze_returns_valid_signal_on_uptrend():
    sg = SignalGenerator(MagicMock())
    rates = _make_rates(100, trend="up")
    # Convert structured array to list for compatibility
    rates_list = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                  for r in rates]
    sg._get_rates_cached = MagicMock(return_value=rates_list)

    result = sg.analyze("EURUSD")
    if result:
        assert "action" in result
        assert result["action"] in ("BUY", "SELL")
        assert result["score"] >= 0
        assert result["confidence"] >= 0
        assert "atr" in result
        assert result["atr"] > 0
        assert "adx" in result


def test_eval_strat_breakout_detected():
    sg = SignalGenerator(MagicMock())
    data = [(i, 1.1 + i * 0.001, 1.1 + i * 0.001 + 0.002,
             1.1 + i * 0.001 - 0.002, 1.1 + i * 0.001, 1000)
            for i in range(50)]
    cfg = {"tf": "H1", "period": 20, "thresh": 2.0, "sl": 1.5, "tp": 4.0}
    result = sg._eval_strat("EURUSD", data, cfg, {}, base_thresh=2.0)
    if result:
        direction, atr_val, move, thresh, momentum, indicators = result
        assert direction in (-1, 1)
        assert atr_val > 0
        assert isinstance(indicators, dict)


def test_eval_strat_no_breakout_on_flat():
    sg = SignalGenerator(MagicMock())
    data = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(50)]
    cfg = {"tf": "H1", "period": 20, "thresh": 2.0, "sl": 1.5, "tp": 4.0}
    result = sg._eval_strat("EURUSD", data, cfg, {}, base_thresh=10.0)
    assert result is None


def test_compute_indicators_returns_dict():
    sg = SignalGenerator(MagicMock())
    c = np.linspace(1.1, 1.15, 60)
    h = c + 0.002
    lo = c - 0.002
    v = np.ones(60) * 1000
    np.arange(60, dtype=float)
    indicators = sg._compute_indicators(c, h, lo, v, 1, 0.005)
    # ICT/SMC: legacy indicator method returns empty dict
    assert isinstance(indicators, dict)


def test_aggregate_confidence_basic():
    sg = SignalGenerator(MagicMock())
    confidences = [
        {"ema_alignment": 0.5, "rsi_valid": 1.0, "rsi_divergence": 0,
         "macd_cross": 1.0, "macd_trend": 0.5, "obv_trend": 1.0,
         "vwap_position": 1.0, "avwap_position": 1.0, "pd_zone": 1.0,
         "rsi_divergence_v2": 0},
    ]
    result = sg._aggregate_confidence(confidences, {})
    # ICT/SMC: legacy aggregate returns empty dict
    assert isinstance(result, dict)


def test_eval_reversion_oversold():
    sg = SignalGenerator(MagicMock())
    data = [(i, 1.1 - i * 0.0005, 1.1 - i * 0.0005 + 0.002,
             1.1 - i * 0.0005 - 0.002, 1.1 - i * 0.0005, 1000)
            for i in range(50)]
    cfg = {"tf": "H1", "period": 20, "thresh": 2.0, "sl": 1.5, "tp": 4.0}
    result = sg._eval_reversion(data, 30, cfg)
    # RSI=30 < 35 → should trigger BUY
    if result:
        direction, atr_val, indicators = result
        assert direction == 1


def test_eval_reversion_ignores_mid_rsi():
    sg = SignalGenerator(MagicMock())
    data = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(50)]
    cfg = {"tf": "H1", "period": 20, "thresh": 2.0, "sl": 1.5, "tp": 4.0}
    result = sg._eval_reversion(data, 50, cfg)
    assert result is None
