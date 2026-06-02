"""Tests for trade_journal.py — in-memory SQLite"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta

import pytest

from engine_simple.trade_journal import TradeJournal


@pytest.fixture
def journal():
    import engine_simple.trade_journal as tj
    orig_path = tj.DB_PATH
    tj.DB_PATH = ":memory:"
    j = TradeJournal()
    yield j
    tj.DB_PATH = orig_path


def make_time(days_ago=1, hour=0):
    dt = datetime.now() - timedelta(days=days_ago, hours=12 - hour)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def test_record_and_count(journal):
    journal.record(dict(
        symbol="EURUSD", direction="BUY", entry=1.1, sl=1.09, tp=1.13,
        lot=0.1, profit=50.0, time_open=make_time(2, 0),
        time_close=make_time(2, 1), reason="TP"
    ))
    journal.record(dict(
        symbol="EURUSD", direction="SELL", entry=1.1, sl=1.11, tp=1.07,
        lot=0.1, profit=-30.0, time_open=make_time(2, 2),
        time_close=make_time(2, 3), reason="SL"
    ))
    stats = journal.get_stats(symbol="EURUSD", days=30)
    assert stats is not None
    assert stats["trade_count"] == 2
    assert abs(stats["trade_winrate"] - 0.5) < 0.001


def test_get_stats_empty(journal):
    stats = journal.get_stats()
    assert stats is None


def test_get_stats_profit_factor(journal):
    journal.record(dict(
        symbol="GBPUSD", direction="BUY", entry=1.25, sl=1.24, tp=1.28,
        lot=0.2, profit=100.0, time_open=make_time(2, 0),
        time_close=make_time(2, 1), reason="TP"
    ))
    journal.record(dict(
        symbol="GBPUSD", direction="SELL", entry=1.25, sl=1.26, tp=1.22,
        lot=0.1, profit=-20.0, time_open=make_time(2, 2),
        time_close=make_time(2, 3), reason="SL"
    ))
    stats = journal.get_stats("GBPUSD", days=30)
    assert stats is not None
    assert stats["trade_profit_factor"] >= 5.0


def test_winrate_lookbacks(journal):
    for i in range(10):
        profit = 10.0 if i < 7 else -10.0
        journal.record(dict(
            symbol="USDJPY", direction="BUY", entry=150.0, sl=149.5, tp=151.0,
            lot=0.1, profit=profit, time_open=make_time(i+1, 0),
            time_close=make_time(i+1, 1), reason="TP" if profit > 0 else "SL"
        ))
    stats = journal.get_stats("USDJPY", days=30)
    assert stats is not None
    assert stats["trade_count"] == 10
    assert abs(stats["trade_winrate"] - 0.7) < 0.001


def test_multi_symbol_isolation(journal):
    journal.record(dict(symbol="EURUSD", profit=50, time_close=make_time(1, 1)))
    journal.record(dict(symbol="GBPUSD", profit=-20, time_close=make_time(1, 1)))
    stats_eur = journal.get_stats("EURUSD", days=30)
    stats_gbp = journal.get_stats("GBPUSD", days=30)
    stats_all = journal.get_stats(days=30)
    assert stats_eur["trade_count"] == 1
    assert stats_gbp["trade_count"] == 1
    assert stats_all["trade_count"] == 2


def test_avg_pnl(journal):
    for i in range(5):
        journal.record(dict(symbol="EURUSD", profit=10.0 * i,
                            time_close=make_time(i+1, 1)))
    stats = journal.get_stats("EURUSD", days=30)
    assert stats is not None
    cumul = sum(10.0 * i for i in range(5))
    assert abs(stats["trade_avg_pnl"] - cumul / 5) < 0.01
