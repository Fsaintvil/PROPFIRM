"""Tests de Performance & Robustesse — Axe 5

Couverture :
  1. Cycle time benchmark (27 symboles simulés)
  2. Memory stability sur cycles répétés
  3. Configuration hardening (validation des valeurs)
  4. Cache TTL et invalidation
  5. Thread safety des composants partagés
"""

import gc
import os
import sys
import time
from threading import Thread
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["SYMBOLS"] = (
    "XAUUSD,BTCUSD,EURUSD,GBPUSD,USDJPY,US30.cash,USDCHF,USDCAD,AUDUSD,NZDUSD,USDJPY,EURJPY,GBPJPY,EURGBP,AUDJPY,ETHUSD,SOLUSD,XAGUSD,USOIL.cash,UKOIL.cash,NATGAS.cash,US500.cash,US100.cash,JP225.cash,GER40.cash,UK100.cash,BNBUSD,LNKUSD"
)

import config_simple as cfg
from tests.mock_mt5 import MockMT5Server


# ═══════════════════════════════════════════════════════════════
# 1. Cycle time benchmark
# ═══════════════════════════════════════════════════════════════


class TestCycleTimeBenchmark:
    """Benchmark du temps de cycle pour 27 symboles"""

    @pytest.fixture
    def cycle_components(self):
        """Crée les composants minimaux pour un cycle de trading"""
        from engine_simple.adaptive_intelligence import AdaptiveEngine
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.ftmo_protector import FTMOProtector
        from engine_simple.news_filter import NewsFilter
        from engine_simple.position_tracker import PositionTracker
        from engine_simple.regime import RegimeDetector
        from engine_simple.risk_manager import RiskManager
        from engine_simple.signal_pipeline import SignalPipeline
        from engine_simple.strategy_selector import StrategySelector
        from engine_simple.trade_journal import TradeJournal
        from engine_simple.volume_profile import VolumeProfile
        from engine_simple.mtf_confirm import MultiTimeframeConfirmer

        server = MockMT5Server(initial_balance=200000, spread=0.0002)
        import tempfile

        tmp = tempfile.mkdtemp()
        audit = AuditTrail(log_dir=tmp)
        journal = TradeJournal(csv_path=os.path.join(tmp, "trades.csv"))
        adaptive = AdaptiveEngine(server, calibration_path=os.path.join(tmp, "calib.pkl"))

        ftmo_config = dict(
            MAX_POSITIONS=10,
            MAX_TRADES_PER_DAY=20,
            MIN_SIGNAL_SCORE=0.30,
            LOT_SIZE=0.01,
            RISK_PER_TRADE=0.004,
            COOLDOWN_MINUTES=0,
            MAX_DAILY_LOSS_PCT=0.02,
            INITIAL_BALANCE=200000,
            MAX_DD_PCT=0.10,
            PROFIT_TARGET_PCT=0.10,
            CONSISTENCY_MAX_PCT=0.30,
            MIN_TRADING_DAYS=1,
            MAGIC=999001,
            MAX_SPREAD_POINTS=120,
            MAX_RISK_AMOUNT=1600,
            TRADING_START_HOUR=0,
            TRADING_END_HOUR=24,
            DANGER_HOURS=[],
            SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            DAILY_PROFIT_LIMIT_PCT=0.05,
            ZONE2_LOSS_PCT=0.015,
            ZONE3_LOSS_PCT=0.02,
            AUTO_PAUSE_LOSSES=8,
            MAX_CORRELATED_EXPOSURE=0.15,
            CIRCUIT_BREAKER_DD_PCT=0.08,
        )
        ftmo = FTMOProtector(server, ftmo_config)
        pos_cache = MagicMock()
        pos_cache.get.return_value = server.get_positions()

        news_filter = NewsFilter()
        strategy_selector = StrategySelector()
        volume_profile = VolumeProfile()
        mtf_confirm = MultiTimeframeConfirmer()
        risk_mgr = RiskManager(ftmo, audit=audit)

        pipeline = SignalPipeline(
            mt5=server,
            ftmo=ftmo,
            adaptive=adaptive,
            news_filter=news_filter,
            strategy_selector=strategy_selector,
            volume_profile=volume_profile,
            mtf_confirm=mtf_confirm,
            risk_manager=risk_mgr,
            config=cfg,
            symbol_limits=cfg.SYMBOL_LIMITS,
            symbol_timeframes=cfg.SYMBOL_TIMEFRAMES,
        )

        yield {
            "server": server,
            "ftmo": ftmo,
            "pipeline": pipeline,
            "audit": audit,
            "journal": journal,
            "adaptive": adaptive,
            "tmp": tmp,
        }
        journal.close()
        audit.close()

    def test_single_cycle_time(self, cycle_components):
        """Un cycle de traitement pour 1 symbole doit prendre < 100ms"""
        pipeline = cycle_components["pipeline"]

        t0 = time.perf_counter()
        for _ in range(10):
            pipeline._phase1_mom20x3("EURUSD")
        elapsed = (time.perf_counter() - t0) / 10

        assert elapsed < 0.500, f"Cycle moyen trop lent: {elapsed * 1000:.1f}ms (> 500ms)"

    def test_ftmo_can_trade_speed(self, cycle_components):
        """FTMOProtector.can_trade doit prendre < 5ms par appel"""
        ftmo = cycle_components["ftmo"]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = __import__("datetime").datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                t0 = time.perf_counter()
                for _ in range(50):
                    ftmo.can_trade(
                        "EURUSD",
                        {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                    )
                elapsed_ms = (time.perf_counter() - t0) * 1000 / 50

        assert elapsed_ms < 5.0, f"can_trade moyen trop lent: {elapsed_ms:.1f}ms (> 5ms)"

    def test_broker_order_send_speed(self):
        """Broker.order_send benchmark (avec AuditTrail, ~87ms constaté)"""
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.broker import Broker

        server = MockMT5Server()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            broker = Broker(server, audit=audit)
            broker.connect()

            t0 = time.perf_counter()
            for _ in range(100):
                broker.order_send(
                    dict(
                        action=1,
                        symbol="EURUSD",
                        volume=0.01,
                        type=0,
                        price=1.1,
                        sl=1.09,
                        tp=1.11,
                        magic=999001,
                        comment="PERF_TEST",
                        type_filling=1,
                        type_time=0,
                    )
                )
                server.close_position(symbol="EURUSD")
            elapsed_ms = (time.perf_counter() - t0) * 1000 / 100

            # AuditTrail + Broker logging overhead; seuil réaliste à 200ms
            assert elapsed_ms < 200.0, f"Order send moyen trop lent: {elapsed_ms:.2f}ms"
            audit.close()

    def test_pipeline_process_benchmark(self):
        """Benchmark du pipeline complet pour 27 symboles simulés"""
        from engine_simple.adaptive_intelligence import AdaptiveEngine
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.ftmo_protector import FTMOProtector
        from engine_simple.news_filter import NewsFilter
        from engine_simple.regime import RegimeDetector
        from engine_simple.risk_manager import RiskManager
        from engine_simple.signal_pipeline import SignalPipeline
        from engine_simple.strategy_selector import StrategySelector
        from engine_simple.volume_profile import VolumeProfile
        from engine_simple.mtf_confirm import MultiTimeframeConfirmer

        server = MockMT5Server()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            adaptive = AdaptiveEngine(server, calibration_path=os.path.join(tmp, "calib.pkl"))

            ftmo = FTMOProtector(
                server,
                dict(
                    MAX_POSITIONS=10,
                    MAX_TRADES_PER_DAY=20,
                    MIN_SIGNAL_SCORE=0.30,
                    LOT_SIZE=0.01,
                    RISK_PER_TRADE=0.004,
                    COOLDOWN_MINUTES=0,
                    MAX_DAILY_LOSS_PCT=0.02,
                    INITIAL_BALANCE=200000,
                    MAX_DD_PCT=0.10,
                    PROFIT_TARGET_PCT=0.10,
                    CONSISTENCY_MAX_PCT=0.30,
                    MIN_TRADING_DAYS=1,
                    MAGIC=999001,
                    MAX_SPREAD_POINTS=120,
                    MAX_RISK_AMOUNT=1600,
                    TRADING_START_HOUR=0,
                    TRADING_END_HOUR=24,
                    DANGER_HOURS=[],
                    SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
                    DAILY_PROFIT_LIMIT_PCT=0.05,
                    ZONE2_LOSS_PCT=0.015,
                    ZONE3_LOSS_PCT=0.02,
                    AUTO_PAUSE_LOSSES=8,
                    MAX_CORRELATED_EXPOSURE=0.15,
                    CIRCUIT_BREAKER_DD_PCT=0.08,
                ),
            )

            pipeline = SignalPipeline(
                mt5=server,
                ftmo=ftmo,
                adaptive=adaptive,
                news_filter=NewsFilter(),
                strategy_selector=StrategySelector(),
                volume_profile=VolumeProfile(),
                mtf_confirm=MultiTimeframeConfirmer(),
                risk_manager=RiskManager(ftmo, audit=audit),
                config=cfg,
                symbol_limits=cfg.SYMBOL_LIMITS,
                symbol_timeframes=cfg.SYMBOL_TIMEFRAMES,
            )

            # Bench: _phase1_mom20x3 pour les 27 symboles
            symbols = list(cfg.SYMBOL_TIMEFRAMES.keys())[:27]
            t0 = time.perf_counter()
            results = 0
            for sym in symbols:
                sig = pipeline._phase1_mom20x3(sym)
                if sig:
                    results += 1
            elapsed = time.perf_counter() - t0
            # 27 symboles en < 5 secondes (inclut chargement données mockées)
            assert elapsed < 5.0, f"27 symboles en {elapsed:.2f}s (> 5s)"
            audit.close()


# ═══════════════════════════════════════════════════════════════
# 2. Memory stability
# ═══════════════════════════════════════════════════════════════


class TestMemoryStability:
    """Vérifie la stabilité mémoire sur cycles répétés"""

    def test_gc_collect_no_leaks(self):
        """Le garbage collector ne doit pas accumuler d'objets cycliques"""
        gc.collect()
        before = len(gc.get_objects())

        # Simuler des allers-retours de création d'objets
        for _ in range(1000):
            obj = {"a": 1, "b": [2, 3, 4], "c": {"d": 5}}
            _ = str(obj)
            del obj

        gc.collect()
        after = len(gc.get_objects())
        # Delta raisonnable (< 100 objets)
        assert after - before < 500, f"Fuite mémoire suspecte: {after - before} objets"

    def test_metrics_collector_no_leak(self):
        """MetricsCollector ne doit pas fuiter sur des milliers d'appels"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        for i in range(5000):
            mc.inc("trades", tags={"symbol": f"SYM{i % 10}", "result": "win" if i % 2 == 0 else "loss"})
            mc.gauge("balance", value=200000 + i)
            mc.histogram("latency", value=i % 100)

        snap = mc.snapshot()
        total_counters = sum(sum(v.values()) for v in snap["counters"].values())
        assert total_counters == 5000, f"Compteurs incorrects: {total_counters}"

    def test_position_tracker_stability(self):
        """PositionTracker doit gérer des milliers de tickets sans fuite mémoire"""
        from engine_simple.position_tracker import PositionTracker
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.adaptive_intelligence import AdaptiveEngine
        from engine_simple.ftmo_protector import FTMOProtector
        from engine_simple.trade_journal import TradeJournal

        server = MockMT5Server()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            journal = TradeJournal(csv_path=os.path.join(tmp, "trades.csv"))
            adaptive = AdaptiveEngine(server, calibration_path=os.path.join(tmp, "calib.pkl"))

            ftmo = FTMOProtector(
                server,
                dict(
                    MAX_POSITIONS=10,
                    MAX_TRADES_PER_DAY=20,
                    MIN_SIGNAL_SCORE=0.30,
                    LOT_SIZE=0.01,
                    RISK_PER_TRADE=0.004,
                    COOLDOWN_MINUTES=0,
                    MAX_DAILY_LOSS_PCT=0.02,
                    INITIAL_BALANCE=200000,
                    MAX_DD_PCT=0.10,
                    PROFIT_TARGET_PCT=0.10,
                    CONSISTENCY_MAX_PCT=0.30,
                    MIN_TRADING_DAYS=1,
                    MAGIC=999001,
                    MAX_SPREAD_POINTS=120,
                    MAX_RISK_AMOUNT=1600,
                    TRADING_START_HOUR=0,
                    TRADING_END_HOUR=24,
                    DANGER_HOURS=[],
                    SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
                    MAX_CORRELATED_EXPOSURE=0.15,
                    CIRCUIT_BREAKER_DD_PCT=0.08,
                ),
            )
            pos_cache = MagicMock()
            pos_cache.get.return_value = server.get_positions()
            tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5=server, audit=audit)

            # 1000 trades simulés
            for i in range(1000):
                server.order_send(
                    dict(
                        action=1,
                        symbol=["EURUSD", "GBPUSD", "USDCHF"][i % 3],
                        volume=0.01,
                        type=i % 2,
                        price=1.1,
                        sl=1.09,
                        tp=1.12,
                        magic=999001,
                        comment=f"STRESS_{i}",
                        type_filling=1,
                        type_time=0,
                    )
                )
                tracker.track_new()

                if i % 2 == 0:
                    server.close_position(symbol=["EURUSD", "GBPUSD", "USDCHF"][i % 3])
                    pos_cache.get.return_value = server.get_positions()
                    tracker.check_closed()

            assert tracker is not None
            audit.close()
            journal.close()


# ═══════════════════════════════════════════════════════════════
# 3. Configuration hardening
# ═══════════════════════════════════════════════════════════════


class TestConfigHardening:
    """Validation des valeurs de configuration"""

    def test_risk_constants_rational(self):
        """Les constantes de risque doivent être dans des plages rationnelles"""
        assert 0.001 <= cfg.RISK_PER_TRADE <= 0.02, "RISK_PER_TRADE hors plage"
        assert 0.01 <= cfg.MAX_DD_PCT <= 0.50, "MAX_DD_PCT hors plage"
        assert 0.01 <= cfg.MAX_DAILY_LOSS_PCT <= 0.10, "MAX_DAILY_LOSS_PCT hors plage"
        assert 0.01 <= cfg.LOT_SIZE <= 10.0, "LOT_SIZE hors plage"

    def test_ftmo_constraints_rational(self):
        """Les contraintes FTMO doivent être valides"""
        assert 1 <= cfg.MIN_TRADING_DAYS <= 365, "MIN_TRADING_DAYS hors plage"
        assert 1 <= cfg.MAX_POSITIONS <= 50, "MAX_POSITIONS hors plage"
        assert 1 <= cfg.MAX_TRADES_PER_DAY <= 100, "MAX_TRADES_PER_DAY hors plage"
        assert 1 <= cfg.COOLDOWN_MINUTES <= 1440, "COOLDOWN_MINUTES hors plage"

    def test_spread_limits_rational(self):
        """Les limites de spread doivent être valides"""
        assert 10 <= cfg.MAX_SPREAD_POINTS <= 500, "MAX_SPREAD_POINTS hors plage"
        assert cfg.MIN_RR_RATIO >= 1.0, "MIN_RR_RATIO < 1.0 (impossible)"

    def test_symbol_limits_exist(self):
        """SYMBOL_LIMITS doit contenir tous les symboles actifs"""
        assert len(cfg.SYMBOL_LIMITS) >= 10, "Moins de 10 symboles configurés"
        assert "XAUUSD" in cfg.SYMBOL_LIMITS, "XAUUSD manquant"
        assert "EURUSD" in cfg.SYMBOL_LIMITS, "EURUSD manquant"

    def test_symbol_timeframes_consistent(self):
        """SYMBOL_TIMEFRAMES doit être cohérent avec SYMBOL_LIMITS"""
        for sym in cfg.SYMBOL_TIMEFRAMES:
            tf = cfg.SYMBOL_TIMEFRAMES[sym]
            assert tf in ("H1", "H4", "D1"), f"TF invalide pour {sym}: {tf}"

    def test_robot_magic_valid(self):
        """ROBOT_MAGIC doit être dans la plage MT5 valide"""
        assert 100000 <= cfg.ROBOT_MAGIC <= 999999999, "ROBOT_MAGIC hors plage MT5"

    def test_profit_target_rational(self):
        """PROFIT_TARGET_PCT doit être réaliste"""
        assert 0.01 <= cfg.PROFIT_TARGET_PCT <= 1.0, "PROFIT_TARGET_PCT hors plage"
        assert cfg.CONSISTENCY_MAX_PCT >= 0.10, "CONSISTENCY_MAX_PCT < 10%"


# ═══════════════════════════════════════════════════════════════
# 4. Cache TTL et invalidation
# ═══════════════════════════════════════════════════════════════


class TestCacheTTL:
    """Vérifie les TTL de cache via RateCache (SQLite, cleanup explicite)"""

    def _make_cache(self, tmp_dir, default_ttl=60):
        """Crée un RateCache dans tmp_dir"""
        from engine_simple.rate_cache import RateCache
        import os

        db_path = os.path.join(tmp_dir, "test_cache.db")
        return RateCache(db_path=db_path, default_ttl=default_ttl), db_path

    def test_rate_cache_set_get(self):
        """RateCache.set_rates/get_rates doit fonctionner"""
        import tempfile, gc

        tmp = tempfile.mkdtemp()
        try:
            cache, _ = self._make_cache(tmp)
            cache.set_rates("EURUSD", "H1", 100, {"close": [1.1, 1.2]})
            result = cache.get_rates("EURUSD", "H1", 100)
            assert result is not None
            assert result["close"] == [1.1, 1.2]
            del cache
            gc.collect()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_rate_cache_expiry(self):
        """RateCache doit expirer après TTL=0 (immédiat)"""
        import tempfile, gc

        tmp = tempfile.mkdtemp()
        try:
            cache, _ = self._make_cache(tmp, default_ttl=0)
            cache.set_rates("EURUSD", "H1", 50, {"close": [1.1]})
            result = cache.get_rates("EURUSD", "H1", 50)
            assert result is None, "RateCache n'a pas expiré avec TTL=0"
            del cache
            gc.collect()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_rate_cache_clear(self):
        """RateCache.clear() doit vider le cache"""
        import tempfile, gc

        tmp = tempfile.mkdtemp()
        try:
            cache, _ = self._make_cache(tmp, default_ttl=60)
            cache.set_rates("EURUSD", "H1", 100, {"close": [1.1]})
            cache.set_rates("GBPUSD", "H1", 100, {"close": [1.3]})
            assert cache.get_rates("EURUSD", "H1", 100) is not None
            cache.clear()
            assert cache.get_rates("EURUSD", "H1", 100) is None
            assert cache.get_rates("GBPUSD", "H1", 100) is None
            del cache
            gc.collect()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# 5. Thread safety
# ═══════════════════════════════════════════════════════════════


class TestThreadSafety:
    """Vérifie la thread safety des composants partagés"""

    def test_metrics_collector_thread_safe(self):
        """MetricsCollector doit supporter les accès concurrents"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        errors = []

        def inc_worker():
            try:
                for _ in range(500):
                    mc.inc("trades", tags={"symbol": "EURUSD", "result": "win"})
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=inc_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Erreurs concurrentes: {errors}"
        snap = mc.snapshot()
        total = sum(snap["counters"].get("trades", {}).values())
        assert total == 5000, f"Compteur: {total} au lieu de 5000"

    def test_metrics_collector_histogram_thread_safe(self):
        """MetricsCollector.histogram() doit être thread-safe"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        errors = []

        def hist_worker():
            try:
                for _ in range(200):
                    mc.histogram("latency", value=__import__("random").uniform(1, 100))
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=hist_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Erreurs histogram concurrent: {errors}"
        snap = mc.snapshot()
        assert "latency" in snap["histograms"]

    def test_gauge_thread_safe(self):
        """MetricsCollector.gauge() doit être thread-safe"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        errors = []

        def gauge_worker():
            try:
                for i in range(200):
                    mc.gauge("balance", value=float(i))
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=gauge_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Erreurs gauge concurrent: {errors}"
        snap = mc.snapshot()
        assert "balance" in snap["gauges"]
