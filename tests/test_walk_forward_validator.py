"""Tests for WalkForwardValidator"""
import json
import os
import tempfile

try:
    from engine_simple.walk_forward_validator import MODEL_NAMES, WalkForwardValidator
except ImportError:
    import sys
    sys.path.insert(0, "scripts/recalibration")
    from walk_forward_validator import MODEL_NAMES, WalkForwardValidator


def test_init_defaults():
    v = WalkForwardValidator(path=os.path.join(tempfile.gettempdir(), "test_val.json"))
    assert v.max_history == 200
    assert v.snapshot_interval == 50
    for m in MODEL_NAMES:
        assert len(v.recent[m]) == 0
    assert v.get_report()["accumulated_trades"] == 0


def test_record_and_accuracy():
    v = WalkForwardValidator(path=os.path.join(tempfile.gettempdir(), "test_val2.json"))
    for i in range(100):
        v.record("DL_LSTM", i % 2 == 0, "RANGING", "EURUSD")
    acc = v.get_accuracy("DL_LSTM")
    assert acc is not None
    assert 0.45 <= acc <= 0.55


def test_regime_accuracy():
    v = WalkForwardValidator(path=os.path.join(tempfile.gettempdir(), "test_val3.json"))
    for _i in range(50):
        v.record("MOM20x3", True, "TREND_UP", "GBPUSD")
    ra = v.get_regime_accuracy("MOM20x3", "TREND_UP")
    assert ra == 1.0


def test_drift_detection():
    v = WalkForwardValidator(path=os.path.join(tempfile.gettempdir(), "test_val4.json"))
    for i in range(60):
        v.record("LGB", i < 10, "RANGING", "USDCAD")
    assert v.detect_drift("LGB", threshold=0.50, window=50) is True
    assert v.get_accuracy("LGB") < 0.5


def test_snapshot_persists():
    path = os.path.join(tempfile.gettempdir(), "test_val5.json")
    if os.path.exists(path):
        os.remove(path)
    v = WalkForwardValidator(snapshot_interval=30, path=path)
    for _i in range(31):
        v.record("DL_LSTM", True, "RANGING")
    # Snapshot should have fired
    assert len(v.history["DL_LSTM"]) >= 1
    assert os.path.exists(path)
    with open(path) as f:
        data = json.load(f)
    assert "history" in data


def test_load_persisted():
    path = os.path.join(tempfile.gettempdir(), "test_val6.json")
    if os.path.exists(path):
        os.remove(path)
    v1 = WalkForwardValidator(snapshot_interval=10, path=path)
    for _i in range(20):
        v1.record("DL_LSTM", True)
    v2 = WalkForwardValidator(path=path)
    assert len(v2.history.get("DL_LSTM", [])) >= 1


def test_empty_report():
    v = WalkForwardValidator(path=os.path.join(tempfile.gettempdir(), "test_val7.json"))
    r = v.get_report()
    for m in MODEL_NAMES:
        assert m in r
        assert r[m]["accuracy"] is None
        assert r[m]["n"] == 0
        assert r[m]["drift"] is False


def test_report_after_trades():
    v = WalkForwardValidator(path=os.path.join(tempfile.gettempdir(), "test_val8.json"))
    for _i in range(50):
        v.record("MOM20x3", True, "TREND_UP", "EURUSD")
    r = v.get_report()
    assert r["MOM20x3"]["accuracy"] == 1.0
    assert r["MOM20x3"]["n"] == 50
    assert r["MOM20x3"]["drift"] is False
