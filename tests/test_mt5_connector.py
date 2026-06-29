"""Tests for mt5_connector.py — with MT5 mock (via conftest)"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock

import MetaTrader5 as mt5

from engine_simple.mt5_connector import MT5Connector


def make_connector():
    return MT5Connector(12345, "pass", "server")


def test_connect_success():
    c = make_connector()
    mt5.initialize.return_value = True
    mt5.account_info.return_value = MagicMock(balance=10000.0, equity=10000.0)
    assert c.connect()
    assert c.connected
    # Vérifie que les credentials sont passés dans initialize()
    call_kwargs = mt5.initialize.call_args[1]
    assert call_kwargs["login"] == 12345
    assert call_kwargs["password"] == "pass"
    assert call_kwargs["server"] == "server"


def test_connect_init_failure():
    c = make_connector()
    mt5.initialize.return_value = False
    assert not c.connect()
    assert not c.connected


def test_disconnect():
    c = make_connector()
    c.connected = True
    c.disconnect()
    assert not c.connected


def test_health_check_ok():
    c = make_connector()
    mt5.account_info.return_value = MagicMock()
    mt5.terminal_info.return_value = MagicMock(connected=True)
    assert c.health_check()


def test_health_check_no_account():
    c = make_connector()
    mt5.account_info.return_value = None
    assert not c.health_check()


def test_health_check_no_terminal():
    c = make_connector()
    mt5.account_info.return_value = MagicMock()
    mt5.terminal_info.return_value = None
    assert not c.health_check()


def test_health_check_not_connected():
    c = make_connector()
    mt5.account_info.return_value = MagicMock()
    mt5.terminal_info.return_value = MagicMock(connected=False)
    assert not c.health_check()


def test_get_positions_filters_magic():
    c = make_connector()
    p1 = MagicMock(magic=999001)
    p2 = MagicMock(magic=999002)
    mt5.positions_get.return_value = [p1, p2]
    result = c.get_positions()
    assert len(result) == 1
    assert result[0].magic == 999001


def test_get_pending_orders_filters_magic():
    c = make_connector()
    o1 = MagicMock(magic=999001)
    o2 = MagicMock(magic=999002)
    mt5.orders_get.return_value = [o1, o2]
    result = c.get_pending_orders()
    assert len(result) == 1


def test_get_symbol_info():
    c = make_connector()
    mock_info = MagicMock()
    mock_info.name = "EURUSD"
    mt5.symbol_info.return_value = mock_info
    result = c.get_symbol_info("EURUSD")
    assert result.name == "EURUSD"


def test_order_send():
    c = make_connector()
    mt5.order_send.return_value = MagicMock(retcode=10009)
    result = c.order_send({"action": 1})
    assert result.retcode == 10009


def test_close_position():
    c = make_connector()
    pos = MagicMock(type=0, symbol="EURUSD", volume=0.1, ticket=12345)
    mock_tick = MagicMock(ask=1.1005, bid=1.1000)
    mt5.symbol_info_tick.return_value = mock_tick
    mt5.order_send.return_value = MagicMock(retcode=10009)
    result = c.close_position(pos)
    assert result.retcode == 10009


def test_update_sl():
    c = make_connector()
    pos = MagicMock(ticket=12345, tp=1.1100)
    mt5.order_send.return_value = MagicMock(retcode=10009)
    result = c.update_sl(pos, 1.0950)
    assert result.retcode == 10009


def test_get_account_info():
    c = make_connector()
    mt5.account_info.return_value = MagicMock(balance=100000)
    info = c.get_account_info()
    assert info.balance == 100000


def test_get_history():
    c = make_connector()
    mt5.history_deals_get.return_value = [MagicMock(profit=50)]
    history = c.get_history(1000, 2000)
    assert len(history) == 1


def test_get_rates_returns_none_on_failure():
    c = make_connector()
    mt5.copy_rates_from_pos.return_value = None
    result = c.get_rates("EURUSD", "H1", 100)
    assert result is None


def test_get_rates_valid():
    c = make_connector()
    mt5.copy_rates_from_pos.return_value = [MagicMock() for _ in range(50)]
    result = c.get_rates("EURUSD", "H1", 50)
    assert len(result) == 50
