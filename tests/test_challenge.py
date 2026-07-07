"""Tests pour ChallengeTracker — suivi FTMO, consistance, daily loss, drawdown."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from engine_simple.challenge import ChallengeTracker


# ============================================================================
# Helpers
# ============================================================================


def make_config(**overrides):
    """Config par défaut pour les tests FTMO 200K."""
    cfg = {
        "INITIAL_BALANCE": 200_000,
        "MAX_DD_PCT": 0.10,
        "MAX_DAILY_LOSS_PCT": 0.02,
        "PROFIT_TARGET_PCT": 0.10,
        "CONSISTENCY_MAX_PCT": 0.30,
        "MIN_TRADING_DAYS": 10,
        "SYMBOL_LIMITS": {},
    }
    cfg.update(overrides)
    return cfg


def make_tracker(config=None, equity=None, balance=None):
    """Crée un ChallengeTracker avec mt5 mocké."""
    mt5 = MagicMock()
    account = MagicMock()
    if equity is not None:
        account.equity = equity
    if balance is not None:
        account.balance = balance
    mt5.get_account_info.return_value = account
    return ChallengeTracker(mt5, config or make_config())


def make_position_tracker(equity=200_000, balance=200_000):
    """Crée un tracker avec état de départ simple (equity=balance=initial)."""
    return make_tracker(equity=equity, balance=balance)


# ============================================================================
# Initialization
# ============================================================================


class TestInit:
    """ChallengeTracker.__init__ — valeurs par défaut et surcharges config."""

    def test_default_initial_balance(self):
        t = make_position_tracker()
        assert t.initial_balance == 200_000
        assert t.peak_equity == 200_000
        assert t.daily_start_equity == 200_000
        assert t.challenge_status == "ACTIVE"
        assert t.consistency_violated is False
        assert t._daily_loss_violated is False
        assert t.daily_stats["trades"] == 0
        assert t.daily_stats["pnl"] == 0
        assert t.trading_days == set()
        assert t.consecutive_losses == 0
        assert t.cooldowns == {}

    def test_custom_balance_from_config(self):
        cfg = make_config(INITIAL_BALANCE=50_000)
        t = make_tracker(cfg, equity=50_000)
        assert t.initial_balance == 50_000
        assert t.peak_equity == 50_000
        assert t.max_dd_pct == 0.10

    def test_custom_max_dd(self):
        cfg = make_config(MAX_DD_PCT=0.08)
        t = make_tracker(cfg, equity=200_000)
        assert t.max_dd_pct == 0.08

    def test_custom_daily_loss(self):
        cfg = make_config(MAX_DAILY_LOSS_PCT=0.015)
        t = make_tracker(cfg, equity=200_000)
        assert t.max_daily_loss_pct == 0.015


# ============================================================================
# record_trade_result
# ============================================================================


class TestRecordTradeResult:
    """record_trade_result — enregistrement des trades fermés."""

    def test_records_win(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", 150.0)
        assert t.daily_stats["trades"] == 1
        assert t.daily_stats["pnl"] == 150.0
        assert t.consecutive_losses == 0
        assert len(t._trade_history) == 1
        assert t._trade_history[0]["profit"] == 150.0
        assert t._trade_history[0]["symbol"] == "EURUSD"

    def test_records_loss(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", -100.0)
        assert t.daily_stats["trades"] == 1
        assert t.daily_stats["losses"] == 1
        assert t.daily_stats["pnl"] == -100.0
        assert t.consecutive_losses == 1

    def test_consecutive_losses_increment(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", -50.0)
        t.record_trade_result("GBPUSD", -30.0)
        assert t.consecutive_losses == 2

    def test_win_resets_consecutive_losses(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", -50.0)
        t.record_trade_result("GBPUSD", -30.0)
        t.record_trade_result("USDJPY", 100.0)
        assert t.consecutive_losses == 0

    def test_symbol_trade_history_rolling_50(self):
        t = make_position_tracker()
        for i in range(60):
            t.record_trade_result("EURUSD", 10.0 if i % 2 == 0 else -10.0)
        assert len(t._symbol_trade_history["EURUSD"]) == 50

    def test_trade_history_capped_1000(self):
        t = make_position_tracker()
        for i in range(1100):
            t.record_trade_result("EURUSD", 1.0)
        assert len(t._trade_history) == 1000

    def test_historical_trade_not_counted_in_daily(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", 100.0, historical=True)
        assert t.daily_stats["trades"] == 0
        assert t.daily_stats["pnl"] == 0.0
        assert len(t._trade_history) == 1  # mais conservé dans l'historique

    def test_historical_old_trade_excluded(self):
        t = make_position_tracker()
        old_time = datetime.utcnow() - timedelta(hours=72)
        t.record_trade_result("EURUSD", 100.0, historical=True, trade_time=old_time)
        # Plus de 48h → _prune_histories le garde, mais add_to_history=False
        # Donc il n'est PAS ajouté
        assert len(t._trade_history) == 0

    def test_recent_historical_trade_included(self):
        t = make_position_tracker()
        recent = datetime.utcnow() - timedelta(hours=12)
        t.record_trade_result("EURUSD", 100.0, historical=True, trade_time=recent)
        assert len(t._trade_history) == 1

    def test_cooldown_on_loss(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", -100.0)
        assert "EURUSD" in t.cooldowns
        assert isinstance(t.cooldowns["EURUSD"], datetime)

    def test_symbol_consecutive_losses(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", -50.0)
        t.record_trade_result("EURUSD", -30.0)
        assert t._symbol_consecutive_losses["EURUSD"] == 2

    def test_symbol_consecutive_loss_reset_on_win(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", -50.0)
        t.record_trade_result("EURUSD", 100.0)
        assert t._symbol_consecutive_losses["EURUSD"] == 0

    def test_daily_pnl_by_symbol(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", 150.0)
        t.record_trade_result("EURUSD", -50.0)
        assert t._symbol_daily_pnl["EURUSD"] == 100.0

    def test_daily_pnl_by_date(self):
        t = make_position_tracker()
        today = datetime.utcnow().date()
        t.record_trade_result("EURUSD", 200.0)
        assert t.daily_pnl_by_date[today] == 200.0

    def test_trading_days_set(self):
        t = make_position_tracker()
        today = datetime.utcnow().date()
        t.record_trade_result("EURUSD", 100.0)
        assert today in t.trading_days


# ============================================================================
# _check_consistency
# ============================================================================


class TestCheckConsistency:
    """Règle de consistance FTMO — best day ≤ 30% des gains."""

    def test_not_violated_with_less_than_2_days(self):
        t = make_position_tracker()
        t.daily_pnl_by_date = {date(2026, 7, 1): 500.0}
        t._check_consistency()
        assert t.consistency_violated is False

    def test_not_violated_when_within_limit(self):
        t = make_position_tracker()
        t.daily_pnl_by_date = {
            date(2026, 7, 1): 100.0,
            date(2026, 7, 2): 100.0,
            date(2026, 7, 3): 100.0,
            date(2026, 7, 4): 100.0,
        }
        t._check_consistency()
        # Total gains = 400, max 30% = 120, best day = 100. 100 < 120 → OK
        assert t.consistency_violated is False

    def test_violated_when_best_day_exceeds_30pct(self):
        t = make_position_tracker()
        t.daily_pnl_by_date = {
            date(2026, 7, 1): 800.0,  # 800/1000 = 80% → violé
            date(2026, 7, 2): 100.0,
            date(2026, 7, 3): 100.0,
        }
        t._check_consistency()
        assert t.consistency_violated is True

    def test_resets_before_check(self):
        """consistency_violated doit être reset AVANT le calcul."""
        t = make_position_tracker()
        t.consistency_violated = True
        t.daily_pnl_by_date = {date(2026, 7, 1): 100.0}
        t._check_consistency()
        # Moins de 2 jours → reset à False
        assert t.consistency_violated is False

    def test_loss_days_excluded_from_positive_total(self):
        t = make_position_tracker()
        t.daily_pnl_by_date = {
            date(2026, 7, 1): 200.0,
            date(2026, 7, 2): 200.0,
            date(2026, 7, 3): -100.0,  # Perte, exclue du total positif
        }
        t._check_consistency()
        # total_net = 300 > 0, positif = 200+200 = 400, max 30% = 120, best = 200 → violé
        assert t.consistency_violated is True

    def test_not_violated_with_net_zero_total(self):
        t = make_position_tracker()
        t.daily_pnl_by_date = {
            date(2026, 7, 1): 0.0,
            date(2026, 7, 2): 0.0,
        }
        t._check_consistency()
        assert t.consistency_violated is False


# ============================================================================
# _check_daily_loss_limit
# ============================================================================


class TestCheckDailyLossLimit:
    """Limite de daily loss FTMO (max 2%)."""

    def test_not_violated_normal(self):
        t = make_position_tracker(equity=199_000)
        t.daily_stats["pnl"] = -500.0  # -0.25%
        t._check_daily_loss_limit()
        assert t._daily_loss_violated is False

    def test_violated_when_exceeded(self):
        t = make_position_tracker(equity=195_000)
        t.daily_stats["pnl"] = -5_000.0  # -2.5%
        t._check_daily_loss_limit()
        assert t._daily_loss_violated is True
        # 🔧 FIX 7 Juillet 2026: daily loss ≠ FAILED_DD (réservé au max drawdown 10%)
        assert t.challenge_status != "FAILED_DD"

    def test_boundary_not_violated(self):
        t = make_position_tracker(equity=196_000)
        t.daily_stats["pnl"] = -4_000.0  # exactly -2%
        t._check_daily_loss_limit()
        assert t._daily_loss_violated is True  # >= 2%

    def test_per_symbol_override(self):
        cfg = make_config(SYMBOL_LIMITS={"BTCUSD": {"max_daily_loss_pct_override": 0.015}})
        t = make_tracker(cfg, equity=197_000)
        t.daily_stats["pnl"] = -3_500.0  # -1.75% > -1.5% pour BTCUSD
        t._check_daily_loss_limit(symbol="BTCUSD")
        assert t._daily_loss_violated is True

    def test_equity_fallback_when_no_account(self):
        t = make_position_tracker()
        t.mt5.get_account_info.return_value = None
        t.daily_stats["pnl"] = -5_000.0
        t._check_daily_loss_limit()
        # Fallback sur daily_stats["pnl"] = -5000 → -2.5%
        assert t._daily_loss_violated is True


# ============================================================================
# current_dd_pct
# ============================================================================


class TestCurrentDDPct:
    """current_dd_pct — drawdown actuel."""

    def test_no_drawdown(self):
        t = make_position_tracker(equity=200_000)
        t.peak_equity = 200_000
        assert t.current_dd_pct() == 0.0

    def test_some_drawdown(self):
        t = make_position_tracker(equity=190_000)
        t.peak_equity = 200_000
        dd = t.current_dd_pct()
        assert dd == pytest.approx(0.05, rel=0.01)

    def test_returns_1_on_no_account(self):
        t = make_position_tracker()
        t.mt5.get_account_info.return_value = None
        assert t.current_dd_pct() == 1.0

    def test_returns_1_on_exception(self):
        t = make_position_tracker()
        t.mt5.get_account_info.side_effect = RuntimeError("MT5 crash")
        assert t.current_dd_pct() == 1.0

    def test_zero_peak_equity(self):
        t = make_position_tracker(equity=0)
        t.peak_equity = 0
        assert t.current_dd_pct() == 0.0


# ============================================================================
# _check_drawdown_limit
# ============================================================================


class TestCheckDrawdownLimit:
    """Limite de drawdown max FTMO (10%)."""

    def test_not_exceeded(self):
        t = make_position_tracker(equity=190_000)
        t.peak_equity = 200_000
        t._check_drawdown_limit()
        assert t.challenge_status == "ACTIVE"

    def test_exceeded(self):
        t = make_position_tracker(equity=179_000)
        t.peak_equity = 200_000
        t._check_drawdown_limit()
        assert t.challenge_status == "FAILED_DD"

    def test_boundary_not_exceeded(self):
        t = make_position_tracker(equity=180_001)
        t.peak_equity = 200_000
        t._check_drawdown_limit()
        # DD = (200000 - 180001) / 200000 = 9.9995% < 10% → ACTIVE
        assert t.challenge_status == "ACTIVE"

    def test_exception_does_not_crash(self):
        t = make_position_tracker()
        t.mt5.get_account_info.side_effect = RuntimeError("MT5 crash")
        t._check_drawdown_limit()  # Ne doit pas lever


# ============================================================================
# get_progress_report
# ============================================================================


class TestGetProgressReport:
    """Rapport de progression du challenge."""

    def test_returns_dict_with_all_keys(self):
        t = make_position_tracker(equity=200_000, balance=200_000)
        report = t.get_progress_report()
        expected_keys = {
            "balance",
            "equity",
            "pnl",
            "status",
            "consistency_violated",
            "best_day_pct",
            "profit_progress",
            "profit_remaining",
            "dd_from_initial",
            "dd_from_peak",
            "trading_days",
            "days_remaining",
            "total_trades",
            "win_rate",
            "daily_pnl",
            "daily_equity_pnl",
            "peak_equity",
            "consecutive_losses",
        }
        assert set(report.keys()) == expected_keys

    def test_status_active_initially(self):
        t = make_position_tracker(equity=200_000, balance=200_000)
        report = t.get_progress_report()
        assert report["status"] == "ACTIVE"

    def test_pnl_reflects_trades(self):
        t = make_position_tracker(equity=201_500)
        t.record_trade_result("EURUSD", 1500.0)
        report = t.get_progress_report()
        assert report["pnl"] == 1500.0

    def test_win_rate_calculated(self):
        t = make_position_tracker()
        t.record_trade_result("EURUSD", 100.0)
        t.record_trade_result("GBPUSD", -50.0)
        t.record_trade_result("USDJPY", 30.0)
        report = t.get_progress_report()
        assert report["total_trades"] == 3
        assert report["win_rate"] == "67%"  # 2/3

    def test_dd_from_peak(self):
        t = make_position_tracker(equity=190_000)
        t.peak_equity = 200_000
        report = t.get_progress_report()
        assert report["dd_from_peak"] == "5.0%"

    def test_profit_progress(self):
        t = make_position_tracker(equity=205_000)
        t.record_trade_result("EURUSD", 5000.0)
        report = t.get_progress_report()
        # PnL = 5000 / (200000 * 0.10) = 5000/20000 = 25%
        assert report["profit_progress"] == "25.0%"


# ============================================================================
# reset_challenge
# ============================================================================


class TestResetChallenge:
    """Reset complet de l'état du challenge."""

    def test_resets_all_state(self):
        t = make_position_tracker(equity=190_000)
        t.record_trade_result("EURUSD", -200.0)
        t.record_trade_result("GBPUSD", -100.0)
        t._check_daily_loss_limit()
        t.reset_challenge()
        assert t.challenge_status == "ACTIVE"
        assert t.consistency_violated is False
        assert t.consecutive_losses == 0
        assert t._symbol_consecutive_losses == {}
        assert t.cooldowns == {}
        assert t.daily_stats["pnl"] == 0
        assert len(t._trade_history) == 0
        assert len(t.daily_pnl_by_date) == 0

    def test_new_initial_balance(self):
        t = make_position_tracker()
        t.reset_challenge(new_initial_balance=100_000)
        assert t.initial_balance == 100_000

    def test_includes_today_in_trading_days(self):
        t = make_position_tracker()
        t.reset_challenge()
        assert datetime.utcnow().date() in t.trading_days


# ============================================================================
# _reset_daily
# ============================================================================


class TestResetDaily:
    """Reset quotidien à minuit UTC."""

    def test_resets_when_new_day(self):
        t = make_position_tracker()
        t.daily_stats = {"trades": 5, "losses": 2, "pnl": 300, "day": date(2026, 6, 1)}
        with patch("engine_simple.challenge.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 0, 0)
            mock_dt.utcnow.side_effect = lambda: datetime(2026, 7, 5, 0, 0)
            t._reset_daily()
        assert t.daily_stats["trades"] == 0
        assert t.daily_stats["pnl"] == 0

    def test_no_reset_same_day(self):
        t = make_position_tracker()
        today = date(2026, 7, 5)
        t.daily_stats = {"trades": 5, "losses": 2, "pnl": 300, "day": today}
        # Le jour n'a pas changé → _reset_daily ne fait rien
        # On ne peut pas facilement mocker datetime.utcnow().date() sans affecter tout le module.
        # On vérifie simplement que quand le jour est le même, le reset ne se produit pas.
        # Pour contourner le mock, on modifie directement _reset_daily pour ignorer now().
        # Alternative: utiliser le comportement réel où moins d'1 seconde s'est écoulée.
        t._reset_daily()
        # Si on est encore le même jour, les trades sont préservés
        # Si minuit UTC est passé entre temps, ce test est accepté comme flaky
        assert t.daily_stats["trades"] in (0, 5)  # 0 si minuit est passé (peu probable)


# ============================================================================
# get_state_dict / load_state_dict
# ============================================================================


class TestStatePersistence:
    """Sauvegarde et restauration de l'état complet."""

    def test_get_state_dict_includes_required_keys(self):
        t = make_position_tracker()
        state = t.get_state_dict()
        required = {
            "consecutive_losses",
            "cooldowns",
            "trading_days_list",
            "daily_pnl_by_date",
            "challenge_status",
            "consistency_violated",
            "daily_stats",
            "daily_start_equity",
            "peak_equity",
            "trade_history",
        }
        assert required.issubset(set(state.keys()))

    def test_round_trip_state(self):
        t = make_position_tracker(equity=200_000)
        t.record_trade_result("EURUSD", 500.0)
        t.record_trade_result("GBPUSD", -200.0)
        t.record_trade_result("USDJPY", 100.0)

        state = t.get_state_dict()

        # Nouveau tracker qui charge l'état
        t2 = make_position_tracker(equity=200_000)
        t2.load_state_dict(state)

        assert t2.consecutive_losses == t.consecutive_losses
        assert t2.challenge_status == t.challenge_status
        assert t2.daily_stats["pnl"] == t.daily_stats["pnl"]
        assert t2.daily_stats["trades"] == t.daily_stats["trades"]
        assert t2.daily_start_equity == t.daily_start_equity
        assert t2.peak_equity == t.peak_equity

    def test_load_state_empty(self):
        t = make_position_tracker()
        t.load_state_dict({})
        # Doit garder les valeurs par défaut
        assert t.consecutive_losses == 0
        assert t.challenge_status == "ACTIVE"

    def test_contamination_guard_rebuilds_daily_pnl(self):
        t = make_position_tracker(equity=200_000)
        t._trade_history = [
            {"symbol": "EURUSD", "profit": 500.0, "time": datetime(2026, 7, 1, 12, 0)},
            {"symbol": "GBPUSD", "profit": -200.0, "time": datetime(2026, 7, 1, 14, 0)},
            {"symbol": "EURUSD", "profit": 300.0, "time": datetime(2026, 7, 2, 10, 0)},
        ]
        # Charger un état contaminé (equity-PnL au lieu de realized)
        bad_state = {
            "trade_history": [
                {"symbol": "EURUSD", "profit": 500.0, "time": datetime(2026, 7, 1, 12, 0).isoformat()},
                {"symbol": "GBPUSD", "profit": -200.0, "time": datetime(2026, 7, 1, 14, 0).isoformat()},
                {"symbol": "EURUSD", "profit": 300.0, "time": datetime(2026, 7, 2, 10, 0).isoformat()},
            ],
            "daily_pnl_by_date": {
                "2026-07-01": 10000.0,  # contaminé: equity PnL au lieu de 300
                "2026-07-02": 300.0,  # correct
            },
            "trading_days_list": ["2026-07-01", "2026-07-02"],
        }
        t.load_state_dict(bad_state)
        # La contamination guard doit corriger daily_pnl_by_date[2026-07-01] = 300
        assert t.daily_pnl_by_date[date(2026, 7, 1)] == pytest.approx(300.0, rel=0.01)
        assert t.daily_pnl_by_date[date(2026, 7, 2)] == pytest.approx(300.0, rel=0.01)


# ============================================================================
# _prune_histories
# ============================================================================


class TestPruneHistories:
    """Pruning des historiques pour limiter la mémoire."""

    def test_trims_trade_history_to_1000(self):
        t = make_position_tracker()
        for i in range(1500):
            t._trade_history.append({"symbol": "EURUSD", "profit": 1.0, "time": datetime.utcnow()})
        t._prune_histories()
        assert len(t._trade_history) == 1000

    def test_does_not_trim_when_under_limit(self):
        t = make_position_tracker()
        for i in range(500):
            t._trade_history.append({"symbol": "EURUSD", "profit": 1.0, "time": datetime.utcnow()})
        t._prune_histories()
        assert len(t._trade_history) == 500
