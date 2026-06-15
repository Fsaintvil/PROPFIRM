"""Tests for signals.py — MOM20x3 breakout + reversion + confluence scoring"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock

import numpy as np

np.random.seed(42)

# SignalGenerator archivé dans retired/ (ICT/SMC déprécié Juin 2026)
# Les tests sont conservés mais le module n'est plus actif
from retired.signals import SignalGenerator  # type: ignore[import-untyped]


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
    result = sg.analyze("UNKNOWN")
    assert result is None


def test_analyze_returns_valid_signal_on_uptrend():
    sg = SignalGenerator(MagicMock())
    rates = _make_rates(100, trend="up")
    # Convert structured array to list for compatibility
    rates_list = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                  for r in rates]
    sg._get_rates_cached = MagicMock(return_value=rates_list)

    result = sg.analyze("XAUUSD")
    assert result is not None, "uptrend should produce a signal"
    assert "action" in result
    assert result["action"] in ("BUY", "SELL")
    assert result["score"] >= 0
    assert result["confidence"] >= 0
    assert "atr" in result
    assert result["atr"] > 0
    assert "adx" in result


def test_placeholder():
    """Placeholder — legacy stub tests removed with the dead methods."""


