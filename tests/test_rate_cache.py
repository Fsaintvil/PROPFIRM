"""Tests for rate_cache.py — SQLite-backed cache with TTL"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
import time

import pytest

from engine_simple.rate_cache import RateCache


@pytest.fixture
def cache():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    c = RateCache(db_path=tmp.name, default_ttl=15)
    yield c
    c.clear()
    os.unlink(tmp.name)


def test_get_rates_miss(cache):
    result = cache.get_rates("EURUSD", "H1", 100)
    assert result is None


def test_set_and_get_rates(cache):
    data = [(1, 1.1, 1.102, 1.098, 1.1, 1000)]
    cache.set_rates("EURUSD", "H1", 100, data)
    result = cache.get_rates("EURUSD", "H1", 100)
    assert result is not None
    assert len(result) == 1
    assert result[0][1] == 1.1


def test_rates_expire(cache):
    data = [(1, 1.1, 1.102, 1.098, 1.1, 1000)]
    cache.set_rates("EURUSD", "H1", 100, data, ttl=0)
    time.sleep(0.01)
    result = cache.get_rates("EURUSD", "H1", 100)
    assert result is None


def test_get_volatility_miss(cache):
    result = cache.get_volatility("EURUSD")
    assert result is None


def test_set_and_get_volatility(cache):
    data = {"cur": 1.1, "atr_v": 0.005}
    cache.set_volatility("EURUSD", data)
    result = cache.get_volatility("EURUSD")
    assert result is not None
    assert result["cur"] == 1.1


def test_volatility_expires(cache):
    data = {"cur": 1.1}
    cache.set_volatility("EURUSD", data, ttl=0)
    time.sleep(0.01)
    result = cache.get_volatility("EURUSD")
    assert result is None


def test_purge_expired(cache):
    cache.set_rates("EURUSD", "H1", 100, [1, 2, 3], ttl=0)
    cache.set_volatility("GBPUSD", {"x": 1}, ttl=0)
    cache.purge_expired()
    assert cache.get_rates("EURUSD", "H1", 100) is None
    assert cache.get_volatility("GBPUSD") is None


def test_clear(cache):
    cache.set_rates("EURUSD", "H1", 100, [1, 2, 3])
    cache.set_volatility("GBPUSD", {"x": 1})
    cache.clear()
    assert cache.get_rates("EURUSD", "H1", 100) is None
    assert cache.get_volatility("GBPUSD") is None


def test_multiple_symbols_isolation(cache):
    cache.set_rates("EURUSD", "H1", 100, [{"rate": 1.1}])
    cache.set_rates("GBPUSD", "H1", 100, [{"rate": 1.25}])
    eur = cache.get_rates("EURUSD", "H1", 100)
    gbp = cache.get_rates("GBPUSD", "H1", 100)
    assert eur[0]["rate"] == 1.1
    assert gbp[0]["rate"] == 1.25
