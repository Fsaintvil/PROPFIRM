"""Pytest configuration — mock heavy deps before any imports"""

import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── Mock torch as a real module tree ──
class MockModule(types.ModuleType):
    pass


# Build torch.nn as a module first
torch_nn = MockModule("torch.nn")


class MockModuleBase:
    """A proper base class mock that can be subclassed and instantiated."""

    def __init__(self, *args, **kwargs):
        self._mock = MagicMock()

    def train(self, mode=True):
        self.training = mode
        return self._mock

    def eval(self):
        self.training = False
        return self._mock

    def parameters(self):
        return []

    def named_parameters(self):
        return []

    def state_dict(self):
        return {}

    def load_state_dict(self, sd, strict=True):
        pass

    def __call__(self, *args, **kwargs):
        return MagicMock()

    def __getattr__(self, name):
        return MagicMock()


torch_nn.Module = MockModuleBase
torch_nn.LSTM = MagicMock
torch_nn.Dropout = MagicMock
torch_nn.Linear = MagicMock
torch_nn.Sigmoid = MagicMock
torch_nn.MSELoss = MagicMock

torch_optim = MockModule("torch.optim")
torch_optim.Adam = lambda *args, **kwargs: MagicMock()

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
sys.modules["torch"] = torch_mod
sys.modules["torch.nn"] = torch_nn
sys.modules["torch.optim"] = torch_optim

# ── Additional torch mocks for dl_ensemble ──
torch_nn.LayerNorm = MagicMock
torch_nn.BCELoss = MagicMock

torch_nn_f = MockModule("torch.nn.functional")
torch_nn_f.softmax = MagicMock(return_value=MagicMock())
torch_nn_f.__package__ = "torch.nn.functional"
torch_nn_f.__path__ = []
sys.modules["torch.nn.functional"] = torch_nn_f

torch_nn_utils = MockModule("torch.nn.utils")
torch_nn_utils.clip_grad_norm_ = MagicMock()
torch_nn_utils.__package__ = "torch.nn.utils"
torch_nn_utils.__path__ = []
sys.modules["torch.nn.utils"] = torch_nn_utils

torch_utils_data = MockModule("torch.utils.data")
torch_utils_data.TensorDataset = MagicMock
torch_utils_data.DataLoader = MagicMock
torch_utils_data.__package__ = "torch.utils.data"
torch_utils_data.__path__ = []
sys.modules["torch.utils.data"] = torch_utils_data

torch_mod.FloatTensor = MagicMock(return_value=MagicMock())

# ── Mock MetaTrader5 ──
mt5_mod = MockModule("MetaTrader5")
mt5_mod.TIMEFRAME_M1 = 1
mt5_mod.TIMEFRAME_M5 = 16389
mt5_mod.TIMEFRAME_M15 = 16387
mt5_mod.TIMEFRAME_H1 = 16385
mt5_mod.TIMEFRAME_H4 = 16386
mt5_mod.TIMEFRAME_D1 = 16408
mt5_mod.ORDER_TYPE_BUY = 0
mt5_mod.ORDER_TYPE_SELL = 1
mt5_mod.TRADE_ACTION_DEAL = 1
mt5_mod.TRADE_ACTION_SLTP = 5
mt5_mod.ORDER_FILLING_IOC = 2
mt5_mod.ORDER_TIME_GTC = 0
mt5_mod.ORDER_TIME_DAY = 1
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
mt5_mod.last_error = MagicMock(return_value=(0, ""))
mt5_mod.history_deals_get = MagicMock(return_value=[])
sys.modules["MetaTrader5"] = mt5_mod


# ═══════════════════════════════════════════════════════════════
# ISOLATION DES TESTS — protection du runtime/ de production
# ═══════════════════════════════════════════════════════════════
# 🐛 FIX 06 Août 2026: Les tests de stress (ex: test_position_tracker_stability
# dans test_performance_robustness.py) appellent le VRAI check_closed() →
# get_adaptive() → AdaptiveParameters(symbol) avec state_dir="runtime" par défaut
# → écrivaient des FAUX trades dans runtime/adaptive_EURUSD.json et
# runtime/adaptive_GBPUSD.json (100 trades simulés alternés, régime RANGING),
# contaminant l'OnlineLearner de production.
# → Cette fixture redirige TOUT state_dir par défaut vers un dossier temporaire.
_TEST_ADAPTIVE_DIR = Path(tempfile.mkdtemp(prefix="adaptive_test_"))


@pytest.fixture(autouse=True)
def _isolate_adaptive_params(monkeypatch):
    """Force AdaptiveParameters/get_adaptive à utiliser un dossier temporaire.

    S'applique uniquement quand state_dir n'est PAS fourni explicitement
    (les tests qui passent state_dir=tmp_path gardent leur comportement).
    Vérifie aussi qu'aucun test n'écrit dans runtime/ après exécution.
    """
    import engine_simple.adaptive_params as ap_mod

    # Vider le cache d'instances pour éviter la contamination inter-tests
    ap_mod._adaptive_instances.clear()

    original_init = ap_mod.AdaptiveParameters.__init__

    def _isolated_init(self, symbol, *args, **kwargs):
        # Ne redirige que le state_dir par défaut (sinon le test qui passe
        # state_dir=tmp_path explicitement garde son comportement).
        if kwargs.get("state_dir", "runtime") == "runtime":
            kwargs["state_dir"] = str(_TEST_ADAPTIVE_DIR)
        original_init(self, symbol, *args, **kwargs)

    monkeypatch.setattr(ap_mod.AdaptiveParameters, "__init__", _isolated_init)
    yield

    # Garde-fou: détecter toute écriture résiduelle dans le vrai runtime/
    # ⚠️ Ne vérifie QUE les symboles de TEST (le robot de production écrit
    # légitimement dans runtime/adaptive_XAUUSD.json etc. pendant pytest).
    import glob as _glob

    test_syms = ("TEST", "RECORD_TRADE_TEST", "DUPLICATE", "GET_TEST")
    for f in _glob.glob("runtime/adaptive_*.json"):
        name = Path(f).stem  # ex: adaptive_TEST123
        if any(ts in name for ts in test_syms):
            pytest.fail(f"TEST CONTAMINATION: {f} écrit dans runtime/ → état de test non isolé")
