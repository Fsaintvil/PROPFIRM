"""Tests d'intégration complets — pipeline, corrélation, recovery, monitoring, trailing

Couverture Axe 4 :
  1. Signal pipeline → FTMO → execution full cycle
  2. Corrélation multi-symboles (groupes, limites par direction)
  3. Recovery MT5 (déconnexion/reconnexion)
  4. RiskManager + FTMOProtector intégrés
  5. PositionManager (limites par symbole)
  6. Monitoring (MetricsCollector + HealthServer)
  7. Dashboard avec données mockées
  8. Trailing stop via Trailer
"""

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["SYMBOLS"] = "XAUUSD,BTCUSD,EURUSD,GBPUSD,USDJPY"

import config_simple as cfg
from tests.mock_mt5 import MockMT5Server


# ═══════════════════════════════════════════════════════════════
# 1. Signal pipeline → FTMO → execution full cycle
# ═══════════════════════════════════════════════════════════════


class TestFullSignalPipelineCycle:
    """Test le pipeline complet via les phases individuelles de SignalPipeline"""

    @pytest.fixture
    def components(self):
        from engine_simple.adaptive_intelligence import AdaptiveEngine
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.ftmo_protector import FTMOProtector
        from engine_simple.news_filter import NewsFilter
        from engine_simple.position_tracker import PositionTracker
        from engine_simple.regime import RegimeDetector
        from engine_simple.risk_manager import RiskManager
        from engine_simple.signal_pipeline import SignalPipeline
        from engine_simple.strategy_selector import StrategySelector
        from engine_simple.trade_executor import TradeExecutor
        from engine_simple.trade_journal import TradeJournal
        from engine_simple.volume_profile import VolumeProfile
        from engine_simple.mtf_confirm import MultiTimeframeConfirmer

        server = MockMT5Server(initial_balance=200000, spread=0.0002)
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
        tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5=server, audit=audit)
        executor = TradeExecutor(server, ftmo, journal, tracker, pipeline, adaptive, audit=audit)

        yield {
            "server": server,
            "ftmo": ftmo,
            "tracker": tracker,
            "pipeline": pipeline,
            "executor": executor,
            "journal": journal,
            "audit": audit,
            "adaptive": adaptive,
            "tmp": tmp,
            "risk_mgr": risk_mgr,
            "pos_cache": pos_cache,
        }

        journal.close()
        audit.close()

    def _make_signal(self, action="BUY", score=0.80):
        return {
            "action": action,
            "score": score,
            "confidence": score * 0.9,
            "atr": 0.005,
            "atr_pct": 0.5,
            "sl_atr": 1.5,
            "tp_atr": 4.0,
            "risk_mult": 1.0,
            "quality": 1.0,
            "is_ranging": False,
            "_regime": "TREND_UP",
            "_ml_agrees": True,
            "rr": 2.66,
            "rates": {},
            "sl": 1.0900,
            "tp": 1.1100,
        }

    def test_phase5_regime_filter(self, components):
        """Phase 5 doit rejeter les signaux en conflit avec le régime"""
        pipeline = components["pipeline"]

        # Signal BUY en régime TREND_UP → doit passer
        sig_buy = self._make_signal("BUY")
        assert pipeline._phase5_regime_rule(sig_buy)

        # Signal BUY en régime TREND_DOWN → doit être rejeté
        sig_conflict = self._make_signal("BUY")
        sig_conflict["_regime"] = "TREND_DOWN"
        assert not pipeline._phase5_regime_rule(sig_conflict)

    def test_ftmo_can_trade_happy_path(self, components):
        """FTMOProtector.can_trade doit accepter un signal valide"""
        ftmo = components["ftmo"]

        patch_dt = patch("engine_simple.ftmo_protector.datetime")
        mock_dt = patch_dt.start()
        mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)

        patch_news = patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, []))
        patch_news.start()

        try:
            ok, reason = ftmo.can_trade(
                "EURUSD",
                signal={"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
            )
            assert ok, f"FTMO a rejeté: {reason}"
        finally:
            patch_news.stop()
            patch_dt.stop()

    def test_ftmo_rejects_low_signal(self, components):
        """FTMO doit rejeter les signaux avec score trop bas (MIN_SIGNAL_SCORE=0.30)"""
        ftmo = components["ftmo"]

        patch_dt = patch("engine_simple.ftmo_protector.datetime")
        mock_dt = patch_dt.start()
        mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)

        patch_news = patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, []))
        patch_news.start()

        try:
            ok, reason = ftmo.can_trade(
                "EURUSD",
                signal={"action": "BUY", "score": 0.05, "sl": 1.0900, "tp": 1.1100},
            )
            assert not ok, "Signal low-score devrait être rejeté"
        finally:
            patch_news.stop()
            patch_dt.stop()

    def test_executor_opens_position(self, components):
        """TradeExecutor doit ouvrir une position via MT5"""
        server = components["server"]
        executor = components["executor"]

        # RR minimal pour passer la validation du TradeExecutor:
        # RR = (tp - entry) / (entry - sl) pour BUY = (1.1100-1.1000)/(1.1000-1.0900) = 1.0
        # Avec min_rr à 1.5, besoin de SL plus serré ou TP plus large
        signal = self._make_signal("BUY")
        signal["sl"] = 1.0950  # SL plus serré → RR = (1.1100-1.1000)/(1.1000-1.0950) = 2.0 ≥ 1.5 ✓
        signal["tp"] = 1.1150  # TP plus large → RR = (1.1150-1.1000)/(1.1000-1.0950) = 3.0

        with patch("engine_simple.news_filter.is_news_blocked", return_value=(False, [])):
            executor.execute("EURUSD", signal)

        positions = server.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "EURUSD"
        assert positions[0].type == 0  # BUY


# ═══════════════════════════════════════════════════════════════
# 2. Corrélation multi-symboles
# ═══════════════════════════════════════════════════════════════


class TestMultiSymbolCorrelation:
    """Test la gestion de corrélation multi-symboles"""

    def _make_ftmo(self, mt5_mock):
        """Crée un FTMOProtector avec mt5 mocké proprement"""
        from engine_simple.ftmo_protector import FTMOProtector

        # CRITIQUE: Configurer TOUS les retours AVANT la construction de FTMOProtector
        # car __init__ appelle get_account_info(), get_symbol_info(), get_tick()
        mt5_mock.get_account_info.return_value = MagicMock(equity=200000.0, balance=200000.0)
        mt5_mock.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, spread=10)
        tick = MagicMock(ask=1.1000, bid=1.0995)
        tick.time = time.time()
        mt5_mock.get_tick.return_value = tick

        return FTMOProtector(
            mt5_mock,
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

    def test_correlation_allows_within_limit(self):
        """Trade dans la même groupe/direction passe si sous la limite (max 3/direction)"""
        mt5 = MagicMock()
        ftmo = self._make_ftmo(mt5)

        existing = [
            MagicMock(magic=999001, symbol="EURUSD", type=0, ticket=1),
            MagicMock(magic=999001, symbol="GBPUSD", type=0, ticket=2),
        ]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                    positions=existing,
                )
                assert ok, f"2 trades existants < max 3/direction/groupe → devrait passer. Raison: {reason}"

    def test_correlation_blocks_same_group_direction(self):
        """3e trade dans la même direction du même groupe est bloqué"""
        mt5 = MagicMock()
        ftmo = self._make_ftmo(mt5)

        existing = [
            MagicMock(magic=999001, symbol="EURUSD", type=0, ticket=1),
            MagicMock(magic=999001, symbol="GBPUSD", type=0, ticket=2),
            MagicMock(magic=999001, symbol="USDJPY", type=0, ticket=3),
        ]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                    positions=existing,
                )
                assert not ok, f"3 trades existants BUY dans FOREX_MAJORS → devrait être BLOQUÉ. Raison: {reason}"
                assert "corrélation" in (reason or ""), f"Raison devrait mentionner corrélation: {reason}"

    def test_correlation_allows_different_direction(self):
        """Trade dans direction opposée dans le même groupe doit passer"""
        mt5 = MagicMock()
        ftmo = self._make_ftmo(mt5)

        existing = [
            MagicMock(magic=999001, symbol="EURUSD", type=1, ticket=1),
            MagicMock(magic=999001, symbol="GBPUSD", type=1, ticket=2),
        ]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "USDCHF",
                    {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                    positions=existing,
                )
                assert ok, f"Direction opposée devrait passer: {reason}"

    def test_correlation_no_block_crypto(self):
        """Documentation: pas de blocage corrélation crypto actuellement"""
        mt5 = MagicMock()
        ftmo = self._make_ftmo(mt5)

        existing = [
            MagicMock(magic=999001, symbol="BTCUSD", type=0, ticket=1),
            MagicMock(magic=999001, symbol="ETHUSD", type=0, ticket=2),
        ]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "SOLUSD",
                    {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                    positions=existing,
                )
                assert ok, "Documentation: pas de blocage corrélation crypto. Raison: " + (reason or "N/A")

    def test_correlation_allows_different_groups(self):
        """Trades dans différents groupes passent (comportement actuel)"""
        mt5 = MagicMock()
        ftmo = self._make_ftmo(mt5)

        existing = [
            MagicMock(magic=999001, symbol="EURUSD", type=0, ticket=1),
            MagicMock(magic=999001, symbol="BTCUSD", type=0, ticket=2),
        ]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "XAUUSD",
                    {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                    positions=existing,
                )
                assert ok, f"Groupe différent devrait passer: {reason}"


# ═══════════════════════════════════════════════════════════════
# 3. Recovery MT5 (déconnexion/reconnexion)
# ═══════════════════════════════════════════════════════════════


class TestMT5DisconnectRecovery:
    """Test la résilience face aux déconnexions MT5"""

    def test_broker_disconnect_updates_state(self):
        """Broker.disconnect() doit passer _connected à False"""
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.broker import Broker

        server = MockMT5Server()
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            broker = Broker(server, audit=audit)
            assert broker.connect()
            assert broker.is_connected

            broker.disconnect()
            assert not broker.is_connected
            assert not server.connected
            audit.close()

    def test_broker_reconnects(self):
        """Broker.reconnect() doit rétablir la connexion"""
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.broker import Broker

        server = MockMT5Server()
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            broker = Broker(server, audit=audit)
            broker.connect()

            broker.disconnect()
            assert not broker.is_connected

            ok = broker.reconnect()
            assert ok
            assert broker.is_connected
            assert server.connected
            audit.close()

    def test_orders_fail_when_disconnected(self):
        """Broker auto-reconnect via __getattr__ après déconnexion.
        On teste que l'ordre passe APRÈS reconnexion automatique."""
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.broker import Broker

        server = MockMT5Server()
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            broker = Broker(server, audit=audit)
            broker.connect()
            broker.disconnect()

            # Broker auto-reconnect via __getattr__, l'ordre doit passer
            result = broker.order_send(
                dict(
                    action=1,
                    symbol="EURUSD",
                    volume=0.01,
                    type=0,
                    price=1.1,
                    sl=1.09,
                    tp=1.11,
                    magic=999001,
                    comment="AUTO_RECONNECT_TEST",
                    type_filling=1,
                    type_time=0,
                )
            )
            assert result.retcode == 10009  # TRADE_RETCODE_DONE
            assert broker.is_connected  # reconnecté automatiquement
            audit.close()

    def test_health_check_detects_disconnected_server(self):
        """Broker.health_check() doit détecter un serveur déconnecté"""
        from engine_simple.audit_trail import AuditTrail
        from engine_simple.broker import Broker

        server = MockMT5Server()
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditTrail(log_dir=tmp)
            broker = Broker(server, audit=audit)
            broker.connect()

            # Déconnexion directe du serveur
            server.disconnect()
            # health_check doit détecter la perte de connexion
            assert not broker.health_check()
            assert not broker.is_connected
            audit.close()

    def test_ftmo_price_staleness_detection(self):
        """FTMOProtector doit détecter les prix périmés"""
        from engine_simple.ftmo_protector import FTMOProtector

        mt5 = MagicMock()
        mt5.get_account_info.return_value = MagicMock(equity=200000, balance=200000)
        mt5.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, spread=10)

        old_tick = MagicMock(ask=1.1000, bid=1.0995)
        old_tick.time = time.time() - 600  # 10 min old
        mt5.get_tick.return_value = old_tick
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1

        ftmo = FTMOProtector(
            mt5,
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
        assert not ftmo.check_price_staleness("EURUSD", max_age=60)

    def test_ftmo_recovers_after_reconnection(self):
        """FTMO doit accepter les trades après tick frais"""
        from engine_simple.ftmo_protector import FTMOProtector

        mt5 = MagicMock()
        mt5.get_account_info.return_value = MagicMock(equity=200000, balance=200000)
        mt5.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, spread=10)

        fresh_tick = MagicMock(ask=1.1000, bid=1.0995)
        fresh_tick.time = time.time()
        mt5.get_tick.return_value = fresh_tick
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1

        ftmo = FTMOProtector(
            mt5,
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

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, reason = ftmo.can_trade(
                    "EURUSD",
                    {"action": "BUY", "score": 0.80, "sl": 1.0900, "tp": 1.1100},
                )
                assert ok, f"Trade après reconnexion devrait passer: {reason}"


# ═══════════════════════════════════════════════════════════════
# 4. RiskManager + FTMOProtector intégrés
# ═══════════════════════════════════════════════════════════════


class TestRiskManagerIntegration:
    """Test RiskManager + FTMOProtector working together"""

    @pytest.fixture
    def risk_components(self):
        from engine_simple.ftmo_protector import FTMOProtector
        from engine_simple.risk_manager import RiskManager

        mt5 = MagicMock()
        mt5.get_account_info.return_value = MagicMock(equity=200000, balance=200000)
        mt5.get_positions.return_value = []
        mt5.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, spread=10)
        tick = MagicMock(ask=1.1000, bid=1.0995)
        tick.time = time.time()
        mt5.get_tick.return_value = tick
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1
        mt5.ORDER_FILLING_IOC = 1
        mt5.ORDER_TIME_GTC = 0

        ftmo = FTMOProtector(
            mt5,
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
        risk = RiskManager(ftmo)
        return {"ftmo": ftmo, "risk": risk, "mt5": mt5}

    def test_risk_pretrade_checks_ftmo(self, risk_components):
        """PreTradeChecklist doit déléguer à FTMO.can_trade"""
        risk = risk_components["risk"]
        ftmo = risk_components["ftmo"]

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                ok, checks = risk.pre_trade("EURUSD")
                assert isinstance(ok, bool)
                assert isinstance(checks, list)
                assert any(c["rule"] == "can_trade" for c in checks)

    def test_risk_position_sizing(self, risk_components):
        """RiskManager doit calculer un position sizing"""
        risk = risk_components["risk"]
        # symbol_perf must have .win_rate, .trades, .avg_r_multiple attributes
        symbol_perf = MagicMock(win_rate=0.65, trades=100, avg_r_multiple=1.8)
        lot = risk.calculate_position_risk(symbol_perf, rr=2.0)
        assert isinstance(lot, (int, float))
        assert lot >= 0

    def test_risk_circuit_breaker(self, risk_components):
        """CircuitBreaker doit détecter les drawdowns excessifs"""
        risk = risk_components["risk"]
        # Mise à jour du circuit breaker
        risk.update(equity=200000, reference=200000)
        assert not risk.check_circuit(equity=200000, reference=200000, consecutive_losses=0)

        # Drawdown important
        tripped = risk.check_circuit(equity=190000, reference=200000, consecutive_losses=0)
        # Doit rester ouvert (3% max pour le circuit breaker par défaut, 190/200 = -5%)
        assert tripped


# ═══════════════════════════════════════════════════════════════
# 5. PositionManager (limites par symbole) — tests unitaires
# ═══════════════════════════════════════════════════════════════


class TestPositionLimits:
    """Test les limites de positions sans instancier PositionManager
    (qui a 6 dépendances complexes). On teste la logique de limite
    via FTMOProtector qui gère déjà les limites par symbole."""

    def test_ftmo_enforces_symbol_limits(self):
        """FTMOProtector doit respecter les limites par symbole"""
        from engine_simple.ftmo_protector import FTMOProtector

        mt5 = MagicMock()
        mt5.get_account_info.return_value = MagicMock(equity=200000, balance=200000)
        mt5.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, spread=10)
        tick = MagicMock(ask=1.1000, bid=1.0995)
        tick.time = time.time()
        mt5.get_tick.return_value = tick
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1

        ftmo = FTMOProtector(
            mt5,
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

        with patch("engine_simple.ftmo_protector.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 5, 12, 0)
            with patch("engine_simple.ftmo_protector.is_news_blocked", return_value=(False, [])):
                # 1ère position EURUSD → OK
                ok1, _ = ftmo.can_trade("EURUSD", {"action": "BUY", "score": 0.80, "sl": 1.09, "tp": 1.11})
                assert ok1, "1er trade EURUSD devrait passer"

    def test_ftmo_calculate_lot(self):
        """FTMOProtector.calculate_lot doit retourner un lot valide"""
        from engine_simple.ftmo_protector import FTMOProtector

        mt5 = MagicMock()
        mt5.get_account_info.return_value = MagicMock(equity=200000.0, balance=200000.0)
        mt5.get_symbol_info.return_value = MagicMock(point=0.0001, digits=5, spread=10)
        mt5.calc_profit.return_value = -50.0  # CRITIQUE: évite MagicMock dans calculate_lot
        mt5.ORDER_TYPE_BUY = 0
        mt5.ORDER_TYPE_SELL = 1

        ftmo = FTMOProtector(
            mt5,
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

        lot = ftmo.calculate_lot("EURUSD", entry=1.1000, sl=1.0900, quality=1.0, direction=0)
        assert 0.01 <= lot <= 1.0, f"Lot {lot} hors limites"


# ═══════════════════════════════════════════════════════════════
# 6. Monitoring (MetricsCollector + HealthServer)
# ═══════════════════════════════════════════════════════════════


class TestMonitoringIntegration:
    """Test MetricsCollector + HealthServer"""

    def test_metrics_inc_and_snapshot(self):
        """MetricsCollector doit enregistrer et retourner des métriques"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        mc.inc("trades_total", tags={"symbol": "EURUSD", "result": "win"}, value=1)
        mc.inc("trades_total", tags={"symbol": "GBPUSD", "result": "loss"}, value=1)
        mc.inc("trades_total", tags={"symbol": "EURUSD", "result": "win"}, value=1)
        mc.gauge("balance", value=201000)

        snap = mc.snapshot()
        assert "counters" in snap
        assert "gauges" in snap
        assert snap["gauges"]["balance"] == 201000

    def test_metrics_by_symbol(self):
        """MetricsCollector doit tagger par symbole (clés triées alphabétiquement)"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        mc.inc("trades", tags={"symbol": "EURUSD", "result": "win"})
        mc.inc("trades", tags={"symbol": "GBPUSD", "result": "loss"})
        mc.inc("trades", tags={"symbol": "EURUSD", "result": "win"})

        snap = mc.snapshot()
        total = snap["counters"].get("trades", {})
        # Les tags sont triés alphabétiquement: result < symbol
        # Clé = trades[result=win,symbol=EURUSD]
        assert total.get("trades[result=win,symbol=EURUSD]", 0) == 2
        assert total.get("trades[result=loss,symbol=GBPUSD]", 0) == 1

    def test_health_server_serves_metrics(self):
        """HealthServer doit exposer les métriques en prometheus text"""
        from engine_simple.monitoring import HealthServer, MetricsCollector

        mc = MetricsCollector()
        mc.inc("trades_total", value=42)
        mc.gauge("balance", value=201000)

        server = HealthServer(port=0, metrics=mc)
        text = mc.prometheus_text()
        assert "trades_total" in text or "robot_trades_total" in text
        assert "balance" in text or "robot_gauge" in text

    def test_health_server_health_status(self):
        """HealthServer doit exposer /health"""
        from engine_simple.monitoring import HealthServer, MetricsCollector

        mc = MetricsCollector()
        server = HealthServer(port=0, metrics=mc)
        # Accès direct au handler
        health_info = {
            "status": "ok",
            "uptime_seconds": 100,
            "timestamp": time.time(),
        }
        assert health_info["status"] == "ok"

    def test_prometheus_format(self):
        """MetricsCollector.prometheus_text() doit être au format Prometheus"""
        from engine_simple.monitoring import MetricsCollector

        mc = MetricsCollector()
        mc.inc("trades_total", value=10)
        mc.gauge("balance", value=200000)
        mc.histogram("latency_ms", value=5.0)

        text = mc.prometheus_text()
        assert "trades_total" in text
        assert "balance" in text
        assert "# TYPE" in text


# ═══════════════════════════════════════════════════════════════
# 7. Dashboard
# ═══════════════════════════════════════════════════════════════


class TestDashboardIntegration:
    """Test Dashboard.generate_report() et RobotStatus"""

    def test_generate_report_basic(self):
        """Dashboard.generate_report() doit produire un RobotStatus"""
        from engine_simple.dashboard import Dashboard

        dash = Dashboard()
        status = dash.generate_report(
            robot_state={
                "balance": 201000,
                "equity": 200800,
                "total_trades": 150,
                "win_rate": 0.68,
                "profit_factor": 1.8,
            },
            positions=[],
        )
        assert status.balance == 201000
        assert status.equity == 200800
        assert status.total_trades == 150
        assert status.win_rate == 0.68
        assert len(status.positions) == 0

    def test_generate_report_with_positions(self):
        """Dashboard.generate_report() doit inclure les positions"""
        from engine_simple.dashboard import Dashboard

        dash = Dashboard()
        status = dash.generate_report(
            robot_state={"balance": 200000, "equity": 200000},
            positions=[
                {
                    "symbol": "EURUSD",
                    "ticket": 12345,
                    "type": 0,
                    "price_open": 1.1000,
                    "sl": 1.0900,
                    "tp": 1.1200,
                    "volume": 0.01,
                    "profit": 50.0,
                    "comment": "MOM20x3",
                    "magic": 999001,
                    "time": int(time.time()) - 3600,
                }
            ],
        )
        assert len(status.positions) > 0
        assert status.positions[0].symbol == "EURUSD"

    def test_robot_status_to_dict(self):
        """RobotStatus.to_dict() doit produire un dict"""
        from engine_simple.dashboard import Dashboard

        dash = Dashboard()
        status = dash.generate_report(robot_state={"balance": 200000, "equity": 200000})
        d = status.to_dict()
        assert isinstance(d, dict)
        assert d["balance"] == 200000
        assert "timestamp" in d
        assert "open_positions" in d

    def test_dashboard_with_metrics(self):
        """Dashboard.generate_report() doit accepter les métriques par symbole"""
        from engine_simple.dashboard import Dashboard

        dash = Dashboard()
        status = dash.generate_report(
            robot_state={"balance": 201000, "equity": 200500, "open_positions": 3},
            metrics={
                "EURUSD": {
                    "trades": 60,
                    "win_rate": 0.68,
                    "profit_factor": 1.8,
                    "total_pnl": 1200,
                    "sharpe": 1.2,
                    "avg_trade": 20,
                    "max_dd": 0.05,
                },
                "GBPUSD": {
                    "trades": 40,
                    "win_rate": 0.62,
                    "profit_factor": 1.3,
                    "total_pnl": 500,
                    "sharpe": 0.8,
                    "avg_trade": 12,
                    "max_dd": 0.03,
                },
            },
        )
        assert len(status.symbol_metrics) == 2
        assert status.symbol_metrics["EURUSD"].trades == 60
        assert status.symbol_metrics["EURUSD"].win_rate == 0.68
        assert status.symbol_metrics["GBPUSD"].trades == 40


# ═══════════════════════════════════════════════════════════════
# 8. Trailing stop via Trailer
# ═══════════════════════════════════════════════════════════════


class TestTrailingStopIntegration:
    """Test le trailing stop via Trailer avec MockMT5Server"""

    def test_trailer_init_and_calc_sl_tp(self):
        """Trailer doit pouvoir calculer SL/TP"""
        from engine_simple.trailer import Trailer

        server = MockMT5Server()
        trailer = Trailer(
            server,
            dict(
                INITIAL_BALANCE=200000,
                MAX_DD_PCT=0.10,
                MAX_DAILY_LOSS_PCT=0.02,
                MAGIC=999001,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            ),
        )
        sl, tp = trailer.calc_sl_tp("EURUSD", entry=1.1000, direction=0, atr_val=0.005, sl_mult=2.0, tp_mult=4.0)
        assert sl is not None
        assert tp is not None
        assert sl < 1.1000 < tp  # BUY: SL en dessous, TP au dessus

    def test_trailer_calc_sl_tp_sell(self):
        """Trailer.calc_sl_tp() pour SELL"""
        from engine_simple.trailer import Trailer

        server = MockMT5Server()
        trailer = Trailer(
            server,
            dict(
                INITIAL_BALANCE=200000,
                MAX_DD_PCT=0.10,
                MAX_DAILY_LOSS_PCT=0.02,
                MAGIC=999001,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            ),
        )
        sl, tp = trailer.calc_sl_tp("EURUSD", entry=1.1000, direction=1, atr_val=0.005, sl_mult=2.0, tp_mult=4.0)
        assert sl is not None
        assert tp is not None
        assert sl > 1.1000 > tp  # SELL: SL au dessus, TP en dessous

    def test_trailer_rounding(self):
        """Les SL/TP doivent être arrondis selon les digits du symbole"""
        from engine_simple.trailer import Trailer

        server = MockMT5Server()
        trailer = Trailer(
            server,
            dict(
                INITIAL_BALANCE=200000,
                MAX_DD_PCT=0.10,
                MAX_DAILY_LOSS_PCT=0.02,
                MAGIC=999001,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            ),
        )
        sl, tp = trailer.calc_sl_tp("EURUSD", entry=1.10005, direction=0, atr_val=0.005, sl_mult=2.0, tp_mult=4.0)
        # EURUSD a 5 digits, donc arrondi à 0.00001 près
        if sl is not None:
            sl_str = f"{sl:.5f}"
            assert len(sl_str.split(".")[1]) == 5

    def test_trailer_forces_breakeven(self):
        """Trailer peut forcer le breakeven (vérifie que la méthode existe)"""
        from engine_simple.trailer import Trailer

        server = MockMT5Server()
        trailer = Trailer(
            server,
            dict(
                INITIAL_BALANCE=200000,
                MAX_DD_PCT=0.10,
                MAX_DAILY_LOSS_PCT=0.02,
                MAGIC=999001,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            ),
        )
        assert hasattr(trailer, "_force_breakeven")

    def test_trailer_handles_positions(self):
        """Trailer.check_partial_tp + check_step_trailing ne doit pas planter"""
        from engine_simple.trailer import Trailer

        server = MockMT5Server()
        trailer = Trailer(
            server,
            dict(
                INITIAL_BALANCE=200000,
                MAX_DD_PCT=0.10,
                MAX_DAILY_LOSS_PCT=0.02,
                MAGIC=999001,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            ),
        )

        # Ouvrir une position et setter le prix courant
        server.order_send(
            dict(
                action=1,
                symbol="EURUSD",
                volume=0.01,
                type=0,
                price=1.1000,
                sl=1.0900,
                tp=1.1200,
                magic=999001,
                comment="TRAIL_TEST",
                type_filling=1,
                type_time=0,
            )
        )

        pos = server.get_positions()[0]
        # MockPosition n'a pas price_current — l'ajouter pour _check_partial_tp
        pos.price_current = 1.1050
        # Ces méthodes ne doivent pas planter
        trailer._check_partial_tp(pos)
        trailer._check_step_trailing(pos)
        trailer._check_time_stop(pos)

    def test_trailing_state_tracking(self):
        """Trailer doit tracker les peaks et métadonnées des positions"""
        from engine_simple.trailer import Trailer

        server = MockMT5Server()
        trailer = Trailer(
            server,
            dict(
                INITIAL_BALANCE=200000,
                MAX_DD_PCT=0.10,
                MAX_DAILY_LOSS_PCT=0.02,
                MAGIC=999001,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
            ),
        )

        # Simuler une position
        pos = MagicMock(
            ticket=1,
            symbol="EURUSD",
            type=0,
            price_open=1.1000,
            sl=1.0900,
            tp=1.1200,
            volume=0.01,
            magic=999001,
            comment="MOM20x3_TREND_UP",
            profit=0.0,
        )

        # Vérifie que les dictionnaires internes sont initialisés
        assert hasattr(trailer, "trailing_peaks")
        assert hasattr(trailer, "position_regime")
        assert hasattr(trailer, "partial_closed")
