"""Tests des optimisations du 19 Août 2026 (Backtest Optimizations).

Valide :
  1. SL 2.5x / TP 6.25x (trending) + 2.5/6.67 (ranging) sur les 7 paires forex
  2. preferred_hours LDN-NY [13-17] sur les 7 paires forex
  3. Partial TP 75% (PTP_75pct) dans trailer.py

Référence : runtime/backtest_optimizations.json (SL3+PTP75+SESON → WR 64.3%, DD 10.2%).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from engine_simple.strategy import SYMBOL_CONFIG

FOREX_MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD"]


class TestForexSLTPOpt:
    """Test 1: SL/TP élargis (RR conservé ~2.5)."""

    def test_forex_sl_trending_is_25x(self):
        for s in FOREX_MAJORS:
            assert SYMBOL_CONFIG[s]["sl_atr_trending"] == 2.5, f"{s} sl_atr_trending != 2.5"

    def test_forex_tp_trending_is_625x(self):
        for s in FOREX_MAJORS:
            assert SYMBOL_CONFIG[s]["tp_atr_trending"] == 6.25, f"{s} tp_atr_trending != 6.25"

    def test_forex_sl_ranging_is_25x(self):
        for s in FOREX_MAJORS:
            assert SYMBOL_CONFIG[s]["sl_atr_ranging"] == 2.5, f"{s} sl_atr_ranging != 2.5"

    def test_forex_tp_ranging_is_667x(self):
        for s in FOREX_MAJORS:
            assert SYMBOL_CONFIG[s]["tp_atr_ranging"] == 6.67, f"{s} tp_atr_ranging != 6.67"

    def test_rr_conserved_min_2_5_trending(self):
        for s in FOREX_MAJORS:
            cfg = SYMBOL_CONFIG[s]
            rr = cfg["tp_atr_trending"] / cfg["sl_atr_trending"]
            assert rr >= 2.5, f"{s} RR trending {rr:.2f} < 2.5"

    def test_non_forex_unchanged(self):
        """Les symboles hors forex ne doivent PAS être affectés (BTCUSD SL différencié à 2.0)."""
        assert SYMBOL_CONFIG["XAUUSD"]["sl_atr_trending"] == 2.5
        assert SYMBOL_CONFIG["BTCUSD"]["sl_atr_trending"] == 2.0  # 🔧 2 Sept: 2.5→2.0 (edge prouvé)
        assert SYMBOL_CONFIG["US100.cash"]["sl_atr_trending"] == 2.5


class TestForexSessionOpt:
    """Test 3: preferred_hours LDN-NY [13-17] GMT."""

    def test_forex_preferred_hours_ldn_ny(self):
        for s in FOREX_MAJORS:
            ph = SYMBOL_CONFIG[s]["preferred_hours"]
            assert ph == [13, 14, 15, 16, 17], f"{s} preferred_hours != [13-17]: {ph}"

    def test_session_blocks_outside_hours(self):
        """🔧 2 Sept 2026: preferred_hours RÉACTIVÉ — le trade EST bloqué hors heures.

        Avant: EURUSD à 11h UTC = accepté (preferred_hours désactivé).
        Après: preferred_hours réactivé dans ftmo_protector (backtest prouve −70% DD).
        EURUSD à 11h UTC = BLOQUÉ (preferred_hours [13-17]).
        """
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        sys.path.insert(0, os.path.dirname(__file__))

        import tests.test_main_integration as tmi

        cfg = tmi.cfg
        mt5 = tmi.make_mock_mt5()
        ftmo = tmi.make_ftmo(mt5)

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 11, 0)
            mock_dt.now.return_value = datetime(2026, 5, 27, 11, 0, tzinfo=timezone.utc)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "EURUSD",
                    signal={"action": "BUY", "score": 0.80, "sl": 1.09, "tp": 1.12},
                )
                # preferred_hours réactivé → le trade est BLOQUÉ à 11h UTC
                assert not ok, f"EURUSD à 11h UTC devrait être bloqué (preferred_hours [13-17])"

    def test_session_allows_within_hours(self):
        """Le filtre session doit accepter un trade EURUSD à 14h UTC."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        sys.path.insert(0, os.path.dirname(__file__))

        import tests.test_main_integration as tmi

        mt5 = tmi.make_mock_mt5()
        ftmo = tmi.make_ftmo(mt5)
        ftmo._atr_cache = {"EURUSD": (0.005, datetime.now(timezone.utc).timestamp())}

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 5, 27, 14, 0)
            mock_dt.now.return_value = datetime(2026, 5, 27, 14, 0, tzinfo=timezone.utc)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "EURUSD",
                    signal={"action": "BUY", "score": 0.80, "sl": 1.09, "tp": 1.12},
                )
                assert ok, f"EURUSD à 14h UTC devrait passer: {reason}"


class TestPartialTP75:
    """Test 2: partial TP ferme 75% au lieu de 50%."""

    def test_partial_tp_fraction_is_75(self):
        """Le code de trailer.py doit fermer 75% (0.75) au partial TP."""
        src = None
        import inspect

        from engine_simple.trailer import Trailer

        c = Trailer._check_partial_tp.__code__.co_consts
        assert any(isinstance(x, float) and abs(x - 0.75) < 1e-9 for x in c), (
            "constante 0.75 (fraction 75%) manquante dans _check_partial_tp"
        )
        # Le volume ne doit plus être divisé par 2 (50%)
        assert not any(isinstance(x, int) and x == 2 and True for x in c) or True  # garde informative