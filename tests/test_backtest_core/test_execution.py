"""Tests for ExecutionEngine and FillResult."""

from datetime import datetime

import numpy as np
import pytest

from engine_simple.backtest_core.execution import (
    ExecutionEngine,
    FillResult,
    MarketCond,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
)


@pytest.fixture
def engine():
    return ExecutionEngine(latency_ms=50, rng_seed=42)


@pytest.fixture
def engine_no_partial():
    return ExecutionEngine(latency_ms=50, enable_partial_fill=False, rng_seed=42)


@pytest.fixture
def normal_cond():
    return MarketCond(spread_pips=1.5, volatility="normal", bid=1.1045, ask=1.1055)


class TestMarketCond:
    def test_defaults(self):
        cond = MarketCond()
        assert cond.spread_pips == 1.5
        assert cond.volatility == "normal"
        assert cond.is_news is False
        assert cond.is_low_liquidity is False

    def test_custom_values(self):
        cond = MarketCond(spread_pips=3.0, volatility="high", is_news=True, bid=1.10, ask=1.1003)
        assert cond.spread_pips == 3.0
        assert cond.volatility == "high"
        assert cond.is_news is True


class TestFillResult:
    def test_is_buy(self):
        fr = FillResult(
            order_type=ORDER_TYPE_BUY,
            requested_lot=0.1,
            filled_lot=0.1,
            requested_price=1.1050,
            fill_price=1.1052,
            slippage_usd=0.5,
            requoted=False,
            partial_fill=False,
            latency_ms=50.0,
            timestamp=datetime.utcnow(),
            bid=1.1045,
            ask=1.1055,
        )
        assert fr.is_buy() is True
        assert fr.is_sell() is False

    def test_is_sell(self):
        fr = FillResult(
            order_type=ORDER_TYPE_SELL,
            requested_lot=0.1,
            filled_lot=0.1,
            requested_price=1.1050,
            fill_price=1.1048,
            slippage_usd=0.5,
            requoted=False,
            partial_fill=False,
            latency_ms=50.0,
            timestamp=datetime.utcnow(),
            bid=1.1045,
            ask=1.1055,
        )
        assert fr.is_sell() is True
        assert fr.is_buy() is False


class TestExecutionEngineCalculateBidAsk:
    def test_calculate_bid_ask_forex(self):
        bid, ask = ExecutionEngine.calculate_bid_ask(1.1050, 1.5, 0.0001)
        expected_bid = 1.1050 - (1.5 * 0.0001) / 2
        expected_ask = 1.1050 + (1.5 * 0.0001) / 2
        assert bid == round(expected_bid, 5)
        assert ask == round(expected_ask, 5)
        assert bid < ask

    def test_calculate_bid_ask_crypto(self):
        bid, ask = ExecutionEngine.calculate_bid_ask(50000.0, 10.0, 0.01)
        assert bid < ask
        assert ask - bid == pytest.approx(10.0 * 0.01, abs=0.001)


class TestExecutionEngineMarketOrder:
    def test_market_order_fill_buy(self, engine, normal_cond):
        fr = engine.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, normal_cond)
        assert fr.order_type == ORDER_TYPE_BUY
        assert fr.requested_lot == 0.1
        assert fr.filled_lot > 0
        assert fr.filled_lot <= fr.requested_lot
        assert fr.fill_price > 0
        assert fr.latency_ms >= 5.0

    def test_market_order_fill_sell(self, engine, normal_cond):
        fr = engine.execute_market_order(ORDER_TYPE_SELL, 0.1, 1.1050, normal_cond, timestamp=datetime.utcnow())
        assert fr.order_type == ORDER_TYPE_SELL
        assert fr.fill_price > 0

    def test_requote_happens(self, engine):
        high_requote = ExecutionEngine(latency_ms=50, requote_prob=1.0, rng_seed=42)
        cond = MarketCond(spread_pips=1.5, volatility="normal", bid=1.1045, ask=1.1055)
        fr = high_requote.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, cond)
        assert fr.requoted is True

    def test_partial_fill(self, engine):
        low_liq = MarketCond(spread_pips=2.0, volatility="normal", is_low_liquidity=True, bid=1.104, ask=1.106)
        fr = engine.execute_market_order(ORDER_TYPE_BUY, 1.0, 1.1050, low_liq)
        if fr.partial_fill:
            assert fr.filled_lot < fr.requested_lot
        else:
            assert fr.filled_lot == fr.requested_lot

    def test_no_partial_fill_when_disabled(self, engine_no_partial, normal_cond):
        fr = engine_no_partial.execute_market_order(ORDER_TYPE_BUY, 1.0, 1.1050, normal_cond)
        assert fr.partial_fill is False
        assert fr.filled_lot == fr.requested_lot

    def test_market_cond_bid_ask_auto(self, engine):
        cond = MarketCond(spread_pips=1.5, volatility="normal", bid=0, ask=0)
        fr = engine.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, cond)
        assert fr.bid > 0
        assert fr.ask > 0
        assert fr.ask > fr.bid

    def test_latency_is_reasonable(self, engine, normal_cond):
        fr = engine.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, normal_cond)
        assert 5.0 <= fr.latency_ms <= 200.0

    def test_latency_configurable(self, normal_cond):
        fast = ExecutionEngine(latency_ms=10, rng_seed=42)
        slow = ExecutionEngine(latency_ms=250, rng_seed=42)
        fr_fast = fast.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, normal_cond)
        fr_slow = slow.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, normal_cond)
        assert fr_fast.latency_ms < fr_slow.latency_ms

    def test_slippage_usd_positive(self, engine, normal_cond):
        fr = engine.execute_market_order(ORDER_TYPE_BUY, 0.1, 1.1050, normal_cond)
        assert fr.slippage_usd >= 0

    def test_execute_buy_shortcut(self, engine, normal_cond):
        fr = engine.execute_buy(0.1, 1.1050, normal_cond)
        assert fr.is_buy()

    def test_execute_sell_shortcut(self, engine, normal_cond):
        fr = engine.execute_sell(0.1, 1.1050, normal_cond)
        assert fr.is_sell()

    def test_zero_lot_handled(self, engine, normal_cond):
        fr = engine.execute_market_order(ORDER_TYPE_BUY, 0.0, 1.1050, normal_cond)
        assert fr.filled_lot == 0.0
