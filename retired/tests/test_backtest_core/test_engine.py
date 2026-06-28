"""Tests for BacktestEngine, BacktestConfig, BacktestResult."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from engine_simple.backtest_core.engine import BacktestEngine, BacktestConfig, BacktestResult
from engine_simple.backtest_core.strategies.base import Strategy, Signal
from engine_simple.backtest_core.trade import SimTrade


class DummyStrategy(Strategy):
    """Stratégie factice : génère des signaux à intervalles réguliers."""

    def __init__(self, every_n=10, action="BUY", score=0.8):
        self.every_n = every_n
        self._action = action
        self._score = score
        self.momentum_period = 20

    def name(self):
        return "DummyStrategy"

    def generate(self, bar_idx, data, regime, open_positions, timestamp=None):
        if bar_idx % self.every_n == 0 and bar_idx > 50:
            close = data["close"][bar_idx]
            atr_val = float(np.std(data["close"][max(0, bar_idx - 20) : bar_idx + 1]) * 0.5) or 0.001
            action = self._action
            sl = close - 2.0 * atr_val if action == "BUY" else close + 2.0 * atr_val
            tp = close + 5.0 * atr_val if action == "BUY" else close - 5.0 * atr_val
            return Signal(
                symbol=data.get("symbol", "EURUSD"),
                action=action,
                score=self._score,
                entry_price=close,
                sl=sl,
                tp=tp,
                regime=regime,
                timestamp=timestamp or datetime.utcnow(),
                strategy=self.name(),
            )
        return None


@pytest.fixture
def sample_data():
    """Génère 300 barres de données OHLCV synthétiques."""
    np.random.seed(42)
    n = 300
    close = 1.10 + np.cumsum(np.random.randn(n) * 0.002)
    close = np.maximum(close, 1.0)
    high = close + np.abs(np.random.randn(n)) * 0.003
    low = close - np.abs(np.random.randn(n)) * 0.003
    open_p = close + np.random.randn(n) * 0.001
    df = pd.DataFrame(
        {
            "timestamp": [datetime(2026, 1, 1) + pd.Timedelta(hours=i) for i in range(n)],
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.randint(100, 1000, n),
        }
    )
    return df


@pytest.fixture
def engine():
    return BacktestEngine()


@pytest.fixture
def strategy():
    return DummyStrategy(every_n=15, action="BUY")


class TestBacktestConfig:
    def test_defaults(self):
        cfg = BacktestConfig()
        assert cfg.initial_balance == 200_000.0
        assert cfg.risk_per_trade == 0.0044
        assert cfg.max_positions == 5
        assert cfg.max_positions_per_symbol == 2
        assert cfg.min_bars_between_trades == 5
        assert cfg.min_bars_warmup == 80
        assert cfg.latency_ms == 100
        assert cfg.requote_prob == 0.02
        assert cfg.enable_partial_fill is True

    def test_timeout_bars_default(self):
        cfg = BacktestConfig()
        assert cfg.timeout_bars == {"H1": 120, "H4": 60, "D1": 30}

    def test_custom_config(self):
        cfg = BacktestConfig(initial_balance=100_000, latency_ms=50)
        assert cfg.initial_balance == 100_000.0
        assert cfg.latency_ms == 50

    def test_dict_construction(self):
        cfg = BacktestConfig(**{"initial_balance": 50_000, "max_positions": 3})
        assert cfg.initial_balance == 50_000.0
        assert cfg.max_positions == 3


class TestBacktestResult:
    def test_empty_result(self):
        cfg = BacktestConfig()
        r = BacktestResult(symbol="EURUSD", timeframe="H1", strategy_name="Test", config=cfg)
        assert r.total_trades == 0
        assert r.win_rate == 0.0
        assert r.net_profit == 0.0

    def test_closed_trades_property(self):
        cfg = BacktestConfig()
        r = BacktestResult(symbol="EURUSD", timeframe="H1", strategy_name="Test", config=cfg)
        t1 = SimTrade(
            "EURUSD",
            "BUY",
            1.10,
            1.09,
            1.13,
            0.002,
            "RANGING",
            0,
            datetime.utcnow(),
            lot=0.1,
        )
        t1.closed = True
        t2 = SimTrade(
            "EURUSD",
            "SELL",
            1.10,
            1.11,
            1.09,
            0.002,
            "RANGING",
            0,
            datetime.utcnow(),
            lot=0.1,
        )
        t2.closed = False
        r.trades = [t1, t2]
        assert len(r.closed_trades) == 1
        assert r.closed_trades[0] == t1


class TestBacktestEngine:
    def test_run_simple(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert isinstance(result, BacktestResult)
        assert result.symbol == "EURUSD"
        assert result.strategy_name == "DummyStrategy"
        assert result.total_trades >= 0
        assert len(result.equity_curve) > 0
        assert len(result.dates) > 0
        assert result.metrics is not None

    def test_run_with_signals(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        if result.total_trades > 0:
            t = result.trades[0]
            assert t.symbol == "EURUSD"

    def test_max_positions(self, sample_data):
        engine = BacktestEngine({"initial_balance": 200_000, "max_positions": 2})
        strategy = DummyStrategy(every_n=5, action="BUY")
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert result.config.max_positions == 2

    def test_min_bars_between_trades(self, sample_data):
        engine = BacktestEngine({"initial_balance": 200_000, "min_bars_between_trades": 20})
        strategy = DummyStrategy(every_n=3, action="BUY")
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert result.config.min_bars_between_trades == 20

    def test_rr_filter(self, sample_data):
        engine = BacktestEngine({"initial_balance": 200_000})
        strat = DummyStrategy(every_n=15, action="BUY")
        result = engine.run("EURUSD", strat, sample_data, timeframe="H1")
        assert result.n_rejected >= 0

    def test_equity_tracking(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert len(result.equity_curve) > 0
        assert len(result.balance_curve) > 0
        assert all(e > 0 for e in result.equity_curve)

    def test_dd_tracking(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert len(result.dd_curve) > 0
        assert all(d >= -0.5 for d in result.dd_curve)
        assert result.metrics["max_dd_pct"] >= 0

    def test_run_multi(self, engine, strategy, sample_data):
        data_dict = {"EURUSD": sample_data, "GBPUSD": sample_data.copy()}
        results = engine.run_multi(["EURUSD", "GBPUSD"], strategy, data_dict, timeframe="H1")
        assert isinstance(results, dict)
        assert "EURUSD" in results
        assert "GBPUSD" in results
        assert isinstance(results["EURUSD"], BacktestResult)

    def test_run_multi_missing_symbol(self, engine, strategy, sample_data):
        data_dict = {"EURUSD": sample_data}
        results = engine.run_multi(["EURUSD", "MISSING"], strategy, data_dict)
        assert "EURUSD" in results
        assert "MISSING" not in results

    def test_run_without_signals(self, sample_data):
        engine = BacktestEngine()
        strategy = DummyStrategy(every_n=999, action="BUY")
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert result.total_trades == 0

    def test_metrics_computed(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        metrics = result.metrics
        assert "n" in metrics
        assert "win_rate" in metrics
        assert "profit_factor" in metrics
        assert "max_dd_pct" in metrics
        assert "sharpe_ratio" in metrics

    def test_total_costs_non_negative(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert result.total_costs >= 0

    def test_n_signals(self, engine, strategy, sample_data):
        result = engine.run("EURUSD", strategy, sample_data, timeframe="H1")
        assert result.n_signals >= 0
        assert result.n_rejected >= 0
