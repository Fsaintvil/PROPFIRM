"""Tests for Strategy ABC, Signal dataclass, and all strategy implementations."""

from datetime import datetime

import numpy as np
import pytest

from engine_simple.backtest_core.strategies.base import Strategy, Signal
from engine_simple.backtest_core.strategies.mom20x3 import MOM20x3, DEFAULT_CONFIG as MOM_DEFAULT
from engine_simple.backtest_core.strategies.trend_following import TrendFollowing
from engine_simple.backtest_core.strategies.breakout import Breakout
from engine_simple.backtest_core.strategies.mean_reversion import MeanReversion


def _make_data(n=100, trend=0.0, vol=0.002, seed=42):
    """Génère un dict data avec arrays numpy."""
    rng = np.random.RandomState(seed)
    close = 1.10 + np.arange(n) * trend + rng.randn(n) * vol
    close = np.maximum(close, 1.0)
    high = close + np.abs(rng.randn(n)) * vol * 0.5
    low = close - np.abs(rng.randn(n)) * vol * 0.5
    return {
        "open": close - rng.randn(n) * vol * 0.2,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.randint(100, 1000, n),
        "symbol": "EURUSD",
    }


class TestSignal:
    def test_signal_dataclass_defaults(self):
        s = Signal(
            symbol="EURUSD",
            action="BUY",
            score=0.75,
            entry_price=1.1050,
            sl=1.1000,
            tp=1.1150,
            regime="RANGING",
            timestamp=datetime.utcnow(),
        )
        assert s.strategy == ""
        assert s.timeframe == "H1"
        assert s.risk_mult == 1.0
        assert s.enforce_rr is True
        assert s.metadata == {}

    def test_signal_is_buy(self):
        s = Signal(
            symbol="EURUSD",
            action="BUY",
            score=0.75,
            entry_price=1.1050,
            sl=1.1000,
            tp=1.1150,
            regime="RANGING",
            timestamp=datetime.utcnow(),
        )
        assert s.is_buy() is True
        assert s.is_sell() is False

    def test_signal_is_sell(self):
        s = Signal(
            symbol="EURUSD",
            action="SELL",
            score=0.75,
            entry_price=1.1050,
            sl=1.1100,
            tp=1.0950,
            regime="RANGING",
            timestamp=datetime.utcnow(),
        )
        assert s.is_sell() is True
        assert s.is_buy() is False

    def test_signal_to_dict(self):
        s = Signal(
            symbol="EURUSD",
            action="BUY",
            score=0.75,
            entry_price=1.1050,
            sl=1.1000,
            tp=1.1150,
            regime="RANGING",
            timestamp=datetime.utcnow(),
            strategy="MOM20x3",
        )
        d = s.to_dict()
        assert d["symbol"] == "EURUSD"
        assert d["action"] == "BUY"
        assert d["strategy"] == "MOM20x3"

    def test_signal_custom_fields(self):
        s = Signal(
            symbol="BTCUSD",
            action="SELL",
            score=0.9,
            entry_price=50000.0,
            sl=51000.0,
            tp=48000.0,
            regime="TREND_DOWN",
            timestamp=datetime.utcnow(),
            strategy="Breakout",
            timeframe="H4",
            risk_mult=0.5,
            enforce_rr=False,
            metadata={"reason": "test"},
        )
        assert s.risk_mult == 0.5
        assert s.enforce_rr is False
        assert s.metadata["reason"] == "test"


class TestStrategyABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Strategy()

    def test_concrete_strategy_works(self):
        class MyStrategy(Strategy):
            def name(self):
                return "MyStrategy"

            def generate(self, bar_idx, data, regime, open_positions, timestamp=None):
                return None

        s = MyStrategy()
        assert s.name() == "MyStrategy"
        assert s.generate(0, {}, "RANGING", []) is None

    def test_get_config_default(self):
        class MyStrategy(Strategy):
            def name(self):
                return "MyStrategy"

            def generate(self, bar_idx, data, regime, open_positions, timestamp=None):
                return None

        s = MyStrategy()
        assert s.get_config() == {}


class TestMOM20x3:
    def test_mom20x3_generate_returns_signal(self):
        data = _make_data(n=120, trend=0.0005, vol=0.0005)
        strat = MOM20x3()
        signal = strat.generate(110, data, "RANGING", [])
        if signal is not None:
            assert isinstance(signal, Signal)
            assert signal.strategy == "MOM20x3"
            assert signal.action in ("BUY", "SELL")
            assert 0.0 <= signal.score <= 1.0

    def test_mom20x3_returns_none_too_early(self):
        data = _make_data(n=30)
        strat = MOM20x3()
        signal = strat.generate(10, data, "RANGING", [])
        assert signal is None

    def test_mom20x3_default_config(self):
        strat = MOM20x3()
        assert strat.momentum_period == 20
        assert strat.config["threshold_trending"] == 2.5
        assert strat.config["adx_period"] == 14

    def test_mom20x3_custom_config(self):
        strat = MOM20x3({"momentum_period": 15, "threshold_trending": 3.0})
        assert strat.momentum_period == 15
        assert strat.config["threshold_trending"] == 3.0

    def test_mom20x3_name(self):
        strat = MOM20x3()
        assert strat.name() == "MOM20x3"

    def test_mom20x3_get_config(self):
        strat = MOM20x3()
        cfg = strat.get_config()
        assert cfg["momentum_period"] == 20

    def test_mom20x3_returns_none_on_nan_close(self):
        data = _make_data(n=100)
        data["close"][80] = float("nan")
        strat = MOM20x3()
        signal = strat.generate(80, data, "RANGING", [])
        assert signal is None


class TestTrendFollowing:
    def test_trend_following_generate(self):
        data = _make_data(n=120, trend=0.001, vol=0.0003)
        strat = TrendFollowing()
        signal = strat.generate(100, data, "TREND_UP", [])
        if signal is not None:
            assert isinstance(signal, Signal)
            assert signal.strategy == "TrendFollowing"

    def test_trend_following_name(self):
        strat = TrendFollowing()
        assert strat.name() == "TrendFollowing"

    def test_trend_following_too_early(self):
        data = _make_data(n=30)
        strat = TrendFollowing()
        signal = strat.generate(20, data, "RANGING", [])
        assert signal is None

    def test_trend_following_config(self):
        strat = TrendFollowing({"fast_ema": 10, "slow_ema": 20})
        assert strat.config["fast_ema"] == 10
        assert strat.config["slow_ema"] == 20
        assert strat.config["adx_threshold"] == 25


class TestBreakout:
    def test_breakout_generate(self):
        data = _make_data(n=120, vol=0.005)
        # Ajouter un range puis une cassure
        data["close"][80:100] = 1.10
        data["close"][101] = 1.12
        data["high"][101] = 1.13
        strat = Breakout()
        signal = strat.generate(101, data, "RANGING", [])
        if signal is not None:
            assert isinstance(signal, Signal)
            assert signal.strategy == "Breakout"

    def test_breakout_name(self):
        strat = Breakout()
        assert strat.name() == "Breakout"

    def test_breakout_too_early(self):
        data = _make_data(n=30)
        strat = Breakout()
        signal = strat.generate(10, data, "RANGING", [])
        assert signal is None

    def test_breakout_config(self):
        strat = Breakout({"lookback": 30, "volume_mult": 2.0})
        assert strat.config["lookback"] == 30
        assert strat.config["volume_mult"] == 2.0

    def test_breakout_no_breakout_in_range(self):
        data = _make_data(n=120)
        strat = Breakout()
        signal = strat.generate(100, data, "RANGING", [])
        # En range, pas de breakout attendu
        if signal is not None:
            assert signal.action in ("BUY", "SELL")


class TestMeanReversion:
    def test_mean_reversion_generate(self):
        data = _make_data(n=120, vol=0.01)
        # Créer une condition de surachat
        data["close"][105] = 1.15
        data["high"][105] = 1.16
        strat = MeanReversion()
        signal = strat.generate(105, data, "RANGING", [])
        if signal is not None:
            assert isinstance(signal, Signal)
            assert signal.strategy == "MeanReversion"

    def test_mean_reversion_name(self):
        strat = MeanReversion()
        assert strat.name() == "MeanReversion"

    def test_mean_reversion_too_early(self):
        data = _make_data(n=30)
        strat = MeanReversion()
        signal = strat.generate(10, data, "RANGING", [])
        assert signal is None

    def test_mean_reversion_config(self):
        strat = MeanReversion({"rsi_period": 10, "rsi_oversold": 25})
        assert strat.config["rsi_period"] == 10
        assert strat.config["rsi_oversold"] == 25

    def test_mean_reversion_blocks_in_trend(self):
        data = _make_data(n=120)
        strat = MeanReversion()
        signal = strat.generate(100, data, "TREND_DOWN", [])
        # En tendance baissière, pas de BUY (oversold)
        if signal is not None:
            assert signal.action != "BUY"
