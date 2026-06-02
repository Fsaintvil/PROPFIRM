"""Pytest configuration — mock heavy deps before any imports"""
import sys
import time
import types
from unittest.mock import MagicMock


# ── Mock torch as a real module tree ──
class MockModule(types.ModuleType):
    pass

# Build torch.nn as a module first
torch_nn = MockModule("torch.nn")
torch_nn.Module = type('Module', (), {'__call__': lambda s, *a, **kw: MagicMock()})
torch_nn.LSTM = MagicMock
torch_nn.Dropout = MagicMock
torch_nn.Linear = MagicMock
torch_nn.Sigmoid = MagicMock
torch_nn.MSELoss = MagicMock

torch_optim = MockModule("torch.optim")
torch_optim.Adam = MagicMock

# Build torch
torch_mod = MockModule("torch")
torch_mod.__version__ = "2.0.0"
torch_mod.nn = torch_nn
torch_mod.optim = torch_optim
torch_mod.Tensor = MagicMock()
torch_mod.from_numpy = MagicMock(return_value=MagicMock())
torch_mod.save = MagicMock()
torch_mod.load = MagicMock(return_value=MagicMock())
torch_mod.no_grad = MagicMock()

# Register as proper submodules so `import torch.nn` works
torch_nn.__package__ = "torch.nn"
torch_nn.__path__ = []
torch_mod.__path__ = []
sys.modules['torch'] = torch_mod
sys.modules['torch.nn'] = torch_nn
sys.modules['torch.optim'] = torch_optim

# ── Mock MetaTrader5 ──
mt5_mod = MockModule("MetaTrader5")
mt5_mod.TIMEFRAME_H1 = 16385
mt5_mod.TIMEFRAME_M15 = 16387
mt5_mod.TIMEFRAME_M5 = 16389
mt5_mod.TIMEFRAME_H4 = 16386
mt5_mod.TIMEFRAME_D1 = 16408
mt5_mod.ORDER_TYPE_BUY = 0
mt5_mod.ORDER_TYPE_SELL = 1
mt5_mod.TRADE_ACTION_DEAL = 1
mt5_mod.TRADE_ACTION_SLTP = 5
mt5_mod.ORDER_FILLING_IOC = 2
mt5_mod.ORDER_TIME_GTC = 0
_tick = MagicMock(ask=1.1, bid=1.099)
_tick.time = time.time()
mt5_mod.symbol_info_tick = MagicMock(return_value=_tick)
mt5_mod.order_calc_profit = MagicMock(return_value=15.0)
mt5_mod.order_send = MagicMock(return_value=MagicMock(retcode=10009))
mt5_mod.copy_rates_from_pos = MagicMock(return_value=None)
mt5_mod.copy_rates_from = MagicMock(return_value=None)
mt5_mod.initialize = MagicMock(return_value=True)
mt5_mod.login = MagicMock(return_value=True)
mt5_mod.shutdown = MagicMock()
mt5_mod.account_info = MagicMock()
mt5_mod.symbol_info = MagicMock()
mt5_mod.get_symbols = MagicMock(return_value=[])
mt5_mod.terminal_info = MagicMock(return_value=MagicMock(connected=True))
mt5_mod.positions_get = MagicMock(return_value=[])
mt5_mod.orders_get = MagicMock(return_value=[])
mt5_mod.history_deals_get = MagicMock(return_value=[])
sys.modules['MetaTrader5'] = mt5_mod
