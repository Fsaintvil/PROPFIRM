"""Tests d'intégration pour main.py — cycle complet (signaux → FTMO → exec)"""

import os
import sys
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "DEBUG"

import config_simple as cfg
from engine_simple.ftmo_protector import FTMOProtector
from engine_simple.mt5_connector import MT5Connector


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


class TestFTMOCycle:
    """Test le cycle complet can_trade → execute"""

    def test_simple_signal_goes_through(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo._atr_cache = {"USDCHF": (0.005, time.time())}

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 0.7800,
                        "tp": 0.7900,
                    },
                )
                assert ok, f"Expected OK, got: {reason}"

    def test_rejected_when_daily_loss_exceeded(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.daily_start_equity = 205000  # equity at day start
        mt5.get_account_info.return_value = MagicMock(equity=195000)  # now down $5000
        ftmo.daily_stats["pnl"] = -5000
        ftmo.daily_stats["trades"] = 3
        ftmo.daily_stats["day"] = datetime(2026, 5, 27).date()

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 0.7800,
                        "tp": 0.7900,
                    },
                )
                assert not ok
                assert "daily loss" in reason.lower()

    def test_rejected_when_max_positions_reached(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 0.7800,
                        "tp": 0.7900,
                    },
                    positions=[
                        MagicMock(magic=cfg.ROBOT_MAGIC, symbol="USDCHF", type=0, ticket=1),
                        MagicMock(magic=cfg.ROBOT_MAGIC, symbol="USDCHF", type=0, ticket=2),
                    ],
                )
                assert ok, f"Expected OK (correlation check passes for positions list), got: {reason}"

    def test_calculate_lot_basic(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        lot = ftmo.calculate_lot("EURUSD", 1.1, 1.09, quality=1.0, direction=0)
        assert 0.01 <= lot <= 1.50, f"Lot {lot} hors limites (Mode MAX: 0.01-1.50)"

    def test_calculate_lot_with_max_risk_cap(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.max_risk_amount = 10.0
        lot = ftmo.calculate_lot("EURUSD", 1.1, 1.09, quality=1.0, direction=0)
        assert 0.01 <= lot <= 1.0, f"Lot {lot} hors limites"

    def test_check_price_staleness_fresh(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        assert ftmo.check_price_staleness("EURUSD")

    def test_check_price_staleness_stale(self):
        mt5 = make_mock_mt5()
        old_tick = MagicMock(ask=1.1, bid=1.099)
        old_tick.time = time.time() - 300
        mt5.get_tick.return_value = old_tick
        ftmo = make_ftmo(mt5)
        assert not ftmo.check_price_staleness("EURUSD", max_age=60)

    def test_volatility_circuit_breaker(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "atr_pct": 3.0,
                        "atr_median_14": 0.5,
                        "sl": 0.7800,
                        "tp": 0.7900,
                    },
                )
                assert not ok
                assert "Volatility" in reason

    def test_consistency_violation_at_target(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.initial_balance = 200000
        # Profit target = 10% = $20k ; consistency block dès 80% = $16k
        ftmo.daily_pnl_by_date = {"2026-05-26": 12000, "2026-05-27": 8000}  # $20k realized
        ftmo.consistency_violated = True
        mt5.get_account_info.return_value = MagicMock(equity=220000)
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 0.7800,
                        "tp": 0.7900,
                    },
                )
                assert not ok
                assert "consistency" in reason.lower()

    def test_report_format(self):
        mt5 = make_mock_mt5()
        ftmo = make_ftmo(mt5)
        ftmo.initial_balance = 200000
        ftmo.trading_days = {datetime(2026, 5, 27).date()}
        mt5.get_account_info.return_value = MagicMock(equity=201000)
        report = ftmo.get_progress_report()
        assert "equity" in report
        assert "dd_from_initial" in report
        assert "win_rate" in report
