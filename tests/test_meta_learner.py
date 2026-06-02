"""Tests for meta_learner.py — pure logic, no MT5"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Import directly to avoid engine_simple.__init__ which pulls in torch
import importlib
import json
import tempfile
from unittest.mock import patch

meta_learner_mod = importlib.import_module("engine_simple.meta_learner")
ModelTracker = meta_learner_mod.ModelTracker
MetaLearner = meta_learner_mod.MetaLearner
REGIMES = meta_learner_mod.REGIMES


def test_modeltracker_record_and_winrate():
    t = ModelTracker("test_model")
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", False)
    t.record("TREND_UP", "GBPUSD", True)
    t.record("TREND_UP", "GBPUSD", True)
    wr = t.win_rate(regime="RANGING")  # 2 wins / 3 trades = 0.667
    assert abs(wr - 2/3) < 0.001, f"Expected {2/3}, got {wr}"
    wr = t.win_rate(regime="TREND_UP")  # 2 wins / 2 trades < 3 min → fallback global
    # Global: 4 wins / 5 trades = 0.8
    assert abs(wr - 0.8) < 0.001, f"Expected 0.8, got {wr}"


def test_modeltracker_winrate_fallback():
    t = ModelTracker("test")
    # No trades → returns 0.5 default
    assert abs(t.win_rate() - 0.5) < 0.001
    # Only 2 trades for a specific regime (below min_trades=3)
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", True)
    # Falls back to global (2 trades < 3 → returns 0.5)
    assert abs(t.win_rate(regime="RANGING") - 0.5) < 0.001


def test_modeltracker_weight():
    t = ModelTracker("test")
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", True)
    w = t.weight("RANGING", base_weight=1.0)
    # wr=1.0, penalty=1.0 → weight = 1.0 * (0.5 + 1.0) / 1.0 = 1.5
    assert abs(w - 1.5) < 0.001


def test_modeltracker_weight_with_penalty():
    t = ModelTracker("test")
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", True)
    t.record("RANGING", "EURUSD", True)
    t.regime_penalty["RANGING"] = 2.0
    w = t.weight("RANGING", base_weight=1.0)
    # wr=1.0, penalty=2.0 → weight = 1.0 * (0.5 + 1.0) / 2.0 = 0.75
    assert abs(w - 0.75) < 0.001


def test_metaleaner_init():
    m = MetaLearner()
    assert len(m.trackers) == 3
    assert "MOM20x3" in m.trackers
    assert "DL_LSTM" in m.trackers


def test_metaleaner_record_trade():
    m = MetaLearner()
    m.record_trade("EURUSD", "RANGING", {"MOM20x3": True, "DL_LSTM": False})
    assert m.trackers["MOM20x3"].global_stats["wins"] == 1
    assert m.trackers["DL_LSTM"].global_stats["losses"] == 1
    assert m.regime_performance["RANGING"]["win"] == 1
    assert m.regime_performance["RANGING"]["loss"] == 1
    assert m.trades_since_recal == 1


def test_metaleaner_get_weights():
    m = MetaLearner()
    # Give MOM20x3 perfect record, DL_LSTM random
    for i in range(10):
        preds = {name: (i % 2 == 0) for name in m.get_model_names()}
        m.record_trade("EURUSD", "RANGING", preds)
    # Give MOM20x3 100% correct
    m.trackers["MOM20x3"].global_stats = {"wins": 10, "losses": 0, "total": 10}
    m.trackers["MOM20x3"].regime_stats["RANGING"] = {"wins": 10, "losses": 0, "total": 10}

    weights = m.get_weights("RANGING")
    assert abs(sum(weights.values()) - 1.0) < 0.001
    assert weights["MOM20x3"] > weights["DL_LSTM"]  # MOM20x3 should have higher weight


def test_ensemble_action_buy():
    m = MetaLearner()
    preds = {name: {"action": "BUY", "score": 0.8} for name in m.get_model_names()}
    action, conf, details = m.get_ensemble_action("RANGING", preds)
    assert action == "BUY", f"Expected BUY, got {action}"
    assert conf >= 0.55


def test_ensemble_action_sell():
    m = MetaLearner()
    preds = {name: {"action": "SELL", "score": 0.8} for name in m.get_model_names()}
    action, conf, details = m.get_ensemble_action("RANGING", preds)
    assert action == "SELL", f"Expected SELL, got {action}"
    assert conf >= 0.55


def test_ensemble_action_hold():
    m = MetaLearner()
    # 2 models say BUY, 1 says SELL → not enough for 55% if equal weights
    # Force equal buy/sell by having 1 BUY, 1 SELL (LGB not in predictions)
    preds = {
        "DL_LSTM": {"action": "BUY", "score": 0.5},
        "MOM20x3": {"action": "SELL", "score": 0.5},
    }
    action, conf, details = m.get_ensemble_action("RANGING", preds)
    assert action == "HOLD", f"Expected HOLD, got {action}"


def test_devil_advocate_detects_disagreement():
    m = MetaLearner()
    # Give MOM20x3 and DL_LSTM opposite strong records to create weight
    for _ in range(10):
        m.record_trade("EURUSD", "RANGING", {"MOM20x3": True, "DL_LSTM": False})

    preds = {
        "MOM20x3": {"action": "BUY", "score": 0.9},
        "DL_LSTM": {"action": "SELL", "score": 0.8},
    }
    devils = m.devil_advocate_check("RANGING", preds, "BUY")
    assert len(devils) > 0, f"Expected devils, got {devils}"


def test_devil_advocate_no_false_positive():
    m = MetaLearner()
    for _ in range(5):
        m.record_trade("EURUSD", "RANGING", {"MOM20x3": True, "RF": True})
    preds = {
        "MOM20x3": {"action": "BUY", "score": 0.8},
        "RF": {"action": "BUY", "score": 0.7},
    }
    devils = m.devil_advocate_check("RANGING", preds, "BUY")
    assert len(devils) == 0, f"Expected no devils, got {len(devils)}"


def test_recalibrate_adjusts_penalties():
    m = MetaLearner()
    # Give one model a terrible record in a specific regime
    for _ in range(10):
        m.record_trade("EURUSD", "RANGING", {"MOM20x3": False})
    # But good in other regimes
    for _ in range(10):
        m.record_trade("EURUSD", "TREND_UP", {"MOM20x3": True})

    m.recalibrate()
    penalty = m.trackers["MOM20x3"].regime_penalty.get("RANGING", 1.0)
    assert penalty > 1.0, f"Expected penalty > 1.0 for losing regime, got {penalty}"
    trend_penalty = m.trackers["MOM20x3"].regime_penalty.get("TREND_UP", 1.0)
    assert trend_penalty <= 1.0, f"Expected penalty <= 1.0 for winning regime, got {trend_penalty}"


def test_get_regime_win_rate():
    m = MetaLearner()
    assert abs(m.get_regime_win_rate("RANGING") - 0.5) < 0.001
    m.regime_performance["RANGING"]["win"] = 7
    m.regime_performance["RANGING"]["loss"] = 3
    assert abs(m.get_regime_win_rate("RANGING") - 0.7) < 0.001


def test_should_recalibrate():
    m = MetaLearner(recalibration_freq=5)
    assert not m.should_recalibrate()
    m.trades_since_recal = 5
    assert m.should_recalibrate()


def test_ensemble_action_mixed_confidence():
    m = MetaLearner()
    preds = {
        "MOM20x3": {"action": "BUY", "score": 0.9},
        "RF": {"action": "BUY", "score": 0.85},
        "XGB": {"action": "SELL", "score": 0.55},
        "LGBM": {"action": "SELL", "score": 0.51},
        "DL_LSTM": {"action": "HOLD", "score": 0.5},
    }
    action, conf, details = m.get_ensemble_action("RANGING", preds)
    assert action in ("BUY", "HOLD"), f"Expected BUY or HOLD, got {action}"


def test_save_state_creates_file():
    m = MetaLearner()
    m.record_trade("EURUSD", "RANGING", {"MOM20x3": True, "DL_LSTM": False})
    m.trackers["MOM20x3"].regime_penalty["RANGING"] = 2.0
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        m.save_state(tmp_path)
        assert os.path.exists(tmp_path)
        with open(tmp_path) as f:
            data = json.load(f)
        assert "MOM20x3" in data
        assert "DL_LSTM" in data
        assert "RANGING" in data["MOM20x3"]["regime_penalty"]
    finally:
        os.unlink(tmp_path)


def test_load_state_restores_penalties():
    m = MetaLearner()
    m.record_trade("EURUSD", "RANGING", {"MOM20x3": True})
    m.trackers["MOM20x3"].regime_penalty["RANGING"] = 2.0
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        m.save_state(tmp_path)
        # Modify penalty
        m.trackers["MOM20x3"].regime_penalty["RANGING"] = 1.0
        # Load restores original
        m.load_state(tmp_path)
        assert abs(m.trackers["MOM20x3"].regime_penalty["RANGING"] - 2.0) < 0.001
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_load_state_missing_file():
    m = MetaLearner()
    m.load_state("nonexistent_file_xyz.json")
    assert len(m.trackers) == 3  # state preserved after missing file


def test_load_state_corrupt_json():
    m = MetaLearner()
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        f.write("not valid json {{{")
        tmp_path = f.name
    try:
        m.load_state(tmp_path)
        assert len(m.trackers) == 3  # state preserved after corrupt json
    finally:
        os.unlink(tmp_path)


def test_load_state_partial_data():
    m = MetaLearner()
    m.record_trade("EURUSD", "RANGING", {"MOM20x3": True})
    m.trackers["MOM20x3"].regime_penalty["RANGING"] = 2.0
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        m.save_state(tmp_path)
        # Add extra model name to saved data (simulating old version)
        with open(tmp_path) as f:
            data = json.load(f)
        data["UNKNOWN_MODEL"] = {"regime_penalty": {"RANGING": 5.0}, "global_stats": {}}
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        m.load_state(tmp_path)
        # Should not crash; unknown model ignored
        assert abs(m.trackers["MOM20x3"].regime_penalty["RANGING"] - 2.0) < 0.001
    finally:
        os.unlink(tmp_path)


def test_save_load_round_trip():
    m = MetaLearner()
    m.record_trade("EURUSD", "RANGING", {"MOM20x3": True, "DL_LSTM": False})
    m.record_trade("EURUSD", "TREND_UP", {"MOM20x3": False, "DL_LSTM": True})
    m.trackers["MOM20x3"].regime_penalty["RANGING"] = 2.0
    m.trackers["DL_LSTM"].regime_penalty["TREND_UP"] = 0.5
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        m.save_state(tmp_path)
        m2 = MetaLearner()
        m2.load_state(tmp_path)
        assert abs(m2.trackers["MOM20x3"].regime_penalty["RANGING"] - 2.0) < 0.001
        assert abs(m2.trackers["DL_LSTM"].regime_penalty["TREND_UP"] - 0.5) < 0.001
    finally:
        os.unlink(tmp_path)


def test_save_state_exception_handled():
    m = MetaLearner()
    m.record_trade("EURUSD", "RANGING", {"MOM20x3": True})
    # Permission denied should not crash
    with patch('builtins.open', side_effect=PermissionError("denied")):
        m.save_state("/nonexistent/path.json")
    assert len(m.trackers) == 3  # state preserved after save failure
