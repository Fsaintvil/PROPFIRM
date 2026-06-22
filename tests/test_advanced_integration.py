"""Tests d'intégration avancés — MockMT5Server, stress tests, cycle complet

Utilise MockMT5Server pour simuler un environnement MT5 complet
sans connexion réelle au broker.
"""
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"


import config_simple as cfg
from tests.mock_mt5 import MockMT5Server

# ── MockMT5Server tests ──

def test_mock_server_connect():
    server = MockMT5Server()
    assert server.connect()
    assert server.health_check()


def test_mock_server_account_info():
    server = MockMT5Server(initial_balance=200000)
    info = server.get_account_info()
    assert info.balance == 200000
    assert info.equity == 200000


def test_mock_server_symbol_info():
    server = MockMT5Server(spread=0.0002)
    info = server.get_symbol_info("EURUSD")
    assert info.bid < info.ask
    assert info.point > 0


def test_mock_server_order_send():
    server = MockMT5Server()
    req = dict(action=1, symbol="EURUSD", volume=0.1, type=0,
               price=1.105, sl=1.100, tp=1.115, magic=999001,
               comment="TEST", type_filling=1, type_time=0)
    result = server.order_send(req)
    assert result.retcode == 10009
    assert result.order > 0
    assert len(server._positions) == 1
    assert server._positions[0].symbol == "EURUSD"


def test_mock_server_close_position():
    server = MockMT5Server()
    req = dict(action=1, symbol="EURUSD", volume=0.1, type=0,
               price=1.105, sl=1.100, tp=1.115, magic=999001,
               comment="TEST", type_filling=1, type_time=0)
    server.order_send(req)
    assert server.open_positions_count == 1
    pos, profit = server.close_position(symbol="EURUSD")
    assert pos is not None
    assert server.open_positions_count == 0
    assert server.deals_count == 1


def test_mock_server_multiple_positions():
    server = MockMT5Server()
    for sym in ["EURUSD", "GBPUSD", "USDCHF"]:
        server.order_send(dict(action=1, symbol=sym, volume=0.05, type=0,
                                price=1.1, sl=1.09, tp=1.12, magic=999001,
                                comment="MULTI", type_filling=1, type_time=0))
    assert server.open_positions_count == 3
    positions = server.get_positions()
    symbols = [p.symbol for p in positions]
    assert "EURUSD" in symbols
    assert "GBPUSD" in symbols
    assert "USDCHF" in symbols


def test_mock_server_disconnect():
    server = MockMT5Server()
    server.disconnect()
    assert not server.health_check()
    result = server.order_send(dict(action=1, symbol="EURUSD", volume=0.1, type=0,
                                     price=1.1, sl=1.09, tp=1.12, magic=999001,
                                     comment="", type_filling=1, type_time=0))
    assert result.retcode != 10009


def test_mock_server_get_rates():
    server = MockMT5Server()
    rates = server.get_rates("EURUSD", "H1", 50)
    assert len(rates) == 50
    for r in rates:
        assert len(r) == 6  # time, open, high, low, close, volume


def test_mock_server_get_rates_multi_tf():
    server = MockMT5Server()
    rates = server.get_rates_multi_tf("EURUSD", ["H1", "M15"])
    assert "H1" in rates
    assert "M15" in rates


# ── Stress tests ──

def test_stress_rapid_orders():
    """100 ordres rapidement — vérifie que le server tient"""
    server = MockMT5Server()
    for i in range(100):
        sym = ["EURUSD", "GBPUSD", "USDCHF"][i % 3]
        direction = i % 2
        req = dict(action=1, symbol=sym, volume=0.01, type=direction,
                   price=1.1, sl=1.09, tp=1.12, magic=999001,
                   comment=f"STRESS_{i}", type_filling=1, type_time=0)
        result = server.order_send(req)
        assert result.retcode == 10009

    assert server.open_positions_count == 100
    assert server._next_ticket >= 1100


def test_stress_close_all():
    """Ouvre 50 positions, ferme les toutes"""
    server = MockMT5Server()
    for i in range(50):
        server.order_send(dict(action=1, symbol="EURUSD", volume=0.01, type=i % 2,
                                price=1.1, sl=1.09, tp=1.12, magic=999001,
                                comment=f"S{i}", type_filling=1, type_time=0))
    assert server.open_positions_count == 50
    profits = []
    for _ in range(50):
        pos, profit = server.close_position(symbol="EURUSD")
        if pos:
            profits.append(profit)
    assert server.open_positions_count == 0
    assert len(profits) > 0


def test_stress_balance_tracking():
    """Vérifie que le balance tracking est cohérent après plusieurs trades"""
    server = MockMT5Server(initial_balance=100000)
    for i in range(20):
        server.order_send(dict(action=1, symbol="EURUSD", volume=0.1, type=0,
                                price=1.1, sl=1.09, tp=1.12, magic=999001,
                                comment=f"T{i}", type_filling=1, type_time=0))
        server.close_position(symbol="EURUSD")
    info = server.get_account_info()
    assert info.balance != 100000  # changed due to profits/losses


def test_stress_concurrent_latency():
    """Mesure le temps d'exécution de 1000 ordres"""
    server = MockMT5Server()
    t0 = time.time()
    for i in range(1000):
        server.order_send(dict(action=1, symbol="EURUSD", volume=0.01, type=i % 2,
                                price=1.1, sl=1.09, tp=1.12, magic=999001,
                                comment=f"LAT{i}", type_filling=1, type_time=0))
    elapsed = time.time() - t0
    # 1000 ordres en < 1 seconde
    assert elapsed < 1.0, f"1000 ordres en {elapsed:.2f}s (trop lent)"
    assert server.open_positions_count == 1000


# ── Broker + position tracking integration ──

def test_broker_with_mock_server():
    import tempfile

    from engine_simple.audit_trail import AuditTrail
    from engine_simple.broker import Broker

    server = MockMT5Server()
    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditTrail(log_dir=tmp)
        broker = Broker(server, audit=audit)
        assert broker.connect()
        assert broker.is_connected

        # Test delegation of MT5 calls
        info = broker.get_symbol_info("EURUSD")
        assert info is not None
        assert info.bid < info.ask

        # Test order via broker
        req = dict(action=1, symbol="EURUSD", volume=0.1, type=0,
                   price=1.105, sl=1.100, tp=1.115, magic=999001,
                   comment="BROKER_TEST", type_filling=1, type_time=0)
        result = broker.order_send(req)
        assert result.retcode == 10009

        # Test latency tracking
        lat = broker.latency.summary()
        assert lat["samples"] > 0
        audit.close()


def test_full_signal_to_execution_flow():
    """Test du flux complet: signal → risk check → execution → close → tracking"""
    import tempfile

    from engine_simple.adaptive_intelligence import AdaptiveEngine
    from engine_simple.audit_trail import AuditTrail
    from engine_simple.ftmo_protector import FTMOProtector
    from engine_simple.position_tracker import PositionTracker
    from engine_simple.trade_executor import TradeExecutor
    from engine_simple.trade_journal import TradeJournal

    server = MockMT5Server(initial_balance=200000, spread=0.0002)

    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditTrail(log_dir=tmp)
        journal = TradeJournal(csv_path=os.path.join(tmp, "test_trades.csv"))
        adaptive = AdaptiveEngine(server, calibration_path=os.path.join(tmp, "calib.pkl"))
        ftmo = FTMOProtector(server, dict(
            MAX_POSITIONS=6, MAX_TRADES_PER_DAY=10, MIN_SIGNAL_SCORE=0.5,
            LOT_SIZE=0.05, RISK_PER_TRADE=0.004, COOLDOWN_MINUTES=0,
            MAX_DAILY_LOSS_PCT=0.05, INITIAL_BALANCE=200000,
            MAX_DD_PCT=0.10, PROFIT_TARGET_PCT=0.10,
            CONSISTENCY_MAX_PCT=0.30, MIN_TRADING_DAYS=1,
            MAGIC=999001, MAX_SPREAD_POINTS=100, MAX_RISK_AMOUNT=1000,
            TRADING_START_HOUR=0, TRADING_END_HOUR=24,
            SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
        ))

        pos_cache = MagicMock()
        pos_cache.get.return_value = server.get_positions()

        tracker = PositionTracker(ftmo, journal, adaptive, pos_cache, mt5=server, audit=audit)
        signals = MagicMock()  # SignalGenerator archivé — MOM20x3 pur via strategy.py
        executor = TradeExecutor(server, ftmo, journal, tracker, signals, adaptive, audit=audit)

        tracker.init_tickets()

        # Simulate a signal and execute (RR must be > 2.0 to pass MIN_RR_RATIO)
        signal = {
            "action": "BUY", "score": 0.80, "confidence": 0.75,
            "atr": 0.005, "sl_atr": 1.5, "tp_atr": 4.0, "risk_mult": 1.0,
            "quality": 1.0, "is_ranging": False, "_regime": "TREND",
            "_ml_agrees": True, "rr": 2.66, "rates": {},
        }
        executor.execute("EURUSD", signal)

        # Verify position was opened
        positions = server.get_positions()
        assert len(positions) > 0
        assert positions[0].symbol == "EURUSD"
        assert positions[0].type == 0  # BUY

        # Track the position
        tracker.track_new()

        # Simulate close
        server.close_position(symbol="EURUSD")
        pos_cache.get.return_value = server.get_positions()

        # Check closed position tracking
        tracker.check_closed()

        # Verify at least one trade got recorded
        assert len(server._deals) > 0
        audit.close()
        journal.close()


# ── Shield integration tests ──

@pytest.mark.skip(reason="shield.py moved to retired/ml_modules/")
def test_shield_ftmo_account_with_integration():
    """FTMOAccount state tracking via test trades."""
    from engine_simple.shield import FTMOAccount

    acc = FTMOAccount(200000, 200000, 200000)
    for pnl in [100, 200, -50, 300, -100]:
        acc.record_trade(pnl)
    assert acc.total_trades == 5
    assert acc.total_profit == 450
    assert acc.current_balance == 200450
    assert acc.consecutive_losses == 1  # last trade was -100
    assert acc.peak_equity == 200550


@pytest.mark.skip(reason="shield.py moved to retired/ml_modules/")
def test_shield_ftmo_account_recovery():
    """FTMOAccount handles loss streak and recovery."""
    from engine_simple.shield import FTMOAccount

    acc = FTMOAccount(200000, 200000, 200000)
    for pnl in [-100, -200, -150, 500]:
        acc.record_trade(pnl)
    assert acc.consecutive_losses == 0  # reset by win
    assert acc.current_balance == 200050
    assert acc.total_trades == 4


def test_shield_position_guard_full_cycle():
    """PositionGuard through track→trail→close lifecycle (SUPPRIMÉ FIX #23)."""
    pytest.skip("PositionGuard removed — ATR hardcodé dangereux (issue #B1)")


# ── Regime + Strategy integration ──

# Note: test_regime_to_strategy_pipeline removed (MOM20x3 was in strategy.py,
# which was dead code. The real signal pipeline is in signals.py → SignalGenerator.)

def test_regime_detector_with_mock_mt5():
    """RegimeDetector via MockMT5Server rates."""
    import numpy as np
    from tests.mock_mt5 import MockMT5Server
    from engine_simple.regime import RegimeDetector

    server = MockMT5Server()
    rates = server.get_rates("EURUSD", "H1", 100)
    assert len(rates) == 100

    hh = np.array([r[2] for r in rates], dtype=float)
    ll = np.array([r[3] for r in rates], dtype=float)
    cc = np.array([r[4] for r in rates], dtype=float)

    detector = RegimeDetector()
    regime, meta = detector.detect(hh, ll, cc)
    assert regime in ("TREND_UP", "TREND_DOWN", "RANGING", "LOW_VOL", "HIGH_VOL")
    for key in ("adx", "atr", "slope", "vol_percentile"):
        assert key in meta


# Note: test_strategy_mom20x3_with_mock_mt5 removed (strategy.py was dead code,
# MOM20x3 never called anywhere. SignalGenerator in signals.py is the real impl.)

# ── Stress test: circulation complète (sans MOM20x3 de strategy.py qui n'existe plus) ──
