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
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.0900, 1.1300, None)
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
        """SL == TP == price : invalide (message directionnel OU protection)."""
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.1000, 1.1000, None)
        assert err is not None
        assert (
            "identique au prix" in err.lower()
            or "pas de protection" in err.lower()
            or "bloqué" in err.lower()
            or "invalide" in err.lower()
        )

    def test_buy_sl_above_price_rejected(self):
        """🐛 FIX 16 Août 2026 (Audit M-EX2): un BUY avec SL au-dessus du prix
        doit être REJETÉ (l'ancien abs() masquait le sens inversé)."""
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.1100, 1.1050, None)
        assert err is not None
        assert "invalide" in err.lower()

    def test_sell_sl_below_price_rejected(self):
        """🐛 FIX 16 Août 2026 (Audit M-EX2): un SELL avec SL sous le prix
        doit être REJETÉ."""
        err = OrderValidator.validate("EURUSD", "SELL", 0.1, 1.1000, 1.0900, 1.1050, None)
        assert err is not None
        assert "invalide" in err.lower()

    def test_rr_below_min(self):
        # RR = (1.1260-1.1000) / (1.1000-1.0900) = 0.026/0.01 = 2.6 → OK (>MIN_RR_RATIO=2.5)
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.0900, 1.1260, None)
        assert err is None
        # RR = (1.1050-1.1000) / (1.1000-1.0900) = 0.005/0.01 = 0.5 → FAIL
        err = OrderValidator.validate("EURUSD", "BUY", 0.1, 1.1000, 1.0900, 1.1050, None)
        assert err is not None
        assert "rr" in err.lower()

    def test_valid_sell_order(self):
        # SELL: risk = (1.1100-1.1000)*0.1, reward = (1.1000-1.0750)*0.1, RR=2.5
        err = OrderValidator.validate("EURUSD", "SELL", 0.1, 1.1000, 1.1100, 1.0750, None)
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
        assert ex.rate_limiter.max_per_minute == 2  # Sécurisé: 2 trades/min/symbole (FIX 21 Juillet 2026)

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
        ex.ftmo.trailer.calc_sl_tp.return_value = (1.0900, 1.1250)
        ex.ftmo.calculate_lot.return_value = 0.1  # <-- fix: return proper number
        ex.mt5.get_account_info.return_value = MagicMock(balance=200000)
        mock_result = MagicMock()
        mock_result.retcode = 10009
        ex.mt5.order_send.return_value = mock_result

        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result = ex.execute("EURUSD", signal)

        assert result is not None
        ex.ftmo.trailer.calc_sl_tp.assert_called_with("EURUSD", 1.1000, 0, 0.005, 2.0, 5.0)

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
        # ⚖️ 04 Aout 2026: RÉGLAGE AGRESSIF ÉQUILIBRÉ → global_max_lot=0.20 (0.15 < 0.20, pas de clamp)
        assert lot_arg["volume"] == 0.15
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

    def test_price_dedup_blocks_persistent_signal_5min(self):
        """🔧 FIX 20 Août 2026: price-dedup bloque un doublon rejoué ~5 min après.
        L'ancienne fenêtre (120s) laissait passer un signal MOM20x3 identique
        rejoué à 5 min (XAUUSD BUY 02:02 + 02:07 → 2 SL = -338$ le 20/08).
        Nouvelle fenêtre : <0.05% d'écart ET age < 600s → considéré doublon.
        🔧🔧 FIX 20/08 (2nd): pos.time est en TEMPS SERVEUR (+3h) → on simule
        un offset serveur de +10800s comme en production (ftmo._server_offset_s)."""
        ex = self.make_executor()
        ex.ftmo._server_offset_s = 10800.0  # FTMO-Demo : serveur décalé de +3h
        pos = MagicMock()
        pos.symbol = "XAUUSD"
        pos.type = 0  # BUY
        pos.price_open = 4515.49
        pos.ticket = 999
        pos.time = time.time() + 10800 - 300  # temps serveur : ouverte il y a 5 min
        ex.mt5.get_positions.return_value = [pos]
        signal = {
            "action": "BUY",
            "entry_price": 4515.12,  # diff 0.008% < 0.05% → doublon
            "sl": 4510.0,
            "tp": 4540.0,
            "high_confidence": True,  # le price-dedup s'applique AUSSI aux high_confidence
        }
        result = ex.execute("XAUUSD", signal)
        assert result is None

    def test_price_dedup_blocks_with_server_offset_realistic(self):
        """🔧🔧 FIX 20/08 (2nd): SANS l'offset serveur, le price-dedup est inerte
        (0 < age toujours faux car pos.time ~ +3h). Avec l'offset appliqué, un
        doublon rejoué à 5 min est bien bloqué. C'était le bug exact du 20/08 :
        les doublons XAUUSD 04:53 et 08:28 (2 SL = -338$ / 2 SL = -113$) sont
        passés car la garde 0 < age < 600 était TOUJOURS fausse en production."""
        ex = self.make_executor()
        ex.ftmo.calculate_lot.return_value = 0.1
        # Pas d'offset défini sur le mock → offset = 0 → pas de faux rejet (fix 10/08)
        pos = MagicMock()
        pos.symbol = "XAUUSD"
        pos.type = 0
        pos.price_open = 4515.49
        pos.ticket = 777
        pos.time = time.time() + 10800  # temps serveur : "futur" local de +3h
        ex.mt5.get_positions.return_value = [pos]
        signal = {
            "action": "BUY",
            "entry_price": 4515.12,
            "sl": 4510.0,
            "tp": 4540.0,
            "high_confidence": True,
        }
        mock_result = MagicMock()
        mock_result.retcode = 10009
        ex.mt5.order_send.return_value = mock_result
        # offset absent → age ≈ -10800 → pas de blocage (préserve le fix 10/08)
        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result = ex.execute("XAUUSD", signal)
        assert result is not None

        # Même scénario MAIS avec l'offset serveur présent (production) :
        # pos.time = now + 10800 (serveur) - 300 (ouverte il y a 5 min)
        ex.ftmo._server_offset_s = 10800.0
        pos.time = time.time() + 10800 - 300
        result = ex.execute("XAUUSD", signal)
        assert result is None  # bloqué : l'age corrigé = 300s ∈ (0, 600)

    def test_price_dedup_negative_age_not_blocked(self):
        """🐛 FIX 10 Août 2026 (conservé): age NÉGATIF (offset serveur ~3h)
        → PAS de faux rejet. La garde 0 < age < 600s reste en place."""
        ex = self.make_executor()
        pos = MagicMock()
        pos.symbol = "USDJPY"
        pos.type = 0  # BUY
        pos.price_open = 152.00
        pos.ticket = 888
        pos.time = time.time() + 10800  # futur (temps serveur FTMO-Demo +3h)
        ex.mt5.get_positions.return_value = [pos]
        signal = {
            "action": "BUY",
            "entry_price": 152.01,  # diff 0.007% < 0.05% MAIS age négatif → pas un doublon
            "sl": 151.50,
            "tp": 153.50,
            "high_confidence": True,  # saute le comptage de positions → atteint le price-dedup
        }
        ex.ftmo.calculate_lot.return_value = 0.1
        mock_result = MagicMock()
        mock_result.retcode = 10009
        ex.mt5.order_send.return_value = mock_result
        with patch("engine_simple.trade_executor.OrderValidator.validate", return_value=None):
            result = ex.execute("USDJPY", signal)
        # Non bloqué par le price-dedup → l'ordre est passé
        assert result is not None
        assert result.retcode == 10009

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
        # ⚖️ 04 Aout 2026: RÉGLAGE AGRESSIF ÉQUILIBRÉ → global_max_lot=0.20 (0.15 < 0.20, pas de clamp)
        assert lot == 0.15

    def test_calc_lot_fallback_min(self):
        ex = self.make_executor()
        ex.ftmo.calculate_lot.return_value = None  # ftmo returns None
        lot = ex._calc_lot("XAUUSD", 1.1000, 1.0900)
        # 🔧 FIX 10 Juillet 2026: fallback min 0.01 (data collection mode)
        assert lot == 0.01
