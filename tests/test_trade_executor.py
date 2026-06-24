"""Tests for trade_executor.py — PerSymbolRateLimiter, ExecutionStats, OrderValidator, TradeExecutor"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock, patch

from engine_simple.trade_executor import (
    PerSymbolRateLimiter,
    ExecutionStats,
    OrderValidator,
    TradeExecutor,
)


# ── PerSymbolRateLimiter ────────────────────────────────────────────


class TestPerSymbolRateLimiter:
    def test_init(self):
        rl = PerSymbolRateLimiter(max_per_minute=1, min_interval_s=0)
        assert rl.max_per_minute == 1
        assert rl.window_seconds == 60

    def test_allow_returns_true_when_available(self):
        rl = PerSymbolRateLimiter(max_per_minute=5, min_interval_s=0)
        assert rl.allow("EURUSD") is True

    def test_allow_exhausts_limit(self):
        rl = PerSymbolRateLimiter(max_per_minute=1, min_interval_s=0)
        assert rl.allow("EURUSD") is True
        assert rl.allow("EURUSD") is False  # 0 remaining

    def test_per_symbol_independence(self):
        rl = PerSymbolRateLimiter(max_per_minute=1, min_interval_s=0)
        assert rl.allow("EURUSD") is True
        assert rl.allow("GBPUSD") is True  # different symbol, different counter
        assert rl.allow("EURUSD") is False  # EURUSD exhausted
        assert rl.allow("GBPUSD") is False  # GBPUSD also exhausted

    def test_old_timestamps_expire(self):
        rl = PerSymbolRateLimiter(max_per_minute=1, window_seconds=0.1, min_interval_s=0)
        assert rl.allow("EURUSD") is True
        assert rl.allow("EURUSD") is False  # exhausted
        time.sleep(0.15)
        assert rl.allow("EURUSD") is True  # window reset

    def test_min_interval_enforced(self):
        """Le PerSymbolRateLimiter impose 5 min entre deux trades sur le même symbole."""
        rl = PerSymbolRateLimiter(max_per_minute=10, min_interval_s=300)
        assert rl.allow("EURUSD") is True
        # Appel immédiat: doit être refusé car < 300s
        assert rl.allow("EURUSD") is False


# ── ExecutionStats ──────────────────────────────────────────────────


class TestExecutionStats:
    def test_init(self):
        es = ExecutionStats()
        assert es.records == []
        assert es.total_attempts == 0

    def test_record_success(self):
        es = ExecutionStats()
        es.record(True, 0.05, 2)
        assert es.total_attempts == 1
        assert es.successful == 1
        assert es.rejected == 0

    def test_record_failure(self):
        es = ExecutionStats()
        es.record(False, 0.1)
        assert es.successful == 0
        assert es.rejected == 1

    def test_success_rate(self):
        es = ExecutionStats()
        assert es.success_rate == 1.0  # no records
        es.record(True, 0.05)
        es.record(True, 0.06)
        es.record(False, 0.1)
        assert es.success_rate == 2 / 3

    def test_avg_slippage(self):
        es = ExecutionStats()
        assert es.avg_slippage == 0.0  # no records
        es.record(True, 0.05, 2)
        es.record(True, 0.06, 4)
        assert es.avg_slippage == 3.0

    def test_p95_latency(self):
        es = ExecutionStats()
        assert es.p95_latency == 0.0  # no records
        for i in range(100):
            es.record(True, i * 0.001)
        # 100 * 0.95 = 95, latencies[95] = 0.095 (0-indexed)
        assert es.p95_latency == 0.095

    def test_summary(self):
        es = ExecutionStats()
        es.record(True, 0.05, 2)
        es.record(True, 0.06, 4)
        es.record(False, 0.10)
        s = es.summary()
        assert s["total"] == 3
        assert s["success_rate"] == 2 / 3
        # Values in seconds: (0.05+0.06+0.10)/3 = 0.07, round(0.07,1) = 0.1
        assert s["avg_latency_ms"] == 0.1
        assert s["avg_slippage_pts"] == 3.0  # (2+4)/2

    def test_record_no_slippage(self):
        es = ExecutionStats()
        es.record(True, 0.05)  # no slippage
        assert "slippage" not in es.records[0]


# ── OrderValidator ──────────────────────────────────────────────────


class TestOrderValidator:
    def test_valid_order(self):
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.0900, 1.1200, None)
        assert err is None

    def test_lot_below_minimum(self):
        err = OrderValidator.validate("EURUSD", "BUY", 0.001, 1.1000, 1.0900, 1.1200, None)
        assert err is not None
        assert "min" in err.lower()

    def test_lot_above_maximum(self):
        err = OrderValidator.validate("EURUSD", "BUY", 20.0, 1.1000, 1.0900, 1.1200, None)
        assert err is not None
        assert "max" in err.lower()

    def test_lot_above_broker_max(self):
        symbol_info = MagicMock()
        symbol_info.volume_max = 1.0
        err = OrderValidator.validate("EURUSD", "BUY", 2.0, 1.1000, 1.0900, 1.1200, symbol_info)
        assert err is not None
        assert "volume_max" in err

    def test_sl_tp_invalid(self):
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.1000, 1.1000, None)
        assert err is not None
        assert "invalide" in err.lower() or "refus" in err.lower() or "nul" in err.lower()

    def test_rr_below_min(self):
        # RR = (1.1200-1.1000) / (1.1000-1.0900) = 0.02/0.01 = 2.0 → OK
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.0900, 1.1200, None)
        assert err is None
        # RR = (1.1050-1.1000) / (1.1000-1.0900) = 0.005/0.01 = 0.5 → FAIL
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.0900, 1.1050, None)
        assert err is not None
        assert "rr" in err.lower()

    def test_valid_sell_order(self):
        # SELL: risk = (1.1100-1.1000)*0.1, reward = (1.1000-1.0800)*0.1, RR=2.0
        err = OrderValidator.validate("EURUSD", "SELL", 0.1, 1.1000, 1.1100, 1.0800, None)
        assert err is None


# ── TradeExecutor ───────────────────────────────────────────────────


class TestTradeExecutor:
    def make_executor(self):
        mt5 = MagicMock()
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        ftmo = MagicMock()
        journal = MagicMock()
        tracker = MagicMock()
        signals = MagicMock()
        adaptive = MagicMock()
        return TradeExecutor(mt5, ftmo, journal, tracker, signals, adaptive)

    def test_init(self):
        ex = self.make_executor()
        assert ex.rate_limiter is not None
        assert ex.rate_limiter.max_per_minute == 2  # Mode modéré: 2 trades/min/symbole

    def test_get_signal_value_dict(self):
        ex = self.make_executor()
        val = ex._get_signal_value({"action": "BUY"}, "action")
        assert val == "BUY"
        val = ex._get_signal_value({"missing": 1}, "nope", "default")
        assert val == "default"

    def test_get_signal_value_object(self):
        ex = self.make_executor()
        obj = MagicMock()
        obj.action = "SELL"
        val = ex._get_signal_value(obj, "action")
        assert val == "SELL"

    def test_execute_fails_without_sl_tp_and_no_atr(self):
        ex = self.make_executor()
        signal = {"action": "BUY", "entry_price": 1.1000, "sl": None, "tp": None}
        tick = MagicMock(ask=1.1000, bid=1.0995)
        ex.mt5.get_tick.return_value = tick
        result = ex.execute("EURUSD", signal)
        assert result is None

    def test_execute_rate_limited(self):
        ex = self.make_executor()
        # Premier trade accepté
        signal1 = {"action": "BUY", "entry_price": 1.1000, "sl": 1.0900, "tp": 1.1300}
        ex.ftmo.calculate_lot.return_value = 0.1
        mock_result = MagicMock()
        mock_result.retcode = 10009
        ex.mt5.order_send.return_value = mock_result
        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result1 = ex.execute("EURUSD", signal1)
        assert result1 is not None
        # Deuxième trade immédiat = refusé (rate limiter)
        result2 = ex.execute("EURUSD", {"action": "BUY", "entry_price": 1.1000})
        assert result2 is None

    def test_execute_calc_sl_tp_from_atr(self):
        ex = self.make_executor()
        signal = {
            "action": "BUY",
            "entry_price": 1.1000,
            "sl": None,
            "tp": None,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 5.0,
        }
        tick = MagicMock(ask=1.1005, bid=1.1000)
        ex.mt5.get_tick.return_value = tick
        ex.ftmo._calc_sl_tp.return_value = (1.0900, 1.1250)
        ex.ftmo.calculate_lot.return_value = 0.1  # <-- fix: return proper number
        ex.mt5.get_account_info.return_value = MagicMock(balance=200000)
        mock_result = MagicMock()
        mock_result.retcode = 10009
        ex.mt5.order_send.return_value = mock_result

        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result = ex.execute("EURUSD", signal)

        assert result is not None
        ex.ftmo._calc_sl_tp.assert_called_with("EURUSD", 1.1000, 0, 0.005, 2.0, 5.0)

    def test_execute_calc_lot_from_ftmo(self):
        ex = self.make_executor()
        signal = {
            "action": "BUY",
            "entry_price": 1.1000,
            "sl": 1.0900,
            "tp": 1.1300,
            "regime": "TREND_UP",
        }
        tick = MagicMock(ask=1.1005, bid=1.1000)
        ex.mt5.get_tick.return_value = tick
        ex.ftmo.calculate_lot.return_value = 0.15
        ex.mt5.get_account_info.return_value = MagicMock(balance=200000)
        mock_result = MagicMock()
        mock_result.retcode = 10009
        ex.mt5.order_send.return_value = mock_result

        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result = ex.execute("XAUUSD", signal)

        assert result is not None
        lot_arg = ex.mt5.order_send.call_args[0][0]
        # XAUUSD max_lot=0.06 (+10%), ftmo retourne 0.15 → clamp à 0.06
        assert lot_arg["volume"] == 0.06
        assert lot_arg["comment"] == "ADAPT_TRE"

    def test_execute_validation_fails(self):
        ex = self.make_executor()
        signal = {
            "action": "BUY",
            "entry_price": 1.1000,
            "sl": 1.0900,
            "tp": 1.1005,  # RR too low
            "regime": "RANGING",
        }
        ex.ftmo.calculate_lot.return_value = 0.1
        result = ex.execute("EURUSD", signal)
        assert result is None  # rejected by validator

    def test_execute_order_failed(self):
        ex = self.make_executor()
        signal = {
            "action": "BUY",
            "entry_price": 1.1000,
            "sl": 1.0900,
            "tp": 1.1300,
            "regime": "RANGING",
        }
        ex.ftmo.calculate_lot.return_value = 0.1
        mock_result = MagicMock()
        mock_result.retcode = 10014  # Market closed
        ex.mt5.order_send.return_value = mock_result

        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result = ex.execute("EURUSD", signal)

        assert result is not None  # still returns the result object
        assert result.retcode == 10014

    def test_regime_to_short(self):
        assert TradeExecutor.REGIME_TO_SHORT["TREND_UP"] == "TRE"
        assert TradeExecutor.REGIME_TO_SHORT["TREND_DOWN"] == "DOW"
        assert TradeExecutor.REGIME_TO_SHORT["RANGING"] == "RAN"
        assert TradeExecutor.REGIME_TO_SHORT["HIGH_VOL"] == "HIG"
        assert TradeExecutor.REGIME_TO_SHORT["LOW_VOL"] == "LOW"

    def test_calc_lot_from_ftmo(self):
        ex = self.make_executor()
        ex.ftmo.calculate_lot.return_value = 0.15
        lot = ex._calc_lot("XAUUSD", 1.1000, 1.0900)
        # XAUUSD max_lot=0.06 (+10%), ftmo retourne 0.15 → clamp à 0.06
        assert lot == 0.06

    def test_calc_lot_fallback_min(self):
        ex = self.make_executor()
        ex.ftmo.calculate_lot.return_value = None  # ftmo returns None
        lot = ex._calc_lot("XAUUSD", 1.1000, 1.0900)
        # Fallback sécurisé: 0.01 minimum
        assert lot == 0.01
