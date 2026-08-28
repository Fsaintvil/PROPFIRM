"""Tests for position_tracker.py — SymbolPerformance + PositionTracker"""

import os
import sys
import time
from collections import OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import ANY, MagicMock, patch

import config_simple as cfg
from engine_simple.position_tracker import SymbolPerformance, PositionTracker


# ── SymbolPerformance ───────────────────────────────────────────────


class TestSymbolPerformance:
    def test_init(self):
        p = SymbolPerformance()
        assert p.trades == 0
        assert p.wins == 0
        assert p.losses == 0
        assert p.total_profit == 0.0

    def test_record_win(self):
        p = SymbolPerformance()
        p.record(100.0, 2.0)
        assert p.trades == 1
        assert p.wins == 1
        assert p.losses == 0
        assert p.total_profit == 100.0
        assert p.total_r_multiple == 2.0
        assert p.gross_profit == 100.0
        assert p.gross_loss == 0.0
        assert p.consecutive_wins == 1
        assert p.consecutive_losses == 0

    def test_record_loss(self):
        p = SymbolPerformance()
        p.record(-50.0, -1.0)
        assert p.trades == 1
        assert p.wins == 0
        assert p.losses == 1
        assert p.total_profit == -50.0
        assert p.gross_profit == 0.0
        assert p.gross_loss == 50.0
        assert p.consecutive_wins == 0
        assert p.consecutive_losses == 1

    def test_win_rate(self):
        p = SymbolPerformance()
        assert p.win_rate == 0.0  # no trades
        p.record(10, 0.5)
        p.record(-5, -0.3)
        p.record(20, 1.0)
        assert p.win_rate == 2 / 3
        assert p.avg_profit == (10 - 5 + 20) / 3
        assert p.avg_r_multiple == (0.5 - 0.3 + 1.0) / 3

    def test_profit_factor(self):
        p = SymbolPerformance()
        assert p.profit_factor == 0.0  # no trades → gross_loss=1 (max(0,1))
        p.record(100, 2.0)
        p.record(-30, -0.5)
        assert p.profit_factor == 100.0 / 30.0
        p.record(-20, -0.4)
        assert p.profit_factor == 100.0 / 50.0

    def test_consecutive_tracking(self):
        p = SymbolPerformance()
        p.record(10, 0.5)
        p.record(20, 1.0)
        assert p.consecutive_wins == 2
        assert p.max_consecutive_wins == 2
        p.record(-5, -0.2)
        assert p.consecutive_wins == 0
        assert p.consecutive_losses == 1
        p.record(-10, -0.5)
        assert p.consecutive_losses == 2
        assert p.max_consecutive_losses == 2
        p.record(15, 0.8)
        assert p.consecutive_losses == 0
        assert p.consecutive_wins == 1
        assert p.max_consecutive_wins == 2  # unchanged

    def test_summary(self):
        p = SymbolPerformance()
        p.record(100, 2.0)
        p.record(-30, -0.5)
        s = p.summary()
        assert s["trades"] == 2
        assert s["win_rate"] == 0.5
        assert s["total_pnl"] == 70.0
        assert s["profit_factor"] == round(100.0 / 30.0, 2)
        assert s["avg_trade"] == 35.0


# ── PositionTracker ─────────────────────────────────────────────────


class TestPositionTracker:
    def make_tracker(self):
        ftmo = MagicMock()
        journal = MagicMock()
        adaptive = MagicMock()
        pos_cache = MagicMock()
        mt5 = MagicMock()
        # Setup MT5 mock for history
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.get_history.return_value = []
        mt5.calc_profit.return_value = 10.0
        audit = MagicMock()
        tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5, audit)
        return tracker, ftmo, journal, adaptive, pos_cache, mt5, audit

    def test_init(self):
        tracker, *_ = self.make_tracker()
        assert tracker._previous_tickets == set()
        assert tracker._recorded_deals == OrderedDict()
        assert tracker._position_meta == {}
        assert tracker.performance == {}

    def test_perf_creates_on_demand(self):
        tracker, *_ = self.make_tracker()
        p = tracker._perf("EURUSD")
        assert isinstance(p, SymbolPerformance)
        # Second call returns same instance
        assert tracker._perf("EURUSD") is p

    def test_perf_separate_per_symbol(self):
        tracker, *_ = self.make_tracker()
        p1 = tracker._perf("EURUSD")
        p2 = tracker._perf("GBPUSD")
        assert p1 is not p2

    def test_init_tickets(self):
        tracker, _, _, _, pos_cache, _, _ = self.make_tracker()
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 12345
        pos_cache.get.return_value = [mock_pos]
        tracker.init_tickets()
        assert tracker._previous_tickets == {12345}

    def test_track_new_adds_meta(self):
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 100
        mock_pos.type = 0  # BUY
        mock_pos.symbol = "US100.cash"  # ?? 13 Aout 2026 (XAUUSD retiré - trou noir mode preuve)
        mock_pos.volume = 0.1
        mock_pos.price_open = 2450.50
        mock_pos.sl = 2440.50
        mock_pos.comment = "ADAPT_TREND"
        mt5.ORDER_TYPE_BUY = 0
        mt5.calc_profit.return_value = 100.0
        pos_cache.get.return_value = [mock_pos]

        with patch.object(tracker.feature_store, "load", return_value=None):
            tracker.track_new()

        assert 100 in tracker._position_meta
        meta = tracker._position_meta[100]
        assert meta["symbol"] == "US100.cash"
        assert meta["entry"] == 2450.50
        assert meta["sl"] == 2440.50
        assert meta["lot"] == 0.1
        assert meta["regime"] == "TREND"
        assert meta["r1_usd"] == 100.0

    def test_track_new_adds_meta_sell(self):
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 101
        mock_pos.type = 1  # SELL
        mock_pos.symbol = "US30.cash"  # ?? 13 Aout 2026 (EURUSD retiré - PF 0.74 après coûts)
        mock_pos.volume = 0.05
        mock_pos.price_open = 1.1000
        mock_pos.sl = 1.1100
        mock_pos.comment = ""
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.calc_profit.return_value = 50.0
        pos_cache.get.return_value = [mock_pos]

        with patch.object(tracker.feature_store, "load", return_value=None):
            tracker.track_new()

        meta = tracker._position_meta[101]
        # 🔧 FIX 10 Juil 2026: comment vide → fallback RANGING au lieu de LEGACY
        assert meta["regime"] == "RANGING"  # no ADAPT_ prefix → fallback RANGING
        assert meta["r1_usd"] == 50.0

    def test_track_new_restores_dl_features(self):
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 102
        mock_pos.type = 0
        mock_pos.symbol = "US100.cash"  # ?? 13 Aout 2026 (XAUUSD retiré - trou noir mode preuve)
        mock_pos.volume = 0.1
        mock_pos.price_open = 2450.50
        mock_pos.sl = 2440.50
        mock_pos.comment = "ADAPT_RAN"
        mt5.ORDER_TYPE_BUY = 0
        mt5.calc_profit.return_value = 100.0
        pos_cache.get.return_value = [mock_pos]

        saved = {"predictions": {"MOM20x3": "BUY"}}

        with patch.object(tracker.feature_store, "load", return_value=saved):
            tracker.track_new()

        meta = tracker._position_meta[102]
        assert meta["predictions"] == {"MOM20x3": "BUY"}

    def test_get_active_count(self):
        tracker, *_ = self.make_tracker()
        assert tracker.get_active_count() == 0
        tracker._position_meta[1] = {"symbol": "EURUSD"}
        assert tracker.get_active_count() == 1

    def test_add_meta(self):
        tracker, *_ = self.make_tracker()
        with patch.object(tracker.feature_store, "save") as mock_save:
            tracker.add_meta(200, {"symbol": "EURUSD", "entry": 1.1000})

        # Nouveau comportement : si ticket n'existe pas dans _position_meta,
        # les données sont stockées dans _meta_extra en attente de track_new()
        assert 200 not in tracker._position_meta
        assert 200 in tracker._meta_extra
        assert tracker._meta_extra[200]["symbol"] == "EURUSD"
        assert tracker._meta_extra[200]["entry"] == 1.1000
        mock_save.assert_called_once()

    def test_performance_summary(self):
        tracker, *_ = self.make_tracker()
        assert tracker.performance_summary() == {}
        tracker._perf("EURUSD").record(100, 2.0)
        s = tracker.performance_summary()
        assert "EURUSD" in s
        assert s["EURUSD"]["trades"] == 1

    def test_global_summary(self):
        tracker, *_ = self.make_tracker()
        g = tracker.global_summary()
        assert g["total_trades"] == 0
        assert g["total_profit"] == 0.0
        assert g["global_win_rate"] == 0.0
        assert g["symbols_tracked"] == 0

        tracker._perf("EURUSD").record(100, 2.0)
        tracker._perf("EURUSD").record(-30, -0.5)
        tracker._perf("GBPUSD").record(50, 1.0)
        g = tracker.global_summary()
        assert g["total_trades"] == 3
        assert g["total_profit"] == 120.0
        assert g["global_win_rate"] == round(2 / 3, 3)  # 0.667
        assert g["symbols_tracked"] == 2

    def test_check_closed_no_change(self):
        tracker, _, _, _, pos_cache, _, _ = self.make_tracker()
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 1
        pos_cache.get.return_value = [mock_pos]
        tracker._previous_tickets = {1}
        # No change -> no closed tickets
        tracker.check_closed()
        assert tracker._recorded_deals == OrderedDict()

    def test_check_closed_no_closing_deal_goes_to_retry(self):
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []  # all positions closed
        mt5.get_history.return_value = []  # no closing deals
        tracker.check_closed()
        # 🔧 FIX 12 Août 2026: plus d'abandon immédiat du PnL — le ticket part en retry.
        # Avant ce fix, 1 était marqué _recorded_deals[1]=None et le PnL perdu (stats fausses).
        assert 1 not in tracker._recorded_deals
        assert 1 in tracker._pending_closures
        assert tracker._pending_closures[1] == tracker.CLOSE_RETRY_ATTEMPTS

    def test_check_closed_retry_recovers_lost_deal(self):
        """🔧 FIX 12 Août 2026: un deal MT5 absent au cycle N mais présent au cycle N+1
        (gap de session post-reconnexion, observé le 11/08 avec 2 XAUUSD −240.65$)
        doit être ENREGISTRÉ via le retry, pas abandonné définitivement."""
        tracker, ftmo, journal, adaptive, pos_cache, mt5, audit = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []  # position fermée

        # Meta du trade (comme le tracker l'aurait stocké à l'ouverture)
        tracker._position_meta[1] = {
            "symbol": "US100.cash",
            "entry": 2450.0,
            "sl": 2440.0,
            "lot": 0.1,
            "regime": "TREND_UP",
            "r1_usd": 100.0,
            "opened_at": time.time() - 3600,
        }

        deal = MagicMock()
        deal.position_id = 1
        deal.symbol = "US100.cash"
        deal.magic = cfg.ROBOT_MAGIC
        deal.profit = -120.5
        deal.type = 1  # SELL (closing a BUY) → direction réelle = BUY
        deal.volume = 0.1
        deal.price = 2455.0
        deal.time = int(time.time()) + 3600  # futur (après _start_time → trade live, pas historique)

        # Cycle 1: MT5 pas encore synchronisé après reconnexion → aucun deal
        mt5.get_history.return_value = []
        tracker.check_closed()
        assert 1 not in tracker._recorded_deals, "le ticket ne doit pas être abandonné au cycle 1"
        assert 1 in tracker._pending_closures

        # Cycle 2: MT5 a synchronisé le deal → le sweep du retry l'enregistre complètement
        mt5.get_history.return_value = [deal]
        tracker.check_closed()
        assert 1 in tracker._recorded_deals, "le retry doit enregistrer le trade laissé pour compte"
        assert 1 not in tracker._pending_closures, "la file de retry doit être nettoyée"
        assert "1_US100.cash" in tracker._recorded_position_ids
        ftmo.record_trade_result.assert_called_with(
            "US100.cash", -120.5, historical=False, trade_time=ANY, direction="BUY"
        )
        journal.record.assert_called_once()
        adaptive.record_result.assert_called_once()
        audit.log_decision.assert_called_once()

    def test_check_closed_retry_abandons_after_exhaustion(self):
        """🔧 FIX 12 Août 2026: après CLOSE_RETRY_ATTEMPTS cycles sans deal MT5,
        le ticket retombe sur le comportement historique (fallback import_history)."""
        tracker, ftmo, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []
        mt5.get_history.return_value = []  # deal jamais synchronisé

        tracker.check_closed()  # enqueue le retry
        assert 1 in tracker._pending_closures

        # On épuise les tentatives (sweeps des cycles suivants)
        for _ in range(tracker.CLOSE_RETRY_ATTEMPTS):
            tracker.check_closed()

        assert 1 not in tracker._pending_closures, "le retry doit avoir été purgé"
        assert 1 in tracker._recorded_deals
        assert tracker._recorded_deals[1] is None
        ftmo.record_trade_result.assert_not_called()

    def test_check_closed_retry_cancelled_on_reappearance(self):
        """🔧 FIX 12 Août 2026: si la position RÉAPPARAÎT (glitch MT5, pas une vraie
        fermeture), le retry doit être annulé — jamais enregistrer un faux trade."""
        tracker, ftmo, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._pending_closures = {1: 6}
        # La position 1 est toujours ouverte (glitch: get_positions avait timeout)
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 1
        pos_cache.get.return_value = [mock_pos]
        mt5.get_history.return_value = []

        tracker.check_closed()
        assert 1 not in tracker._pending_closures, "retry doit être annulé si la position réapparaît"
        assert 1 not in tracker._recorded_deals
        ftmo.record_trade_result.assert_not_called()

    @patch("engine_simple.position_tracker.FeatureStore")
    def test_check_closed_with_deal(self, mock_fs):
        tracker, ftmo, journal, adaptive, pos_cache, mt5, audit = self.make_tracker()
        # Setup: ticket 1 was open, now it's gone
        mock_pos = MagicMock()
        mock_pos.magic = cfg.ROBOT_MAGIC
        mock_pos.ticket = 1
        pos_cache.get.return_value = []
        tracker._previous_tickets = {1}

        # Setup meta
        tracker._position_meta[1] = {
            "symbol": "US100.cash",  # ?? 13 Aout 2026 (XAUUSD retiré - trou noir mode preuve)
            "entry": 2450.0,
            "sl": 2440.0,
            "lot": 0.1,
            "regime": "RANGING",
            "r1_usd": 100.0,
            "opened_at": time.time() - 3600,
        }

        # Setup closing deal — time APRÈS _start_time (trade live = pas historique)
        deal = MagicMock()
        deal.position_id = 1
        deal.symbol = "US100.cash"
        deal.magic = cfg.ROBOT_MAGIC
        deal.profit = 150.0
        deal.type = 1  # SELL (closing a BUY)
        deal.volume = 0.1
        deal.price = 2455.0
        deal.time = int(time.time()) + 3600  # futur (après start)
        mt5.get_history.return_value = [deal]

        tracker.check_closed()

        # Verify recording — pas historical car trade live (time > _start_time)
        assert 1 in tracker._recorded_deals
        assert "1_US100.cash" in tracker._recorded_position_ids
        ftmo.record_trade_result.assert_called_with("US100.cash", 150.0, historical=False, trade_time=ANY, direction="BUY")
        journal.record.assert_called_once()
        adaptive.record_result.assert_called_once()
        audit.log_decision.assert_called_once()

    def test_check_closed_skips_duplicate(self):
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []
        tracker._recorded_deals[1] = None

        deal = MagicMock()
        deal.position_id = 1
        deal.symbol = "US500.cash"
        deal.magic = cfg.ROBOT_MAGIC
        deal.profit = 50.0
        deal.type = 1
        deal.volume = 0.1
        deal.price = 5010.0
        deal.time = int(time.time())
        mt5.get_history.return_value = [deal]

        tracker.check_closed()
        # Should not record again
        ftmo = tracker.ftmo
        ftmo.record_trade_result.assert_not_called()

    def test_check_closed_skips_duplicate_position_id(self):
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []
        tracker._recorded_position_ids["1_US100.cash"] = None  # ?? 13 Aout 2026 (XAUUSD retiré)

        deal = MagicMock()
        deal.position_id = 1
        deal.symbol = "US100.cash"
        deal.magic = cfg.ROBOT_MAGIC
        deal.profit = 50.0
        deal.type = 1
        deal.volume = 0.1
        deal.price = 2455.0
        deal.time = int(time.time())
        mt5.get_history.return_value = [deal]

        tracker.check_closed()
        assert 1 not in tracker._recorded_deals

    # ── FIX 14 Août 2026: anti-pollution perf monitor ─────────────────

    def test_historical_trade_does_not_feed_perf_monitor(self):
        """🐛 FIX 14 Août 2026: un trade HISTORIQUE (deal MT5 fermé avant le démarrage
        du robot, is_historical=True) ne doit PAS alimenter le performance monitor.
        Avant ce fix, les deals rejoués au restart (dont SELL bannis et ancienne
        config) polluaient recent_trades → rolling windows WR 31-45% au lieu de 83%."""
        tracker, ftmo, journal, adaptive, pos_cache, mt5, audit = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []

        tracker._position_meta[1] = {
            "symbol": "US100.cash",
            "entry": 2450.0,
            "sl": 2440.0,
            "lot": 0.1,
            "regime": "TREND_UP",
            "r1_usd": 100.0,
            "opened_at": time.time() - 3600,
        }

        deal = MagicMock()
        deal.position_id = 1
        deal.symbol = "US100.cash"
        deal.magic = cfg.ROBOT_MAGIC
        deal.profit = 150.0
        deal.type = 1  # SELL (closing a BUY) → direction réelle = BUY
        deal.volume = 0.1
        deal.price = 2455.0
        deal.time = int(time.time()) - 7200  # AVANT _start_time → historique
        mt5.get_history.return_value = [deal]

        with patch("engine_simple.performance_monitor.record_trade") as mock_record_trade:
            tracker.check_closed()

        # Le perf monitor ne doit PAS être appelé pour un trade historique rejoué
        mock_record_trade.assert_not_called()
        # Mais le FTMO record_trade_result et le journal restent fonctionnels (sauvegarde)
        ftmo.record_trade_result.assert_called()
        journal.record.assert_called_once()

    # ── FIX 17 Août 2026: agrégation des closes partielles ─────────────

    def test_find_closing_deal_aggregates_partial_closes(self):
        """🐛 FIX 17 Août 2026 (Log Analyst): un partial TP génère 2-3 deals OUT
        sur le MÊME position_id → _find_closing_deal doit SOMMER les profits et
        volumes au lieu de ne prendre que le premier deal (perdait le PnL réel,
        cf. T4 AUDUSD: 3 closes, 1 seule comptée → stats GR fausses)."""
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []

        # Partial TP: 2 closes sur le même ticket (50% puis 50%)
        deal1 = MagicMock()
        deal1.position_id = 1
        deal1.symbol = "US100.cash"
        deal1.magic = cfg.ROBOT_MAGIC
        deal1.profit = 75.0  # première close (50%)
        deal1.type = 1
        deal1.volume = 0.05
        deal1.price = 2455.0
        deal1.time = int(time.time()) + 3600
        deal1.entry = 1  # DEAL_ENTRY_OUT

        deal2 = MagicMock()
        deal2.position_id = 1
        deal2.symbol = "US100.cash"
        deal2.magic = cfg.ROBOT_MAGIC
        deal2.profit = 85.0  # close finale (50%)
        deal2.type = 1
        deal2.volume = 0.05
        deal2.price = 2460.0
        deal2.time = int(time.time()) + 3700
        deal2.entry = 1  # DEAL_ENTRY_OUT

        # Un swap/rollover (entry=2) doit être EXCLU de l'agrégation
        swap = MagicMock()
        swap.position_id = 1
        swap.symbol = "US100.cash"
        swap.magic = cfg.ROBOT_MAGIC
        swap.profit = 5.0
        swap.type = 1
        swap.volume = 0.0
        swap.price = 2457.0
        swap.time = int(time.time()) + 3600
        swap.entry = 2  # DEAL_ENTRY_INOUT → à exclure

        mt5.get_history.return_value = [deal1, swap, deal2]

        closing = tracker._find_closing_deal(1)

        assert closing is not None
        # Profit TOTAL = somme des 2 closes partielles (75 + 85 = 160), swap exclu
        assert closing.profit == 160.0, f"profit agrégé attendu 160.0, trouvé {closing.profit}"
        # Volume total = somme (0.05 + 0.05 = 0.10)
        assert closing.volume == 0.10, f"volume agrégé attendu 0.10, trouvé {closing.volume}"
        # Attributs du DERNIER deal (close finale)
        assert closing.price == 2460.0
        assert closing.time == deal2.time

    def test_find_closing_deal_ignores_non_out_deals(self):
        """🐛 FIX 17 Août 2026: un deal avec entry=0 (DEAL_ENTRY_IN) ou entry=2
        (INOUT/swap) ne doit PAS compter comme clôture. Seuls les entry=1
        (DEAL_ENTRY_OUT) sont des closes réelles."""
        tracker, _, _, _, pos_cache, mt5, _ = self.make_tracker()
        tracker._previous_tickets = {1}
        pos_cache.get.return_value = []

        # Deal de clôture réel (entry=1)
        real_close = MagicMock()
        real_close.position_id = 1
        real_close.symbol = "US100.cash"
        real_close.magic = cfg.ROBOT_MAGIC
        real_close.profit = 120.0
        real_close.type = 1
        real_close.volume = 0.1
        real_close.price = 2458.0
        real_close.time = int(time.time()) + 3600
        real_close.entry = 1

        # Deal d'ouverture rejoué (entry=0) → ne doit pas être pris
        open_deal = MagicMock()
        open_deal.position_id = 1
        open_deal.symbol = "US100.cash"
        open_deal.magic = cfg.ROBOT_MAGIC
        open_deal.profit = 0.0  # déjà exclu par profit != 0
        open_deal.entry = 0
        open_deal.type = 0
        open_deal.volume = 0.1

        mt5.get_history.return_value = [real_close, open_deal]

        closing = tracker._find_closing_deal(1)
        assert closing is not None
        # Seul le profit du real_close est compté (pas l'open ni aucun swap)
        assert closing.profit == 120.0
        assert closing.volume == 0.10
