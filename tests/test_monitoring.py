"""Tests pour le module Monitoring (MetricsCollector, HealthServer, structured logging)"""
import json
import logging
import os
import sys
import tempfile
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

from engine_simple.monitoring import (
    HealthServer,
    MetricsCollector,
    setup_structured_logging,
)


def test_metrics_counter():
    m = MetricsCollector()
    m.inc("trades_total", {"symbol": "EURUSD"})
    m.inc("trades_total", {"symbol": "EURUSD"})
    m.inc("trades_total", {"symbol": "GBPUSD"})
    snap = m.snapshot()
    assert snap["counters"]["trades_total"]["trades_total[symbol=EURUSD]"] == 2
    assert snap["counters"]["trades_total"]["trades_total[symbol=GBPUSD]"] == 1


def test_metrics_gauge():
    m = MetricsCollector()
    m.gauge("equity", 100000)
    m.gauge("equity", 95000)
    snap = m.snapshot()
    assert snap["gauges"]["equity"] == 95000


def test_metrics_histogram():
    m = MetricsCollector()
    for v in [10, 20, 30, 40, 50]:
        m.histogram("latency_ms", v)
    snap = m.snapshot()
    h = snap["histograms"]["latency_ms"]
    assert h["min"] == 10
    assert h["max"] == 50
    assert h["avg"] == 30
    assert h["p50"] == 30


def test_metrics_prometheus_text():
    m = MetricsCollector()
    m.inc("trades_total", {"symbol": "EURUSD"})
    m.gauge("equity", 200000)
    text = m.prometheus_text()
    assert "robot_trades_total" in text
    assert "robot_gauge" in text
    assert "EURUSD" in text
    assert "200000" in text


def test_metrics_thread_safe():
    m = MetricsCollector()
    errors = []

    def worker():
        try:
            for _ in range(100):
                m.inc("test")
                m.gauge("x", 1)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    snap = m.snapshot()
    assert snap["counters"]["test"]["test"] == 1000


def test_health_server_endpoints():
    m = MetricsCollector()
    m.inc("trades_total", {"test": "1"})
    server = HealthServer(port=9091, metrics=m, health_check=lambda: {"status": "ok", "balance": 1000})
    server.start()
    time.sleep(0.3)

    # Test health endpoint
    conn = HTTPConnection("localhost", 9091, timeout=5)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["status"] == "ok"
    assert data["balance"] == 1000
    assert "uptime_seconds" in data
    conn.close()

    # Test metrics endpoint
    conn = HTTPConnection("localhost", 9091, timeout=5)
    conn.request("GET", "/metrics")
    resp = conn.getresponse()
    assert resp.status == 200
    text = resp.read().decode()
    assert "robot_trades_total" in text
    conn.close()

    # Test ready endpoint
    conn = HTTPConnection("localhost", 9091, timeout=5)
    conn.request("GET", "/ready")
    resp = conn.getresponse()
    assert resp.status == 200
    conn.close()

    # Test 404
    conn = HTTPConnection("localhost", 9091, timeout=5)
    conn.request("GET", "/nonexistent")
    resp = conn.getresponse()
    assert resp.status == 404
    conn.close()

    server.stop()


def test_setup_structured_logging():
    tmp = tempfile.mkdtemp()
    try:
        handler = setup_structured_logging(log_dir=tmp, app_name="test")
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        handler.setLevel(logging.DEBUG)
        root_logger.info("hello world")
        handler.flush()
        handler.close()
        root_logger.removeHandler(handler)
        log_file = Path(tmp) / "test_structured.jsonl"
        assert log_file.exists()
        content = log_file.read_text()
        assert "hello world" in content
        assert "timestamp" in content
        assert "level" in content
    finally:
        import shutil
        for _ in range(5):
            try:
                shutil.rmtree(tmp)
                break
            except PermissionError:
                import time
                time.sleep(0.1)
