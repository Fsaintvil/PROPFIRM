"""Tests for SimTrade."""

from datetime import datetime

import pytest

from engine_simple.backtest_core.trade import (
    SimTrade,
    SL,
    TP,
    TIMEOUT,
    STOP,
    TRAILING_LEVELS,
    BE_BUFFER_ATR,
)


@pytest.fixture
def buy_trade():
    return SimTrade(
        symbol="EURUSD",
        action="BUY",
        entry=1.1050,
        sl=1.1000,
        tp=1.1150,
        atr_val=0.0025,
        regime="RANGING",
        bar_idx=100,
        bar_time=datetime(2026, 6, 15, 8, 0),
        lot=0.1,
    )


@pytest.fixture
def sell_trade():
    return SimTrade(
        symbol="EURUSD",
        action="SELL",
        entry=1.1050,
        sl=1.1100,
        tp=1.0950,
        atr_val=0.0025,
        regime="RANGING",
        bar_idx=100,
        bar_time=datetime(2026, 6, 15, 8, 0),
        lot=0.1,
    )


class TestSimTradeOpen:
    def test_open_buy(self, buy_trade):
        assert buy_trade.symbol == "EURUSD"
        assert buy_trade.action == "BUY"
        assert buy_trade.direction == 0
        assert buy_trade.entry == 1.1050
        assert buy_trade.sl == 1.1000
        assert buy_trade.tp == 1.1150
        assert buy_trade.closed is False
        assert buy_trade.result is None

    def test_open_sell(self, sell_trade):
        assert sell_trade.action == "SELL"
        assert sell_trade.direction == 1
        assert sell_trade.closed is False

    def test_lot_default(self):
        t = SimTrade(
            symbol="EURUSD",
            action="BUY",
            entry=1.10,
            sl=1.09,
            tp=1.13,
            atr_val=0.002,
            regime="RANGING",
            bar_idx=0,
            bar_time=datetime.utcnow(),
        )
        assert t.lot == 0.0

    def test_partial_lot_is_half(self, buy_trade):
        assert buy_trade.partial_lot == buy_trade.lot * 0.5

    def test_peak_price_is_entry(self, buy_trade):
        assert buy_trade.peak_price == buy_trade.entry

    def test_trailing_sl_is_initial_sl(self, buy_trade):
        assert buy_trade.trailing_sl == buy_trade.sl

    def test_is_buy_property(self, buy_trade):
        assert buy_trade.is_buy is True
        assert buy_trade.is_sell is False

    def test_is_sell_property(self, sell_trade):
        assert sell_trade.is_sell is True
        assert sell_trade.is_buy is False


class TestSimTradeClose:
    def test_sl_hit_buy(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1060,
            low=1.0995,
            close=1.1005,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.closed is True
        assert buy_trade.result == SL
        assert buy_trade.close_price == buy_trade.trailing_sl
        assert buy_trade.bars_held == 5

    def test_sl_hit_sell(self, sell_trade):
        sell_trade.check_sl_tp(
            high=1.1110,
            low=1.1030,
            close=1.1100,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert sell_trade.closed is True
        assert sell_trade.result == SL

    def test_tp_hit_buy(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1160,
            low=1.1040,
            close=1.1155,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.closed is True
        assert buy_trade.result == TP
        assert buy_trade.close_price == buy_trade.trailing_tp

    def test_tp_hit_sell(self, sell_trade):
        sell_trade.check_sl_tp(
            high=1.1060,
            low=1.0940,
            close=1.0950,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert sell_trade.closed is True
        assert sell_trade.result == TP

    def test_no_hit_no_close(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1070,
            low=1.1030,
            close=1.1055,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.closed is False

    def test_already_closed(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1160,
            low=1.1040,
            close=1.1155,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.closed is True
        buy_trade.check_sl_tp(
            high=1.1200,
            low=1.1000,
            close=1.1100,
            bar_idx=110,
            bar_time=datetime(2026, 6, 15, 12, 0),
        )
        assert buy_trade.close_bar == 105

    def test_gap_jump_sl_buy(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1080,
            low=1.1060,
            close=1.1070,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
            gap_open=1.0990,
        )
        assert buy_trade.closed is True
        assert buy_trade.result == SL
        assert buy_trade.close_price == 1.0990

    def test_gap_jump_sl_sell(self, sell_trade):
        sell_trade.check_sl_tp(
            high=1.1080,
            low=1.1060,
            close=1.1070,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
            gap_open=1.1110,
        )
        assert sell_trade.closed is True
        assert sell_trade.result == SL

    def test_gap_jump_tp_buy(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1080,
            low=1.1060,
            close=1.1070,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
            gap_open=1.1160,
        )
        assert buy_trade.closed is True
        assert buy_trade.result == TP

    def test_gap_jump_tp_sell(self, sell_trade):
        sell_trade.check_sl_tp(
            high=1.1080,
            low=1.1060,
            close=1.1070,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
            gap_open=1.0940,
        )
        assert sell_trade.closed is True
        assert sell_trade.result == TP


class TestSimTradeFloatingPnl:
    def test_get_floating_pnl_buy(self, buy_trade):
        pnl = buy_trade.get_floating_pnl(1.1070)
        # 1.1070 - 1.1050 = 0.0020 = 20 pips * $10 * 0.1 = $20
        assert pnl == pytest.approx(20.0, abs=1.0)

    def test_get_floating_pnl_sell(self, sell_trade):
        pnl = sell_trade.get_floating_pnl(1.1030)
        # 1.1050 - 1.1030 = 0.0020 = 20 pips * $10 * 0.1 = $20
        assert pnl == pytest.approx(20.0, abs=1.0)

    def test_get_floating_pnl_negative(self, buy_trade):
        pnl = buy_trade.get_floating_pnl(1.1030)
        assert pnl < 0


class TestSimTradeTrailing:
    def test_trailing_no_update_below_threshold(self, buy_trade):
        buy_trade.update_peak(1.1060, 1.1040)
        buy_trade.update_trailing(0.0025)
        assert buy_trade.trailing_sl == buy_trade.sl

    def test_trailing_updates_after_first_lock(self, buy_trade):
        buy_trade.update_peak(1.1080, 1.1040)
        buy_trade.update_trailing(atr_v=0.0025)
        # profit = (1.1080 - 1.1050) / 0.0025 = 1.2 ATR > 0.5 (1er seuil)
        # trailing dist = 0.15 * 0.0025 = 0.000375 (RANGING 2e niveau v3)
        expected_sl = 1.1080 - 0.15 * 0.0025
        assert buy_trade.trailing_sl > buy_trade.sl
        assert buy_trade.trailing_sl == pytest.approx(expected_sl, abs=0.0001)

    def test_trailing_multiple_levels(self, buy_trade):
        # profit = (1.1110 - 1.1050) / 0.0025 = 2.4 ATR > 2.0 (3e seuil)
        # trailing dist = 0.10 * 0.0025 = 0.00025 (RANGING 3e niveau v3)
        buy_trade.update_peak(1.1110, 1.1040)
        buy_trade.update_trailing(atr_v=0.0025)
        expected_sl = 1.1110 - 0.10 * 0.0025
        assert buy_trade.trailing_sl == pytest.approx(expected_sl, abs=0.0001)

    def test_trailing_highest_level(self, buy_trade):
        # profit = (1.1180 - 1.1050) / 0.0025 = 5.2 ATR > 4.0
        # trailing dist = 0.05 * 0.0025 = 0.000125 (RANGING 4e niveau v3)
        buy_trade.update_peak(1.1180, 1.1040)
        buy_trade.update_trailing(atr_v=0.0025)
        expected_sl = 1.1180 - 0.05 * 0.0025
        assert buy_trade.trailing_sl == pytest.approx(expected_sl, abs=0.0001)

    def test_trailing_never_goes_back(self, buy_trade):
        buy_trade.update_peak(1.1080, 1.1040)
        buy_trade.update_trailing(atr_v=0.0025)
        sl_after_first = buy_trade.trailing_sl
        buy_trade.update_peak(1.1080, 1.1040)
        buy_trade.update_trailing(atr_v=0.0025)
        assert buy_trade.trailing_sl == sl_after_first

    def test_trailing_sell(self, sell_trade):
        sell_trade.update_peak(1.1060, 1.1020)
        sell_trade.update_trailing(atr_v=0.0025)
        # profit = (1.1050 - 1.1020) / 0.0025 = 1.2 ATR > 0.5 (1er seuil)
        # trailing dist = 0.15 * 0.0025 = 0.000375 (RANGING 2e niveau v3)
        expected_sl = 1.1020 + 0.15 * 0.0025
        assert sell_trade.trailing_sl < sell_trade.sl
        assert sell_trade.trailing_sl == pytest.approx(expected_sl, abs=0.0001)

    def test_trailing_zero_atr_does_nothing(self, buy_trade):
        buy_trade.update_peak(1.1080, 1.1040)
        buy_trade.update_trailing(atr_v=0)
        assert buy_trade.trailing_sl == buy_trade.sl


class TestSimTradePartialTP:
    def test_partial_tp_triggers(self, buy_trade):
        buy_trade.update_peak(1.1130, 1.1040)
        buy_trade.check_partial_tp(atr_v=0.0025)
        # progress = (1.1130 - 1.1050) / (1.1150 - 1.1050) = 0.80 > 0.60
        assert buy_trade.partial_closed is True
        assert buy_trade.trailing_sl > buy_trade.sl

    def test_partial_tp_not_triggered_below_60(self, buy_trade):
        buy_trade.update_peak(1.1080, 1.1040)
        buy_trade.check_partial_tp(atr_v=0.0025)
        # progress = (1.1080 - 1.1050) / (1.1150 - 1.1050) = 0.30 < 0.60
        assert buy_trade.partial_closed is False

    def test_partial_tp_already_closed(self, buy_trade):
        buy_trade.closed = True
        buy_trade.check_partial_tp(atr_v=0.0025)
        assert buy_trade.partial_closed is False

    def test_partial_tp_only_once(self, buy_trade):
        buy_trade.update_peak(1.1130, 1.1040)
        buy_trade.check_partial_tp(atr_v=0.0025)
        assert buy_trade.partial_closed is True
        sl_after = buy_trade.trailing_sl
        buy_trade.check_partial_tp(atr_v=0.0025)
        assert buy_trade.trailing_sl == sl_after

    def test_partial_tp_sell(self, sell_trade):
        sell_trade.update_peak(1.1060, 1.0970)
        sell_trade.check_partial_tp(atr_v=0.0025)
        assert sell_trade.partial_closed is True

    def test_partial_tp_zero_atr(self, buy_trade):
        buy_trade.update_peak(1.1130, 1.1040)
        buy_trade.check_partial_tp(atr_v=0)
        assert buy_trade.partial_closed is False


class TestSimTradeTimeout:
    def test_timeout_after_max_bars(self, buy_trade):
        buy_trade.check_timeout(250, datetime(2026, 6, 20, 8, 0), max_bars=120)
        assert buy_trade.closed is True
        assert buy_trade.result == TIMEOUT

    def test_no_timeout_before_max_bars(self, buy_trade):
        buy_trade.check_timeout(110, datetime(2026, 6, 16, 8, 0), max_bars=120)
        assert buy_trade.closed is False

    def test_timeout_not_triggered_on_closed(self, buy_trade):
        buy_trade.closed = True
        buy_trade.check_timeout(250, datetime(2026, 6, 20, 8, 0))
        assert buy_trade.result is None


class TestSimTradeForceClose:
    def test_force_close(self, buy_trade):
        buy_trade.force_close(1.1030, 120, datetime(2026, 6, 16, 12, 0), reason=STOP)
        assert buy_trade.closed is True
        assert buy_trade.result == STOP
        assert buy_trade.close_price == 1.1030
        assert buy_trade.bars_held == 20
        assert isinstance(buy_trade.profit_usd, float)

    def test_force_close_no_double_close(self, buy_trade):
        buy_trade.force_close(1.1030, 120, datetime(2026, 6, 16, 12, 0))
        buy_trade.force_close(1.1050, 130, datetime(2026, 6, 16, 14, 0))
        assert buy_trade.close_bar == 120


class TestSimTradePnl:
    def test_profit_calculated_on_close(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1160,
            low=1.1040,
            close=1.1155,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.profit_usd != 0.0
        assert isinstance(buy_trade.profit_usd_cost, float)
        assert isinstance(buy_trade.profit_pct, float)

    def test_gross_pnl_property(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1160,
            low=1.1040,
            close=1.1155,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.gross_pnl == buy_trade.profit_usd

    def test_net_pnl_property(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1160,
            low=1.1040,
            close=1.1155,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        assert buy_trade.net_pnl == buy_trade.profit_usd_cost

    def test_total_cost_property(self, buy_trade):
        assert isinstance(buy_trade.total_cost, float)


class TestSimTradeToDict:
    def test_to_dict_keys(self, buy_trade):
        d = buy_trade.to_dict()
        assert "symbol" in d
        assert "action" in d
        assert "entry" in d
        assert "sl" in d
        assert "tp" in d
        assert "result" in d
        assert "profit_usd" in d
        assert "total_cost" in d

    def test_to_dict_values(self, buy_trade):
        buy_trade.check_sl_tp(
            high=1.1160,
            low=1.1040,
            close=1.1155,
            bar_idx=105,
            bar_time=datetime(2026, 6, 15, 10, 0),
        )
        d = buy_trade.to_dict()
        assert d["action"] == "BUY"
        assert d["result"] == "TP"
        assert d["closed"] is True
