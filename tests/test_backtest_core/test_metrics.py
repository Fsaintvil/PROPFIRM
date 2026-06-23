"""Tests for MetricsCalculator."""

from datetime import datetime

import numpy as np
import pytest

from engine_simple.backtest_core.metrics import MetricsCalculator
from engine_simple.backtest_core.trade import SimTrade


def _make_trade(action, entry, sl, tp, close_price, profit_usd, profit_usd_cost, close_time):
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
    t.result = "TP" if profit_usd > 0 else "SL"
    t.profit_usd = profit_usd
    t.profit_usd_cost = profit_usd_cost
    t.profit_pct = profit_usd / (0.1 * 100_000 * entry) * 100
    t.profit_pct_cost = profit_usd_cost / (0.1 * 100_000 * entry) * 100
    return t


@pytest.fixture
def empty_trades():
    return []


@pytest.fixture
def profitable_trades():
    trades = []
    for i in range(20):
        t = _make_trade(
            "BUY",
            1.1050,
            1.1000,
            1.1150,
            1.1150,
            profit_usd=20.0,
            profit_usd_cost=18.0,
            close_time=datetime(2026, 6, i + 1, 12, 0),
        )
        trades.append(t)
    for i in range(10):
        t = _make_trade(
            "SELL",
            1.1050,
            1.1100,
            1.0950,
            1.1100,
            profit_usd=-15.0,
            profit_usd_cost=-16.5,
            close_time=datetime(2026, 6, i + 1, 14, 0),
        )
        trades.append(t)
    return trades


@pytest.fixture
def mixed_trades():
    trades = []
    for i in range(15):
        win = i % 3 != 0
        pnl = 10.0 if win else -8.0
        cost = 8.0 if win else -10.0
        t = _make_trade(
            "BUY" if win else "SELL",
            1.1050,
            1.1000,
            1.1150,
            1.1150 if win else 1.1000,
            profit_usd=pnl,
            profit_usd_cost=cost,
            close_time=datetime(2026, 6, i + 1, 10 + i % 12, 0),
        )
        trades.append(t)
    return trades


class TestMetricsCalculator:
    def test_empty_trades(self, empty_trades):
        m = MetricsCalculator.compute(empty_trades)
        assert m["error"] == "no trades"
        assert m["n"] == 0

    def test_no_closed_trades(self):
        t = _make_trade("BUY", 1.1050, 1.1000, 1.1150, 1.1050, 0, 0, datetime.utcnow())
        t.closed = False
        m = MetricsCalculator.compute([t])
        assert "error" in m

    def test_basic_metrics(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert m["n"] == 30
        assert m["n_wins"] == 20
        assert m["n_losses"] == 10
        assert m["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert m["net_profit"] > 0
        assert m["profit_factor"] > 1.0

    def test_sharpe_ratio(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert isinstance(m["sharpe_ratio"], float)

    def test_sortino_ratio(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert isinstance(m["sortino_ratio"], float)

    def test_drawdown_from_trades(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert m["max_dd_pct"] >= 0
        assert m["max_dd_usd"] >= 0
        assert isinstance(m["max_dd_duration_days"], int)

    def test_drawdown_from_equity_curve(self, profitable_trades):
        eq_curve = [200_000 + 200 * i for i in range(100)]
        dates = [datetime(2026, 6, 1) + __import__("datetime").timedelta(hours=i) for i in range(100)]
        m = MetricsCalculator.compute(profitable_trades, equity_curve=eq_curve, dates=dates)
        assert m["max_dd_pct"] >= 0

    def test_direction_analysis(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert "direction" in m
        assert "long" in m["direction"]
        assert "short" in m["direction"]
        assert m["direction"]["long"]["trades"] > 0
        assert m["direction"]["short"]["trades"] > 0

    def test_session_analysis(self, mixed_trades):
        m = MetricsCalculator.compute(mixed_trades)
        assert "sessions" in m
        for sess in ("asia", "london", "ny"):
            assert sess in m["sessions"]
            assert "trades" in m["sessions"][sess]
            assert "win_rate" in m["sessions"][sess]

    def test_monthly_analysis(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert "monthly_returns" in m
        assert len(m["monthly_returns"]) >= 1
        assert "month" in m["monthly_returns"][0]
        assert "pnl" in m["monthly_returns"][0]

    def test_yearly_analysis(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert "yearly" in m
        assert "2026" in m["yearly"]

    def test_significance(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert "z_score" in m
        assert "p_value" in m
        assert "significant" in m
        assert isinstance(m["significant"], bool)

    def test_expectancy(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert m["expectancy"] > 0

    def test_avg_trade_positive(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert m["avg_trade"] > 0

    def test_consecutive_losses(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert m["max_consecutive_losses"] >= 0
        assert m["max_consecutive_wins"] >= 0

    def test_calmar_ratio(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert isinstance(m["calmar_ratio"], float)

    def test_recovery_factor(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert isinstance(m["recovery_factor"], float)

    def test_return_pct(self, profitable_trades):
        m = MetricsCalculator.compute(profitable_trades)
        assert m["return_pct"] > 0

    def test_trades_per_day(self, mixed_trades):
        m = MetricsCalculator.compute(mixed_trades)
        assert m["trades_per_day"] > 0

    def test_trades_per_month(self, mixed_trades):
        m = MetricsCalculator.compute(mixed_trades)
        assert m["trades_per_month"] > 0


class TestMetricsCalculatorCompare:
    def test_compare_returns_string(self, profitable_trades):
        m1 = MetricsCalculator.compute(profitable_trades)
        results = {"EURUSD": m1}
        output = MetricsCalculator.compare(results)
        assert isinstance(output, str)
        assert "EURUSD" in output
        assert "Trades" in output

    def test_compare_multiple(self, profitable_trades, mixed_trades):
        m1 = MetricsCalculator.compute(profitable_trades)
        m2 = MetricsCalculator.compute(mixed_trades)
        results = {"PROFIT": m1, "MIXED": m2}
        output = MetricsCalculator.compare(results)
        assert "PROFIT" in output
        assert "MIXED" in output

    def test_compare_skips_errors(self):
        results = {"ERR": {"error": "no trades", "n": 0}}
        output = MetricsCalculator.compare(results)
        assert "ERR" not in output
