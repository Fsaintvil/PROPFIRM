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

    def test_skips_below_60_progress(self, trailer, mock_mt5):
        """Progress < 60% → skip."""
        pos = _make_position(current=1.11000, entry=1.10500, tp=1.11500)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_not_called()

    def test_partial_close_at_60_progress(self, trailer, mock_mt5):
        """Progress ≥ 60% → ordre de fermeture partielle."""
        pos = _make_position(current=1.11150, entry=1.10500, tp=1.11500, sl=1.10200, volume=0.10)
        trailer._check_partial_tp(pos)
        mock_mt5.order_send.assert_called_once()
        args = mock_mt5.order_send.call_args[0][0]
        assert args["action"] == 1  # TRADE_ACTION_DEAL
        assert args["volume"] == pytest.approx(0.05, rel=0.01)
        assert args["comment"] == "PARTIAL_TP"
        assert "1001" in trailer.partial_closed

    def test_sets_breakeven_after_partial(self, trailer, mock_mt5):
        """Après partial TP, le SL doit être relevé au BE."""
        pos = _make_position(current=1.11150, entry=1.10500, tp=1.11500, sl=1.10200, volume=0.10)
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
        """SELL direction: progress > 60% → ordre partiel."""
        pos = _make_position(direction=1, entry=1.10500, current=1.09800, tp=1.09500, sl=1.10800, volume=0.10)
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
        """Position > 12h profitable → close."""
        trailer.position_open_times["1001"] = {"open_time": (datetime.utcnow() - timedelta(hours=13)).timestamp()}
        pos = _make_position(profit=50.0)
        trailer._check_time_stop(pos)
        mock_mt5.order_send.assert_called_once()

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
        """Profit > 1.5 ATR → trailing SL au 1er niveau RANGING EURUSD (0.50×ATR)."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        # RANGING: niveaux EURUSD = [(1.5, 0.5), (3.0, 0.35), (5.0, 0.2), (8.0, 0.1)]
        # profit_atr≈2.0 → trail_dist=0.50 → SL = 1.10900 - 0.50*0.0020 = 1.10800
        with patch("engine_simple.trailer.random.uniform", return_value=0.0):
            pos = _make_position(current=1.10900, entry=1.10500, sl=1.10200)
            trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        assert args[1] == pytest.approx(1.10800, abs=0.0003), f"SL={args[1]}"

    def test_trailing_sell_direction(self, trailer, mock_mt5):
        """SELL: trailing SL baissier."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        with patch("engine_simple.trailer.random.uniform", return_value=0.0):
            pos = _make_position(direction=1, current=1.10000, entry=1.10500, sl=1.10800)
            trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        # SL doit descendre: peak=1.10000 + 0.50*0.0020 = 1.10100
        assert args[1] == pytest.approx(1.10100, abs=0.0003), f"SL={args[1]}"

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

    def test_regime_affects_trailing_distance(self, trailer, mock_mt5):
        """Régime TREND_UP → trailing plus serré (0.80×ATR vs 0.50×ATR RANGING)."""
        trailer._atr_cache["EURUSD"] = (0.0020, time.time())
        trailer.position_regime["1002"] = "TREND_UP"
        with patch("engine_simple.trailer.random.uniform", return_value=0.0):  # no jitter
            pos = _make_position(ticket=1002, current=1.10900, entry=1.10500, sl=1.10200)
            trailer._check_step_trailing(pos)
        mock_mt5.update_sl.assert_called_once()
        args = mock_mt5.update_sl.call_args[0]
        # TREND_UP: niveaux EURUSD = [(2.0, 1.0), ...]
        # profit_atr≈2.0 → trail_dist=1.0 → SL = 1.10900 - 1.0*0.0020 = 1.10700
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
        """BUY: SL = entry - 2×ATR, TP = entry + 5×ATR."""
        sl, tp = trailer.calc_sl_tp("EURUSD", 1.10500, 0, atr_val=0.0020)
        assert sl == pytest.approx(1.10100, abs=0.0003)  # 1.10500 - 2*0.0020
        assert tp == pytest.approx(1.11300, abs=0.0005)  # 1.10500 + 4*0.0020

    def test_sell_sl_tp_with_atr(self, trailer, mock_mt5):
        """SELL: SL = entry + 2×ATR, TP = entry - 5×ATR."""
        sl, tp = trailer.calc_sl_tp("EURUSD", 1.10500, 1, atr_val=0.0020)
        assert sl == pytest.approx(1.10900, abs=0.0003)  # 1.10500 + 2*0.0020
        assert tp == pytest.approx(1.09700, abs=0.0005)  # 1.10500 - 4*0.0020

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
        """Persist: les données sont correctement sérialisées."""
        trailer.partial_closed.add("1001")
        trailer.partial_closed.add("1002")

        # Patcher pathlib.Path.exists et read_text au niveau du module
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="{}"):
                with patch("pathlib.Path.with_suffix") as mock_suffix:
                    mock_tmp = MagicMock()
                    mock_suffix.return_value = mock_tmp

                    trailer._persist_partial_closed()

                    # Vérifier le JSON écrit
                    mock_tmp.write_text.assert_called_once()
                    written = json.loads(mock_tmp.write_text.call_args[0][0])
                    assert "partial_closed" in written
                    assert "1001" in written["partial_closed"]
                    assert "1002" in written["partial_closed"]

    def test_no_crash_on_write_error(self, trailer):
        """Erreur d'écriture → log warning, pas de crash."""
        trailer.partial_closed.add("1001")
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
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
