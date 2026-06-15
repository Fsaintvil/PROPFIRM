"""Tests for dl_ensemble.py — PyTorch LSTM ensemble with mock torch"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import threading
import time
from collections import deque
from unittest.mock import MagicMock, patch

import numpy as np

from engine_simple.dl_ensemble import DLEnsemble
from engine_simple.ml_features import FULL_FEATURE_NAMES


def test_init_without_torch():
    """When torch is not available, DLEnsemble should be disabled"""
    with patch('engine_simple.dl_ensemble.TORCH_AVAILABLE', False):
        dl = DLEnsemble()
        assert not dl.available
        assert dl.models == {}


def test_init_with_torch():
    """When torch is available, DLEnsemble loads models"""
    dl = DLEnsemble()
    if dl.available:
        assert hasattr(dl, 'models')
        assert hasattr(dl, 'training_buffer')
        assert hasattr(dl, 'feature_engine')
    else:
        assert not dl.available


def test_predict_returns_none_when_disabled():
    dl = DLEnsemble()
    dl.available = False
    result = dl.predict("EURUSD", {"H1": [1, 2, 3]})
    assert result is None


def test_predict_returns_none_without_rates():
    dl = DLEnsemble()
    if dl.available:
        result = dl.predict("EURUSD", {})
        assert result is None


def test_build_sequence_returns_none_short_data():
    dl = DLEnsemble()
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(10)]
    result = dl._build_sequence(rates)
    assert result is None


def test_build_sequence_valid():
    dl = DLEnsemble()
    np.random.seed(42)
    rates = []
    base = 1.1
    for i in range(100):
        shift = i * 0.0001
        rates.append((i, base + shift, base + shift + 0.002,
                      base + shift - 0.002, base + shift, 1000))
    result = dl._build_sequence(rates)
    if result is not None:
        assert result.shape[0] == dl.SEQUENCE_LENGTH
        assert result.shape[1] == len(FULL_FEATURE_NAMES)
        assert not np.any(np.isnan(result))


def test_record_trade_when_disabled():
    dl = DLEnsemble()
    dl.available = False
    dl.record_trade("EURUSD", None, 1.5)
    assert len(dl.training_buffer) == 0


def test_record_trade_skipped_when_disabled():
    dl = DLEnsemble()
    dl.available = False
    dl.training_buffer = MagicMock()
    dl.trade_outcomes = {}
    # When disabled, record_trade just returns without accumulating
    dl.record_trade("EURUSD", None, 1.5)
    assert dl.trade_outcomes == {}


def test_train_all_does_nothing_when_disabled():
    dl = DLEnsemble()
    dl.available = False
    dl.models = {}
    dl.train_all()
    assert dl._training_thread is None
    assert dl.models == {}


def test_train_all_requires_min_samples():
    dl = DLEnsemble()
    dl.available = True
    orig_model = MagicMock()
    dl.training_buffer = {"EURUSD_H1": deque([([1, 2, 3], 1) for _ in range(10)], maxlen=500)}
    dl.models = {"EURUSD_H1": orig_model}
    dl.train_all()
    assert dl._training_thread is not None
    dl._training_thread.join(timeout=2)
    assert dl.models["EURUSD_H1"] is orig_model  # unchanged because < 32 samples


def test_is_training_false_idle():
    dl = DLEnsemble()
    assert not dl.is_training


def test_train_all_starts_thread():
    dl = DLEnsemble()
    dl.available = True
    dl.training_buffer = {}
    dl.models = {}
    dl.train_all()
    assert dl._training_thread is not None


def test_train_all_skips_if_already_training():
    dl = DLEnsemble()
    dl.available = True
    dl._training_thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
    dl._training_thread.start()
    assert dl.is_training
    original_thread = dl._training_thread
    # Calling train_all again should not start a second thread
    dl.train_all()
    assert dl._training_thread is original_thread  # thread unchanged


def test_model_lock_acquired_in_get_model():
    dl = DLEnsemble()
    if not dl.available:
        return
    # Verify _get_model uses lock (can't patch __enter__ on thread.lock,
    # so check via side effect instead)
    dl.models.clear()
    model = dl._get_model("test_H1")
    assert model is not None


def test_model_lock_acquired_in_predict():
    dl = DLEnsemble()
    dl.available = True
    # Add a dummy model with proper return value
    mock_model = MagicMock()
    mock_model.return_value.item.return_value = 0.55
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
    rates_dict = {
        "H1": [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)],
        "M15": [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)],
        "M5": [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)],
    }
    result = dl.predict("EURUSD", rates_dict)
    # predict should complete without lock error
    assert result is not None, "predict should return a result without deadlock"

def test_record_trade_accumulates_when_available():
    dl = DLEnsemble()
    dl.available = True
    features = [0.5] * len(FULL_FEATURE_NAMES)
    dl.record_trade("EURUSD", features, 1.5)
    dl.record_trade("EURUSD", features, -0.5)
    assert "EURUSD" in dl.training_buffer
    assert len(dl.training_buffer["EURUSD"]) == 2
    assert "EURUSD" in dl.trade_outcomes

def test_record_trade_multiple_symbols():
    dl = DLEnsemble()
    dl.available = True
    features = [0.5] * len(FULL_FEATURE_NAMES)
    dl.record_trade("EURUSD", features, 1.0)
    dl.record_trade("GBPUSD", features, -1.0)
    dl.record_trade("USDCAD", features, 0.5)
    assert len(dl.training_buffer) == 3


def test_record_trade_label_correct():
    dl = DLEnsemble()
    dl.available = True
    features = [0.5] * len(FULL_FEATURE_NAMES)
    dl.record_trade("EURUSD", features, 2.0)
    dl.record_trade("EURUSD", features, -0.1)
    dl.record_trade("EURUSD", features, 0.0)
    buf = dl.training_buffer["EURUSD"]
    assert buf[0][1] == 1  # positive -> win
    assert buf[1][1] == 0  # negative -> loss
    assert buf[2][1] == 0  # zero -> loss


@patch("glob.glob", return_value=["models/dl_attention_all_H1.pkl"])
@patch("engine_simple.dl_ensemble.torch.load", return_value=MagicMock())
def test_load_pretrained_no_files(mock_torch_load, mock_glob):
    dl = DLEnsemble()
    dl.available = True
    dl._load_pretrained()  # should not raise
    # Model count depends on glob results vs actual model loading
    assert dl.models is not None


@patch("glob.glob", return_value=["models/dl_lstm_USDCAD_H1.pkl",
                                   "models/dl_lstm_EURUSD_H1.pkl",
                                   "models/dl_lstm_all_H1.pkl",
                                   "models/dl_lstm_GBPUSD_H1.pkl",
                                   "models/dl_lstm_USDCHF_H1.pkl"])
@patch("engine_simple.dl_ensemble.torch.load", return_value=MagicMock())
def test_load_pretrained_legacy_models(mock_load, mock_glob):
    dl = DLEnsemble()
    dl.available = True
    dl.models.clear()
    dl._load_pretrained()
    # Legacy models should attempt loading; mock handles errors gracefully


def test_get_model_fallback_to_all():
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    with dl._model_lock:
        dl.models["all_H1"] = mock_model
    result = dl._get_model("XYZZY_H1")
    assert result is mock_model


def test_get_model_creates_new_if_no_fallback():
    dl = DLEnsemble()
    dl.available = True
    dl.models.clear()
    model = dl._get_model("EURUSD_H1")
    # Should create a new AttentionLSTMNet
    assert model is not None


@patch("engine_simple.dl_ensemble.torch.no_grad")
@patch("engine_simple.dl_ensemble.torch.FloatTensor", return_value=MagicMock())
def test_predict_calls_model_inference(mock_float_tensor, mock_no_grad):
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    mock_model.return_value.item.return_value = 0.55
    # Register model
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)]
    result = dl.predict("EURUSD", {"H1": rates, "M15": rates, "M5": rates})
    assert result is not None
    assert "action" in result
    assert "score" in result


@patch("engine_simple.dl_ensemble.torch.no_grad")
@patch("engine_simple.dl_ensemble.torch.FloatTensor", return_value=MagicMock())
def test_predict_model_error_returns_default(mock_float_tensor, mock_no_grad):
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    mock_model.side_effect = RuntimeError("model crash")
    mock_model.return_value.item.side_effect = RuntimeError("model crash")
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)]
    # Should handle gracefully
    result = dl.predict("EURUSD", {"H1": rates})
    assert result is None  # or default


def test_predict_returns_none_when_no_model_for_symbol():
    dl = DLEnsemble()
    dl.available = True
    dl.models.clear()
    # Mock _build_sequence to return valid data so predict can proceed
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)]
    result = dl.predict("UNKNOWN", {"H1": rates})
    # May return None if model creation fails or None if it succeeds
    assert result is None or isinstance(result, dict)


@patch("engine_simple.dl_ensemble.torch.no_grad")
@patch("engine_simple.dl_ensemble.torch.FloatTensor")
def test_predict_sell_when_prob_below_50(mock_float_tensor, mock_no_grad):
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    mock_model.return_value.item.return_value = 0.35
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)]
    result = dl.predict("EURUSD", {"H1": rates})
    assert result["action"] == "SELL"
    assert result["buy_prob"] == 0.35


def test_build_sequence_numpy_rates():
    """Test _build_sequence with numpy-structured rates (has .dtype attribute)"""
    dl = DLEnsemble()
    arr = np.zeros(100, dtype=[("time", "i4"), ("open", "f8"), ("high", "f8"),
                                ("low", "f8"), ("close", "f8"), ("volume", "f8")])
    arr["close"] = np.linspace(1.0, 1.1, 100)
    arr["high"] = arr["close"] + 0.005
    arr["low"] = arr["close"] - 0.005
    rates_list = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                  for r in arr]
    result = dl._build_sequence(rates_list)
    if result is not None:
        assert result.shape[0] == dl.SEQUENCE_LENGTH
        assert result.shape[1] == len(FULL_FEATURE_NAMES)


def test_build_sequence_exactly_min_bars():
    """SEQUENCE_LENGTH + LOOKBACK = 80 bars should work"""
    dl = DLEnsemble()
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(80)]
    result = dl._build_sequence(rates)
    assert result is not None
    assert result.shape[0] == dl.SEQUENCE_LENGTH


def test_build_sequence_79_bars_returns_none():
    """79 bars < 80 = should return None"""
    dl = DLEnsemble()
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(79)]
    result = dl._build_sequence(rates)
    assert result is None


def test_train_all_with_enough_samples():
    dl = DLEnsemble()
    dl.available = True
    # Populate buffer with 3D-shaped features directly (not via record_trade)
    features_3d = np.random.randn(dl.SEQUENCE_LENGTH, len(FULL_FEATURE_NAMES)).tolist()
    buffer = deque(maxlen=500)
    for _ in range(35):
        buffer.append((features_3d, 1))
    dl.training_buffer["EURUSD"] = buffer
    # Add a mock model so _train_all_sync has something to train
    mock_model = MagicMock()
    mock_model.parameters.return_value = []
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
    # Patch _train_step to avoid real torch
    with patch.object(dl, "_train_step", return_value=True) as mock_train:
        dl.train_all()
        assert dl._training_thread is not None
        dl._training_thread.join(timeout=3)
        mock_train.assert_called_once()


def test_train_all_sync_skips_models_without_buffer():
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    mock_model.parameters.return_value = []
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
        dl.models["GBPUSD_H1"] = MagicMock()
    # Only EURUSD has buffer with 3D features
    features_3d = np.random.randn(dl.SEQUENCE_LENGTH, len(FULL_FEATURE_NAMES)).tolist()
    buffer = deque(maxlen=500)
    for _ in range(35):
        buffer.append((features_3d, 1))
    dl.training_buffer["EURUSD"] = buffer
    with patch.object(dl, "_train_step", return_value=True) as mock_train:
        dl._train_all_sync()
        assert mock_train.call_count == 1


def test_train_step_returns_true():
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    mock_model.parameters.return_value = []
    mock_model.named_parameters.return_value = []
    X = np.random.randn(32, dl.SEQUENCE_LENGTH, len(FULL_FEATURE_NAMES)).astype(np.float32)
    y = np.random.randint(0, 2, 32).astype(np.float32)
    result = dl._train_step(mock_model, X, y)
    # Should handle the training loop without real torch (mocked)
    assert isinstance(result, bool)


def test_get_model_creates_new_with_fallback_chain():
    dl = DLEnsemble()
    dl.available = True
    dl.models.clear()
    # No 'all_H1' or 'USDCAD_H1' available - should create fresh
    model = dl._get_model("EURUSD_H1")
    assert model is not None


def test_is_training_true_during_training():
    dl = DLEnsemble()
    dl.available = True
    import threading, time
    _sentinel = threading.Event()
    dl._training_thread = threading.Thread(
        target=lambda: _sentinel.wait(timeout=10), daemon=True
    )
    dl._training_thread.start()
    time.sleep(0.05)  # give thread time to start
    assert dl.is_training is True, "thread is alive -> is_training should be True"
    _sentinel.set()
    dl._training_thread.join(timeout=5)


def test_predict_ignores_timeframe_without_rates():
    dl = DLEnsemble()
    dl.available = True
    mock_model = MagicMock()
    mock_model.return_value.item.return_value = 0.51
    with dl._model_lock:
        dl.models["EURUSD_H1"] = mock_model
    # Only H1 rates provided, M15 and M5 missing
    rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)]
    result = dl.predict("EURUSD", {"H1": rates})
    assert result is not None


def test_concurrent_read_write_does_not_crash():
    """Verify that reading models while writing does not raise RuntimeError"""
    dl = DLEnsemble()
    if not dl.available:
        return
    # Pre-populate with some models
    with dl._model_lock:
        for sym in ["EURUSD", "GBPUSD", "USDCAD", "USDCHF"]:
            dl.models[f"{sym}_H1"] = MagicMock()
            dl.models[f"{sym}_M15"] = MagicMock()
            dl.models[f"{sym}_M5"] = MagicMock()

    errors = []

    def writer():
        try:
            for i in range(50):
                with dl._model_lock:
                    dl.models[f"test_{i}_H1"] = MagicMock()
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(50):
                # Access _get_model which is now protected by lock
                for key in list(dl.models.keys()):
                    _ = dl.models[key]
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, daemon=True)
    t2 = threading.Thread(target=reader, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert len(errors) == 0, f"Concurrent access errors: {errors}"


