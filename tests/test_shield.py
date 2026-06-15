"""Tests for shield.py — FTMOAccount + PositionGuard"""
import os
import sys
import time
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from pathlib import Path
from engine_simple.shield import FTMOAccount
# PositionGuard supprimé (FIX #23 — hardcodait ATR=0.005)
from engine_simple.ftmo_config import BE_BUFFER_BY_REGIME, FIRST_LOCK_ATR, TRAILING_BY_REGIME


class TestFTMOAccount:
    def test_initial_state(self):
        acc = FTMOAccount(100000, 100000, 100000)
        assert acc.status == "ACTIVE"
        assert acc.drawdown_pct == 0.0
        assert acc.profit_pct == 0.0
        assert acc.total_trades == 0
        assert acc.consecutive_losses == 0

    def test_drawdown_calculation(self):
        acc = FTMOAccount(100000, 110000, 105000)
        assert abs(acc.drawdown_pct - (5000 / 110000)) < 0.001

    def test_profit_pct_positive(self):
        acc = FTMOAccount(100000, 110000, 110000)
        assert abs(acc.profit_pct - 0.10) < 0.001

    def test_profit_pct_negative(self):
        acc = FTMOAccount(100000, 95000, 95000)
        assert acc.profit_pct < 0

    def test_record_winning_trade(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(500)
        assert acc.total_trades == 1
        assert acc.consecutive_losses == 0
        assert acc.total_profit == 500
        assert acc.current_balance == 100500

    def test_record_losing_trade(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(-200)
        assert acc.total_trades == 1
        assert acc.consecutive_losses == 1
        assert acc.current_balance == 99800

    def test_consecutive_losses_trigger_cooldown(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(-100)
        acc.record_trade(-100)
        acc.record_trade(-100)
        assert acc.consecutive_losses == 3
        assert acc.global_cooldown_until > time.time()
        assert not acc.can_trade()

    def test_win_resets_consecutive_losses(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(-100)
        acc.record_trade(-100)
        acc.record_trade(300)
        assert acc.consecutive_losses == 0

    def test_can_trade_active(self):
        acc = FTMOAccount(100000, 100000, 100000)
        assert acc.can_trade()

    def test_cannot_trade_if_failed(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.status = "FAILED_DD"
        assert not acc.can_trade()

    def test_cannot_trade_if_max_dd_exceeded(self):
        acc = FTMOAccount(100000, 100000, 50000)
        assert not acc.can_trade(max_dd_pct=0.10)

    def test_cannot_trade_if_daily_loss_exceeded(self):
        acc = FTMOAccount(100000, 100000, 100000)
        today = str(datetime.utcnow().date())
        acc.daily_pnl[today] = -5000
        assert not acc.can_trade(max_daily_loss_pct=0.02)

    def test_check_failure_dd(self):
        acc = FTMOAccount(100000, 100000, 50000)
        result = acc.check_failure(max_dd_pct=0.10)
        assert result == "FAILED_DD"
        assert acc.status == "FAILED_DD"

    def test_check_failure_daily_loss(self):
        acc = FTMOAccount(100000, 100000, 100000)
        today = str(datetime.utcnow().date())
        acc.daily_pnl[today] = -5000
        result = acc.check_failure(max_daily_loss_pct=0.02)
        assert result == "FAILED_DAILY_LOSS"
        assert acc.status == "FAILED_DAILY_LOSS"

    def test_check_pass_requires_profit_target(self):
        acc = FTMOAccount(100000, 100000, 100000)
        assert not acc.check_pass(profit_target_pct=0.10)

    def test_check_pass_requires_min_days(self):
        acc = FTMOAccount(100000, 110000, 110000)
        assert not acc.check_pass(profit_target_pct=0.05, min_trading_days=10)

    def test_check_pass_success(self):
        acc = FTMOAccount(100000, 110000, 110000)
        for d in range(10):
            day = f"2026-01-{d+1:02d}"
            acc.record_trade(100, date=day)
        assert acc.check_pass(profit_target_pct=0.05, min_trading_days=10)
        assert acc.status == "PASSED"

    def test_consistency_violated(self):
        acc = FTMOAccount(100000, 110000, 110000)
        acc.total_profit = 1000
        acc.daily_pnl = {"2026-01-01": 400, "2026-01-02": 600}
        assert acc._consistency_violated(consistency_max_pct=0.30)

    def test_consistency_ok(self):
        acc = FTMOAccount(100000, 110000, 110000)
        acc.total_profit = 1000
        acc.daily_pnl = {"2026-01-01": 100, "2026-01-02": 200}
        assert not acc._consistency_violated(consistency_max_pct=0.30)

    def test_save_and_load(self, tmp_path):
        import engine_simple.shield as shield
        orig_path = shield.FTMOAccount.STATE_PATH
        shield.FTMOAccount.STATE_PATH = tmp_path / "robot_state.json"
        acc = FTMOAccount(100000, 105000, 102000)
        acc.total_trades = 5
        acc.total_profit = 2000
        acc.consecutive_losses = 1
        acc.save()

        loaded = FTMOAccount.load()
        assert loaded is not None
        assert loaded.initial_balance == 100000
        assert loaded.peak_equity == 105000
        assert loaded.current_balance == 102000
        assert loaded.total_trades == 5
        assert loaded.total_profit == 2000
        assert loaded.consecutive_losses == 1
        shield.FTMOAccount.STATE_PATH = orig_path

    def test_load_nonexistent(self):
        import engine_simple.shield as shield
        orig_path = shield.FTMOAccount.STATE_PATH
        shield.FTMOAccount.STATE_PATH = Path("/nonexistent/path.json")
        assert FTMOAccount.load() is None
        shield.FTMOAccount.STATE_PATH = orig_path

    def test_peak_equity_updated_on_win(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(5000)
        assert acc.peak_equity == 105000

    def test_peak_equity_not_updated_on_loss(self):
        acc = FTMOAccount(100000, 105000, 105000)
        acc.record_trade(-1000)
        assert acc.peak_equity == 105000

    def test_trading_days_recorded(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(100, date="2026-06-01")
        acc.record_trade(50, date="2026-06-01")
        assert len(acc.trading_days) == 1
        acc.record_trade(75, date="2026-06-02")
        assert len(acc.trading_days) == 2

    def test_daily_pnl_tracked(self):
        acc = FTMOAccount(100000, 100000, 100000)
        acc.record_trade(100, date="2026-06-01")
        acc.record_trade(-50, date="2026-06-01")
        assert acc.daily_pnl["2026-06-01"] == 50


class TestPositionGuard:
    """PositionGuard supprimé (FIX #23) — tests conservés comme documentation."""

    def test_positionguard_removed(self):
        """PositionGuard a été supprimé car ATR hardcodé à 0.005 (destructif)."""
        pytest.skip("PositionGuard removed — ATR hardcodé dangereux (issue #B1)")

    def test_trailing_by_regime_has_expected_structure(self):
        for regime, levels in TRAILING_BY_REGIME.items():
            for threshold, mult in levels:
                assert threshold > 0
                assert 0 < mult <= 1.0

    def test_be_buffer_by_regime_has_expected_values(self):
        for regime, mult in BE_BUFFER_BY_REGIME.items():
            assert 0 < mult <= 2.0
    def test_first_lock_atr_constant(self):
        assert FIRST_LOCK_ATR > 0
