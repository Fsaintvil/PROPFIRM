import os

from improvements.confidence_optimization import suggest_adaptive_threshold
from improvements.signal_conflict_resolution import resolve_signals


def test_suggest_adaptive_threshold_no_data():
    base = 0.6
    assert suggest_adaptive_threshold([], base) == base


def test_suggest_adaptive_threshold_with_data():
    recent = [10.0, -5.0, 2.0, 3.0, -1.0]
    new = suggest_adaptive_threshold(recent, 0.6)
    assert isinstance(new, float)
    assert 0.0 <= new <= 0.95


def test_resolve_signals_prefers_stronger():
    base = {
        "meta_learning": {"action": "buy", "confidence": 0.6},
        "regime_detection": {"action": "sell", "confidence": 0.2},
        "combined_signal": "hold",
        "confidence": 0.2,
    }
    resolved = resolve_signals(base)
    assert resolved["combined_signal"] in ("buy", "sell", "hold")
    assert 0.0 <= float(resolved.get("confidence", 0.0)) <= 0.85
