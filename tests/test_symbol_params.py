"""Tests pour SymbolParamManager — paramètres unifiés par symbole."""

from unittest.mock import MagicMock, patch

import pytest

from engine_simple.symbol_params import (
    LOT_PROGRESSION_RULES,
    SymbolParamManager,
    configure,
    get_lot_for_wr,
    get_manager,
    get_symbol_param,
    get_symbol_params,
    update_dyn_score,
)


# ============================================================================
# LOT_PROGRESSION_RULES + get_lot_for_wr
# ============================================================================


class TestLotProgressionRules:
    """Règles de progression de lot basées sur WR."""

    def test_rules_are_ordered(self):
        """Les règles doivent être dans l'ordre croissant de WR."""
        for i in range(len(LOT_PROGRESSION_RULES) - 1):
            assert LOT_PROGRESSION_RULES[i][1] <= LOT_PROGRESSION_RULES[i + 1][0]

    def test_covers_full_range(self):
        """Les règles couvrent 0% à 100%."""
        assert LOT_PROGRESSION_RULES[0][0] == 0.00
        assert LOT_PROGRESSION_RULES[-1][1] == 1.01

    def test_get_lot_for_wr_below_60(self):
        assert get_lot_for_wr(0.50) == 0.05

    def test_get_lot_for_wr_60_to_65(self):
        assert get_lot_for_wr(0.62) == 0.10

    def test_get_lot_for_wr_65_to_70(self):
        assert get_lot_for_wr(0.68) == 0.15

    def test_get_lot_for_wr_70_to_75(self):
        assert get_lot_for_wr(0.72) == 0.25

    def test_get_lot_for_wr_75_to_80(self):
        assert get_lot_for_wr(0.78) == 0.35

    def test_get_lot_for_wr_above_80(self):
        assert get_lot_for_wr(0.95) == 0.50

    def test_get_lot_for_wr_zero_returns_base(self):
        assert get_lot_for_wr(0.0) == 0.05

    def test_get_lot_for_wr_custom_base(self):
        assert get_lot_for_wr(0.50, lot_base=0.10) == 0.10

    def test_get_lot_for_wr_custom_max(self):
        assert get_lot_for_wr(0.95, lot_max=0.30) == 0.30


# ============================================================================
# SymbolParamManager — singleton
# ============================================================================


class TestGetManager:
    """get_manager() — singleton pattern."""

    def test_returns_same_instance(self):
        m1 = get_manager()
        m2 = get_manager()
        assert m1 is m2

    def test_is_symbol_param_manager(self):
        m = get_manager()
        assert isinstance(m, SymbolParamManager)


# ============================================================================
# SymbolParamManager — init & injection
# ============================================================================


class TestSymbolParamManagerInit:
    """Initialisation et injection des dépendances."""

    def test_default_state(self):
        m = SymbolParamManager()
        assert m._tracker is None
        assert m._ftmo is None
        assert m._learner is None
        assert m._dyn_scores == {}

    def test_set_tracker(self):
        m = SymbolParamManager()
        tracker = MagicMock()
        m.set_tracker(tracker)
        assert m._tracker is tracker

    def test_set_ftmo(self):
        m = SymbolParamManager()
        ftmo = MagicMock()
        m.set_ftmo(ftmo)
        assert m._ftmo is ftmo

    def test_set_learner(self):
        m = SymbolParamManager()
        learner = MagicMock()
        m.set_learner(learner)
        assert m._learner is learner


# ============================================================================
# Dynamic min_score
# ============================================================================


class TestDynamicScore:
    """update_dyn_score / get_dyn_score."""

    def test_update_and_get(self):
        m = SymbolParamManager()
        m.update_dyn_score("EURUSD", 0.80)
        assert m.get_dyn_score("EURUSD") == 0.80

    def test_get_none_for_unknown(self):
        m = SymbolParamManager()
        assert m.get_dyn_score("UNKNOWN") is None

    def test_update_overwrites(self):
        m = SymbolParamManager()
        m.update_dyn_score("EURUSD", 0.70)
        m.update_dyn_score("EURUSD", 0.85)
        assert m.get_dyn_score("EURUSD") == 0.85


# ============================================================================
# _get_ol_params
# ============================================================================


class TestGetOLParams:
    """_get_ol_params — paramètres OnlineLearner."""

    def test_returns_empty_when_no_learner(self):
        m = SymbolParamManager()
        assert m._get_ol_params("EURUSD") == {}

    def test_returns_params_when_learner_set(self):
        m = SymbolParamManager()
        learner = MagicMock()
        learner.get_params.return_value = {
            "thresh": 2.3,
            "risk_mult": 0.85,
            "sl_mult": 1.8,
            "tp_mult": 4.5,
            "sample_size": 50,
        }
        m.set_learner(learner)
        params = m._get_ol_params("EURUSD")
        assert params["ol_thresh"] == 2.3
        assert params["ol_risk_mult"] == 0.85
        assert params["ol_sample_size"] == 50

    def test_exception_returns_empty(self):
        m = SymbolParamManager()
        learner = MagicMock()
        learner.get_params.side_effect = RuntimeError("OL failed")
        m.set_learner(learner)
        assert m._get_ol_params("EURUSD") == {}


# ============================================================================
# _get_tracker_metrics
# ============================================================================


class TestGetTrackerMetrics:
    """_get_tracker_metrics — métriques depuis PositionTracker."""

    def test_returns_empty_when_no_tracker(self):
        m = SymbolParamManager()
        assert m._get_tracker_metrics("EURUSD") == {}

    def test_returns_metrics_when_tracker_set(self):
        m = SymbolParamManager()
        tracker = MagicMock()
        perf = MagicMock()
        perf.trades = 55
        perf.wins = 30
        perf.losses = 25
        perf.win_rate = 0.5455
        perf.total_profit = 350.0
        perf.profit_factor = 1.35
        perf.avg_r_multiple = 1.5
        perf.gross_profit = 1350.0
        perf.gross_loss = -1000.0
        perf.consecutive_wins = 5
        perf.consecutive_losses = 3
        perf.max_consecutive_wins = 8
        perf.max_consecutive_losses = 4
        tracker.get_symbol_performance.return_value = perf
        m.set_tracker(tracker)
        metrics = m._get_tracker_metrics("EURUSD")
        assert metrics["Trades"] == 55
        assert metrics["WR_all"] == pytest.approx(0.5455, rel=0.01)
        assert metrics["PnL"] == 350.0
        assert metrics["PF"] == pytest.approx(1.35, rel=0.01)

    def test_exception_returns_empty(self):
        m = SymbolParamManager()
        tracker = MagicMock()
        tracker.get_symbol_performance.side_effect = RuntimeError("Tracker failed")
        m.set_tracker(tracker)
        assert m._get_tracker_metrics("EURUSD") == {}


# ============================================================================
# _get_wr_50
# ============================================================================


class TestGetWR50:
    """_get_wr_50 — win rate 50 derniers trades."""

    def test_returns_none_when_no_ftmo(self):
        m = SymbolParamManager()
        wr = m._get_wr_50("EURUSD")
        assert wr["WR_50"] is None

    def test_returns_none_with_few_trades(self):
        m = SymbolParamManager()
        ftmo = MagicMock()
        ftmo._symbol_trade_history = {"EURUSD": [{"profit": 10}] * 4}
        m.set_ftmo(ftmo)
        wr = m._get_wr_50("EURUSD")
        assert wr["WR_50"] is None
        assert wr["WR_50_trades"] == 4

    def test_calculates_wr_correctly(self):
        m = SymbolParamManager()
        ftmo = MagicMock()
        ftmo._symbol_trade_history = {"EURUSD": [{"profit": 100}] * 30 + [{"profit": -50}] * 20}
        m.set_ftmo(ftmo)
        wr = m._get_wr_50("EURUSD")
        # 30/50 = 60%
        assert wr["WR_50"] == pytest.approx(0.60, rel=0.01)
        assert wr["WR_50_trades"] == 50

    def test_exception_returns_safe_defaults(self):
        m = SymbolParamManager()
        ftmo = MagicMock()
        ftmo._symbol_trade_history.side_effect = AttributeError("no attr")
        m.set_ftmo(ftmo)
        wr = m._get_wr_50("EURUSD")
        assert wr["WR_50"] is None


# ============================================================================
# get_all_params (intégration partielle)
# ============================================================================


class TestGetAllParams:
    """get_all_params — assemble tous les paramètres."""

    @patch("engine_simple.symbol_params.SymbolParamManager._get_static_config")
    @patch("engine_simple.symbol_params.SymbolParamManager._get_ol_params")
    @patch("engine_simple.symbol_params.SymbolParamManager._get_tracker_metrics")
    @patch("engine_simple.symbol_params.SymbolParamManager._get_wr_50")
    @patch("engine_simple.symbol_params.SymbolParamManager._get_global_fallbacks")
    @patch("engine_simple.symbol_params.SymbolParamManager._get_trailing_params")
    @patch("engine_simple.symbol_params.SymbolParamManager._get_lot_params")
    def test_assembles_all_params(self, mock_lot, mock_trail, mock_global, mock_wr, mock_tracker, mock_ol, mock_static):
        m = SymbolParamManager()
        m.update_dyn_score("EURUSD", 0.80)

        mock_static.return_value = {
            "min_score": 0.70,
            "risk_mult": 1.0,
            "timeframe": "H1",
            "conf": 0.85,
            "min_rr": 1.5,
        }
        mock_ol.return_value = {"ol_thresh": 2.3}
        mock_tracker.return_value = {"Trades": 50, "WR_all": 0.64, "PnL": 200.0, "PF": 1.5}
        mock_wr.return_value = {"WR_50": 0.60, "WR_50_trades": 50}
        mock_global.return_value = {"max_spread_points": 40}
        mock_trail.return_value = {"trailing_levels": {}, "first_lock_atr": 1.5}
        mock_lot.return_value = {"lot_base": 0.05, "lot_current": 0.15}

        params = m.get_all_params("EURUSD")

        # Vérifier les sources
        assert params["timeframe"] == "H1"
        assert params["risk_mult"] == 1.0
        assert params["min_score"] == 0.70
        assert params["dyn_score"] == 0.80
        assert params["effective_min_score"] == 0.80  # max(0.80, 0.70)
        assert params["ol_thresh"] == 2.3
        assert params["Trades"] == 50
        assert params["WR_all"] == 0.64
        assert params["WR_50"] == 0.60
        assert params["lot_current"] == 0.15
        assert params["cfg_score"] == 0.70

    def test_works_without_dyn_score(self):
        m = SymbolParamManager()
        with patch.object(m, "_get_static_config", return_value={"min_score": 0.70}):
            with patch.object(m, "_get_ol_params", return_value={}):
                with patch.object(m, "_get_tracker_metrics", return_value={}):
                    with patch.object(m, "_get_wr_50", return_value={}):
                        with patch.object(m, "_get_global_fallbacks", return_value={}):
                            with patch.object(m, "_get_trailing_params", return_value={}):
                                with patch.object(m, "_get_lot_params", return_value={}):
                                    params = m.get_all_params("EURUSD")
        # effective_min_score = cfg_score (0.70) puisque pas de dyn_score
        assert params["effective_min_score"] == 0.70


# ============================================================================
# Fonctions module-level
# ============================================================================


class TestModuleFunctions:
    """get_symbol_params, get_symbol_param, update_dyn_score, configure."""

    def test_get_symbol_params_returns_dict(self):
        with patch("engine_simple.symbol_params.get_manager") as mock_get:
            mgr = MagicMock()
            mgr.get_all_params.return_value = {"risk_mult": 1.0}
            mock_get.return_value = mgr
            params = get_symbol_params("EURUSD")
            assert params["risk_mult"] == 1.0

    def test_get_symbol_param_returns_value(self):
        with patch("engine_simple.symbol_params.get_manager") as mock_get:
            mgr = MagicMock()
            mgr.get_all_params.return_value = {"risk_mult": 1.0}
            mock_get.return_value = mgr
            val = get_symbol_param("EURUSD", "risk_mult")
            assert val == 1.0

    def test_get_symbol_param_returns_default(self):
        with patch("engine_simple.symbol_params.get_manager") as mock_get:
            mgr = MagicMock()
            mgr.get_all_params.return_value = {"risk_mult": 1.0}
            mock_get.return_value = mgr
            val = get_symbol_param("EURUSD", "nonexistent", default=0.5)
            assert val == 0.5

    def test_update_dyn_score_delegates(self):
        with patch("engine_simple.symbol_params.get_manager") as mock_get:
            mgr = MagicMock()
            mock_get.return_value = mgr
            update_dyn_score("EURUSD", 0.85)
            mgr.update_dyn_score.assert_called_once_with("EURUSD", 0.85)

    def test_configure_sets_dependencies(self):
        m = SymbolParamManager()
        with patch("engine_simple.symbol_params.get_manager", return_value=m):
            tracker = MagicMock()
            ftmo = MagicMock()
            learner = MagicMock()
            configure(tracker=tracker, ftmo=ftmo, learner=learner)
            assert m._tracker is tracker
            assert m._ftmo is ftmo
            assert m._learner is learner
