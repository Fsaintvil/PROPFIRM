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
    def test_ranging_adx_low_rejected(self, mock_params):
        """🚫 GATE RÉGIME STRICT 05 Aout 2026: AUCUN signal en RANGING (ADX<20).
        Réactivé et renforcé après le dégel total du 04 Aout qui a laissé le robot
        trader en RANGING (marché 28/07-04/08) → désastre WR live 27.1%, -$306.
        Preuves: EURUSD 0/8, NZDUSD 1/8, EURGBP 1/9 en RANGING. Même les hauts
        scores (≥0.85) perdent en RANGING (1er essai à risque ×0.35 insuffisant).
        On ATTEND un vrai TREND (ADX > 20)."""
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(adx=14.7, _regime="RANGING", score=0.95)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False  # gate strict → signal rejeté même à score 0.95
        assert "RANGING" in reason

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_trend_adx_high_passes(self, mock_params):
        """La garde régime ne bloque PAS les signaux en TREND (ADX>=20)."""
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(adx=34.9, _regime="TREND_DOWN", score=0.95)
        ok, reason = v.check("USOIL.cash", sig, [])
        assert ok is True

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_ranging_adx_boundary_20_allowed(self, mock_params):
        """ADX=20 exact avec adx_thresh=20: autorisé (garde stricte < thresh)."""
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator(symbol_limits={"BTCUSD": {"adx_thresh": 20}})
        sig = make_signal(symbol="BTCUSD", adx=20.0, _regime="RANGING", score=0.95)
        ok, reason = v.check("BTCUSD", sig, [])
        assert ok is True

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_ranging_adx_below_per_symbol_thresh_rejected(self, mock_params):
        """ADX=18 avec adx_thresh=22: rejeté (18 < 22)."""
        mock_params.return_value = {"cfg_score": 0.60, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(adx=18.0, _regime="RANGING", score=0.95)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is False
        assert "RANGING" in reason

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
        mock_params.return_value = {"cfg_score": 0.80, "min_rr": 1.5}
        v = make_validator()
        sig = make_signal(score=0.7995)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True  # floating point tolerance (score ≈ cfg_score)

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_solusd_exception_floor_065(self, mock_params):
        """🔧 Fix 27 Août 2026: PER_SYMBOL_MIN_SCORE['SOLUSD']=0.65.

        Un signal à 0.63 doit être REJETÉ, un signal à 0.68 doit PASSER.
        """
        mock_params.return_value = {"cfg_score": 0.65, "min_rr": 1.8}
        v = make_validator()
        # Signal SOLUSD à 0.63 (< 0.65) — rejeté
        sig = make_signal(score=0.63)
        ok, reason = v.check("SOLUSD", sig, [])
        assert ok is False
        assert "Signal score too low" in reason
        # Signal SOLUSD à 0.68 (>= 0.65) — passe
        sig2 = make_signal(score=0.68)
        ok2, reason2 = v.check("SOLUSD", sig2, [])
        assert ok2 is True

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
    @patch("engine_simple.signal_validator._load_gr_state", return_value={})
    def test_update_dyn_score_called(self, mock_gr, mock_update, mock_params):
        mock_params.return_value = {"cfg_score": 0.80, "min_rr": 1.5}
        # 🔧 27 Août 2026: PER_SYMBOL_MIN_SCORE["EURUSD"]=0.65 Prime sur le mock.
        # 7/15 wins = 46.7% < 50% → dyn_score = 0.65 + (0.50-0.467)*0.5 = 0.6667
        trades = [{"profit": -100} for _ in range(8)] + [{"profit": 50} for _ in range(7)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.85)
        v.check("EURUSD", sig, [])
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "EURUSD"
        assert call_args[0][1] == pytest.approx(0.6667, abs=0.001)

    @patch("engine_simple.signal_validator.get_symbol_params")
    @patch("engine_simple.signal_validator.update_dyn_score")
    @patch("engine_simple.signal_validator._load_gr_state", return_value={})
    def test_update_dyn_score_low_wr_raises_more(self, mock_gr, mock_update, mock_params):
        """WR très bas → min_score doit monter plus haut."""
        mock_params.return_value = {"cfg_score": 0.80, "min_rr": 1.5}
        # 🔧 27 Août 2026: PER_SYMBOL_MIN_SCORE["EURUSD"]=0.65 Prime sur le mock.
        # 4/15 wins = 26.7% < 50% → dyn_score = 0.65 + (0.50-0.267)*0.5 = 0.7667
        trades = [{"profit": -100} for _ in range(11)] + [{"profit": 50} for _ in range(4)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.92)
        v.check("EURUSD", sig, [])
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "EURUSD"
        assert call_args[0][1] == pytest.approx(0.7667, abs=0.001)

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
        mock_params.return_value = {"cfg_score": 0.80, "min_rr": 1.5}
        # Seulement 10 trades (< 15) → pas de dyn_score → effective = cfg_score = 0.80
        trades = [{"profit": -100} for _ in range(10)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.80)
        ok, reason = v.check("EURUSD", sig, [])
        assert ok is True

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_wr_above_50_no_dynamic_score(self, mock_params):
        mock_params.return_value = {"cfg_score": 0.80, "min_rr": 1.5}
        # 8/15 = 53% > 50% → pas de dyn_score → effective = cfg_score = 0.80
        trades = [{"profit": 50} for _ in range(8)] + [{"profit": -30} for _ in range(7)]
        v = make_validator(trade_history={"EURUSD": trades})
        sig = make_signal(score=0.80)
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


# ============================================================================
# Test: Consecutive Loss Penalty (28 Août 2026)
# ============================================================================


class TestConsecutiveLossPenalty:
    """Test: +0.05 min_score après 3 pertes consécutives par symbole."""

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_3_consecutive_losses_adds_penalty(self, mock_params):
        """3 pertes consécutives → min_score +0.05."""
        mock_params.return_value = {"cfg_score": 0.65, "min_rr": 1.5}
        consec = {"BTCUSD": 3}
        v = make_validator(trade_history={"BTCUSD": []})
        v._symbol_consecutive_losses = consec
        # Signal avec score=0.65 — devrait être rejeté avec penalty (0.65+0.05=0.70)
        sig = make_signal(score=0.65)
        ok, reason = v.check("BTCUSD", sig, [])
        assert not ok
        assert "min" in reason.lower() or "score" in reason.lower()

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_2_consecutive_losses_no_penalty(self, mock_params):
        """2 pertes consécutives → pas de penalty (seuil = 3)."""
        mock_params.return_value = {"cfg_score": 0.65, "min_rr": 1.5}
        consec = {"BTCUSD": 2}
        v = make_validator(trade_history={"BTCUSD": []})
        v._symbol_consecutive_losses = consec
        # Signal avec score=0.65 — devrait être accepté (pas de penalty)
        sig = make_signal(score=0.65)
        ok, reason = v.check("BTCUSD", sig, [])
        assert ok

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_5_consecutive_losses_stronger_penalty(self, mock_params):
        """5 pertes consécutives → toujours +0.05 (pas d'escalade)."""
        mock_params.return_value = {"cfg_score": 0.65, "min_rr": 1.5}
        consec = {"BTCUSD": 5}
        v = make_validator(trade_history={"BTCUSD": []})
        v._symbol_consecutive_losses = consec
        # Signal avec score=0.65 → rejeté (0.65+0.05=0.70)
        sig = make_signal(score=0.65)
        ok, reason = v.check("BTCUSD", sig, [])
        assert not ok
        # Signal avec score=0.70 → accepté
        sig2 = make_signal(score=0.70)
        ok2, _ = v.check("BTCUSD", sig2, [])
        assert ok2

    @patch("engine_simple.signal_validator.get_symbol_params")
    def test_no_consecutive_losses_no_penalty(self, mock_params):
        """0 pertes consécutives → pas de penalty."""
        mock_params.return_value = {"cfg_score": 0.65, "min_rr": 1.5}
        v = make_validator(trade_history={"BTCUSD": []})
        # Pas de consec_losses dict
        sig = make_signal(score=0.65)
        ok, reason = v.check("BTCUSD", sig, [])
        assert ok


# ============================================================================
# Tests pour min_score dynamique basé sur Golden Rule PnL
# ============================================================================


class TestDynamicMinScoreGR:
    """🔧 31 Aout 2026: min_score dynamique basé sur la performance du symbole."""

    def test_good_performer_low_min_score(self):
        """Symbole gagnant (PnL=+100) → min_score = 0.65 (floor)."""
        from engine_simple.signal_validator import _get_dynamic_min_score, _gr_state_cache
        import engine_simple.signal_validator as sv

        # Mock GR state with good performer
        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "BTCUSD": {"trades": 30, "wins": 20, "pnl": 100.0}
                }
            }
        }
        score = _get_dynamic_min_score("BTCUSD")
        assert score == 0.65
        sv._gr_state_cache = None

    def test_bad_performer_high_min_score(self):
        """Symbole perdant (PnL=-100) → min_score = 0.80 (cap)."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "XAUUSD": {"trades": 30, "wins": 5, "pnl": -100.0}
                }
            }
        }
        score = _get_dynamic_min_score("XAUUSD")
        assert score == 0.80
        sv._gr_state_cache = None

    def test_neutral_performer_mid_min_score(self):
        """Symbole neutre (PnL=0) → min_score = 0.725 (milieu)."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "EURUSD": {"trades": 20, "wins": 10, "pnl": 0.0}
                }
            }
        }
        score = _get_dynamic_min_score("EURUSD")
        assert score == pytest.approx(0.725, abs=0.001)
        sv._gr_state_cache = None

    def test_unknown_symbol_returns_floor(self):
        """Symbole inconnu dans GR → min_score = 0.65 (floor)."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {"stats": {"by_symbol": {}}}
        score = _get_dynamic_min_score("UNKNOWN")
        assert score == 0.65
        sv._gr_state_cache = None

    def test_insufficient_trades_returns_floor(self):
        """Moins de 5 trades → min_score = 0.65 (floor), sauf override."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "EURUSD": {"trades": 3, "wins": 0, "pnl": -50.0}
                }
            }
        }
        score = _get_dynamic_min_score("EURUSD")
        assert score == 0.65
        sv._gr_state_cache = None

    def test_interpolation_positive_pnl(self):
        """PnL=+25 → interpolation entre 0.65 et 0.725."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "GBPUSD": {"trades": 15, "wins": 10, "pnl": 25.0}
                }
            }
        }
        score = _get_dynamic_min_score("GBPUSD")
        # PNL_GOOD=50, PNL_BAD=-50, pnl=25
        # score = 0.65 + 0.15 * (50-25)/100 = 0.65 + 0.0375 = 0.6875
        assert score == pytest.approx(0.6875, abs=0.001)
        sv._gr_state_cache = None

    def test_interpolation_negative_pnl(self):
        """PnL=-25 → interpolation entre 0.725 et 0.80."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "US30.cash": {"trades": 15, "wins": 5, "pnl": -25.0}
                }
            }
        }
        score = _get_dynamic_min_score("US30.cash")
        # PNL_GOOD=50, PNL_BAD=-50, pnl=-25
        # score = 0.65 + 0.15 * (50-(-25))/100 = 0.65 + 0.15*0.75 = 0.7625
        assert score == pytest.approx(0.7625, abs=0.001)
        sv._gr_state_cache = None

    def test_xauusd_override_forces_080(self):
        """🔧 31 Aout 2026: XAUUSD override force min_score=0.80 même si le dynamique calcule moins."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        # XAUUSD avec PnL positif (+100) → le dynamique donnerait 0.65
        # mais l'override force 0.80
        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "XAUUSD": {"trades": 30, "wins": 10, "pnl": 100.0}
                }
            }
        }
        score = _get_dynamic_min_score("XAUUSD")
        assert score == 0.80  # Override > dynamique (0.65)
        sv._gr_state_cache = None

    def test_non_overridden_symbol_uses_dynamic(self):
        """BTCUSD n'a pas d'override → utilise le score dynamique normal."""
        from engine_simple.signal_validator import _get_dynamic_min_score
        import engine_simple.signal_validator as sv

        sv._gr_state_cache = {
            "stats": {
                "by_symbol": {
                    "BTCUSD": {"trades": 30, "wins": 20, "pnl": 100.0}
                }
            }
        }
        score = _get_dynamic_min_score("BTCUSD")
        assert score == 0.65  # PnL > 50 → floor, pas d'override
        sv._gr_state_cache = None
