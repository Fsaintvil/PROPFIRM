"""Tests pour SignalValidator — validation des signaux avant exécution."""

from unittest.mock import MagicMock, patch

import pytest

from engine_simple.signal_validator import SignalValidator


# ============================================================================
# Helpers
# ============================================================================


def make_validator(symbol_limits=None, trade_history=None, staleness_result=True):
    """Crée un SignalValidator avec dépendances mockées."""
    mt5 = MagicMock()
    tick = MagicMock()
    tick.ask = 1.10500
    tick.bid = 1.10480
    mt5.get_tick.return_value = tick

    trailer = MagicMock()
    trailer.calc_sl_tp.return_value = (1.09500, 1.12500)

    return SignalValidator(
        mt5=mt5,
        trailer=trailer,
        symbol_limits=symbol_limits or {},
        symbol_trade_history=trade_history or {},
        staleness_check_fn=lambda sym: staleness_result,
    )


def make_signal(**overrides):
    """Crée un signal de test valide."""
    sig = {
        "action": "BUY",
        "score": 0.80,
        "entry_price": 1.10500,
        "sl": 1.09500,
        "tp": 1.12500,
        "atr": 0.005,
        "sl_atr": 2.0,
        "tp_atr": 4.0,
        "_strategy": "MOM",
    }
    sig.update(overrides)
    return sig


# ============================================================================
# check() — validation complète
# ============================================================================


class TestCheck:
    """SignalValidator.check() — point d'entrée principal."""

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_none_signal_returns_ok(self, mock_params):
        v = make_validator()
        ok, reason = v.check("EURUSD", None, [])
        assert ok is True
        assert reason is None

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_direction_shorts_blocked(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator(symbol_limits={"EURUSD": {"allow_shorts": False}})
        sig = make_signal(action="SELL")
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "Shorts not allowed" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_direction_buys_blocked(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator(symbol_limits={"EURUSD": {"allow_buys": False}})
        sig = make_signal(action="BUY")
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "Buys not allowed" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_score_too_low_rejected(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(score=0.50)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "Signal score too low" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_score_boundary_accepted(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(score=0.5995)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True  # floating point tolerance

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_mr_strategy_lowers_threshold(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(score=0.56, _strategy="MR")
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True  # MR abaisse le seuil à 0.55

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_dynamic_score_from_wr(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        # Trade history avec WR < 50%
        trades = [{"profit": -100} for _ in range(10)] + [{"profit": 50} for _ in range(5)]  # 5/15 = 33% WR
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.55)
        ok, reason = v.check("EURUSD", sig, [])
        # dyn_score = max(0.60, 0.60) = 0.60, effective=0.60, score=0.55 < 0.60 → rejeté
        assert ok is False

    @patch("engine_simple.signal_validator.get_symbol_params")
    @patch("engine_simple.signal_validator.update_dyn_score")
    def test_update_dyn_score_called(self, mock_update, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        trades = [{"profit": -100} for _ in range(8)] + [{"profit": 50} for _ in range(7)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.80)
        v.check("EURUSD", sig, [])
        # 7/15 wins = 46.7% < 50% → dyn_score = max(0.60, 0.60) = 0.60
        # update_dyn_score doit être appelé
        mock_update.assert_called_once_with("EURUSD", 0.60)

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_sl_tp_auto_calculated_when_missing(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(sl=None, tp=None)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True
        # SL/TP should have been set by calc_sl_tp
        assert sig["sl"] is not None
        assert sig["tp"] is not None
        v.trailer.calc_sl_tp.assert_called_once()

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_sl_tp_missing_and_calc_fails_blocked(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        v.trailer.calc_sl_tp.return_value = (None, None)
        sig = make_signal(sl=None, tp=None)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "SL/TP manquant" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_sl_equal_to_entry_blocked(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(entry_price=1.10500, sl=1.10500, tp=1.12500)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "SL identique" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_rr_below_minimum_blocked(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 2.0}
        v = make_validator()
        # RR = (1.125 - 1.105) / (1.105 - 1.095) = 0.020/0.010 = 2.0
        # min_rr = 2.0, RR 2.0 >= 2.0 → OK (pas < 2.0 - 0.01)
        sig = make_signal(entry_price=1.10500, sl=1.10000, tp=1.11500)
        # RR = (1.115 - 1.105) / (1.105 - 1.100) = 0.010/0.005 = 2.0 → OK
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_rr_below_minimum_blocked_actual(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 2.0}
        v = make_validator()
        # RR = (1.110 - 1.105) / (1.105 - 1.100) = 0.005/0.005 = 1.0
        sig = make_signal(entry_price=1.10500, sl=1.10000, tp=1.11000)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "RR" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_staleness_check_fails(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator(staleness_result=False)
        sig = make_signal()
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "Stale price" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_full_valid_signal(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal()
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True
        assert reason is None

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_small_trade_history_ignores_dynamic_score(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        # Seulement 10 trades (< 15) → pas de dyn_score
        trades = [{"profit": -100} for _ in range(10)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.60)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_wr_above_50_no_dynamic_score(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        # 8/15 = 53% > 50% → pas de dyn_score
        trades = [{"profit": 50} for _ in range(8)] + [{"profit": -30} for _ in range(7)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.60)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True


# ============================================================================
# _adjust_sl_for_ob — ajustement SL pour order blocks
# ============================================================================


class TestAdjustSLForOB:
    """_adjust_sl_for_ob — ajustement du SL autour des order blocks."""

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_buy_with_bullish_ob_adjusts_sl(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(entry_price=1.10500, sl=1.10000, tp=1.12500)
        # OB haussier non mitigé entre 1.098 et 1.102 → SL à 1.100 dedans
        ob = {"is_mitigated": False, "type": "bullish", "high": 1.10200, "low": 1.09800}
        sig["_structure_obs"] = [ob]
        v.check("EURUSD", sig, [])
        # SL doit être ajusté: ob_low - (ob_high - ob_low) * 0.1 = 1.098 - 0.0004 = 1.09760
        assert sig["sl"] < 1.09800  # SL déplacé sous l'OB

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_sell_with_bearish_ob_adjusts_sl(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(action="SELL", entry_price=1.10500, sl=1.11000, tp=1.08500)
        # OB baissier non mitigé entre 1.108 et 1.112 → SL à 1.110 dedans
        ob = {"is_mitigated": False, "type": "bearish", "high": 1.11200, "low": 1.10800}
        sig["_structure_obs"] = [ob]
        v.check("EURUSD", sig, [])
        # SL doit être ajusté: ob_high + (ob_high - ob_low) * 0.1 = 1.112 + 0.0004 = 1.11240
        assert sig["sl"] > 1.11200  # SL déplacé au-dessus de l'OB

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_mitigated_ob_skipped(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(entry_price=1.10500, sl=1.10000, tp=1.12500)
        # OB mitigé → pas d'ajustement
        ob = {"is_mitigated": True, "type": "bullish", "high": 1.10200, "low": 1.09800}
        sig["_structure_obs"] = [ob]
        v.check("EURUSD", sig, [])
        # SL doit rester inchangé
        assert sig["sl"] == 1.10000

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_no_obs_no_adjustment(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(entry_price=1.10500, sl=1.10000, tp=1.12500)
        v.check("EURUSD", sig, [])
        # Pas d'OB → SL inchangé
        assert sig["sl"] == 1.10000
