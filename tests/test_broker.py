"""Tests dédiés pour Broker + LatencyTracker"""
import time
from unittest.mock import MagicMock

import pytest

from engine_simple.broker import Broker, LatencyTracker


class TestLatencyTracker:
    def test_empty_percentiles(self):
        lt = LatencyTracker()
        assert lt.p50 == 0
        assert lt.p95 == 0
        assert lt.p99 == 0
        assert lt.avg == 0

    def test_record_and_percentile(self):
        lt = LatencyTracker()
        for d in [0.01, 0.02, 0.03, 0.04, 0.05]:
            lt.record("op", d)
        assert lt.p50 == pytest.approx(0.03)
        assert lt.p95 == pytest.approx(0.05)
        assert lt.avg == pytest.approx(0.03)

    def test_single_sample(self):
        lt = LatencyTracker()
        lt.record("op", 0.042)
        assert lt.p50 == 0.042
        assert lt.p95 == 0.042
        assert lt.avg == 0.042

    def test_summary(self):
        lt = LatencyTracker()
        lt.record("op", 0.05)
        s = lt.summary()
        assert "avg_ms" in s
        assert "p50_ms" in s
        assert "p95_ms" in s
        assert "p99_ms" in s
        assert s["samples"] == 1

    def test_empty_summary(self):
        lt = LatencyTracker()
        s = lt.summary()
        assert s["avg_ms"] == 0
        assert s["samples"] == 0

    def test_large_percentile_edge(self):
        lt = LatencyTracker(max_samples=5)
        for i in range(5):
            lt.record("op", i * 0.01)
        assert lt.percentile(100) == 0.04
        assert lt.percentile(0) == 0.0

    def test_deque_maxlen(self):
        lt = LatencyTracker(max_samples=3)
        for i in range(10):
            lt.record("op", i * 0.01)
        assert len(lt._samples) == 3


class TestBroker:
    @pytest.fixture
    def mock_mt5(self):
        m = MagicMock()
        m.connect = MagicMock(return_value=True)
        m.disconnect = MagicMock()
        m.health_check = MagicMock(return_value=True)
        m.get_rates = MagicMock(return_value=[1.0, 2.0])
        return m

    @pytest.fixture
    def mock_audit(self):
        a = MagicMock()
        a.log_error = MagicMock()
        return a

    def test_connect_success(self, mock_mt5):
        b = Broker(mock_mt5)
        assert b.connect() is True
        assert b.is_connected is True

    def test_connect_failure(self, mock_mt5):
        mock_mt5.connect = MagicMock(return_value=False)
        b = Broker(mock_mt5, max_connect_attempts=3)
        assert b.connect() is False
        assert b.is_connected is False

    def test_disconnect(self, mock_mt5):
        b = Broker(mock_mt5)
        b.connect()
        assert b.is_connected is True
        b.disconnect()
        assert b.is_connected is False

    def test_reconnect(self, mock_mt5):
        b = Broker(mock_mt5)
        b.connect()
        b.disconnect()
        assert b.reconnect() is True
        assert b.is_connected is True

    def test_health_check_connected(self, mock_mt5):
        b = Broker(mock_mt5)
        b.connect()
        assert b.health_check() is True

    def test_health_check_disconnected_triggers_reconnect(self, mock_mt5):
        mock_mt5.health_check = MagicMock(return_value=False)
        mock_mt5.connect = MagicMock(return_value=False)
        b = Broker(mock_mt5, max_connect_attempts=2)
        b._connected = True
        b._consecutive_failures = 4
        b._max_failures = 5
        assert b.health_check() is False
        mock_mt5.connect.assert_called()

    def test_health_check_cached(self, mock_mt5):
        b = Broker(mock_mt5)
        b._connected = True
        b._last_health_check = time.time()
        assert b.health_check() is True
        mock_mt5.health_check.assert_not_called()

    def test_call_delegates_to_mt5(self, mock_mt5):
        b = Broker(mock_mt5)
        b.connect()
        result = b.get_rates("EURUSD")
        mock_mt5.get_rates.assert_called_with("EURUSD")
        assert result == [1.0, 2.0]

    def test_call_raises_when_disconnected(self, mock_mt5):
        mock_mt5.connect = MagicMock(return_value=False)
        b = Broker(mock_mt5, max_connect_attempts=1)
        b._connected = False
        with pytest.raises(ConnectionError, match="MT5 deconnecte"):
            b.get_rates("EURUSD")

    def test_call_raises_for_missing_method(self, mock_mt5):
        mock_mt5.nonexistent_method = None
        b = Broker(mock_mt5)
        b.connect()
        with pytest.raises(AttributeError, match="n'a pas d'attribut"):
            b.nonexistent_method()

    def test_getattr_for_attribute(self, mock_mt5):
        mock_mt5.some_attr = "hello"
        b = Broker(mock_mt5)
        b.connect()
        assert b.some_attr == "hello"

    def test_getattr_raises_for_missing(self, mock_mt5):
        b = Broker(mock_mt5)
        b.connect()
        with pytest.raises(AttributeError, match="_nonexistent"):
            b._nonexistent

    def test_rate_limit_blocks_excessive_calls(self, mock_mt5):
        b = Broker(mock_mt5)
        b._max_calls_per_second = 2
        b.connect()
        t0 = time.time()
        for _ in range(3):
            b.get_rates("EURUSD")
        elapsed = time.time() - t0
        assert elapsed >= 0.05

    def test_audit_logged_on_call_failure(self, mock_mt5, mock_audit):
        mock_mt5.bad_method = MagicMock(side_effect=ValueError("fail"))
        b = Broker(mock_mt5, audit=mock_audit)
        b.connect()
        with pytest.raises(ValueError):
            b.bad_method()
        mock_audit.log_error.assert_called_once()

    def test_latency_tracked_on_call(self, mock_mt5):
        mock_mt5.get_rates = MagicMock(side_effect=lambda *a: time.sleep(0.001))
        b = Broker(mock_mt5)
        b.connect()
        b.get_rates("EURUSD")
        assert b.latency.p50 > 0

    def test_latency_tracked_on_connect(self, mock_mt5):
        mock_mt5.connect = MagicMock(side_effect=lambda: time.sleep(0.001) or True)
        b = Broker(mock_mt5)
        b.connect()
        assert b.latency.p50 > 0
