"""Tests for CostModel and pip utilities."""

from datetime import datetime, time

import numpy as np
import pytest

from engine_simple.backtest_core.costs import (
    CostModel,
    get_pip_info,
    get_contract_size,
    DEFAULT_SPREAD_PIPS,
    DEFAULT_SWAP,
)


@pytest.fixture
def cost_model():
    return CostModel()


@pytest.fixture
def cost_model_custom():
    return CostModel(
        {
            "spread": {"EURUSD": 2.0},
            "commission": {"forex": (7.0, "per_lot")},
        }
    )


class TestGetPipInfo:
    def test_pip_info_forex(self):
        pip_size, pip_value = get_pip_info("EURUSD")
        assert pip_size == 0.0001
        assert pip_value == 10.0

    def test_pip_info_xau(self):
        pip_size, pip_value = get_pip_info("XAUUSD")
        assert pip_size == 0.01
        assert pip_value == 10.0

    def test_pip_info_indices(self):
        pip_size, pip_value = get_pip_info("US500.cash")
        assert pip_size == 1.0  # 1 index point = 1 pip
        assert pip_value == 1.0  # $1/point/lot (USD-denominated)

    def test_pip_info_jp225(self):
        pip_size, pip_value = get_pip_info("JP225.cash")
        assert pip_size == 1.0  # 1 index point = 1 pip
        assert pip_value == 0.0091  # ¥1 ≈ $0.0091 (USDJPY≈110)

    def test_pip_info_crypto(self):
        pip_size, pip_value = get_pip_info("BTCUSD")
        assert pip_size == 0.01
        assert pip_value == 1.0

    def test_pip_info_oil(self):
        pip_size, pip_value = get_pip_info("USOIL.cash")
        assert pip_size == 0.01
        assert pip_value == 10.0

    def test_pip_info_unknown_symbol(self):
        pip_size, pip_value = get_pip_info("ZZZZZZ")
        assert pip_size == 0.0001
        assert pip_value == 10.0


class TestGetContractSize:
    def test_forex_contract(self):
        assert get_contract_size("EURUSD") == 100_000.0

    def test_xau_contract(self):
        assert get_contract_size("XAUUSD") == 100.0

    def test_indices_contract(self):
        assert get_contract_size("US500.cash") == 1.0

    def test_crypto_contract(self):
        assert get_contract_size("BTCUSD") == 1.0


class TestCostModelSpread:
    def test_spread_static_default(self, cost_model):
        sp = cost_model.get_spread("EURUSD")
        assert sp == DEFAULT_SPREAD_PIPS.get("EURUSD", 1.5)
        assert sp > 0

    def test_spread_static_xau(self, cost_model):
        sp = cost_model.get_spread("XAUUSD")
        assert sp == DEFAULT_SPREAD_PIPS.get("XAUUSD", 5.0)

    def test_spread_historical_override(self, cost_model):
        sp = cost_model.get_spread("EURUSD", historical_spread=25.0)
        # 25 points / 10 = 2.5 pips
        assert sp == 2.5

    def test_spread_news_widening(self, cost_model):
        normal = cost_model.get_spread("EURUSD")
        news = cost_model.get_spread("EURUSD", is_news=True)
        assert news >= normal

    def test_spread_low_liquidity(self, cost_model):
        dt = datetime(2026, 6, 20, 23, 0)
        normal = cost_model.get_spread("EURUSD")
        low_liq = cost_model.get_spread("EURUSD", timestamp=dt)
        assert low_liq >= normal

    def test_spread_weekend(self, cost_model):
        dt = datetime(2026, 6, 21, 12, 0)
        assert dt.weekday() == 6
        weekend = cost_model.get_spread("EURUSD", timestamp=dt)
        normal = cost_model.get_spread("EURUSD")
        assert weekend >= normal

    def test_spread_high_volatility(self, cost_model):
        normal = cost_model.get_spread("EURUSD")
        high_vol = cost_model.get_spread("EURUSD", volatility="high")
        assert high_vol >= normal

    def test_spread_unknown_symbol(self, cost_model):
        sp = cost_model.get_spread("ZZZZZZ")
        assert sp == 2.0


class TestCostModelCommission:
    def test_commission_forex(self, cost_model):
        comm = cost_model.get_commission("EURUSD", 1.0, 1.1050)
        assert comm == 3.5 * 1.0 * 2
        assert comm > 0

    def test_commission_crypto(self, cost_model):
        comm = cost_model.get_commission("BTCUSD", 1.0, 50000.0)
        expected = 50000.0 * 1.0 * 0.0007 * 2
        assert comm == pytest.approx(expected, abs=0.01)
        assert comm > 0

    def test_commission_metals(self, cost_model):
        comm = cost_model.get_commission("XAUUSD", 0.5, 2000.0)
        assert comm == 5.0 * 0.5 * 2

    def test_commission_indices(self, cost_model):
        comm = cost_model.get_commission("US500.cash", 1.0, 5000.0)
        assert comm == 2.0 * 1.0 * 2

    def test_commission_crypto_small_lot(self, cost_model):
        comm = cost_model.get_commission("BTCUSD", 0.01, 50000.0)
        assert comm > 0
        assert comm < 10.0

    def test_commission_custom(self, cost_model_custom):
        comm = cost_model_custom.get_commission("EURUSD", 1.0, 1.1050)
        assert comm == 7.0 * 1.0 * 2


class TestCostModelSwap:
    def test_swap_overnight_forex(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 16, 12, 0)
        swap = cost_model.get_swap("EURUSD", 0, entry, exit_t)
        assert isinstance(swap, float)

    def test_swap_no_swap_crypto(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 20, 12, 0)
        swap = cost_model.get_swap("BTCUSD", 0, entry, exit_t)
        assert swap == 0.0

    def test_swap_zero_rate(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 16, 12, 0)
        swap = cost_model.get_swap("BTCUSD", 0, entry, exit_t)
        assert swap == 0.0

    def test_swap_same_day(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 15, 20, 0)
        swap = cost_model.get_swap("EURUSD", 0, entry, exit_t)
        assert swap == 0.0

    def test_swap_long_vs_short(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 17, 12, 0)
        swap_long = cost_model.get_swap("EURUSD", 0, entry, exit_t)
        swap_short = cost_model.get_swap("EURUSD", 1, entry, exit_t)
        assert isinstance(swap_long, float)
        assert isinstance(swap_short, float)

    def test_swap_unknown_symbol(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 16, 12, 0)
        swap = cost_model.get_swap("ZZZZZZ", 0, entry, exit_t)
        assert swap == 0.0


class TestCostModelSlippage:
    def test_slippage_normal(self, cost_model):
        slip = cost_model.get_slippage("EURUSD", 1.1050, volatility="normal")
        assert slip >= 0

    def test_slippage_high_vol(self, cost_model):
        np.random.seed(42)
        slip = cost_model.get_slippage("EURUSD", 1.1050, volatility="high")
        assert slip >= 0

    def test_slippage_news(self, cost_model):
        np.random.seed(42)
        slip = cost_model.get_slippage("EURUSD", 1.1050, is_news=True)
        assert slip >= 0

    def test_slippage_crypto(self, cost_model):
        np.random.seed(42)
        slip = cost_model.get_slippage("BTCUSD", 50000.0)
        assert slip >= 0

    def test_slippage_reproducible(self, cost_model):
        np.random.seed(123)
        s1 = cost_model.get_slippage("EURUSD", 1.1050)
        np.random.seed(123)
        s2 = cost_model.get_slippage("EURUSD", 1.1050)
        assert s1 == s2


class TestCostModelTotalCost:
    def test_total_cost(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 15, 20, 0)
        tc = cost_model.get_total_cost(
            "EURUSD",
            0,
            1.0,
            1.1050,
            1.1100,
            entry,
            exit_t,
        )
        assert "spread_cost" in tc
        assert "commission" in tc
        assert "swap" in tc
        assert "slippage_entry" in tc
        assert "slippage_exit" in tc
        assert "total" in tc
        assert tc["total"] > 0
        assert tc["total"] == round(
            tc["spread_cost"] + tc["commission"] + tc["swap"] + tc["slippage_entry"] + tc["slippage_exit"], 2
        )

    def test_total_cost_includes_all_components(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 16, 12, 0)
        tc = cost_model.get_total_cost(
            "XAUUSD",
            0,
            0.1,
            2000.0,
            2010.0,
            entry,
            exit_t,
        )
        assert tc["spread_cost"] > 0
        assert tc["commission"] > 0
        assert isinstance(tc["swap"], float)
        assert tc["total"] == round(
            tc["spread_cost"] + tc["commission"] + tc["swap"] + tc["slippage_entry"] + tc["slippage_exit"], 2
        )

    def test_total_cost_no_swap(self, cost_model):
        entry = datetime(2026, 6, 15, 8, 0)
        exit_t = datetime(2026, 6, 15, 12, 0)
        tc = cost_model.get_total_cost(
            "BTCUSD",
            0,
            0.1,
            50000.0,
            51000.0,
            entry,
            exit_t,
        )
        assert tc["swap"] == 0

    def test_to_dict(self, cost_model):
        d = cost_model.to_dict()
        assert "spread" in d
        assert "commission" in d
        assert "slippage" in d
        assert "swap" in d
        assert "n_symbols" in d["swap"]
