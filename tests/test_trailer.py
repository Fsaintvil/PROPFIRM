"""
Tests for Trailer — ATR trailing, partial TP, time-stop, structure exit.

Trailing ATR protège chaque trade ouvert. Aucun test n'existait avant Juillet 2026.
✅ Coverage cible : 85%+ sur _check_step_trailing, _check_partial_tp, _reconstruct_peak
"""

import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest
import numpy as np

from engine_simple.trailer import Trailer


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_mt5():
    m = MagicMock()
    m.get_symbol_info.return_value = MagicMock(
        point=0.00001,
        digits=5,
        trade_stops_level=0,
        volume_step=0.01,
    )
    m.get_rates.return_value = [
        (int(time.time()) - i * 3600, 1.10500, 1.10550, 1.10400, 1.10450, 1000, 0, 500) for i in range(50)
    ]
    m.get_tick.return_value = MagicMock(ask=1.10500, bid=1.10490)
    m.order_send.return_value = MagicMock(retcode=10009)
    m.update_sl.return_value = MagicMock(retcode=10009)
    return m


@pytest.fixture
def config():
    return {"SL_PIPS": 15, "TP_MULTIPLIER": 2.0}


@pytest.fixture
def trailer(mock_mt5, config):
    t = Trailer(mock_mt5, config)
    # Mock les attributs normalement gérés par FTMOProtector
    t.partial_closed = set()
    t.trailing_peaks = {}
    t.position_regime = {"1001": "RANGING", "1002": "TREND_UP"}
    t.position_meta = {}
    t.position_open_times = {
        "1001": {"open_time": (datetime.utcnow() - timedelta(hours=2)).timestamp()},
        "1002": {"open_time": (datetime.utcnow() - timedelta(hours=2)).timestamp()},
    }
    t._atr_cache = {}
    t._profile_cache = {}
    t.peak_profit = {}
    t._time_stop_cooldown = {}
    return t


def _make_position(
    ticket=1001,
    symbol="EURUSD",
    direction=0,
    entry=1.10500,
    current=1.10800,
    sl=1.10200,
    tp=1.11500,
    volume=0.10,
    profit=15.0,
):
    """Helper pour créer un mock de position MT5."""
    pos = MagicMock()
    pos.ticket = ticket
    pos.symbol = symbol
    pos.type = direction  # 0=BUY, 1=SELL
    pos.price_open = entry
    pos.price_current = current
    pos.sl = sl
    pos.tp = tp
    pos.volume = volume
    pos.profit = profit
    pos.time = int(time.time()) - 7200  # 2h ago
    pos.identifier = ticket
    pos.magic = 999001
    return pos


# ── ATR Cache ───────────────────────────────────────────────────────────────


class TestGetATR:
    def test_returns_cached_value(self, trailer, mock_mt5):
        """L'ATR en cache doit être retourné sans appel MT5."""
        trailer._atr_cache["EURUSD"] = (0.0015, time.time())
        val = trailer._get_atr("EURUSD")
        assert val == 0.0015
        mock_mt5.get_rates.assert_not_called()

    def test_expired_cache_fetches_new(self, trailer, mock_mt5):
        """Cache expiré → appel MT5."""
        trailer._atr_cache["EURUSD"] = (0.0015, time.time() - 120)  # expired
        val = trailer._get_atr("EURUSD")
        mock_mt5.get_rates.assert_called_once()
        assert val is not None

    def test_insufficient_rates_returns_none(self, trailer, mock_mt5):
        """Pas assez de données → None."""
        mock_mt5.get_rates.return_value = [(1.10, 1.11, 1.09, 1.105, 100, 0, 0) for _ in range(3)]
        val = trailer._get_atr("EURUSD")
        assert val is None

    def test_none_rates_returns_none(self, trailer, mock_mt5):
        """MT5 retourne None → None."""
        mock_mt5.get_rates.return_value = None
        val = trailer._get_atr("EURUSD")
        assert val is None


# ── Pip Offset ──────────────────────────────────────────────────────────────


class TestPipOffset:
    def test_standard_forex(self, trailer, mock_mt5):
        mock_mt5.get_symbol_info.return_value = MagicMock(point=0.00001, digits=5)
        val = trailer._pip_offset("EURUSD", 10)
        assert val == pytest.approx(0.0010, rel=0.1)

    def test_jpy_pair(self, trailer, mock_mt5):
        mock_mt5.get_symbol_info.return_value = MagicMock(point=0.001, digits=3)
        val = trailer._pip_offset("USDJPY", 10)
        # JPY: point=0.001, digits=3 → pip_size=0.01, 10 pips=0.1
        assert val == pytest.approx(0.100, rel=0.1)

    def test_none_info_returns_default(self, trailer, mock_mt5):
        mock_mt5.get_symbol_info.return_value = None
        val = trailer._pip_offset("UNKNOWN", 10)
        assert val == 0.001


# ── Profile Cache ───────────────────────────────────────────────────────────


class TestGetProfile:
    def test_returns_profile(self, trailer):
        profile = trailer._get_profile("EURUSD")
        assert profile is not None

    def test_caches_profile(self, trailer):
        p1 = trailer._get_profile("EURUSD")
        p2 = trailer._get_profile("EURUSD")
        assert p1 is p2


# ── Partial TP ──────────────────────────────────────────────────────────────


class TestCheckPartialTP:
    def test_skips_if_already_closed(self, trailer, mock_mt5):
        """Déjà partiellement fermé → skip."""
        trailer.partial_closed.add("1001")
        pos = _make_position()
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_not_called()

    def test_skips_if_no_sl_no_tp(self, trailer, mock_mt5):
        """Pas de SL/TP → skip."""
        pos = _make_position(sl=None, tp=None)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_not_called()

    def test_skips_below_65_progress(self, trailer, mock_mt5):
        """Progress < 65% → skip (seuil 31 Juil 2026 — Quant Auditor R3)."""
        pos = _make_position(current=1.10800, entry=1.10500, tp=1.11500)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_not_called()

    def test_xauusd_partial_tp_at_55_progress(self, trailer, mock_mt5):
        """🔧 FIX 19 Août 2026 (Council): XAUUSD partial_tp_progress=0.55 → déclenche
        à 60% du chemin (défaut 65% ne déclencherait pas). Sécurise les gains plus tôt
        sur un symbole WR 28.6% mais gros winners."""
        # TP=1.11500, entry=1.10500 → 60% = 1.11100. current=1.11100 → progress=0.60.
        pos = _make_position(ticket=1001, symbol="XAUUSD", current=1.11100, entry=1.10500, tp=1.11500, sl=1.10200, volume=0.10)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_called_once()

    def test_eurusd_keeps_65_threshold(self, trailer, mock_mt5):
        """🔧 FIX 19 Août 2026: EURUSD sans override garde le seuil 65%."""
        # progress=0.60 (< 0.65) → pas de partial pour EURUSD
        pos = _make_position(ticket=1001, symbol="EURUSD", current=1.11100, entry=1.10500, tp=1.11500, sl=1.10200, volume=0.10)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_not_called()

    def test_partial_close_at_65_progress(self, trailer, mock_mt5):
        """Progress ≥ 65% → ordre de fermeture partielle (seuil 31 Juil 2026)."""
        # TP=1.11500, entry=1.10500 → 65% = 1.11150. current=1.11200 → progress=0.70
        pos = _make_position(current=1.11200, entry=1.10500, tp=1.11500, sl=1.10200, volume=0.10)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_called_once()
        args = mock_mt5.order_send.call_args[0][0]
        assert args["action"] == 1  # TRADE_ACTION_DEAL
        # 🔧 19 Août: 50%→75% (PTP_75pct). 0.10×0.75=0.075 → quantisé au lot_step 0.01
        # (fix 16 Août: arrondi demi-vers-le-haut) → 8×0.01 = 0.08
        assert args["volume"] == pytest.approx(0.08, rel=0.01)
        assert args["comment"] == "PARTIAL_TP"
        assert "1001" in trailer.partial_closed

    def test_sets_breakeven_after_partial(self, trailer, mock_mt5):
        """Après partial TP (65%), le SL doit être relevé au BE."""
        pos = _make_position(current=1.11200, entry=1.10500, tp=1.11500, sl=1.10200, volume=0.10)
        # Mock ATR pour calcul BE
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        trailer._check_partial_tp(pos)
        # Vérifie que update_sl a été appelé (BE)
        mock_mt5.update_sl.assert_called_once()

    def test_skips_small_volume(self, trailer, mock_mt5):
        """Volume trop petit pour splitter → skip."""
        pos = _make_position(volume=0.01)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_not_called()

    def test_handles_sell_direction(self, trailer, mock_mt5):
        """SELL direction: progress > 40% → ordre partiel."""
        pos = _make_position(direction=1, entry=1.10500, current=1.09750, tp=1.09500, sl=1.10800, volume=0.10)
        trailer._check_partial_tp(pos)
        assert "1001" in trailer.partial_closed


# ── Time-stop ──────────────────────────────────────────────────────────────


class TestCheckTimeStop:
    def test_no_open_time_skips(self, trailer, mock_mt5):
        """Pas d'open_time → skip."""
        pos = _make_position(ticket=9999)
        trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_not_called()

    def test_recent_position_skips(self, trailer, mock_mt5):
        """Position < 12h → skip."""
        trailer.position_open_times["1001"] = {"open_time": datetime.utcnow().timestamp()}
        pos = _make_position()
        trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_not_called()

    def test_old_position_closes(self, trailer, mock_mt5):
        """🔧 FIX 28 Août 2026: position profitable > 12h NE ferme PLUS (trailing gère).
        Seule exception: weekend close window."""
        trailer.position_open_times["1001"] = {"open_time": (datetime.utcnow() - timedelta(hours=13)).timestamp()}
        pos = _make_position(profit=50.0)
        with patch.object(Trailer, "_is_weekend_close_window", return_value=False):
            trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_not_called()  # Profitable → trailing gère

    def test_respects_cooldown(self, trailer, mock_mt5):
        """Cooldown 5min entre tentatives."""
        trailer.position_open_times["1001"] = {"open_time": (datetime.utcnow() - timedelta(hours=13)).timestamp()}
        trailer._time_stop_cooldown["1001"] = time.time()  # vient de tenter
        pos = _make_position(profit=50.0)
        trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_not_called()

    def test_loss_position_closes_earlier(self, trailer, mock_mt5):
        """Position perdante fermée après 4h."""
        trailer.position_open_times["1001"] = {"open_time": (datetime.utcnow() - timedelta(hours=5)).timestamp()}
        pos = _make_position(profit=-30.0)
        trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_called_once()

    def test_xauusd_profit_closes_at_8h_not_12h(self, trailer, mock_mt5):
        """🔧 FIX 28 Août 2026: XAUUSD profitable > 8h NE ferme PLUS (trailing gère).
        Le time-stop profit est désormais inutile — le trailing stop N1-N5 gère les trades gagnants."""
        trailer.position_open_times["1001"] = {"open_time": (datetime.utcnow() - timedelta(hours=9)).timestamp()}
        pos = _make_position(ticket=1001, symbol="XAUUSD", profit=80.0)
        with patch.object(Trailer, "_is_weekend_close_window", return_value=False):
            trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_not_called()  # Profitable → trailing gère

    def test_default_symbol_still_12h(self, trailer, mock_mt5):
        """🔧 FIX 19 Août 2026: les symboles SANS override gardent le défaut 12h."""
        trailer.position_open_times["1001"] = {"open_time": (datetime.utcnow() - timedelta(hours=9)).timestamp()}
        pos = _make_position(ticket=1001, symbol="EURUSD", profit=50.0)
        with patch.object(Trailer, "_is_weekend_close_window", return_value=False):
            trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_not_called()


# ── ATR Trailing (Core) ────────────────────────────────────────────────────


class TestCheckStepTrailing:
    def test_no_atr_returns_early(self, trailer, mock_mt5):
        """ATR None → skip."""
        mock_mt5.get_rates.return_value = None
        pos = _make_position()
        trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_not_called()

    def test_below_first_threshold_returns_early(self, trailer, mock_mt5):
        """Profit < 1.0 ATR → skip."""
        trailer._atr_cache["EURUSD"] = (0.0100, time.time())  # ATR large
        pos = _make_position(current=1.10700, entry=1.10500)  # 0.2 ATR profit
        trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_not_called()

    def test_first_trailing_level_buy(self, trailer, mock_mt5):
        """Profit > 1.8 ATR → trailing SL au 1er niveau RANGING (0.80×ATR — 31 Juil 2026)."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        # RANGING (31 Juil 2026): niveaux = [(1.8, 0.80), (2.5, 0.55), (3.5, 0.40), (5.0, 0.25), (5.5, 0.15)]
        # profit_atr≈2.0 → trail_dist=0.80 → SL = 1.10900 - 0.80*0.0020 = 1.10740
        with patch("engine_simple.trailer.random.uniform", return_value=0.0):
            pos = _make_position(current=1.10900, entry=1.10500, sl=1.10200)
            trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        assert args[1] == pytest.approx(1.10740, abs=0.0003), f"SL={args[1]}"

    def test_trailing_sell_direction(self, trailer, mock_mt5):
        """SELL: trailing SL baissier (RANGING 0.80×ATR — 31 Juil 2026)."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        with patch("engine_simple.trailer.random.uniform", return_value=0.0):
            pos = _make_position(direction=1, current=1.10000, entry=1.10500, sl=1.10800)
            trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        # SL doit descendre: peak=1.10000 + 0.80*0.0020 = 1.10160
        assert args[1] == pytest.approx(1.10160, abs=0.0003), f"SL={args[1]}"

    def test_higher_trailing_level(self, trailer, mock_mt5):
        """Profit > 3.0 ATR → trailing SL au 3e niveau RANGING (0.20×ATR)."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        # profit = (1.11200 - 1.10500) / 0.0020 = 3.5 ATR
        pos = _make_position(current=1.11200, entry=1.10500, sl=1.10200)
        trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        # peak=1.11200 - 0.20*0.0020 = 1.11160
        sl_new = args[1]
        assert sl_new > 1.10800, f"SL={sl_new} devrait être serré (profit élevé)"

    def test_peak_advances_with_price(self, trailer, mock_mt5):
        """Le peak doit suivre le prix montant."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        pos = _make_position(current=1.10800, entry=1.10500, sl=1.10200)
        trailer._check_step_trailing(pos)
        assert trailer.trailing_peaks.get("1001", 0) == pytest.approx(1.10800, abs=0.0001)

    def test_retracement_forces_breakeven(self, trailer, mock_mt5):
        """Retracement > 1 ATR → force BE si SL sous l'entrée."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        # peak atteint 1.11200, retrace à 1.10750 (4.5 pips = 2.25 ATR)
        pos = _make_position(current=1.10750, entry=1.10500, sl=1.10200)
        trailer.trailing_peaks["1001"] = 1.11200  # peak précédent
        trailer._check_step_trailing(pos)
        # Doit forcer BE car retracement > 1 ATR
        mock_mt5.update_sl.assert_called()

    def test_sell_retracement_forces_breakeven(self, trailer, mock_mt5):
        """🔧 FIX 20 Août 2026 (symétrie BUY/SELL): un SELL qui retrace > 1 ATR
        doit aussi forcer le BE. Avant, seul le BUY avait cette protection."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        # SELL: peak (creux) atteint 1.09800, retrace à 1.10150 (retracement ~1.75 ATR)
        pos = _make_position(direction=1, ticket=1002, current=1.10150, entry=1.10500, sl=1.10800)
        trailer.trailing_peaks["1002"] = 1.09800  # creux précédent
        trailer._check_step_trailing(pos)
        # Doit forcer BE car retracement > 1 ATR et SL au-dessus de l'entrée
        mock_mt5.update_sl.assert_called()

    def test_regime_affects_trailing_distance(self, trailer, mock_mt5):
        """Régime TREND_UP → N1 à 1.80×ATR → trail=1.00 → SL=1.10700 (31 Juil 2026)."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        trailer.position_regime["1002"] = "TREND_UP"
        with patch("engine_simple.trailer.random.uniform", return_value=0.0):  # no jitter
            pos = _make_position(ticket=1002, current=1.10900, entry=1.10500, sl=1.10200)
            trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        # TREND_UP: [(1.80,1.00), (2.50,0.70), (3.50,0.50), (5.00,0.30), (6.00,0.20)]
        # profit_atr=2.0 > 1.80 → trail=1.00 → SL = 1.10900 - 1.00*0.002 = 1.10700
        assert args[1] == pytest.approx(1.10700, abs=0.0005)

    def test_retry_on_retcode_10016(self, trailer, mock_mt5):
        """Retcode 10016 (trop proche) → retry avec distance augmentée."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        mock_mt5.update_sl.return_value = MagicMock(retcode=10016)
        pos = _make_position(current=1.10900, entry=1.10500, sl=1.10200)
        trailer._check_step_trailing(pos)
        # update_sl doit être appelé 2 fois (retry)
        assert mock_mt5.update_sl.call_count == 2


# ── Peak Reconstruction ────────────────────────────────────────────────────


class TestReconstructPeak:
    def test_buy_peak_from_rates(self, trailer, mock_mt5):
        """BUY: peak = max des hauts depuis l'ouverture."""
        now = int(time.time())
        pos_time = now - 7200  # position ouverte il y a 2h
        rates = [
            (now - i * 3600, 1.105, 1.106, 1.104, 1.105, 0, 0, 0)
            for i in range(12)  # 12h de données
        ]
        rates[1] = (rates[1][0], 1.105, 1.11200, 1.104, 1.105, 0, 0, 0)  # peak il y a 1h
        mock_mt5.get_rates.return_value = rates
        pos = _make_position(current=1.10800, entry=1.10500)
        # position.time = now - 7200
        peak = trailer._reconstruct_peak(pos)
        assert peak == pytest.approx(1.11200, abs=0.0001)

    def test_sell_peak_from_rates(self, trailer, mock_mt5):
        """SELL: peak = min des bas depuis l'ouverture."""
        now = int(time.time())
        rates = [(now - i * 3600, 1.105, 1.106, 1.104, 1.105, 0, 0, 0) for i in range(12)]
        rates[2] = (rates[2][0], 1.105, 1.106, 1.09800, 1.105, 0, 0, 0)  # valley il y a 2h
        mock_mt5.get_rates.return_value = rates
        pos = _make_position(direction=1, current=1.10000, entry=1.10500)
        peak = trailer._reconstruct_peak(pos)
        assert peak == pytest.approx(1.09800, abs=0.0001)

    def test_fallback_to_price_open(self, trailer, mock_mt5):
        """Pas assez de rates → fallback price_open/current."""
        mock_mt5.get_rates.return_value = None
        pos = _make_position(current=1.10800, entry=1.10500)
        peak = trailer._reconstruct_peak(pos)
        assert peak == max(pos.price_open, pos.price_current)


# ── Force Breakeven ────────────────────────────────────────────────────────


class TestForceBreakeven:
    def test_sl_moves_to_entry(self, trailer, mock_mt5):
        """Force BE: SL relevé à l'entrée + buffer."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        pos = _make_position(sl=1.10200)
        trailer._force_breakeven(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        # BE = entry + buffer (0.50×ATR pour RANGING)
        assert args[1] > pos.price_open  # SL doit être au-dessus de l'entrée

    def test_skips_if_sl_already_better(self, trailer, mock_mt5):
        """SL déjà meilleur que BE proposé → skip."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        pos = _make_position(sl=1.10700)  # déjà proche du price_open
        trailer._force_breakeven(pos)
        # SL déjà au-dessus de l'entrée + buffer → pas d'amélioration
        be_sl = pos.price_open + 0.50 * 0.0020  # ≈ 1.10600
        if 1.10700 > be_sl:
            mock_mt5.update_sl.assert_not_called()


# ── SL/TP Calculation ──────────────────────────────────────────────────────


class TestCalcSLTP:
    def test_buy_sl_tp_with_atr(self, trailer, mock_mt5):
        """BUY: SL = entry - 1.8×ATR, TP = entry + 5.0×ATR (avec min_dist 1.5×ATR)."""
        sl, tp = trailer.calc_sl_tp("EURUSD", 1.10500, 0, atr_val=0.0020)
        # sl_dist = max(1.8*0.0020, 1.5*0.0020) = max(0.0036, 0.0030) = 0.0036
        # tp_dist = 0.0036 * 5.0/1.8 = 0.0100
        # SL = 1.10500 - 0.0036 = 1.10140
        # TP = 1.10500 + 0.0100 = 1.11500
        assert sl == pytest.approx(1.10140, abs=0.0003)
        assert tp == pytest.approx(1.11500, abs=0.0005)

    def test_sell_sl_tp_with_atr(self, trailer, mock_mt5):
        """SELL: SL = entry + 1.8×ATR, TP = entry - 5.0×ATR (avec min_dist 1.5×ATR)."""
        sl, tp = trailer.calc_sl_tp("EURUSD", 1.10500, 1, atr_val=0.0020)
        # sl_dist = max(1.8*0.0020, 1.5*0.0020) = 0.0036
        # tp_dist = 0.0036 * 5.0/1.8 = 0.0100
        # SL = 1.10500 + 0.0036 = 1.10860
        # TP = 1.10500 - 0.0100 = 1.09500
        assert sl == pytest.approx(1.10860, abs=0.0003)
        assert tp == pytest.approx(1.09500, abs=0.0005)

    def test_fallback_without_atr(self, trailer, mock_mt5):
        """Sans ATR → fallback pips."""
        sl, tp = trailer.calc_sl_tp("EURUSD", 1.10500, 0, atr_val=None)
        # 15 pips SL, 30 pips TP
        assert sl is not None
        assert tp is not None
        assert sl < 1.10500
        assert tp > 1.10500


# ── Persist Partial Closed ─────────────────────────────────────────────────


class TestPersistPartialClosed:
    def test_writes_to_state_file(self, trailer):
        """Persist: les données sont correctement sérialisées via state_manager."""
        trailer.partial_closed.add("1001")
        trailer.partial_closed.add("1002")

        with patch("engine_simple.trailer.update_state_field") as mock_update:
            trailer._persist_partial_closed()

            mock_update.assert_called_once()
            args = mock_update.call_args
            # Premier arg = state_path, deuxième = field, troisième = value
            assert args[0][1] == "partial_closed"
            value = args[0][2]
            assert "1001" in value
            assert "1002" in value

    # NOTE (16 Août 2026, Vague 5): test "ne doit pas crasher" — valeur = non-propagation
    def test_no_crash_on_write_error(self, trailer):
        """Erreur d'écriture → log debug, pas de crash."""
        trailer.partial_closed.add("1001")
        with patch("engine_simple.trailer.update_state_field", side_effect=OSError("Permission denied")):
            trailer._persist_partial_closed()  # ne doit pas planter


# ── Structure Exit ─────────────────────────────────────────────────────────


class TestCheckStructureExit:
    def test_insufficient_rates_skips(self, trailer, mock_mt5):
        """Pas assez de rates H1 → skip."""
        mock_mt5.get_rates.return_value = [(1.10, 1.11, 1.09, 1.105, 100, 0, 0) for _ in range(5)]
        pos = _make_position()
        trailer._check_structure_exit(pos)
        mock_mt5.update_sl.assert_not_called()

    def test_no_exit_signal_skips(self, trailer, mock_mt5):
        """Pas de BOS/CHoCH détecté → skip."""
        with patch("engine_simple.trailer.structure_exit_signal", return_value=(False, None, None)):
            mock_mt5.get_rates.return_value = [
                (int(time.time()) - i * 3600, 1.105, 1.106, 1.104, 1.105, 0, 0, 0) for i in range(30)
            ]
            pos = _make_position()
            trailer._check_structure_exit(pos)
            mock_mt5.update_sl.assert_not_called()


# ── Verrouillage config trailing (alignement AGENTS.md) ────────────────────


class TestTrailingConfigLock:
    """Fige la config TRAILING_BY_REGIME (31 Juillet 2026 R2) pour garantir
    l'alignement doc (AGENTS.md) ↔ code. Toute modification de ces niveaux doit
    être une décision explicite, testée et documentée — pas une dérive silencieuse."""

    def test_trailing_levels_match_agents_doc(self):
        from engine_simple.ftmo_config import TRAILING_BY_REGIME

        expected = {
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25), (5.50, 0.15)],
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30), (6.00, 0.20)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30), (6.00, 0.20)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40), (6.00, 0.25)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.20, 0.35), (4.50, 0.20), (5.50, 0.12)],
        }
        assert TRAILING_BY_REGIME == expected

    def test_get_trailing_for_symbol_fallback(self):
        """Symbole sans config spécifique → fallback sur TRAILING_BY_REGIME."""
        from engine_simple.ftmo_config import get_trailing_for_symbol, TRAILING_BY_REGIME

        for regime, levels in TRAILING_BY_REGIME.items():
            assert get_trailing_for_symbol("GBPUSD", regime) == levels

    def test_progressive_be_thresholds(self):
        """Breakeven progressif : les seuils 1.00/1.30×ATR sont dans BE_PROGRESSIVE_LEVELS.
        Ce test verrouille le contrat (AGENTS.md 31 Juillet 2026 + FIX 17 Août 2026)."""
        from engine_simple.trailer import BE_PROGRESSIVE_LEVELS

        # La fonction doit référencer BE_PROGRESSIVE_LEVELS (module-level)
        import engine_simple.trailer as trailer_mod

        src = [c for c in trailer_mod.Trailer._check_progressive_be.__code__.co_names]
        assert "BE_PROGRESSIVE_LEVELS" in src, "la fonction doit utiliser BE_PROGRESSIVE_LEVELS"
        thresholds = [lvl[0] for lvl in BE_PROGRESSIVE_LEVELS]
        assert any(abs(t - 1.30) < 1e-9 for t in thresholds), "seuil 1.30×ATR manquant"
        assert any(abs(t - 1.00) < 1e-9 for t in thresholds), "seuil 1.00×ATR manquant"

    def test_progressive_be_extra_levels(self):
        """🔧 FIX 17 Août 2026: paliers supplémentaires — montée PLUS rapide du SL.
        Les paliers 1.60/1.90/2.20/2.50×ATR doivent exister pour éviter la zone morte
        entre 1.30×ATR et le lock N1 (le SL restait fixe à entry+0.15×ATR pendant que
        le profit grimpait jusqu'à ~2×ATR de plus)."""
        from engine_simple.trailer import BE_PROGRESSIVE_LEVELS

        # Vérifier que la constante contient les paliers attendus, strictement croissants
        thresholds = [lvl[0] for lvl in BE_PROGRESSIVE_LEVELS]
        buffers = [lvl[1] for lvl in BE_PROGRESSIVE_LEVELS]
        assert thresholds == sorted(thresholds), "seuils doivent être croissants"
        assert 1.30 in thresholds, "palier 1.30×ATR manquant"
        assert 1.60 in thresholds, "palier 1.60×ATR manquant (zone morte 1.30→N1)"
        assert 1.90 in thresholds, "palier 1.90×ATR manquant"
        assert 2.20 in thresholds, "palier 2.20×ATR manquant"
        assert 2.50 in thresholds, "palier 2.50×ATR manquant (raccord N1 BTCUSD TREND)"
        # Le buffer SL doit être strictement croissant aussi (le SL ne doit jamais reculer)
        assert buffers == sorted(buffers), "buffers doivent être croissants"
        # À 2.50×ATR de profit, le SL doit être au moins à entry+0.75×ATR
        assert buffers[-1] >= 0.75, f"buffer max attendu ≥0.75×ATR, trouvé {buffers[-1]}"

    def test_progressive_be_fast_rise(self, trailer, mock_mt5):
        """🔧 FIX 17 Août 2026: le SL monte PLUS VITE avec le profit (pas de zone morte).

        Avant le fix, le SL restait fixe à entry+0.15×ATR entre 1.30×ATR et le lock N1
        (BTCUSD TREND lock=2.50×ATR) → un trade à 2.27×ATR de profit avait encore
        SL=entry+0.15×ATR. Après le fix, à chaque palier de 0.30×ATR le SL gagne
        +0.15×ATR. On vérifie le SL cible à différents niveaux de profit."""
        from engine_simple.trailer import BE_PROGRESSIVE_LEVELS

        atr = 0.0020  # ATR EURUSD-like
        trailer._atr_cache["EURUSD"] = (atr, time.time())
        entry = 1.10000

        # Table: (profit_atr, buffer_atr attendu) dérivé de BE_PROGRESSIVE_LEVELS
        # On calcule le buffer attendu comme le plus grand buffer dont le seuil < profit
        expected = {}
        for profit_atr in (1.05, 1.35, 1.65, 1.95, 2.25, 2.55):
            buf = 0.0
            for thresh, buffer in BE_PROGRESSIVE_LEVELS:
                if profit_atr > thresh:
                    buf = buffer
            expected[profit_atr] = buf

        # Le buffer doit être croissant avec le profit (montée rapide)
        bufs = [expected[p] for p in (1.05, 1.35, 1.65, 1.95, 2.25, 2.55)]
        assert bufs == sorted(bufs) and bufs[-1] >= 0.75, f"montée attendue, trouvé {bufs}"

        # Vérification fonctionnelle: appeler _check_progressive_be avec un SL très bas
        # → le SL doit monter au buffer attendu pour un profit de 2.25×ATR
        pos = _make_position(ticket=7777, symbol="EURUSD", direction=0, entry=entry,
                             current=entry + 2.25 * atr, sl=1.05000, tp=1.16000)
        mock_mt5.reset_mock()
        trailer._check_progressive_be(pos)
        # Le SL mis à jour doit être au niveau attendu (entry + buffer×ATR)
        update_sl_args = mock_mt5.update_sl.call_args
        assert update_sl_args is not None, "update_sl doit avoir été appelé"
        new_sl = update_sl_args[0][1]
        expected_sl = round(entry + expected[2.25] * atr, mock_mt5.get_symbol_info.return_value.digits)
        assert abs(new_sl - expected_sl) < 1e-9, f"SL attendu {expected_sl}, trouvé {new_sl}"
        # Et il doit être nettement au-dessus de l'entrée (plus rapide qu'avant le fix)
        assert new_sl > entry, "le SL doit être au-dessus de l'entrée à 2.25×ATR"
        # tolérance flottante (0.6×ATR - epsilon) pour éviter l'arrondi binaire
        assert new_sl - entry >= 0.60 * atr - 1e-9, f"montée trop lente: SL={new_sl} vs entry={entry}"

    def test_partial_tp_threshold_065(self):
        """Partial TP : progress ≥ 0.65 (65% du TP) — config 31 Juillet R3."""
        from engine_simple.trailer import Trailer

        src = [c for c in Trailer._check_partial_tp.__code__.co_consts if isinstance(c, float)]
        assert any(abs(c - 0.65) < 1e-9 for c in src), "seuil 0.65 du partial TP manquant"

    def test_progressive_be_uses_peak_after_retracement(self, trailer, mock_mt5):
        """🔧 FIX 17 Août 2026: le BE progressif utilise le PEAK, pas le prix courant.

        Cas réel ticket AUDUSD 519685971: pic à 1.06×ATR puis retracement sous 1.00×ATR.
        Avant le fix, profit_atr était calculé sur price_current (retracé) → le BE
        (seuil 1.00×ATR) ne se déclenchait jamais et le SL restait figé 52h.
        Après le fix, le peak stocké dans trailing_peaks doit déclencher le BE."""
        from engine_simple.trailer import BE_PROGRESSIVE_LEVELS

        atr = 0.0020
        trailer._atr_cache["AUDUSD"] = (atr, time.time())
        entry = 0.65000
        # Le prix a atteint un pic à +1.06×ATR puis a RETRACÉ sous le seuil BE
        peak = entry + 1.06 * atr
        current = entry + 0.60 * atr  # retracé sous 1.00×ATR

        pos = _make_position(ticket=519685971, symbol="AUDUSD", direction=0, entry=entry,
                             current=current, sl=None, tp=entry + 4.0 * atr)
        trailer.trailing_peaks["519685971"] = peak  # peak enregistré par le trailing

        mock_mt5.reset_mock()
        trailer._check_progressive_be(pos)

        # Le BE doit se déclencher (peak ≥ 1.00×ATR) même si le prix courant est retracé
        update_sl_args = mock_mt5.update_sl.call_args
        assert update_sl_args is not None, "update_sl doit avoir été appelé (peak dépassait 1.00×ATR)"
        new_sl = update_sl_args[0][1]
        # Palier atteint: profit_atr=1.06 → 1.30 pas atteint → buffer=0.0 → SL = entry
        assert abs(new_sl - round(entry, 5)) < 1e-9, f"BE attendu à entry, trouvé {new_sl}"
        # Le SL doit être ≥ entry (protection sécurisée)
        assert new_sl >= entry - 1e-9

    def test_progressive_be_peak_above_first_level(self, trailer, mock_mt5):
        """🔧 FIX 17 Août 2026: peak > 1.30×ATR → SL = entry+0.15×ATR même retracé.

        Vérifie qu'avec un peak à 1.45×ATR (mais prix courant retracé à 1.10×ATR),
        le SL cible le buffer du palier 1.30 (0.15×ATR) au lieu de rester à 0."""
        atr = 0.0020
        trailer._atr_cache["AUDUSD"] = (atr, time.time())
        entry = 0.65000
        peak = entry + 1.45 * atr
        current = entry + 1.10 * atr  # retracé mais resté au-dessus du seuil BE

        pos = _make_position(ticket=519685972, symbol="AUDUSD", direction=0, entry=entry,
                             current=current, sl=None, tp=entry + 4.0 * atr)
        trailer.trailing_peaks["519685972"] = peak

        mock_mt5.reset_mock()
        trailer._check_progressive_be(pos)

        update_sl_args = mock_mt5.update_sl.call_args
        assert update_sl_args is not None, "update_sl doit avoir été appelé"
        new_sl = update_sl_args[0][1]
        expected_sl = round(entry + 0.15 * atr, 5)  # palier 1.30 → buffer 0.15×ATR
        assert abs(new_sl - expected_sl) < 1e-9, f"SL attendu {expected_sl}, trouvé {new_sl}"

    def test_time_stop_weekend_window_reduces_max_hours(self, trailer, mock_mt5, monkeypatch):
        """🔧 FIX 17 Août 2026: fermeture pré-weekend pour les symboles non-24/7.

        Un trade AUDUSD (weekend_trading=false) dans la fenêtre de clôture du
        vendredi voit max_hours réduit à WEEKEND_CLOSE_MAX_HOURS (défaut 2h) →
        le time-stop se déclenche AVANT la fermeture du marché. Sans le fix,
        un trade de 13h (norme profitable) restait exposé tout le week-end."""
        from config_simple import SYMBOL_LIMITS as _cfg_symbol_limits

        # Simuler vendredi 16h UTC (dans la fenêtre de clôture)
        fake_now = datetime(2026, 8, 21, 16, 30, 0)  # vendredi 21/08/2026 16:30 UTC
        _FakeDatetime._fixed = fake_now
        friday_ts = fake_now.timestamp()
        monkeypatch.setattr("engine_simple.trailer.datetime", _FakeDatetime)

        # Config: AUDUSD ne trade pas le week-end
        monkeypatch.setattr(
            "engine_simple.trailer.cfg.SYMBOL_LIMITS",
            {**_cfg_symbol_limits, "AUDUSD": {"weekend_trading": False}},
        )
        # AUDUSD pas dans NO_TRAILING_SYMBOLS (pour que le time-stop s'applique normalement)

        # Position ouverte vendredi 14:00 UTC → 2.5h d'ancienneté à 16:30
        trailer.position_open_times["1001"] = {"open_time": friday_ts - 2.5 * 3600}
        pos = _make_position(ticket=1001, symbol="AUDUSD", profit=10.0)
        mock_mt5.reset_mock()

        trailer._check_time_stop(pos)

        # max_hours réduit à 2h → 2.5h > 2h → le time-stop doit tenter la fermeture
        mock_mt5.order_send.assert_called_once()

    def test_time_stop_weekend_window_crypto_skipped(self, trailer, mock_mt5, monkeypatch):
        """🔧 FIX 17 Août 2026: crypto 24/7 (BTCUSD, weekend_trading=true) PAS réduit.

        Pendant la fenêtre du vendredi, un BTCUSD reste soumis au max_hours normal
        (12h profitable) — pas de fermeture anticipée inutile."""
        fake_now = datetime(2026, 8, 21, 16, 30, 0)  # vendredi 16:30 UTC
        _FakeDatetime._fixed = fake_now
        monkeypatch.setattr("engine_simple.trailer.datetime", _FakeDatetime)

        # BTCUSD a weekend_trading=true par défaut (config real)
        trailer.position_open_times["1001"] = {"open_time": fake_now.timestamp() - 5 * 3600}
        pos = _make_position(ticket=1001, symbol="BTCUSD", profit=10.0)
        mock_mt5.reset_mock()

        trailer._check_time_stop(pos)

        # 5h < 12h max → skip (pas de fermeture anticipée pour un symbole 24/7)
        mock_mt5.order_send.assert_not_called()


class _FakeDatetime:
    """Substitut minimal de datetime pour les tests de fenêtre week-end.

    `now()` et `utcnow()` retournent une datetime FIXE ; les autres méthodes (fromtimestamp)
    délèguent au vrai datetime pour rester fonctionnelles dans _check_time_stop.
    """

    @staticmethod
    def utcnow():
        return _FakeDatetime._fixed

    @staticmethod
    def now(tz=None):
        """🔧 FIX AUDIT: support datetime.now(timezone.utc)."""
        from datetime import timezone as _tz
        if tz is not None:
            return _FakeDatetime._fixed.replace(tzinfo=_tz.utc)
        return _FakeDatetime._fixed

    @staticmethod
    def fromtimestamp(ts, tz=None):
        from datetime import timezone as _tz
        from datetime import datetime as _dt
        dt = _dt.fromtimestamp(ts)
        if tz is not None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt

    @staticmethod
    def utcfromtimestamp(ts):
        return datetime.utcfromtimestamp(ts)

    _fixed = datetime(2026, 8, 21, 16, 30, 0)  # vendredi 16:30 UTC (remplacé par les tests)
