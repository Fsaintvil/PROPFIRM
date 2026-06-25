"""Tests for risk_manager.py — PreTradeChecklist, KellySizing, VaR, StressTest, CircuitBreaker, RiskManager"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock

import numpy as np

import config_simple as cfg
from engine_simple.risk_manager import (
    PreTradeChecklist,
    KellySizing,
    VaREstimator,
    StressTester,
    CircuitBreaker,
    RiskManager,
)
from engine_simple.position_tracker import SymbolPerformance


# ── PreTradeChecklist ───────────────────────────────────────────────


class TestPreTradeChecklist:
    def make_checklist(self, ftmo=None):
        if ftmo is None:
            ftmo = MagicMock()
            ftmo.can_trade.return_value = (True, "")
            ftmo.challenge_status = "ACTIVE"
            ftmo.current_dd_pct.return_value = 0.0
            ftmo.max_dd_pct = 0.10
        return PreTradeChecklist(ftmo)

    def test_check_passes(self):
        cl = self.make_checklist()
        ok, checks = cl.check("EURUSD")
        assert ok is True
        assert len(checks) >= 1

    def test_check_blocked_by_can_trade(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (False, "cooldown")
        ftmo.challenge_status = "ACTIVE"
        ftmo.current_dd_pct.return_value = 0.0
        ftmo.max_dd_pct = 0.10
        cl = self.make_checklist(ftmo)
        ok, checks = cl.check("EURUSD")
        assert ok is False
        assert any(c["rule"] == "can_trade" and not c["pass"] for c in checks)

    def test_check_blocked_by_challenge_status(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (True, "")
        ftmo.challenge_status = "FAILED_DD"
        ftmo.current_dd_pct.return_value = 0.0
        ftmo.max_dd_pct = 0.10
        cl = self.make_checklist(ftmo)
        ok, checks = cl.check("EURUSD")
        assert ok is False
        assert any(c["rule"] == "challenge_status" for c in checks)

    def test_dd_warning_at_80pct(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (True, "")
        ftmo.challenge_status = "ACTIVE"
        ftmo.current_dd_pct.return_value = 0.085  # 85% of max
        ftmo.max_dd_pct = 0.10
        cl = self.make_checklist(ftmo)
        ok, checks = cl.check("EURUSD")
        assert ok is True  # still allowed, just warning
        assert any(c["rule"] == "dd_warning" and not c["pass"] for c in checks)

    def test_rr_below_min(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (True, "")
        ftmo.challenge_status = "ACTIVE"
        ftmo.current_dd_pct.return_value = 0.0
        ftmo.max_dd_pct = 0.10
        cl = self.make_checklist(ftmo)
        ok, checks = cl.check("EURUSD", signal={"rr": 0.5})
        assert ok is False
        assert any(c["rule"] == "min_rr" for c in checks)

    def test_rr_above_min(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (True, "")
        ftmo.challenge_status = "ACTIVE"
        ftmo.current_dd_pct.return_value = 0.0
        ftmo.max_dd_pct = 0.10
        cl = self.make_checklist(ftmo)
        ok, checks = cl.check("EURUSD", signal={"rr": 2.5})
        assert ok is True
        assert not any(c["rule"] == "min_rr" for c in checks)

    def test_summary(self):
        cl = self.make_checklist()
        ok, checks = cl.check("EURUSD")
        summary = cl.summary(checks)
        assert "passed" in summary
        assert "failed" in summary

    def test_audit_logged(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (True, "")
        ftmo.challenge_status = "ACTIVE"
        ftmo.current_dd_pct.return_value = 0.0
        ftmo.max_dd_pct = 0.10
        audit = MagicMock()
        cl = PreTradeChecklist(ftmo, audit)
        cl.check("EURUSD")
        audit.log_risk_check.assert_called_once()


# ── KellySizing ─────────────────────────────────────────────────────


class TestKellySizing:
    def test_default_kelly(self):
        k = KellySizing()
        assert k.fraction == 0.25
        assert k.max_risk_pct == 0.01

    def test_calculate_with_no_trades(self):
        k = KellySizing()
        perf = SymbolPerformance()
        risk = k.calculate(perf, 2.0)
        # No trades -> wr=0.5, uses default rr=2.0
        # kelly = 0.5 - 0.5/2.0 = 0.25, fractional = 0.0625
        # base_risk ~ 0.004
        assert risk >= cfg.RISK_PER_TRADE  # at least base
        assert risk <= k.max_risk_pct

    def test_calculate_with_positive_win_rate(self):
        k = KellySizing()
        perf = SymbolPerformance()
        perf.record(100, 2.0)
        perf.record(100, 2.0)  # 2 wins
        perf.record(-50, -1.0)  # 1 loss
        # wr = 2/3, avg_r = (2+2-1)/3 = 1.0
        # kelly = 0.667 - 0.333/1.0 = 0.333
        # fractional = 0.333 * 0.25 = 0.0833
        # risk = 0.004 * 1.0833 ≈ 0.00433
        risk = k.calculate(perf, 2.0)
        assert risk >= cfg.RISK_PER_TRADE
        assert risk <= k.max_risk_pct

    def test_calculate_with_negative_kelly(self):
        k = KellySizing()
        perf = SymbolPerformance()
        perf.record(-50, -1.0)
        perf.record(-50, -1.0)
        perf.record(-50, -1.0)
        # wr = 0, kelly = 0 - 1/avg_r → negative → clamped to 0
        risk = k.calculate(perf, 1.0)
        assert risk == cfg.RISK_PER_TRADE  # base risk only

    def test_calculate_caps_at_max_risk(self):
        k = KellySizing(fraction=1.0, max_risk_pct=0.02)
        perf = SymbolPerformance()
        perf.record(100, 2.0)
        perf.record(100, 2.0)
        perf.record(100, 2.0)
        # wr = 1.0, kelly = 1.0, fractional = 1.0
        # risk = 0.004 * 2.0 = 0.008 < 0.02
        risk = k.calculate(perf, 2.0)
        assert risk <= k.max_risk_pct

    def test_calculate_rr_zero(self):
        k = KellySizing()
        perf = SymbolPerformance()
        perf.record(-50, 0)  # rr = 0
        risk = k.calculate(perf, 2.0)
        assert risk == cfg.RISK_PER_TRADE  # base


# ── VaREstimator ────────────────────────────────────────────────────


class TestVaREstimator:
    def test_init(self):
        var = VaREstimator()
        assert var.confidence == 0.95
        assert var.lookback == 100
        assert len(var._returns) == 0

    def test_parametric_var_insufficient_data(self):
        var = VaREstimator()
        var_pct = var.parametric_var(100000)
        assert var_pct == 100000 * 0.02  # default fallback

    def test_parametric_var_sufficient_data(self):
        var = VaREstimator()
        for _ in range(50):
            var.add_return(np.random.normal(0.001, 0.01))
        var_val = var.parametric_var(100000)
        assert var_val > 0

    def test_historical_var_insufficient_data(self):
        var = VaREstimator()
        var_val = var.historical_var(100000)
        assert var_val == 100000 * 0.02

    def test_historical_var_sufficient_data(self):
        var = VaREstimator(lookback=20)
        for _ in range(20):
            var.add_return(-0.01)
        var_val = var.historical_var(100000)
        assert var_val > 0

    def test_cvar_insufficient_data(self):
        var = VaREstimator()
        cvar = var.cvar(100000)
        assert cvar == 100000 * 0.03

    def test_cvar_sufficient_data(self):
        var = VaREstimator(lookback=20)
        for _ in range(20):
            var.add_return(-0.02)
        cvar = var.cvar(100000)
        assert cvar > 0

    def test_add_return_updates(self):
        var = VaREstimator()
        var.add_return(0.01)
        assert len(var._returns) == 1

    def test_add_return_respects_maxlen(self):
        var = VaREstimator(lookback=5)
        for i in range(10):
            var.add_return(float(i))
        assert len(var._returns) == 5  # limited by deque maxlen
        assert list(var._returns) == [5.0, 6.0, 7.0, 8.0, 9.0]


# ── StressTester ────────────────────────────────────────────────────


class TestStressTester:
    def test_init(self):
        st = StressTester()
        assert "3sigma_down" in st.scenarios
        assert len(st.scenarios) == 5

    def test_run_buy(self):
        st = StressTester()
        results = st.run("EURUSD", 1.1000, 1.0900, 0.1, 0.005, 1.1000, action="BUY")
        assert len(results) == 5
        for name, r in results.items():
            assert "pnl_estimate" in r
            assert "hits_sl" in r
            assert "worst_case" in r

    def test_run_sell(self):
        st = StressTester()
        results = st.run("EURUSD", 1.1000, 1.1100, 0.1, 0.005, 1.1000, action="SELL")
        assert len(results) == 5

    def test_worst_case_is_sl_hit(self):
        st = StressTester()
        results = st.run("EURUSD", 1.1000, 1.0900, 0.1, 0.005, 1.1000, action="BUY")
        wc = results["flash_crash_2pct"]["worst_case"]
        # worst case = (SL - entry) * lot * 100000
        assert wc == (1.0900 - 1.1000) * 0.1 * 100000  # -$100


# ── CircuitBreaker ──────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_init(self):
        cb = CircuitBreaker()
        assert cb.max_loss_pct == 0.03
        assert cb.window_minutes == 30
        assert cb.is_tripped is False

    def test_check_does_not_trip_under_threshold(self):
        cb = CircuitBreaker(max_loss_pct=0.03)
        tripped = cb.check(100000, 100000, 0)
        assert tripped is False
        assert cb.is_tripped is False

    def test_check_trips_on_loss_threshold(self):
        cb = CircuitBreaker(max_loss_pct=0.03)
        tripped = cb.check(96000, 100000, 0)  # 4% loss
        assert tripped is True
        assert cb.is_tripped is True

    def test_check_trips_on_consecutive_losses(self):
        cb = CircuitBreaker(max_consecutive=3)
        tripped = cb.check(100000, 100000, 5)  # 5 consecutive losses
        assert tripped is True
        assert cb.is_tripped is True

    def test_tripped_blocks_further_trades(self):
        cb = CircuitBreaker(max_loss_pct=0.03)
        cb.check(95000, 100000, 0)  # trip
        assert cb.is_tripped is True
        # Even with no loss now, still tripped
        still_tripped = cb.check(100000, 100000, 0)
        assert still_tripped is True

    def test_tripped_cooldown_expires(self):
        cb = CircuitBreaker(max_loss_pct=0.03)
        cb._cooldown_seconds = 0.1  # 100ms cooldown for test
        cb.check(95000, 100000, 0)  # trip
        assert cb.is_tripped is True
        time.sleep(0.2)
        released = cb.check(100000, 100000, 0)
        assert released is False
        assert cb.is_tripped is False

    def test_update_adds_snapshot(self):
        cb = CircuitBreaker()
        now = time.time()
        cb.update(100000, 100000)
        assert len(cb._pnl_snapshots) == 1
        ts, eq = cb._pnl_snapshots[0]
        assert eq == 100000

    def test_update_prunes_old_snapshots(self):
        cb = CircuitBreaker(window_minutes=0.001)  # very short window
        cb.update(100000, 100000)
        time.sleep(0.1)
        cb.update(100001, 100001)
        # First snapshot should be pruned
        assert len(cb._pnl_snapshots) == 1

    def test_does_not_trip_with_small_loss(self):
        cb = CircuitBreaker(max_loss_pct=0.05)
        tripped = cb.check(98000, 100000, 0)  # 2% < 5%
        assert tripped is False


# ── RiskManager (integration) ───────────────────────────────────────


class TestRiskManager:
    def make_risk_manager(self):
        ftmo = MagicMock()
        ftmo.can_trade.return_value = (True, "")
        ftmo.challenge_status = "ACTIVE"
        ftmo.current_dd_pct.return_value = 0.0
        ftmo.max_dd_pct = 0.10
        return RiskManager(ftmo)

    def test_init(self):
        rm = self.make_risk_manager()
        assert rm.checklist is not None
        assert rm.kelly is not None
        assert rm.var_estimator is not None
        assert rm.stress_tester is not None
        assert rm.circuit_breaker is not None

    def test_pre_trade(self):
        rm = self.make_risk_manager()
        ok, checks = rm.pre_trade("EURUSD")
        assert ok is True

    def test_calculate_position_risk(self):
        rm = self.make_risk_manager()
        perf = SymbolPerformance()
        perf.record(100, 2.0)
        risk = rm.calculate_position_risk(perf, 2.0)
        assert risk > 0

    def test_check_circuit(self):
        rm = self.make_risk_manager()
        tripped = rm.check_circuit(100000, 100000, 0)
        assert tripped is False

    def test_estimate_var(self):
        rm = self.make_risk_manager()
        var = rm.estimate_var(100000)
        assert var == 100000 * 0.02  # fallback

    def test_stress_test(self):
        rm = self.make_risk_manager()
        results = rm.stress_test("EURUSD", 1.1000, 1.0900, 0.1, 0.005, 1.1000)
        assert len(results) == 5

    def test_update(self):
        rm = self.make_risk_manager()
        rm.update(100000, 100000)
        assert len(rm.circuit_breaker._pnl_snapshots) == 1

    def test_summary(self):
        rm = self.make_risk_manager()
        s = rm.summary()
        assert "circuit_tripped" in s
        assert s["circuit_tripped"] is False
        assert "var_95" in s
        assert "var_samples" in s
