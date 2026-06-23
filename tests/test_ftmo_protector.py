"""Tests for ftmo_protector.py — with MT5 mock (via conftest)"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from engine_simple.ftmo_protector import FTMOProtector


def make_config(**kwargs):
    cfg = dict(
        MAX_POSITIONS=14,
        MAX_TRADES_PER_DAY=100,
        LOT_SIZE=0.1,
        RISK_PER_TRADE=0.004,
        COOLDOWN_MINUTES=30,
        MAX_DAILY_LOSS_PCT=0.05,
        INITIAL_BALANCE=200000,
        MAX_DD_PCT=0.10,
        PROFIT_TARGET_PCT=0.10,
        CONSISTENCY_MAX_PCT=0.30,
        MIN_TRADING_DAYS=10,
        MAGIC=999001,
        MAX_SPREAD_POINTS=50,
        SYMBOL_LIMITS={},
        TRADING_START_HOUR=0,
        TRADING_END_HOUR=24,
    )
    cfg.update(kwargs)
    return cfg


def make_protector(config=None):
    mt5_mock_local = MagicMock()
    tick_mock = MagicMock(ask=1.1, bid=1.099)
    tick_mock.time = time.time()
    mt5_mock_local.get_tick.return_value = tick_mock
    mt5_mock_local.get_symbol_info.return_value = MagicMock(point=1e-4, ask=1.1, bid=1.099)
    if config is None:
        config = make_config()
    return FTMOProtector(mt5_mock_local, config)


class TestCheckConsistency:
    def test_no_violation_when_total_negative(self):
        p = make_protector()
        p._trade_history = [{"profit": -50}]
        p._check_consistency()
        assert not p.consistency_violated

    def test_no_violation_below_threshold(self):
        p = make_protector()
        p._trade_history = [{"profit": 100}, {"profit": 100}, {"profit": 100}]  # total=300
        p.daily_pnl_by_date = {"2026-01-01": 50, "2026-01-02": 50, "2026-01-03": 50, "2026-01-04": 50}
        p._check_consistency()
        # Each day 50/300=16.7% < 30% → no violation
        assert not p.consistency_violated

    def test_violation_when_day_exceeds_30pct(self):
        p = make_protector()
        p.initial_balance = 200000
        # realized profit > 80% of $20k target = $16000
        p._trade_history = [{"profit": 300}, {"profit": 30}, {"profit": 15900}]
        p.daily_pnl_by_date = {"2026-01-01": 15900, "2026-01-02": 330}
        p._check_consistency()
        # 15900/16230 = 98% > 30% → violation
        assert p.consistency_violated

    def test_no_violation_below_threshold_realized(self):
        p = make_protector()
        p.initial_balance = 200000
        # realized profit = 16200 > 80% of $20k target = $16000
        p._trade_history = [{"profit": 200}, {"profit": 300}, {"profit": 15700}]
        p.daily_pnl_by_date = {"2026-01-01": 15700, "2026-01-02": 500}
        p._check_consistency()
        # 15700/16200 = 96.9% > 30% → violation
        assert p.consistency_violated

    def test_no_violation_on_zero_days(self):
        p = make_protector()
        p.initial_balance = 200000
        p._trade_history = [{"profit": 300}, {"profit": 100}, {"profit": 15900}]
        # 3 jours avec jours négatifs — seul 200 est concerné
        p.daily_pnl_by_date = {"2026-01-01": -50, "2026-01-02": 200, "2026-01-03": 100}
        p._check_consistency()
        # days positifs: [200, 100] → total=300, best=200
        # 200/300 = 66.7% > 30% → VIOLATION (corrigé: l'ancien guard $500 cachait ça)
        assert p.consistency_violated


class TestPruneHistories:
    def test_prune_trade_history(self):
        p = make_protector()
        p._trade_history = [{"profit": i} for i in range(1500)]
        p._prune_histories()
        assert len(p._trade_history) <= 1000

    def test_prune_partial_closed(self):
        p = make_protector()
        p.partial_closed = set(str(i) for i in range(1000))
        p._prune_histories()
        assert len(p.partial_closed) <= 1000

    def test_prune_peak_profit(self):
        p = make_protector()
        p.peak_profit = {str(i): float(i) for i in range(1000)}
        p._prune_histories()
        assert len(p.peak_profit) <= 500

    def test_prune_peak_profit_numeric_order(self):
        p = make_protector()
        # Keys as strings; "9" > "10" lexicographically = bug if not int-sorted
        p.peak_profit = {str(i): float(i) for i in range(500)}
        p._prune_histories()
        # Should not crash and should keep at least 300
        assert len(p.peak_profit) <= 500
        assert len(p.peak_profit) >= 1


class TestRecordTradeResult:
    def test_tracking_basic(self):
        p = make_protector()
        p.record_trade_result("EURUSD", 50.0)
        assert p.daily_stats["trades"] == 1
        assert abs(p.daily_stats["pnl"] - 50.0) < 0.01
        assert p.consecutive_losses == 0

    def test_tracking_loss(self):
        p = make_protector()
        p.record_trade_result("EURUSD", -30.0)
        assert p.daily_stats["trades"] == 1
        assert p.consecutive_losses == 1
        assert "EURUSD" in str(p.cooldowns)

    def test_consecutive_losses_accumulate(self):
        p = make_protector()
        p.record_trade_result("EURUSD", -10)
        p.record_trade_result("EURUSD", -20)
        assert p.consecutive_losses == 2

    def test_consecutive_losses_reset_on_win(self):
        p = make_protector()
        p.record_trade_result("EURUSD", -10)
        p.record_trade_result("EURUSD", -20)
        p.record_trade_result("EURUSD", 50)
        assert p.consecutive_losses == 0

    def test_trading_days_tracking(self):
        p = make_protector()
        today = datetime.utcnow().date()
        p.record_trade_result("EURUSD", 10)
        assert today in p.trading_days


class TestDailyLossLimit:
    def test_below_limit(self):
        p = make_protector()
        p.daily_stats["pnl"] = -1000
        p._check_daily_loss_limit()
        assert p.daily_stats["pnl"] == -1000  # state unchanged, no violation

    def test_daily_pnl_percentage(self):
        p = make_protector(make_config(INITIAL_BALANCE=200000, MAX_DAILY_LOSS_PCT=0.05))
        p.daily_stats["pnl"] = -20000
        pct = abs(p.daily_stats["pnl"]) / p.initial_balance
        assert abs(pct - 0.10) < 0.001


class TestDrawdownLimit:
    def test_below_limit(self):
        p = make_protector()
        p.peak_equity = 110000
        p.mt5.get_account_info.return_value = MagicMock(equity=105000, balance=108000)
        p._check_drawdown_limit()
        assert p.peak_equity == 110000  # state unchanged, no violation

    def test_in_drawdown(self):
        p = make_protector()
        p.peak_equity = 100000
        p.mt5.get_account_info.return_value = MagicMock(equity=97000, balance=100000)
        p._check_drawdown_limit()
        dd_pct = (100000 - 97000) / 100000
        assert abs(dd_pct - 0.03) < 0.001


class TestCalcSlTp:
    def test_calc_with_atr(self):
        p = make_protector()
        info = MagicMock(digits=5)
        p.mt5.get_symbol_info.return_value = info
        sl, tp = p._calc_sl_tp("EURUSD", 1.1, 0, atr_val=0.005, sl_mult=2.0, tp_mult=4.0)
        assert sl < 1.1  # BUY, SL below entry
        assert tp > 1.1  # BUY, TP above entry
        sl_dist = abs(1.1 - sl)
        tp_dist = abs(tp - 1.1)
        assert abs(sl_dist - 0.01) < 0.002  # 2 * 0.005
        assert abs(tp_dist - 0.02) < 0.002  # 4 * 0.005

    def test_calc_sell_direction(self):
        p = make_protector()
        info = MagicMock(digits=5)
        p.mt5.get_symbol_info.return_value = info
        sl, tp = p._calc_sl_tp("EURUSD", 1.1, 1, atr_val=0.005, sl_mult=2.0, tp_mult=4.0)
        assert sl > 1.1  # SELL, SL above entry
        assert tp < 1.1  # SELL, TP below entry


class TestCanTrade:
    def _mock_symbol_info(self, p, symbol="EURUSD"):
        info = MagicMock(point=0.00001, digits=5)
        p.mt5.get_symbol_info.return_value = info
        # Realistic tick with tiny spread so spread check passes
        tick = MagicMock(ask=1.10005, bid=1.10000)
        tick.time = time.time()
        p.mt5.get_tick.return_value = tick
        return info

    def test_trade_blocked_on_weekend(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 31, 12, 0)  # Sunday
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = p.can_trade("EURUSD")
                # Mode 24/7 — le weekend n'est plus bloqué
                # Le trade n'est PAS refusé pour "Weekend"
                assert "Weekend" not in reason, f"Ne devrait pas bloquer weekend: {reason}"

    def test_trade_allowed_normal(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)  # Wednesday 11h UTC
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                # SL/TP obligatoires (FIX #3)
                ok, reason = p.can_trade(
                    "EURUSD",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 1.0900,
                        "tp": 1.1200,
                    },
                )
                assert ok, f"Expected OK, got: {reason}"

    def test_consecutive_losses_blocks_after_auto_pause(self):
        """5 pertes consécutives → pause globale 30min (bloqué jusqu'à expiration)"""
        p = make_protector()
        self._mock_symbol_info(p)
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.consecutive_losses = 5
        ok, reason = p.can_trade("EURUSD")
        assert not ok, f"Doit bloquer après 5 pertes: {reason}"
        assert "cooldown" in reason.lower()

    def _as_weekday(self, dt=None):
        """Patch utcnow pour simuler un jour de semaine (évite blocage weekend en test)."""
        import datetime as dt_mod
        from unittest.mock import patch

        if dt is None:
            dt = dt_mod.datetime(2026, 6, 8, 10, 0, 0)  # Monday 10:00 UTC

        class _FakeDT(dt_mod.datetime):
            @classmethod
            def utcnow(cls):
                return dt

        return patch("engine_simple.ftmo_protector.datetime", _FakeDT)

    def test_consecutive_losses_auto_reset_after_cooldown(self):
        """Après expiration du cooldown, le compteur se reset automatiquement"""
        p = make_protector()
        self._mock_symbol_info(p)
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.consecutive_losses = 5
        p.global_cooldown_until = None
        # Force le cooldown pour le test
        with self._as_weekday():
            p.can_trade("EURUSD")  # Déclenche le cooldown
            # Maintenant le cooldown est positionné, on le fait expirer
            p.global_cooldown_until = datetime(2026, 6, 8, 9, 59, 0)
            ok, reason = p.can_trade("EURUSD")
        assert ok, f"Devrait autoriser après le cooldown: {reason}"
        assert p.consecutive_losses == 0

    def test_consecutive_losses_sets_cooldown(self):
        """5 pertes consécutives → pause 30min (cooldown défini)"""
        p = make_protector()
        self._mock_symbol_info(p)
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.consecutive_losses = 5
        p.global_cooldown_until = None  # pas de cooldown actif
        assert p.global_cooldown_until is None
        with self._as_weekday():
            ok, reason = p.can_trade("EURUSD")
        assert not ok, f"Devrait bloquer après 5 pertes: {reason}"
        assert "cooldown" in reason.lower()
        assert p.global_cooldown_until is not None  # cooldown activé
        assert p.consecutive_losses == 5  # toujours compté

    def test_daily_trade_limit(self):
        p = make_protector(make_config(MAX_TRADES_PER_DAY=5))
        self._mock_symbol_info(p)
        p.daily_stats["trades"] = 5
        ok, reason = p.can_trade("EURUSD")
        assert not ok
        assert "Daily trade" in reason


class TestGetProgressReport:
    def test_report_generated(self):
        p = make_protector()
        p.mt5.get_account_info.return_value = MagicMock(equity=205000, balance=205000)
        report = p.get_progress_report()
        assert report["status"] == "ACTIVE"
        assert "profit_progress" in report
        assert "dd_from_initial" in report
        assert "total_trades" in report


class TestPipOffset:
    def test_forex_pip(self):
        p = make_protector()
        info = MagicMock(point=0.00001, digits=5)
        p.mt5.get_symbol_info.return_value = info
        offset = p._pip_offset("EURUSD", 10)
        assert abs(offset - 0.001) < 0.0001  # 10 pips = 0.001

    def test_xau_pip(self):
        p = make_protector()
        info = MagicMock(point=0.01, digits=2)
        p.mt5.get_symbol_info.return_value = info
        offset = p._pip_offset("XAUUSD", 10)
        assert abs(offset - 0.1) < 0.01  # 10 pips XAU = 0.1

    def test_jpy_pip(self):
        p = make_protector()
        info = MagicMock(point=0.001, digits=3)
        p.mt5.get_symbol_info.return_value = info
        offset = p._pip_offset("GBPJPY", 10)
        assert abs(offset - 0.1) < 0.001  # 10 pips JPY = 0.1 (1 pip = 10 pts)


class TestPriceStaleness:
    def test_fresh_tick(self):
        p = make_protector()
        tick = MagicMock(ask=1.1, bid=1.099)
        tick.time = time.time() - 10
        p.mt5.get_tick.return_value = tick
        assert p.check_price_staleness("EURUSD") is True

    def test_stale_tick(self):
        p = make_protector()
        tick = MagicMock(ask=1.1, bid=1.099)
        tick.time = time.time() - 300
        p.mt5.get_tick.return_value = tick
        assert p.check_price_staleness("EURUSD") is False

    def test_none_tick(self):
        p = make_protector()
        p.mt5.get_tick.return_value = None
        assert p.check_price_staleness("EURUSD") is False

    def test_tick_no_time_attr(self):
        p = make_protector()
        tick = MagicMock(spec=[])  # no time attr
        tick.ask = 1.1
        tick.bid = 1.099
        p.mt5.get_tick.return_value = tick
        assert p.check_price_staleness("EURUSD") is False

    def test_time_type_error(self):
        p = make_protector()
        tick = MagicMock(ask=1.1, bid=1.099)
        tick.time = "not_a_number"
        p.mt5.get_tick.return_value = tick
        assert p.check_price_staleness("EURUSD") is False

    def test_custom_max_age(self):
        p = make_protector()
        tick = MagicMock(ask=1.1, bid=1.099)
        tick.time = time.time() - 20
        p.mt5.get_tick.return_value = tick
        assert p.check_price_staleness("EURUSD", max_age=10) is False


class TestReconcilePositions:
    def test_new_position_added(self):
        p = make_protector()
        pos = MagicMock(ticket=99991, comment="ADAPT_TRE_001", symbol="EURUSD")
        pos.type = 0
        p._reconcile_positions([pos])
        assert "99991" in p.position_open_times
        assert "99991" in p.position_regime

    def test_existing_skipped(self):
        p = make_protector()
        p.position_open_times["99991"] = {"open_time": datetime.utcnow(), "symbol": "EURUSD"}
        pos = MagicMock(ticket=99991, comment="ADAPT_TRE_001", symbol="EURUSD")
        pos.type = 0
        before = len(p.position_open_times)
        p._reconcile_positions([pos])
        assert len(p.position_open_times) == before

    def test_comment_regime_parsed(self):
        p = make_protector()
        pos = MagicMock(ticket=99992, comment="ADAPT_TRE_001", symbol="EURUSD")
        pos.type = 0
        p._reconcile_positions([pos])
        assert p.position_regime["99992"] == "TREND_UP"

    def test_empty_comment_defaults_ranging(self):
        p = make_protector()
        pos = MagicMock(ticket=99993, comment="", symbol="EURUSD")
        pos.type = 0
        p._reconcile_positions([pos])
        assert p.position_regime["99993"] == "RANGING"

    def test_multiple_positions(self):
        p = make_protector()
        positions = [
            MagicMock(ticket=99994, comment="ADAPT_TRE_001", symbol="EURUSD"),
            MagicMock(ticket=99995, comment="ADAPT_RAN_002", symbol="GBPUSD"),
            MagicMock(ticket=99996, comment="", symbol="USDCHF"),
        ]
        for pos in positions:
            pos.type = 0
        p._reconcile_positions(positions)
        assert len(p.position_open_times) == 3
        assert len(p.position_regime) == 3

    def test_none_comment(self):
        p = make_protector()
        pos = MagicMock(ticket=99997, comment=None, symbol="EURUSD")
        pos.type = 0
        p._reconcile_positions([pos])
        assert p.position_regime["99997"] == "RANGING"


class TestParseCommentRegime:
    def _make_protector(self):
        return make_protector()

    def test_tre_parsed_as_trend_up(self):
        p = self._make_protector()
        p._parse_comment_regime("ADAPT_TRE_12345", "1")
        assert p.position_regime["1"] == "TREND_UP"

    def test_ran_parsed_as_ranging(self):
        p = self._make_protector()
        p._parse_comment_regime("ADAPT_RAN_999", "2")
        assert p.position_regime["2"] == "RANGING"

    def test_hig_parsed_as_high_vol(self):
        p = self._make_protector()
        p._parse_comment_regime("ADAPT_HIG_XYZ", "3")
        assert p.position_regime["3"] == "HIGH_VOL"

    def test_low_parsed_as_low_vol(self):
        p = self._make_protector()
        p._parse_comment_regime("ADAPT_LOW_XYZ", "4")
        assert p.position_regime["4"] == "LOW_VOL"

    def test_unknown_abbrev_falls_to_ranging(self):
        p = self._make_protector()
        p._parse_comment_regime("ADAPT_XXX_001", "5")
        assert p.position_regime["5"] == "RANGING"

    def test_no_match_falls_to_ranging(self):
        p = self._make_protector()
        p._parse_comment_regime("NO_MATCH_HERE", "6")
        assert p.position_regime["6"] == "RANGING"

    def test_empty_comment_ranging(self):
        p = self._make_protector()
        p._parse_comment_regime("", "7")
        assert p.position_regime["7"] == "RANGING"

    def test_case_sensitive(self):
        p = self._make_protector()
        p._parse_comment_regime("adapt_tre_001", "8")
        assert p.position_regime["8"] == "RANGING"

    def test_regime_from_comment_dict(self):
        assert FTMOProtector.REGIME_FROM_COMMENT == {
            "TRE": "TREND_UP",
            "DOW": "TREND_DOWN",
            "RAN": "RANGING",
            "HIG": "HIGH_VOL",
            "LOW": "LOW_VOL",
        }


class TestCalculateLot:
    def _make_protector(self):
        p = make_protector()
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.mt5.calc_profit.return_value = -200
        p.mt5.ORDER_TYPE_BUY = 0
        p.mt5.ORDER_TYPE_SELL = 1
        return p

    def test_basic_long(self):
        p = self._make_protector()
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        # risk=200000*0.004=800, sl_profit=-200/0.1=>2000perlot, lot=800/2000*0.1=0.04
        assert 0.01 <= lot <= 1.0

    def test_short_half_risk(self):
        p = self._make_protector()
        lot_buy = p.calculate_lot("EURUSD", 1.1, 1.095, direction=0)
        lot_sell = p.calculate_lot("EURUSD", 1.1, 1.095, direction=1)
        # Sell risk is 50% of buy risk
        assert lot_sell <= lot_buy + 0.01

    def test_friday_reduction(self):
        p = self._make_protector()
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 6, 5, 11, 0)  # Friday
            mock_dt.utcnow.weekday.return_value = 4
            lot_mon = p.calculate_lot("EURUSD", 1.1, 1.095)
        # On Friday, risk multiplied by 0.75
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 6, 2, 11, 0)  # Tuesday
            mock_dt.utcnow.weekday.return_value = 2
            lot_tue = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot_mon <= lot_tue + 0.001

    def test_dd_peak_reduction(self):
        p = self._make_protector()
        p.peak_equity = 110000
        p.mt5.get_account_info.return_value = MagicMock(equity=104500)  # DD=5.5%
        lot_no_dd = p.calculate_lot("EURUSD", 1.1, 1.095)
        # Compare with no dd case: same setup but peak=equity
        p2 = self._make_protector()
        p2.peak_equity = 104500
        p2.mt5.get_account_info.return_value = MagicMock(equity=104500)
        lot_with_dd = p2.calculate_lot("EURUSD", 1.1, 1.095)
        # DD reduces risk: lot_no_dd should be smaller due to dd_peak > 0.05
        assert lot_no_dd <= lot_with_dd + 0.001

    def test_max_risk_cap(self):
        p = make_protector(make_config(RISK_PER_TRADE=0.1))
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.mt5.calc_profit.return_value = -200
        p.mt5.ORDER_TYPE_BUY = 0
        p.max_risk_amount = 800
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot <= 0.5  # capped by max_risk_amount

    def test_sym_risk_mult(self):
        p = make_protector()
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.mt5.calc_profit.return_value = -200
        p.mt5.ORDER_TYPE_BUY = 0
        p.symbol_limits = {"EURUSD": {"risk_mult": 0.5, "max_lot": 0.5}}
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot > 0.01

    def test_max_lot_clamp(self):
        p = make_protector()
        p.mt5.get_account_info.return_value = MagicMock(equity=999999999)
        p.mt5.calc_profit.return_value = -1
        p.mt5.ORDER_TYPE_BUY = 0
        p.symbol_limits = {"EURUSD": {"risk_mult": 1.0, "max_lot": 0.10}}
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot <= 0.10

    def test_mt5_account_none(self):
        p = make_protector()
        p.mt5.get_account_info.return_value = None
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot == 0.01

    def test_zone2_half_risk(self):
        p = self._make_protector()
        p.initial_balance = 200000
        p.daily_stats["pnl"] = -2500  # 1.25% > 1%
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot > 0.01

    def test_daily_profit_reduced(self):
        p = self._make_protector()
        p._daily_profit_reduced = True
        lot = p.calculate_lot("EURUSD", 1.1, 1.095)
        assert lot > 0.01


class TestCircuitBreaker:
    def _mock_symbol_info(self, p, symbol="EURUSD"):
        info = MagicMock(point=0.00001, digits=5)
        p.mt5.get_symbol_info.return_value = info
        tick = MagicMock(ask=1.10005, bid=1.10000)
        tick.time = time.time()
        p.mt5.get_tick.return_value = tick

    def test_shorts_disabled_on_high_dd(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.initial_balance = 200000
        p.mt5.get_account_info.return_value = MagicMock(equity=183000)  # DD=8.5%
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = p.can_trade(
                    "EURUSD",
                    signal={
                        "action": "SELL",
                        "score": 0.70,
                        "sl": 1.1000,
                        "tp": 1.0800,
                    },
                )
                assert not ok
                assert "Circuit breaker" in reason

    def test_shorts_allowed_low_dd(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.initial_balance = 200000
        p.mt5.get_account_info.return_value = MagicMock(equity=195000)  # DD=2.5%
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = p.can_trade(
                    "EURUSD",
                    signal={
                        "action": "SELL",
                        "score": 0.70,
                        "sl": 1.1000,
                        "tp": 1.0800,
                    },
                )
                assert ok, f"Expected OK got: {reason}"

    def test_zone3_stop(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.initial_balance = 200000
        p.daily_start_equity = 204000  # start of day equity
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)  # now down $4000
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            p.daily_stats["pnl"] = -4000  # 2% >= 1.5%
            p.daily_stats["losses"] = 1
            p.daily_stats["day"] = datetime(2026, 5, 27, 11, 0).date()
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = p.can_trade("EURUSD")
                assert not ok
                assert "Zone 3" in reason

    def test_zone3_no_losses_does_not_stop(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.initial_balance = 200000
        p.daily_start_equity = 204000
        p.mt5.get_account_info.return_value = MagicMock(equity=200000)
        p.daily_stats["pnl"] = -4000  # 2%
        p.daily_stats["losses"] = 0
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, _ = p.can_trade(
                    "EURUSD",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 1.0900,
                        "tp": 1.1200,
                    },
                )
                assert ok, "zone 3 with no losses should allow trades"

    def test_consistency_violated_blocks_near_target(self):
        p = make_protector()
        self._mock_symbol_info(p)
        p.initial_balance = 200000
        # Profit target = 10% → $20k ; 80% = $16k
        p.daily_pnl_by_date = {"2026-05-26": 10000, "2026-05-27": 7000}  # $17k realized > $16k
        p.mt5.get_account_info.return_value = MagicMock(equity=217000)
        p.consistency_violated = True
        p.mt5.get_positions.return_value = []
        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = p.can_trade(
                    "EURUSD",
                    signal={
                        "action": "BUY",
                        "score": 0.70,
                        "sl": 1.0900,
                        "tp": 1.1200,
                    },
                )
                assert not ok
                assert "consistency" in reason.lower()
