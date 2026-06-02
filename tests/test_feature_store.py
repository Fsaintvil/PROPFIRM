"""Tests for feature_store.py — SQLite-backed position feature persistence"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest

from engine_simple.feature_store import FeatureStore


@pytest.fixture
def store():
    f = FeatureStore(":memory:")
    yield f
    f.close()


def test_load_missing_ticket(store):
    data = store.load(99999)
    assert data == {}


def test_save_and_load(store):
    meta = {"symbol": "EURUSD", "entry": 1.1, "regime": "TREND_UP"}
    store.save(12345, meta)
    loaded = store.load(12345)
    assert loaded["symbol"] == "EURUSD"
    assert loaded["regime"] == "TREND_UP"


def test_save_overwrite(store):
    store.save(1, {"version": 1})
    store.save(1, {"version": 2})
    loaded = store.load(1)
    assert loaded["version"] == 2


def test_delete(store):
    store.save(1, {"data": "test"})
    store.delete(1)
    loaded = store.load(1)
    assert loaded == {}


def test_delete_nonexistent(store):
    store.delete(99999)
    loaded = store.load(99999)
    assert loaded == {}


def test_close(store):
    store.close()
    # double close should not raise
    store.close()


def test_multiple_tickets(store):
    store.save(1, {"symbol": "EURUSD"})
    store.save(2, {"symbol": "GBPUSD"})
    store.save(3, {"symbol": "USDCAD"})
    assert store.load(1)["symbol"] == "EURUSD"
    assert store.load(2)["symbol"] == "GBPUSD"
    assert store.load(3)["symbol"] == "USDCAD"


def test_complex_meta(store):
    meta = {"symbol": "XAUUSD", "entry": 1950.50, "sl": 1940.0,
            "lot": 0.22, "regime": "RANGING",
            "predictions": {"MOM20x3": "BUY", "DL_LSTM": "SELL"},
            "r1_usd": 45.0}
    store.save(999, meta)
    loaded = store.load(999)
    assert loaded["predictions"]["MOM20x3"] == "BUY"
    assert loaded["r1_usd"] == 45.0
