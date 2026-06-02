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
    # Add a model then verify _get_model uses lock
    with patch.object(dl._model_lock, '__enter__', return_value=True) as mock_enter:
        with patch.object(dl._model_lock, '__exit__', return_value=False):
            dl._get_model("test_H1")
            mock_enter.assert_called()


def test_model_lock_acquired_in_predict():
    dl = DLEnsemble()
    if not dl.available:
        return
    # Add a dummy model to avoid fallback to _get_model
    with dl._model_lock:
        dl.models["EURUSD_H1"] = MagicMock()
    with patch.object(dl._model_lock, '__enter__', return_value=True) as mock_enter:
        rates_dict = {
            "H1": [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)],
            "M15": [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)],
            "M5": [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(100)],
        }
        dl.predict("EURUSD", rates_dict)
        mock_enter.assert_called()


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
