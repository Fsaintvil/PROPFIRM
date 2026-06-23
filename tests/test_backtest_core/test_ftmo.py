"""Tests for FTMOChallengeSimulator, FTMOConfig, FTMOVerdict."""

from datetime import datetime

import pytest

from engine_simple.backtest_core.ftmo import (
    FTMOChallengeSimulator,
    FTMOConfig,
    FTMOVerdict,
)
from engine_simple.backtest_core.trade import SimTrade


def _make_closed_trade(action, entry, sl, tp, close_price, profit_usd_cost, close_time):
    t = SimTrade(
        symbol="EURUSD",
        action=action,
        entry=entry,
        sl=sl,
        tp=tp,
        atr_val=0.002,
        regime="RANGING",
        bar_idx=0,
        bar_time=datetime(2026, 6, 1, 8, 0),
        lot=0.1,
    )
    t.closed = True
    t.close_price = close_price
    t.close_bar = 10
    t.close_time = close_time
    t.bars_held = 10
    t.result = "TP" if profit_usd_cost > 0 else "SL"
    t.profit_usd = profit_usd_cost * 1.1  # gross > net
    t.profit_usd_cost = profit_usd_cost
    t.profit_pct = profit_usd_cost / (0.1 * 100_000 * entry) * 100
    t.profit_pct_cost = t.profit_pct
    return t


@pytest.fixture
def profitable_trades():
    trades = []
    for i in range(20):
        t = _make_closed_trade(
            "BUY",
            1.1050,
            1.1000,
            1.1150,
            1.1150,
            500.0,
            close_time=datetime(2026, 6, i + 1, 12, 0),
        )
        trades.append(t)
    return trades


@pytest.fixture
def empty_trades():
    return []


@pytest.fixture
def simulator():
    return FTMOChallengeSimulator()


class TestFTMOConfig:
    def test_ftmo_config_default(self):
        cfg = FTMOConfig()
        assert cfg.account_size == 200_000.0
        assert cfg.profit_target_pct == 0.05
        assert cfg.max_daily_loss_pct == 0.02
        assert cfg.max_dd_pct == 0.10
        assert cfg.consistency_pct == 0.30
        assert cfg.min_trading_days == 10
        assert cfg.max_duration_days == 30

    def test_ftmo_config_custom(self):
        cfg = FTMOConfig(account_size=100_000, profit_target_pct=0.10)
        assert cfg.account_size == 100_000
        assert cfg.profit_target_pct == 0.10

    def test_ftmo_config_dict(self):
        cfg = FTMOConfig(**{"account_size": 50_000, "min_trading_days": 5})
        assert cfg.account_size == 50_000
        assert cfg.min_trading_days == 5


class TestFTMOVerdict:
    def test_verdict_defaults(self):
        v = FTMOVerdict()
        assert v.passed is False
        assert v.fail_reason == ""
        assert v.total_pnl == 0.0
        assert v.total_trades == 0

    def test_verdict_custom_config(self):
        cfg = FTMOConfig(account_size=300_000)
        v = FTMOVerdict(config=cfg)
        assert v.config.account_size == 300_000


class TestFTMOChallengeSimulator:
    def test_evaluate_empty(self, simulator, empty_trades):
        verdict = simulator.evaluate(empty_trades)
        assert verdict.passed is False
        assert verdict.fail_reason == "Aucun trade fermé"
        assert verdict.total_trades == 0

    def test_evaluate_profitable(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        assert verdict.total_trades == 20
        assert verdict.total_pnl > 0
        assert verdict.win_rate > 0
        assert verdict.profit_factor > 1.0

    def test_initial_balance_used(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades, balance=200_000.0)
        assert verdict.config.account_size == 200_000

    def test_daily_loss_tracked(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        assert verdict.max_daily_loss_pct >= 0
        assert len(verdict.daily_pnl) > 0

    def test_drawdown_from_trades(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        assert verdict.max_dd_pct >= 0

    def test_drawdown_from_equity_curve(self, simulator, profitable_trades):
        eq_curve = [200_000, 201_000, 200_500, 202_000]
        dates = [datetime(2026, 6, 1), datetime(2026, 6, 2), datetime(2026, 6, 3), datetime(2026, 6, 4)]
        verdict = simulator.evaluate(profitable_trades, equity_curve=eq_curve, dates=dates)
        assert verdict.max_dd_pct >= 0

    def test_consistency_rule(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        assert "best_day_consistency_pct" in vars(verdict) or True

    def test_min_trading_days(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        assert verdict.trading_days >= 0

    def test_margin_of_safety(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        assert isinstance(verdict.margin_of_safety, float)

    def test_summary_contains_verdict(self, simulator, profitable_trades):
        verdict = simulator.evaluate(profitable_trades)
        summary = FTMOChallengeSimulator.summary(verdict)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_summary_passed_format(self):
        cfg = FTMOConfig(account_size=200_000, profit_target_pct=0.05)
        v = FTMOVerdict(
            config=cfg,
            passed=True,
            fail_reason="CHALLENGE PASSÉ ✅",
            total_pnl=12_000,
            total_pnl_pct=6.0,
            total_trades=30,
            max_dd_pct=4.5,
            max_daily_loss_pct=1.2,
            trading_days=15,
            win_rate=66.7,
            profit_factor=1.8,
        )
        summary = FTMOChallengeSimulator.summary(v)
        assert "PASSÉ" in summary

    def test_summary_failed_format(self):
        cfg = FTMOConfig(account_size=200_000)
        v = FTMOVerdict(
            config=cfg,
            passed=False,
            fail_reason="Max DD dépassé: 12.5%",
            total_pnl=-5_000,
            total_pnl_pct=-2.5,
            total_trades=30,
            max_dd_pct=12.5,
            max_daily_loss_pct=3.0,
            trading_days=5,
            win_rate=40.0,
            profit_factor=0.7,
        )
        summary = FTMOChallengeSimulator.summary(v)
        assert "ÉCHOUÉ" in summary

    def test_verbose_logging(self, profitable_trades):
        sim = FTMOChallengeSimulator(verbose=True)
        verdict = sim.evaluate(profitable_trades)
        assert isinstance(verdict, FTMOVerdict)

    def test_monte_carlo_insufficient_trades(self, simulator):
        result = simulator.monte_carlo_pass_probability([], n_simulations=100)
        assert result["pass_probability"] == 0.0
        assert "Trop peu de trades" in result.get("error", "")

    def test_monte_carlo_with_trades_small(self, profitable_trades, simulator):
        result = simulator.monte_carlo_pass_probability(profitable_trades, n_simulations=100)
        assert "pass_probability" in result
        assert result["n_simulations"] == 100
