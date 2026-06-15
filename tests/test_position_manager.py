"""Tests for position_manager.py — PositionManager"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

import numpy as np
import pytest

import config_simple as cfg
from engine_simple.position_manager import PositionManager


class TestPositionManager:
    def make_manager(self, mt5=None, ftmo=None, adaptive=None,
                     signals=None, regime=None, pos_cache=None):
        mt5 = mt5 or MagicMock()
        ftmo = ftmo or MagicMock()
        # PositionGuard SUPPRIMÉ (FIX #23) — plus de paramètre
        adaptive = adaptive or MagicMock()
        adaptive.vigilance.return_value = {
            "regime": "RANGING", "adx": 15, "atr": 0.003, "action": "HOLD",
        }
        signals = signals or MagicMock()
        regime = regime or MagicMock()
        regime.detect.return_value = ("RANGING", {"adx": 15})
        pos_cache = pos_cache or MagicMock()
        pos_cache.get.return_value = []
        return PositionManager(mt5, ftmo, adaptive, signals, regime, pos_cache)

    def make_pos(self, ticket=1, symbol="EURUSD", ptype=0, volume=0.1,
                 price_open=1.1000, sl=1.0900, tp=1.1300, profit=0.0,
                 comment="ADAPT_RAN", magic=None):
        pos = MagicMock()
        pos.ticket = ticket
        pos.symbol = symbol
        pos.type = ptype
        pos.volume = volume
        pos.price_open = price_open
        pos.sl = sl
        pos.tp = tp
        pos.profit = profit
        pos.magic = magic or cfg.ROBOT_MAGIC
        pos.comment = comment
        return pos

    # ── manage_positions ────────────────────────────────────────────

    def test_manage_positions_no_positions(self):
        pm = self.make_manager()
        pm.manage_positions()
        # No crash with empty positions
        assert True

    def test_manage_positions_with_open_positions(self):
        pm = self.make_manager()
        pos = self.make_pos(ticket=1, symbol="EURUSD", profit=25.0)
        pm._pos_cache.get.return_value = [pos]
        pm.manage_positions()
        # ftmo reconciliation called
        pm.ftmo._reconcile_positions.assert_called_once()
        # invariant check called
        pm.ftmo.check_invariants.assert_called_with(pos)
        # PositionGuard REMOVED (Juin 2026) — ATR hardcodé 0.005 dangereux supprimé
        # Les tests suivants ne sont plus pertinents ; les assertions FTMO sont suffisantes

    def test_manage_positions_guard_removed(self):
        """PositionGuard a été supprimé car ATR était hardcodé à 0.005 (destructif)."""
        pytest.skip("PositionGuard removed — ATR hardcodé dangereux (issue #B1)")

    def test_manage_positions_guard_removed_2(self):
        pytest.skip("PositionGuard removed")

    def test_manage_positions_guard_removed_3(self):
        pytest.skip("PositionGuard removed")

    # ── vigilance_scan ──────────────────────────────────────────────

    def test_vigilance_scan_no_rates(self):
        pm = self.make_manager()
        pm._get_rates_for_vigilance = MagicMock(return_value=None)
        pm.vigilance_scan()
        # Should skip silently
        assert True

    def test_vigilance_scan_no_adaptive_result(self):
        pm = self.make_manager()
        pm._get_rates_for_vigilance = MagicMock(return_value={"H1": [(1, 1.1, 1.1, 1.1, 1.1, 100)] * 30})
        pm.adaptive.vigilance.return_value = None
        pm.vigilance_scan()
        # Should skip
        assert True

    def test_vigilance_scan_calls_adaptive_vigilance(self):
        pm = self.make_manager()
        rates = {"H1": [(1, 1.1, 1.1, 1.1, 1.1, 100)] * 30,
                 "M15": [(1, 1.1, 1.1, 1.1, 1.1, 100)] * 30,
                 "M5": [(1, 1.1, 1.1, 1.1, 1.1, 100)] * 30}
        pm._get_rates_for_vigilance = MagicMock(return_value=rates)
        pm.vigilance_scan()
        # Should call adaptive.vigilance for EACH symbol in cfg.SYMBOLS
        assert pm.adaptive.vigilance.call_count == len(cfg.SYMBOLS)

    def test_vigilance_scan_regime_detect(self):
        pm = self.make_manager()
        rates = {"H1": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)],
                 "M15": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)],
                 "M5": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)]}
        pm._get_rates_for_vigilance = MagicMock(return_value=rates)
        pm.vigilance_scan()
        # regime_detector.detect should be called
        assert pm._regime_detector.detect.call_count == len(cfg.SYMBOLS)

    def test_vigilance_scan_with_open_position(self):
        pm = self.make_manager()
        rates = {"H1": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)],
                 "M15": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)],
                 "M5": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)]}
        pm._get_rates_for_vigilance = MagicMock(return_value=rates)
        pos = self.make_pos(ticket=1, symbol=cfg.SYMBOLS[0], profit=10.0,
                            comment="ADAPT_TREND")
        pm._pos_cache.get.return_value = [pos]  # has position for first symbol
        pm.vigilance_scan()
        # Should not crash
        assert True

    def test_vigilance_detects_regime_shift(self):
        pm = self.make_manager()
        rates = {"H1": [(i, 1.1, 1.1, 1.12, 1.1, 100) for i in range(30)],
                 "M15": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)],
                 "M5": [(i, 1.1, 1.1, 1.1, 1.1, 100) for i in range(30)]}
        pm._get_rates_for_vigilance = MagicMock(return_value=rates)
        pos = self.make_pos(ticket=1, symbol=cfg.SYMBOLS[0], profit=15.0,
                            comment="ADAPT_RAN")
        pm._pos_cache.get.return_value = [pos]
        pm.vigilance_scan()
        # No crash with regime comparison
        assert True

    def test_vigilance_error_handling(self):
        pm = self.make_manager()
        pm._get_rates_for_vigilance = MagicMock(side_effect=Exception("test error"))
        pm.vigilance_scan()
        # Should catch the exception and continue (see except in vigilance_scan)
        # Since the error happens in _get_rates_for_vigilance which is called inside
        # the try block, it should be caught
        assert True

    # ── _get_rates_for_vigilance ────────────────────────────────────

    def test_get_rates_cache_miss(self):
        pm = self.make_manager()
        pm.mt5.get_rates_multi_tf.return_value = {
            "H1": [(1, 1.1, 1.1, 1.1, 1.1, 100)]}
        rates = pm._get_rates_for_vigilance("EURUSD")
        assert rates is not None
        assert "H1" in rates
        assert "EURUSD" in pm._vigilance_rate_cache

    def test_get_rates_cache_hit(self):
        pm = self.make_manager()
        cached_rates = {"H1": [(1, 1.1, 1.1, 1.1, 1.1, 100)]}
        pm._vigilance_rate_cache["EURUSD"] = {"rates": cached_rates, "time": time.time()}
        rates = pm._get_rates_for_vigilance("EURUSD")
        assert rates is cached_rates  # same object
        pm.mt5.get_rates_multi_tf.assert_not_called()

    def test_get_rates_cache_expired(self):
        pm = self.make_manager()
        old_rates = {"H1": [(1, 1.1, 1.1, 1.1, 1.1, 100)]}
        pm._vigilance_rate_cache["EURUSD"] = {"rates": old_rates, "time": time.time() - 120}
        pm.mt5.get_rates_multi_tf.return_value = {"H1": [(2, 1.2, 1.2, 1.2, 1.2, 200)]}
        rates = pm._get_rates_for_vigilance("EURUSD")
        assert rates is not old_rates  # new rates
        pm.mt5.get_rates_multi_tf.assert_called_once()

    def test_get_rates_returns_none(self):
        pm = self.make_manager()
        pm.mt5.get_rates_multi_tf.return_value = None
        rates = pm._get_rates_for_vigilance("EURUSD")
        assert rates is None
