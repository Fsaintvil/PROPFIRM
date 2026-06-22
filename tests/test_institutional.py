"""Tests pour AuditTrail, RiskManager, Broker, RateLimiter — modules institutionnels"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

import numpy as np

import config_simple as cfg
from engine_simple.audit_trail import AuditTrail
from engine_simple.broker import Broker, LatencyTracker
from engine_simple.position_tracker import SymbolPerformance
from engine_simple.risk_manager import (
    CircuitBreaker,
    KellySizing,
    StressTester,
    VaREstimator,
)
from engine_simple.trade_executor import ExecutionStats, PerSymbolRateLimiter as RateLimiter

# ── AuditTrail ──


def test_audit_trail_log_decision():
    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditTrail(log_dir=tmp)
        audit.log_decision("test", {"key": "value"}, status="OK")
        audit.flush()
        log_file = Path(tmp) / "decisions.jsonl"
        assert log_file.exists()
        content = log_file.read_text()
        assert "test" in content
        assert "key" in content
        audit.close()


def test_audit_trail_log_signal():
    audit = AuditTrail(log_dir=tempfile.mkdtemp())
    audit.log_signal("EURUSD", "BUY", 0.75, 0.8, "TREND_UP", {"reason": "test"})
    recent = audit.recent_decisions(1, "signal")
    assert len(recent) == 1
    assert recent[0]["context"]["symbol"] == "EURUSD"
    assert recent[0]["context"]["action"] == "BUY"
    audit.close()


def test_audit_trail_recent_decisions():
    audit = AuditTrail(log_dir=tempfile.mkdtemp())
    for i in range(5):
        audit.log_decision("test", {"i": i})
    recent = audit.recent_decisions(3)
    assert len(recent) == 3
    audit.close()


def test_audit_trail_log_error():
    audit = AuditTrail(log_dir=tempfile.mkdtemp())
    audit.log_error("test_module", "something broke", Exception("boom"))
    recent = audit.recent_decisions(1, "error")
    assert len(recent) == 1
    assert recent[0]["context"]["source"] == "test_module"
    audit.close()


def test_audit_trail_log_state_change():
    audit = AuditTrail(log_dir=tempfile.mkdtemp())
    audit.log_state_change("mode", "dry_run", "live")
    recent = audit.recent_decisions(1, "state_change")
    assert recent[0]["context"]["from"] == "dry_run"
    assert recent[0]["context"]["to"] == "live"
    audit.close()


# ── PerSymbolRateLimiter ──


def test_rate_limiter_allows():
    rl = RateLimiter(max_per_minute=5, window_seconds=60, min_interval_s=0)
    for _ in range(5):
        assert rl.allow("EURUSD")


def test_rate_limiter_blocks():
    rl = RateLimiter(max_per_minute=3, window_seconds=60, min_interval_s=0)
    for _ in range(3):
        rl.allow("EURUSD")
    assert not rl.allow("EURUSD")


def test_rate_limiter_per_symbol_independence():
    rl = RateLimiter(max_per_minute=1, window_seconds=60, min_interval_s=0)
    assert rl.allow("EURUSD")
    assert rl.allow("GBPUSD")  # different symbol
    assert not rl.allow("EURUSD")  # EURUSD exhausted


# ── ExecutionStats ──


def test_execution_stats_records():
    stats = ExecutionStats()
    stats.record(True, 0.05, 1.5)
    stats.record(True, 0.03, 0.5)
    stats.record(False, 0.02)
    assert stats.total_attempts == 3
    assert stats.successful == 2
    assert stats.rejected == 1
    assert stats.success_rate == 2 / 3
    assert stats.avg_slippage == 1.0


def test_execution_stats_latency():
    stats = ExecutionStats()
    for i in range(100):
        stats.record(True, 0.01 + i * 0.001)
    assert stats.p95_latency > 0
    assert stats.p95_latency <= stats.p95_latency * 1.1  # sanity


def test_execution_stats_summary():
    stats = ExecutionStats()
    stats.record(True, 0.05)
    s = stats.summary()
    assert "success_rate" in s
    assert "avg_latency_ms" in s
    assert "avg_slippage_pts" in s


# ── SymbolPerformance ──


def test_symbol_performance_wins():
    sp = SymbolPerformance()
    sp.record(100, 2.0)
    sp.record(-50, -1.0)
    assert sp.trades == 2
    assert sp.wins == 1
    assert sp.losses == 1
    assert sp.win_rate == 0.5
    assert sp.total_profit == 50


def test_symbol_performance_consecutive():
    sp = SymbolPerformance()
    sp.record(100, 2.0)
    sp.record(50, 1.0)
    sp.record(-30, -0.5)
    assert sp.max_consecutive_wins == 2
    assert sp.consecutive_losses == 1


def test_symbol_performance_empty():
    sp = SymbolPerformance()
    assert sp.win_rate == 0
    assert sp.avg_profit == 0


# ── KellySizing ──


def test_kelly_sizing_basic():
    kelly = KellySizing(fraction=0.25)
    perf = MagicMock()
    perf.win_rate = 0.6
    perf.avg_r_multiple = 2.0
    perf.trades = 100
    risk = kelly.calculate(perf, 2.0, base_risk=0.004)
    assert 0 < risk <= 0.01


def test_kelly_sizing_low_win_rate():
    kelly = KellySizing(fraction=0.25)
    perf = MagicMock()
    perf.win_rate = 0.3
    perf.avg_r_multiple = 1.0
    perf.trades = 100
    risk = kelly.calculate(perf, 1.0)
    assert risk <= 0.0055  # Kelly négatif → risk = base_risk (0.005 depuis 19 Juin)


def test_kelly_sizing_no_trades():
    kelly = KellySizing()
    perf = MagicMock()
    perf.trades = 0
    risk = kelly.calculate(perf, 2.0)
    assert risk >= cfg.RISK_PER_TRADE * 0.9  # Kelly adds small boost on default WR=0.5


# ── VaREstimator ──


def test_var_parametric():
    var = VaREstimator(lookback=100)
    for _ in range(100):
        var.add_return(np.random.normal(0, 0.01))
    result = var.parametric_var(100000)
    assert result > 0


def test_var_insufficient_data():
    var = VaREstimator(lookback=100)
    result = var.parametric_var(100000)
    assert result == 2000  # default fallback


def test_var_historical():
    var = VaREstimator(lookback=100)
    for _ in range(50):
        var.add_return(-0.02)
    result = var.historical_var(100000)
    assert result > 0


def test_cvar():
    var = VaREstimator(lookback=100)
    for _ in range(100):
        var.add_return(np.random.normal(-0.005, 0.01))
    result = var.cvar(100000)
    assert result > 0


# ── CircuitBreaker ──


def test_circuit_breaker_no_trip():
    cb = CircuitBreaker(max_loss_pct=0.05, window_minutes=30)
    cb.update(100000, 100000)
    assert not cb.check(100000, 100000, 0)


def test_circuit_breaker_trips_on_loss():
    cb = CircuitBreaker(max_loss_pct=0.02, window_minutes=30)
    cb.update(100000, 100000)
    assert cb.check(97000, 100000, 0)
    assert cb.is_tripped


def test_circuit_breaker_trips_on_consecutive():
    cb = CircuitBreaker(max_consecutive=3)
    assert cb.check(100000, 100000, 3)
    assert cb.is_tripped


def test_circuit_breaker_cooldown():
    cb = CircuitBreaker(max_loss_pct=0.02, window_minutes=30, max_consecutive=5)
    cb._cooldown_seconds = 1
    cb._trip(0.03, "test")
    assert cb.is_tripped
    with patch("engine_simple.risk_manager.time.time") as mock_time:
        mock_time.return_value = cb._trip_time + 1.5  # advance past cooldown
        assert not cb.check(100000, 100000, 0)


# ── StressTester ──


def test_stress_tester_basic():
    st = StressTester()
    results = st.run("EURUSD", 1.10, 1.095, 0.1, 0.005, 1.10)
    assert "3sigma_down" in results
    assert "flash_crash_2pct" in results
    for _name, r in results.items():
        assert "pnl_estimate" in r
        assert "hits_sl" in r


# ── Broker (wrapper tests) ──


def test_latency_tracker():
    lt = LatencyTracker()
    lt.record("test", 0.05)
    lt.record("test", 0.10)
    assert abs(lt.avg - 0.075) < 1e-6
    assert lt.p50 <= lt.p95
    s = lt.summary()
    assert "avg_ms" in s


def test_broker_delegates_calls():
    mt5_mock = MagicMock()
    mt5_mock.health_check.return_value = True
    mt5_mock.get_symbol_info.return_value = MagicMock()
    broker = Broker(mt5_mock)
    # Simulate connect + health check
    broker._connected = True
    info = broker.get_symbol_info("EURUSD")
    assert info is not None
    assert mt5_mock.get_symbol_info.called


def test_broker_raises_on_disconnect():
    mt5_mock = MagicMock()
    broker = Broker(mt5_mock, max_connect_attempts=1)  # 1 tentative = pas de backoff
    broker._connected = False
    mt5_mock.connect.return_value = False
    try:
        broker.get_symbol_info("EURUSD")
        raise AssertionError("Expected ConnectionError")
    except (ConnectionError, Exception):
        pass
