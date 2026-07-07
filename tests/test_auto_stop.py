"""Tests for engine_simple/auto_stop.py — AutoStop"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

import numpy as np
import pytest

from engine_simple.auto_stop import (
    compute_adx,
    _NumpyEncoder,
    _validate_adx_snapshot,
    load_state,
    save_state,
    check_adx,
    decision,
    STATE_FILE,
    ADX_LOW_THRESHOLD,
    ADX_HIGH_THRESHOLD,
    RATIO_STOP,
    SYMBOLS_MIN_RESUME,
    PAUSE_MIN_DURATION,
    ADX_SNAPSHOT_TTL,
    STATE_TTL,
    ACTIVE_SYMBOLS,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_rates(n, base=1.1, trend=0, vol=0.001, seed=42):
    rng = np.random.RandomState(seed)
    closes = [base + trend * i + rng.normal(0, vol) for i in range(n)]
    highs = [c + abs(rng.normal(0, vol * 2)) for c in closes]
    lows = [c - abs(rng.normal(0, vol * 2)) for c in closes]
    return np.array(highs, dtype=float), np.array(lows, dtype=float), np.array(closes, dtype=float)


def _make_rates_list(n, base=1.1, trend=0, vol=0.001, seed=42):
    h, l, c = _make_rates(n, base, trend, vol, seed)
    return h.tolist(), l.tolist(), c.tolist()


def _mock_rates(high, low, close):
    """Return list of MT5-style tuples (time,open,high,low,close,volume,spread,real_volume)."""
    return [(0, 0.0, high[i], low[i], close[i], 100, 0, 0) for i in range(len(close))]


# ── compute_adx ──────────────────────────────────────────────────────


class TestComputeADX:
    def test_too_short_returns_zero(self):
        assert compute_adx([1, 2], [1, 2], [1, 2], period=14) == 0.0

    def test_exact_period_plus_two(self):
        h, l, c = _make_rates_list(16, trend=0.0005, vol=0.0002)
        result = compute_adx(h, l, c, period=14)
        assert isinstance(result, float)
        assert result >= 0.0

    def test_flat_prices_returns_zero(self):
        h = [1.0] * 30
        l = [1.0] * 30
        c = [1.0] * 30
        assert compute_adx(h, l, c, period=14) == 0.0

    def test_trending_produces_positive_dx(self):
        h, l, c = _make_rates_list(30, trend=0.001, vol=0.0001)
        dx = compute_adx(h, l, c, period=14)
        assert dx > 10.0

    def test_numpy_arrays_accepted(self):
        h, l, c = _make_rates(30, trend=0.001, vol=0.0001)
        dx = compute_adx(h, l, c, period=14)
        assert dx > 0.0

    def test_atr_zero_returns_zero(self):
        h = [1.0] * 30
        l = [1.0] * 30
        c = [1.0 + 0.001 * i for i in range(30)]
        result = compute_adx(h, l, c, period=14)
        assert result == 0.0


# ── _NumpyEncoder ────────────────────────────────────────────────────


class TestNumpyEncoder:
    def test_bool_conversion(self):
        enc = _NumpyEncoder()
        assert enc.default(np.bool_(True)) is True
        assert enc.default(np.bool_(False)) is False

    def test_integer_conversion(self):
        enc = _NumpyEncoder()
        assert enc.default(np.int32(42)) == 42
        assert enc.default(np.int64(99)) == 99

    def test_floating_conversion(self):
        enc = _NumpyEncoder()
        result = enc.default(np.float64(3.14))
        assert isinstance(result, float)
        assert abs(result - 3.14) < 1e-10

    def test_ndarray_conversion(self):
        enc = _NumpyEncoder()
        arr = np.array([1, 2, 3])
        assert enc.default(arr) == [1, 2, 3]

    def test_json_serialize_with_numpy_types(self):
        data = {"val": np.float64(1.5), "flag": np.bool_(True), "n": np.int32(10)}
        out = json.dumps(data, cls=_NumpyEncoder)
        parsed = json.loads(out)
        assert parsed["val"] == 1.5
        assert parsed["flag"] is True
        assert parsed["n"] == 10


# ── _validate_adx_snapshot ──────────────────────────────────────────


class TestValidateADXSnapshot:
    def test_not_dict_returns_empty(self):
        assert _validate_adx_snapshot(None) == {}
        assert _validate_adx_snapshot("bad") == {}

    def test_fixes_string_bools(self):
        snap = {"XAUUSD": {"adx": 25.0, "low": "False"}}
        result = _validate_adx_snapshot(snap)
        assert result["XAUUSD"]["low"] is False

    def test_fixes_string_bool_true(self):
        snap = {"BTCUSD": {"adx": 15.0, "low": "True"}}
        result = _validate_adx_snapshot(snap)
        assert result["BTCUSD"]["low"] is True

    def test_fixes_non_bool_low(self):
        snap = {"EURUSD": {"adx": 20.0, "low": 1}}
        result = _validate_adx_snapshot(snap)
        assert result["EURUSD"]["low"] is True

    def test_fixes_string_adx(self):
        snap = {"XAUUSD": {"adx": "22.5", "low": False}}
        result = _validate_adx_snapshot(snap)
        assert result["XAUUSD"]["adx"] == 22.5

    def test_fixes_invalid_string_adx_to_zero(self):
        snap = {"XAUUSD": {"adx": "notanumber", "low": False}}
        result = _validate_adx_snapshot(snap)
        assert result["XAUUSD"]["adx"] == 0.0

    def test_fixes_missing_adx_to_zero(self):
        snap = {"XAUUSD": {"adx": None, "low": False}}
        result = _validate_adx_snapshot(snap)
        assert result["XAUUSD"]["adx"] == 0.0

    def test_non_dict_data_gets_default(self):
        snap = {"XAUUSD": "corrupted"}
        result = _validate_adx_snapshot(snap)
        assert result["XAUUSD"] == {"adx": 0.0, "low": False}

    def test_valid_data_unchanged(self):
        snap = {"XAUUSD": {"adx": 25.0, "low": False}}
        result = _validate_adx_snapshot(snap)
        assert result["XAUUSD"]["adx"] == 25.0
        assert result["XAUUSD"]["low"] is False


# ── load_state ───────────────────────────────────────────────────────


class TestLoadState:
    @patch("engine_simple.auto_stop.STATE_FILE", new_callable=MagicMock)
    def test_no_file_returns_default(self, mock_file):
        mock_file.exists.return_value = False
        state = load_state()
        assert state["auto_paused"] is False
        assert state["auto_paused_at"] is None
        assert state["auto_paused_until"] is None
        assert state["adx_snapshot"] == {}
        assert state["adx_snapshot_ts"] == 0.0

    @patch("engine_simple.auto_stop.STATE_FILE", new_callable=MagicMock)
    def test_loads_valid_state(self, mock_file):
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = json.dumps(
            {
                "auto_paused": True,
                "auto_paused_at": "2026-07-01T12:00:00",
                "auto_paused_until": "2026-07-01T12:30:00",
                "adx_snapshot": {"XAUUSD": {"adx": 15.0, "low": True}},
                "adx_snapshot_ts": 1000.0,
            }
        )
        state = load_state()
        assert state["auto_paused"] is True
        assert state["adx_snapshot"]["XAUUSD"]["low"] is True

    @patch("engine_simple.auto_stop.STATE_FILE", new_callable=MagicMock)
    def test_corrupt_json_returns_default(self, mock_file):
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "not json{{{"
        state = load_state()
        assert state["auto_paused"] is False

    @patch("engine_simple.auto_stop.STATE_FILE", new_callable=MagicMock)
    def test_oserror_returns_default(self, mock_file):
        mock_file.exists.return_value = True
        mock_file.read_text.side_effect = OSError("permission denied")
        state = load_state()
        assert state["auto_paused"] is False


# ── save_state ───────────────────────────────────────────────────────


@patch("engine_simple.auto_stop.STATE_FILE", new_callable=MagicMock)
class TestSaveState:
    def test_writes_state(self, mock_file):
        mock_file.parent.mkdir = MagicMock()
        state = {"auto_paused": True}
        with patch("engine_simple.auto_stop.STATE_FILE.write_text") as mock_write:
            save_state(state)
            mock_write.assert_called_once()
            args, _ = mock_write.call_args
            parsed = json.loads(args[0])
            assert parsed["auto_paused"] is True

    def test_oserror_logged(self, mock_file):
        mock_file.parent.mkdir = MagicMock()
        state = {"auto_paused": True}
        with patch("engine_simple.auto_stop.STATE_FILE.write_text") as mock_write:
            mock_write.side_effect = OSError("disk full")
            with patch("engine_simple.auto_stop.logger") as mock_log:
                save_state(state)
                mock_log.error.assert_called_once()

    def test_handles_numpy_types(self, mock_file):
        mock_file.parent.mkdir = MagicMock()
        state = {"val": np.float64(2.5), "flag": np.bool_(True)}
        with patch("engine_simple.auto_stop.STATE_FILE.write_text") as mock_write:
            save_state(state)
            args, _ = mock_write.call_args
            parsed = json.loads(args[0])
            assert parsed["val"] == 2.5
            assert parsed["flag"] is True


# ── check_adx ────────────────────────────────────────────────────────


class TestCheckADX:
    @patch("engine_simple.auto_stop.cfg", None)
    @patch("engine_simple.auto_stop.MT5Connector", None)
    def test_no_cfg_returns_defaults(self):
        ratio, total, details = check_adx()
        assert ratio == 0.0
        assert total == 0
        assert details == {}

    @patch("engine_simple.auto_stop.cfg", MagicMock())
    @patch("engine_simple.auto_stop.MT5Connector", MagicMock())
    def test_connector_returns_none_returns_zero(self):
        connector = MagicMock()
        connector.get_rates.return_value = None
        ratio, total, details = check_adx(connector)
        assert total == 0

    @patch("engine_simple.auto_stop.cfg", MagicMock())
    @patch("engine_simple.auto_stop.MT5Connector", MagicMock())
    def test_connector_short_data_skipped(self):
        connector = MagicMock()
        connector.get_rates.return_value = [(0, 1, 2, 1, 1.5, 100, 0, 0)] * 5
        ratio, total, details = check_adx(connector)
        assert total == 0

    @patch("engine_simple.auto_stop.cfg", MagicMock())
    @patch("engine_simple.auto_stop.MT5Connector", MagicMock())
    def test_returns_ratio_and_details(self):
        h, l, c = _make_rates_list(30, trend=0.002, vol=0.0002)
        rates = _mock_rates(h, l, c)
        connector = MagicMock()
        connector.get_rates.return_value = rates
        ratio, total, details = check_adx(connector)
        assert total > 0
        for sym, d in details.items():
            assert "adx" in d
            assert "low" in d
            assert isinstance(d["low"], bool)
            assert isinstance(d["adx"], float)

    @patch("engine_simple.auto_stop.cfg", MagicMock())
    @patch("engine_simple.auto_stop.MT5Connector", MagicMock())
    def test_exception_on_symbol_skipped(self):
        connector = MagicMock()
        connector.get_rates.side_effect = RuntimeError("API error")
        ratio, total, details = check_adx(connector)
        assert total == 0

    @patch("engine_simple.auto_stop.cfg", MagicMock())
    @patch("engine_simple.auto_stop.MT5Connector", MagicMock())
    def test_low_adx_detected(self):
        h = [1.0] * 30
        l = [1.0] * 30
        c = [1.0] * 30
        rates = _mock_rates(h, l, c)
        connector = MagicMock()
        connector.get_rates.return_value = rates
        ratio, total, details = check_adx(connector)
        for d in details.values():
            assert d["low"] is True


class TestCheckADXFallback:
    @patch("engine_simple.auto_stop.cfg", MagicMock())
    @patch("engine_simple.auto_stop.MT5Connector", MagicMock())
    def test_no_connector_fallback_handles_import_error(self):
        with patch("builtins.__import__") as mock_import:
            mock_import.side_effect = ImportError("No module named MetaTrader5")
            ratio, total, details = check_adx(mt5_connector=None)
            assert total == 0


# ── decision ─────────────────────────────────────────────────────────


class TestDecision:
    def _default_state(self):
        return {
            "auto_paused": False,
            "auto_paused_at": None,
            "auto_paused_until": None,
            "adx_snapshot": {},
            "adx_snapshot_ts": 0.0,
        }

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.check_adx")
    @patch("engine_simple.auto_stop.save_state")
    def test_stop_when_ratio_exceeds(self, mock_save, mock_check, mock_load):
        mock_load.return_value = self._default_state()
        mock_check.return_value = (
            1.0,
            3,
            {
                "XAUUSD": {"adx": 15.0, "low": True},
                "BTCUSD": {"adx": 12.0, "low": True},
                "US30.cash": {"adx": 10.0, "low": True},
            },
        )
        verdict, state = decision(force_check=True)
        assert verdict == "STOP"
        assert state["auto_paused"] is True

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.save_state")
    def test_waits_when_paused_and_not_expired(self, mock_save, mock_load):
        future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        mock_load.return_value = {
            "auto_paused": True,
            "auto_paused_at": datetime.utcnow().isoformat(),
            "auto_paused_until": future,
            "adx_snapshot": {},
            "adx_snapshot_ts": time.time(),
        }
        verdict, state = decision(force_check=False)
        assert verdict == "WAIT"

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.check_adx")
    @patch("engine_simple.auto_stop.save_state")
    def test_resume_when_enough_symbols_ok(self, mock_save, mock_check, mock_load):
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        mock_load.return_value = {
            "auto_paused": True,
            "auto_paused_at": past,
            "auto_paused_until": past,
            "adx_snapshot": {},
            "adx_snapshot_ts": 0.0,
        }
        mock_check.return_value = (
            0.0,
            3,
            {
                "XAUUSD": {"adx": 25.0, "low": False},
                "BTCUSD": {"adx": 28.0, "low": False},
                "US30.cash": {"adx": 15.0, "low": True},
            },
        )
        verdict, state = decision(force_check=True)
        assert verdict == "RESUME"
        assert state["auto_paused"] is False

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.check_adx")
    @patch("engine_simple.auto_stop.save_state")
    def test_noop_when_not_paused_and_ratio_below(self, mock_save, mock_check, mock_load):
        mock_load.return_value = self._default_state()
        mock_check.return_value = (
            0.0,
            3,
            {
                "XAUUSD": {"adx": 25.0, "low": False},
                "BTCUSD": {"adx": 28.0, "low": False},
                "US30.cash": {"adx": 30.0, "low": False},
            },
        )
        verdict, state = decision(force_check=True)
        assert verdict == "NOOP"
        assert state["auto_paused"] is False

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.check_adx")
    @patch("engine_simple.auto_stop.save_state")
    def test_noop_when_total_zero(self, mock_save, mock_check, mock_load):
        mock_load.return_value = self._default_state()
        mock_check.return_value = (0.0, 0, {})
        verdict, state = decision(force_check=True)
        assert verdict == "NOOP"

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.save_state")
    def test_uses_cached_snapshot_when_fresh(self, mock_save, mock_load):
        mock_load.return_value = {
            "auto_paused": False,
            "auto_paused_at": None,
            "auto_paused_until": None,
            "adx_snapshot": {
                "XAUUSD": {"adx": 15.0, "low": True},
                "BTCUSD": {"adx": 12.0, "low": True},
                "US30.cash": {"adx": 10.0, "low": True},
            },
            "adx_snapshot_ts": time.time(),
        }
        mock_save.return_value = None
        verdict, state = decision(force_check=False)
        assert verdict == "STOP"

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.check_adx")
    @patch("engine_simple.auto_stop.save_state")
    def test_waits_and_prolongs_when_still_low(self, mock_save, mock_check, mock_load):
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        mock_load.return_value = {
            "auto_paused": True,
            "auto_paused_at": past,
            "auto_paused_until": past,
            "adx_snapshot": {},
            "adx_snapshot_ts": 0.0,
        }
        mock_check.return_value = (
            1.0,
            3,
            {
                "XAUUSD": {"adx": 15.0, "low": True},
                "BTCUSD": {"adx": 12.0, "low": True},
                "US30.cash": {"adx": 10.0, "low": True},
            },
        )
        verdict, state = decision(force_check=True)
        assert verdict == "WAIT"

    @patch("engine_simple.auto_stop.load_state")
    @patch("engine_simple.auto_stop.check_adx")
    @patch("engine_simple.auto_stop.save_state")
    def test_stale_state_clears_pause(self, mock_save, mock_check, mock_load):
        very_old = (datetime.utcnow() - timedelta(days=10)).isoformat()
        mock_load.return_value = {
            "auto_paused": True,
            "auto_paused_at": very_old,
            "auto_paused_until": very_old,
            "adx_snapshot": {},
            "adx_snapshot_ts": 0.0,
        }
        mock_check.return_value = (
            0.0,
            3,
            {
                "XAUUSD": {"adx": 25.0, "low": False},
                "BTCUSD": {"adx": 28.0, "low": False},
                "US30.cash": {"adx": 30.0, "low": False},
            },
        )
        verdict, state = decision(force_check=True)
        assert verdict == "NOOP"
        assert state["auto_paused"] is False


# ── Constants ────────────────────────────────────────────────────────


class TestConstants:
    def test_constants_are_defined(self):
        assert ADX_LOW_THRESHOLD == 22
        assert ADX_HIGH_THRESHOLD == 18
        assert isinstance(RATIO_STOP, float)
        assert isinstance(SYMBOLS_MIN_RESUME, int)
        assert isinstance(PAUSE_MIN_DURATION, int)
        assert isinstance(ADX_SNAPSHOT_TTL, int)

    def test_active_symbols_non_empty(self):
        assert len(ACTIVE_SYMBOLS) >= 3
        assert "XAUUSD" in ACTIVE_SYMBOLS
        assert "USDJPY" in ACTIVE_SYMBOLS  # BTCUSD retiré de la liste active (Juil 2026)
