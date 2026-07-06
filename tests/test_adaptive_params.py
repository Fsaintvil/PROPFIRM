"""Tests pour AdaptiveParameters — ajustement dynamique des paramètres."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engine_simple.adaptive_params import (
    AdaptedParams,
    AdaptiveParameters,
    get_adapted_params,
    get_adaptive,
    record_trade,
)


class TestAdaptedParams:
    """AdaptedParams dataclass — paramètres adaptés pour un symbole."""

    def test_default_values(self):
        p = AdaptedParams()
        assert p.threshold_mult == 1.0
        assert p.risk_mult == 1.0
        assert p.sl_mult == 1.0
        assert p.tp_mult == 1.0
        assert p.trailing_mult == 1.0
        assert p.win_rate == 0.5
        assert p.profit_factor == 1.0
        assert p.confidence == 0.0
        assert p.sample_size == 0

    def test_to_dict_contains_all_keys(self):
        p = AdaptedParams(threshold_mult=0.9, risk_mult=1.2, win_rate=0.65, sample_size=50)
        d = p.to_dict()
        assert d["threshold_mult"] == 0.9
        assert d["risk_mult"] == 1.2
        assert d["win_rate"] == 0.65
        assert d["sample_size"] == 50
        assert set(d.keys()) == {
            "threshold_mult",
            "risk_mult",
            "sl_mult",
            "tp_mult",
            "trailing_mult",
            "win_rate",
            "profit_factor",
            "avg_pnl",
            "sample_size",
            "last_update",
            "confidence",
        }


class TestAdaptiveParametersInit:
    """AdaptiveParameters.__init__ — initialisation et chargement."""

    def test_default_init(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        assert ap.symbol == "BTCUSD"
        assert ap.lookback == 100
        assert ap.min_trades == 20
        assert ap._trades == []
        assert ap._params.sample_size == 0

    def test_loads_existing_state(self, tmp_path):
        state_file = tmp_path / "adaptive_BTCUSD.json"
        state = {
            "params": AdaptedParams(threshold_mult=0.9, win_rate=0.65, sample_size=30).to_dict(),
            "trades": [{"pnl": 100, "win": True, "time": time.time()}] * 30,
        }
        with open(state_file, "w") as f:
            json.dump(state, f)
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        assert ap._params.threshold_mult == 0.9
        assert ap._params.win_rate == 0.65
        assert len(ap._trades) == 30

    def test_loads_corrupted_state_gracefully(self, tmp_path):
        state_file = tmp_path / "adaptive_BTCUSD.json"
        state_file.write_text("not valid json")
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        assert ap._params.threshold_mult == 1.0  # defaults préservés


class TestRecordTrade:
    """record_trade — enregistrement et recalcule."""

    def test_records_trade(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        ap.record_trade(pnl=150.0, win=True)
        assert len(ap._trades) == 1
        assert ap._trades[0]["pnl"] == 150.0
        assert ap._trades[0]["win"] is True

    def test_maintains_lookback_limit(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", lookback=10, state_dir=str(tmp_path))
        for i in range(20):
            ap.record_trade(pnl=10.0, win=True)
        assert len(ap._trades) == 10  # capped

    def test_saves_state_to_disk(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        ap.record_trade(pnl=100.0, win=True)
        state_file = tmp_path / "adaptive_BTCUSD.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert len(data["trades"]) == 1
        assert data["params"]["win_rate"] == 0.5  # < min_trades, conf=0.5


class TestRecalculate:
    """_recalculate — logique d'adaptation."""

    def test_below_min_trades_low_confidence(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=20, state_dir=str(tmp_path))
        for i in range(10):
            ap.record_trade(pnl=100.0, win=True)
        assert ap._params.confidence == pytest.approx(10 / 20 * 0.5, rel=0.01)

    def test_wr_above_60_more_aggressive(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        for i in range(20):
            ap.record_trade(pnl=100.0, win=True)
        # WR=100% > 60% → risk_mult=1.1, threshold_mult=0.95, tp_mult=1.1
        # PF=2.0 (all wins, fallback) → pas de changement PF
        # Streak: last_5_wr=100% > 0.8 → risk_mult *= 1.1 → 1.1*1.1 = 1.21
        assert ap._params.threshold_mult == 0.95
        assert ap._params.risk_mult == pytest.approx(1.21, rel=0.01)

    def test_wr_55_to_60_slightly_aggressive(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        for i in range(12):
            ap.record_trade(pnl=100.0, win=True)
        for i in range(8):
            ap.record_trade(pnl=-50.0, win=False)
        # WR=60% → 0.60 NOT > 0.60 → `wr > 0.55` branch: risk_mult=1.05, threshold_mult=0.98
        # PF=1200/400=3.0 > 2 → tp_mult=1.2
        # Streak: last 5 trades = 5 losses (from 8-loss batch) → wr=0 < 0.2 → risk_mult*=0.7 → 0.735
        assert ap._params.threshold_mult == 0.98
        assert ap._params.risk_mult == pytest.approx(0.735, rel=0.01)

    def test_wr_45_to_50_conservative(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        for i in range(9):
            ap.record_trade(pnl=100.0, win=True)
        for i in range(11):
            ap.record_trade(pnl=-50.0, win=False)
        # WR=45% → `wr < 0.50` branch: risk_mult=0.9, threshold_mult=1.02
        # PF=900/550=1.636 → pas de changement PF
        # Streak: last 5 = 5 losses → wr=0 < 0.2 → risk_mult*=0.7 → 0.63
        assert ap._params.threshold_mult == 1.02
        assert ap._params.risk_mult == pytest.approx(0.63, rel=0.01)

    def test_wr_below_45_very_conservative(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        for i in range(8):
            ap.record_trade(pnl=100.0, win=True)
        for i in range(12):
            ap.record_trade(pnl=-50.0, win=False)
        # WR=40% < 45% → risk_mult=0.8, threshold_mult=1.05, sl_mult=0.9
        # PF=800/600=1.333 → pas de changement PF
        # Streak: last 5 = 5 losses → wr=0 < 0.2 → risk_mult*=0.7 → 0.56
        assert ap._params.threshold_mult == 1.05
        assert ap._params.risk_mult == pytest.approx(0.56, rel=0.01)
        assert ap._params.sl_mult == 0.9

    def test_pf_above_2_increases_tp(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        # 18 wins + 2 losses → WR=90% (>60%), PF=18*100/(2*50)=1800/100=18
        # WR > 60% → tp_mult=1.1 (initial), risk_mult=1.1
        # PF > 2 → tp_mult=1.2 (écrase), risk_mult inchangé
        # Streak: last 5 = 3 wins + 2 losses → 60% > 0.2 ET < 0.8 → pas de streak adjust
        for i in range(18):
            ap.record_trade(pnl=100.0, win=True)
        for i in range(2):
            ap.record_trade(pnl=-50.0, win=False)
        assert ap._params.tp_mult == 1.2  # PF > 2 écrase tp_mult

    def test_pf_below_1_reduces_risk(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        # Tous perdants → WR=0% (<45%), risk_mult=0.8, sl_mult=0.9
        # PF=0 → < 1 → risk_mult*=0.7 → 0.56, tp_mult=0.9
        # Streak: last 5 = 5 losses → wr=0 < 0.2 → risk_mult*=0.7 → 0.392
        for i in range(20):
            ap.record_trade(pnl=-50.0, win=False)
        assert ap._params.risk_mult == pytest.approx(0.8 * 0.7 * 0.7, rel=0.01)
        assert ap._params.tp_mult == 0.9

    def test_losing_streak_reduces_risk(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        # 15 gagnants (100) puis 5 perdants (50)
        for i in range(15):
            ap.record_trade(pnl=100.0, win=True)
        for i in range(5):
            ap.record_trade(pnl=-50.0, win=False)
        # WR=75% > 60% → risk_mult=1.1, PF=1500/250=6.0 > 2 → tp_mult=1.2
        # Streak: last_5_wr = 0/5 = 0% < 0.2 → risk_mult *= 0.7 → 0.77
        assert ap._params.risk_mult == pytest.approx(1.1 * 0.7, rel=0.01)

    def test_winning_streak_boosts_risk(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", min_trades=5, lookback=50, state_dir=str(tmp_path))
        for i in range(20):
            ap.record_trade(pnl=50.0, win=True)
        # WR=100% > 60% → risk_mult=1.1, PF=2.0 (fallback, all wins)
        # Streak: last_5_wr = 100% > 0.8 → risk_mult *= 1.1 → 1.21
        assert ap._params.risk_mult == pytest.approx(1.1 * 1.1, rel=0.01)


class TestGetMethods:
    """get_adapted_params, get_status."""

    def test_get_adapted_params(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        params = ap.get_adapted_params()
        assert isinstance(params, AdaptedParams)

    def test_get_status(self, tmp_path):
        ap = AdaptiveParameters("BTCUSD", state_dir=str(tmp_path))
        status = ap.get_status()
        assert status["symbol"] == "BTCUSD"
        assert "sample_size" in status
        assert "confidence" in status
        assert "win_rate" in status


class TestConvenienceFunctions:
    """Fonctions module-level — get_adaptive, record_trade, get_adapted_params."""

    def test_get_adaptive_creates_instance(self):
        ap = get_adaptive("TEST123", lookback=50, min_trades=10)
        assert ap.symbol == "TEST123"
        assert ap.lookback == 50

    def test_get_adaptive_returns_same_instance(self):
        ap1 = get_adaptive("DUPLICATE")
        ap2 = get_adaptive("DUPLICATE")
        assert ap1 is ap2

    def test_record_trade_convenience(self):
        sym = "RECORD_TRADE_TEST"
        ap = get_adaptive(sym, lookback=50, min_trades=5)
        record_trade(sym, pnl=100.0, win=True)
        assert len(ap._trades) >= 1
        assert ap._trades[-1]["pnl"] == 100.0

    def test_get_adapted_params_convenience(self):
        ap = get_adaptive("GET_TEST", lookback=50, min_trades=5)
        params = get_adapted_params("GET_TEST")
        assert isinstance(params, AdaptedParams)
        assert params.sample_size == 0
