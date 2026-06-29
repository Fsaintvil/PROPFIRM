"""Tests pour TradeExecutor, PositionTracker, trailing et partial TP"""

import os
import sys
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

import config_simple as cfg
from engine_simple.ftmo_protector import FTMOProtector
from engine_simple.mt5_connector import MT5Connector
from engine_simple.position_tracker import PositionTracker
from engine_simple.trade_executor import TradeExecutor
from engine_simple.trade_journal import TradeJournal


def make_mock_mt5():
    mock = MagicMock(spec=MT5Connector)
    tick = MagicMock(ask=1.10005, bid=1.10000)
    tick.time = time.time()
    mock.get_tick.return_value = tick
    mock.get_symbol_info.return_value = MagicMock(point=0.00001, digits=5, ask=1.10005, bid=1.10000)
    mock.get_account_info.return_value = MagicMock(equity=200000, balance=200000)
    mock.get_positions.return_value = []
    mock.get_pending_orders.return_value = []
    mock.get_history.return_value = []
    mock.health_check.return_value = True
    mock.ORDER_TYPE_BUY = 0
    mock.ORDER_TYPE_SELL = 1
    mock.ORDER_FILLING_IOC = 2
    mock.ORDER_TIME_GTC = 0
    mock.ORDER_TIME_DAY = 1
    mock.calc_profit.return_value = -50.0
    return mock


def make_ftmo(mt5_mock):
    return FTMOProtector(
        mt5_mock,
        dict(
            MAX_POSITIONS=cfg.MAX_POSITIONS,
            MAX_TRADES_PER_DAY=cfg.MAX_TRADES_PER_DAY,
            LOT_SIZE=cfg.LOT_SIZE,
            RISK_PER_TRADE=cfg.RISK_PER_TRADE,
            COOLDOWN_MINUTES=cfg.COOLDOWN_MINUTES,
            MAX_DAILY_LOSS_PCT=cfg.MAX_DAILY_LOSS_PCT,
            INITIAL_BALANCE=200000,
            MAX_DD_PCT=cfg.MAX_DD_PCT,
            PROFIT_TARGET_PCT=cfg.PROFIT_TARGET_PCT,
            CONSISTENCY_MAX_PCT=cfg.CONSISTENCY_MAX_PCT,
            MIN_TRADING_DAYS=cfg.MIN_TRADING_DAYS,
            MAGIC=cfg.ROBOT_MAGIC,
            MAX_SPREAD_POINTS=cfg.MAX_SPREAD_POINTS,
            MAX_RISK_AMOUNT=cfg.MAX_RISK_AMOUNT,
            SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
        ),
    )


class TestTradeExecutor:
    def test_execute_buy_success(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        journal = MagicMock(spec=TradeJournal)
        tracker = MagicMock(spec=PositionTracker)
        signals = MagicMock()
        adaptive = MagicMock()
        adaptive.build_dl_features.return_value = {}
        adaptive.learner = MagicMock()
        adaptive.learner.get_summary.return_value = "test"
        ftmo._calc_sl_tp = MagicMock(return_value=(1.095, 1.115))
        ftmo.calculate_lot = MagicMock(return_value=0.05)
        result = MagicMock()
        result.retcode = 10009
        result.order = 12345
        result.price = 1.09585
        mt5.order_send.return_value = result
        executor = TradeExecutor(mt5, ftmo, journal, tracker, signals, adaptive)
        executor.execute(
            "XAUUSD",
            {
                "action": "BUY",
                "score": 0.70,
                "confidence": 0.5,
                "atr": 0.005,
                "sl_atr": 2.0,
                "tp_atr": 4.0,
                "risk_mult": 1.0,
                "is_ranging": False,
                "_regime": "RANGING",
                "rates": {},
            },
        )
        assert mt5.order_send.called
        args, kwargs = mt5.order_send.call_args
        req = args[0]
        assert req["symbol"] == "XAUUSD"
        assert req["type"] == 0
        # XAUUSD max_lot=0.10, ftmo retourne 0.05 → pas de clamp
        assert req["volume"] == 0.05

    def test_execute_rr_too_low(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        journal = MagicMock()
        tracker = MagicMock()
        signals = MagicMock()
        adaptive = MagicMock()
        ftmo._calc_sl_tp = MagicMock(return_value=(1.0999, 1.1001))  # RR~1.0
        ftmo.calculate_lot = MagicMock(return_value=0.05)
        executor = TradeExecutor(mt5, ftmo, journal, tracker, signals, adaptive)
        executor.execute(
            "EURUSD",
            {
                "action": "BUY",
                "score": 0.70,
                "confidence": 0.5,
                "atr": 0.005,
                "sl_atr": 2.0,
                "tp_atr": 4.0,
                "risk_mult": 1.0,
                "is_ranging": False,
                "_regime": "RANGING",
                "rates": {},
            },
        )
        assert not mt5.order_send.called

    def test_execute_symbol_info_missing(self):
        mt5 = make_mock_mt5()
        mt5.get_symbol_info.return_value = None
        ftmo = make_ftmo(mt5)
        journal = MagicMock()
        tracker = MagicMock()
        signals = MagicMock()
        adaptive = MagicMock()
        executor = TradeExecutor(mt5, ftmo, journal, tracker, signals, adaptive)
        executor.execute("EURUSD", {"action": "BUY"})
        assert not mt5.order_send.called

    def test_execute_order_failed(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        journal = MagicMock()
        tracker = MagicMock()
        signals = MagicMock()
        adaptive = MagicMock()
        ftmo._calc_sl_tp = MagicMock(return_value=(1.095, 1.115))
        ftmo.calculate_lot = MagicMock(return_value=0.05)
        result = MagicMock()
        result.retcode = 10010
        result.order = None
        mt5.order_send.return_value = result
        executor = TradeExecutor(mt5, ftmo, journal, tracker, signals, adaptive)
        executor.execute(
            "EURUSD",
            {
                "action": "BUY",
                "score": 0.70,
                "confidence": 0.5,
                "atr": 0.005,
                "sl_atr": 2.0,
                "tp_atr": 4.0,
                "risk_mult": 1.0,
                "is_ranging": False,
                "_regime": "RANGING",
                "rates": {},
            },
        )
        assert mt5.order_send.called


class TestPositionTracker:
    def test_track_new_position(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        journal = MagicMock(spec=TradeJournal)
        adaptive = MagicMock()
        pos_cache = MagicMock()
        pos_cache.get.return_value = [
            MagicMock(
                ticket=1,
                magic=cfg.ROBOT_MAGIC,
                type=0,
                symbol="XAUUSD",
                price_open=2350.0,
                sl=2348.0,
                volume=0.1,
                comment="ADAPT_RAN",
                profit=0,
            )
        ]
        tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5=mt5)
        tracker.init_tickets()
        tracker.track_new()
        assert 1 in tracker._position_meta
        assert tracker._position_meta[1]["symbol"] == "XAUUSD"
        assert tracker._position_meta[1]["regime"] == "RANGING"

    def test_detect_closed_position(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        journal = MagicMock(spec=TradeJournal)
        adaptive = MagicMock()
        pos_cache = MagicMock()

        deal = MagicMock()
        deal.position_id = 1
        deal.symbol = "EURUSD"
        deal.type = 0
        deal.profit = 50.0
        deal.price = 1.105
        deal.volume = 0.05
        deal.magic = cfg.ROBOT_MAGIC
        deal.time = time.time()
        mt5.get_history.return_value = [deal]

        pos_cache.get.side_effect = [
            [
                MagicMock(
                    ticket=1,
                    magic=cfg.ROBOT_MAGIC,
                    type=0,
                    symbol="EURUSD",
                    price_open=1.10,
                    sl=1.095,
                    volume=0.05,
                    comment="ADAPT_RAN",
                    profit=0,
                )
            ],
            [],  # second call: position is gone
        ]
        tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5=mt5)
        tracker.init_tickets()
        tracker._position_meta[1] = {"regime": "RAN", "r1_usd": 50, "predictions": {}}
        tracker.check_closed()
        assert ftmo.consecutive_losses == 0

    def test_track_non_robot_position_ignored(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        journal = MagicMock()
        adaptive = MagicMock()
        pos_cache = MagicMock()
        pos_cache.get.return_value = [
            MagicMock(
                ticket=2,
                magic=999999,
                type=0,
                symbol="EURUSD",
                price_open=1.10,
                sl=1.095,
                volume=0.05,
                comment="",
                profit=0,
            )
        ]
        tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5=mt5)
        tracker.init_tickets()
        tracker.track_new()
        assert 2 not in tracker._position_meta


class TestATRTrailing:
    def _make_buy_pos(self, ticket=1, entry=1.1000, current=1.1050, sl=1.0980):
        pos = MagicMock(
            ticket=ticket,
            type=0,
            symbol="EURUSD",
            price_open=entry,
            price_current=current,
            sl=sl,
            volume=0.05,
            comment="ADAPT_RAN",
            magic=cfg.ROBOT_MAGIC,
        )
        return pos

    def _make_sell_pos(self, ticket=1, entry=1.1000, current=1.0950, sl=1.1020):
        pos = MagicMock(
            ticket=ticket,
            type=1,
            symbol="EURUSD",
            price_open=entry,
            price_current=current,
            sl=sl,
            volume=0.05,
            comment="ADAPT_RAN",
            magic=cfg.ROBOT_MAGIC,
        )
        return pos

    @patch("engine_simple.trailer.random.uniform", return_value=0.0)
    def test_trailing_respects_regime_levels(self, mock_rand):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.trailer._get_atr = MagicMock(return_value=0.005)
        ftmo.position_regime = {"1": "RANGING"}
        ftmo.trailer.position_regime = ftmo.position_regime
        result = MagicMock()
        result.retcode = 10009
        mt5.update_sl.return_value = result
        pos = self._make_buy_pos(current=1.1200)  # profit = 0.02, 4.0 ATR
        ftmo._check_step_trailing(pos)
        assert mt5.update_sl.called
        new_sl = mt5.update_sl.call_args[0][1]
        # EURUSD RANGING fallback (TRAILING_BY_REGIME level 3, profit 4.0 ATR > 3.0 thresh): trail = 0.20
        expected_sl = round(1.1200 - 0.20 * 0.005, 5)
        assert abs(new_sl - expected_sl) < 0.0001

    @patch("engine_simple.trailer.random.uniform", return_value=0.0)
    def test_trailing_sell_position(self, mock_rand):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.trailer._get_atr = MagicMock(return_value=0.005)
        ftmo.position_regime = {"1": "RANGING"}
        ftmo.trailer.position_regime = ftmo.position_regime
        result = MagicMock()
        result.retcode = 10009
        mt5.update_sl.return_value = result
        pos = self._make_sell_pos(current=1.0920)  # profit = 0.008, 1.6 ATR > 0.80
        original_sl = pos.sl
        ftmo._check_step_trailing(pos)
        assert mt5.update_sl.called
        new_sl = mt5.update_sl.call_args[0][1]
        assert new_sl < original_sl  # trailing down for sell
        # Verify position.sl is synced after successful update
        assert pos.sl == new_sl

    @patch("engine_simple.trailer.random.uniform", return_value=0.0)
    def test_trailing_does_not_move_sl_backward_buy(self, mock_rand):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.trailer._get_atr = MagicMock(return_value=0.005)
        ftmo.position_regime = {"1": "RANGING"}
        ftmo.trailer.position_regime = ftmo.position_regime
        pos = self._make_buy_pos(current=1.1080, sl=1.1060)
        ftmo._check_step_trailing(pos)
        assert mt5.update_sl.called is False

    @patch("engine_simple.trailer.random.uniform", return_value=0.0)
    def test_trailing_high_vol_wide_dist(self, mock_rand):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.trailer._get_atr = MagicMock(return_value=0.005)
        ftmo.position_regime = {"1": "HIGH_VOL"}
        ftmo.trailer.position_regime = ftmo.position_regime
        result = MagicMock()
        result.retcode = 10009
        mt5.update_sl.return_value = result
        pos = self._make_buy_pos(current=1.1080)
        ftmo._check_step_trailing(pos)
        assert mt5.update_sl.called
        new_sl = mt5.update_sl.call_args[0][1]
        # EURUSD HIGH_VOL fallback (TRAILING_BY_REGIME level 0, profit 1.6 ATR > 1.00 thresh): trail = 1.00
        expected_sl = round(1.1080 - 1.00 * 0.005, 5)
        assert abs(new_sl - expected_sl) < 0.0001


class TestPartialTP:
    def _make_buy_pos(self, ticket=1, entry=1.1000, current=1.1150, sl=1.0950, tp=1.1200):
        pos = MagicMock(
            ticket=ticket,
            type=0,
            symbol="EURUSD",
            price_open=entry,
            price_current=current,
            sl=sl,
            tp=tp,
            volume=0.10,
            comment="ADAPT_RAN",
            magic=cfg.ROBOT_MAGIC,
        )
        pos.profit = 25.0
        return pos

    def _make_sell_pos(self, ticket=1, entry=1.1000, current=1.0850, sl=1.1050, tp=1.0800):
        pos = MagicMock(
            ticket=ticket,
            type=1,
            symbol="EURUSD",
            price_open=entry,
            price_current=current,
            sl=sl,
            tp=tp,
            volume=0.10,
            comment="ADAPT_RAN",
            magic=cfg.ROBOT_MAGIC,
        )
        pos.profit = 25.0
        return pos

    def test_partial_tp_below_threshold(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        pos = self._make_buy_pos(current=1.1050)  # progress = (1.105-1.10)/(1.12-1.10) = 25%
        ftmo._check_partial_tp(pos)
        assert not mt5.order_send.called

    def test_partial_tp_at_60pct(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo._get_atr = MagicMock(return_value=0.005)
        result = MagicMock()
        result.retcode = 10009
        mt5.order_send.return_value = result
        pos = self._make_buy_pos(current=1.1120)  # progress = 60%
        ftmo._check_partial_tp(pos)
        assert mt5.order_send.called

    def test_partial_tp_sell(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo._get_atr = MagicMock(return_value=0.005)
        result = MagicMock()
        result.retcode = 10009
        mt5.order_send.return_value = result
        pos = self._make_sell_pos(current=1.0880)  # progress = (1.10-1.088)/(1.10-1.08) = 60%
        ftmo._check_partial_tp(pos)
        assert mt5.order_send.called

    def test_partial_tp_sets_be(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo._get_atr = MagicMock(return_value=0.005)
        result = MagicMock()
        result.retcode = 10009
        mt5.order_send.return_value = result
        mt5.update_sl.return_value = result
        pos = self._make_buy_pos(current=1.1120)  # 60% progress
        ftmo._check_partial_tp(pos)
        assert mt5.update_sl.called

    def test_partial_tp_already_closed(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.partial_closed.add("1")
        pos = self._make_buy_pos(current=1.1120)
        ftmo._check_partial_tp(pos)
        assert not mt5.order_send.called

    def test_partial_tp_tick_none_returns(self):
        mt5 = make_mock_mt5()
        mt5.get_tick.return_value = None
        ftmo = make_ftmo(mt5)
        ftmo._get_atr = MagicMock(return_value=0.005)
        pos = self._make_buy_pos(current=1.1120)
        ftmo._check_partial_tp(pos)
        assert not mt5.order_send.called

    def test_partial_tp_direction_backwards_returns(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        pos = self._make_buy_pos(current=1.0900)  # price below entry, wrong direction
        ftmo._check_partial_tp(pos)
        assert not mt5.order_send.called

    def test_time_stop(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        result = MagicMock()
        result.retcode = 10009
        mt5.order_send.return_value = result
        pos = self._make_buy_pos(current=1.1010)
        pos.profit = -25.0  # losing position → max_hours=4
        sixteen_hours_ago = datetime.utcnow() - __import__("datetime").timedelta(hours=16)
        ftmo.position_open_times = {"1": {"open_time": sixteen_hours_ago, "symbol": "EURUSD"}}
        ftmo.trailer.position_open_times = ftmo.position_open_times
        ftmo._check_time_stop(pos)
        assert mt5.order_send.called

    def test_time_stop_tick_none_returns(self):
        mt5 = make_mock_mt5()
        mt5.get_tick.return_value = None
        ftmo = make_ftmo(mt5)
        pos = self._make_buy_pos(current=1.1010)
        pos.profit = -25.0
        sixteen_hours_ago = datetime.utcnow() - __import__("datetime").timedelta(hours=16)
        ftmo.position_open_times = {"1": {"open_time": sixteen_hours_ago, "symbol": "EURUSD"}}
        ftmo.trailer.position_open_times = ftmo.position_open_times
        ftmo._check_time_stop(pos)
        assert not mt5.order_send.called
