"""Tests pour RetrainingPipeline (Phase 7 ML Pipeline)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

from unittest.mock import MagicMock

import numpy as np
import pytest

from engine_simple.ml_features import FULL_FEATURE_NAMES
from engine_simple.mlflow_tracker import MLFLOW_AVAILABLE, MLflowTracker
from engine_simple.retraining_pipeline import RetrainingPipeline


@pytest.fixture
def mock_journal():
    j = MagicMock()
    j.conn = MagicMock()
    return j


@pytest.fixture
def mock_feature_store():
    fs = MagicMock()
    fs.load.return_value = {}
    return fs


@pytest.fixture
def mock_dl():
    dl = MagicMock()
    dl.available = False
    return dl


class TestRetrainingPipeline:
    def test_init(self, mock_journal, mock_feature_store, mock_dl):
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        assert p.journal == mock_journal
        assert p.feature_store == mock_feature_store
        assert p.dl == mock_dl
        assert p.mlflow is not None
        assert p._model_dir.exists()

    def test_build_dataset_empty(self, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchall.return_value = []
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=50)
        assert result == {}

    def test_build_dataset_no_features(self, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (1, "BUY", 10.0), (2, "SELL", -5.0)
        ]
        mock_feature_store.load.return_value = {}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=1)
        assert result == {}

    def test_build_dataset_with_features(self, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (i, "BUY", 10.0) for i in range(10)
        ]
        n_features = len(FULL_FEATURE_NAMES)
        seq = [[0.5] * n_features for _ in range(20)]
        mock_feature_store.load.return_value = {"dl_features": seq}

        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=5)
        assert "EURUSD" in result
        assert result["EURUSD"]["X"].shape == (10, 20, n_features)
        assert result["EURUSD"]["y"].shape == (10,)
        assert np.allclose(result["EURUSD"]["y"], 1.0)

    def test_build_dataset_wrong_shape_features(self, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchall.return_value = [(1, "BUY", 10.0)]
        # wrong shape: 10 features instead of 20
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * len(FULL_FEATURE_NAMES) for _ in range(10)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=1)
        assert result == {}

    def test_walk_forward_split(self, mock_journal, mock_feature_store, mock_dl):
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        n = 100
        X = np.random.randn(n, 20, len(FULL_FEATURE_NAMES)).astype(np.float32)
        y = np.random.randint(0, 2, size=n).astype(np.float32)
        splits = p.walk_forward_split(X, y, n_splits=4)
        assert len(splits) >= 3
        for Xtr, _ytr, Xva, _yva in splits:
            assert len(Xtr) + len(Xva) <= n

    def test_walk_forward_split_insufficient(self, mock_journal, mock_feature_store, mock_dl):
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        X = np.random.randn(15, 20, 31).astype(np.float32)
        y = np.random.randint(0, 2, size=15).astype(np.float32)
        splits = p.walk_forward_split(X, y, n_splits=5)
        assert len(splits) <= 2

    def test_train_dl_no_torch(self, mock_journal, mock_feature_store, mock_dl):
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        X = np.random.randn(50, 20, len(FULL_FEATURE_NAMES)).astype(np.float32)
        y = np.random.randint(0, 2, size=50).astype(np.float32)
        result = p.train_dl("TEST", X, y)
        # PyTorch may or may not be available in test env;
        # either way we should get a dict back
        assert isinstance(result, dict)

    def test_push_model(self, mock_journal, mock_feature_store, mock_dl):
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        model = MagicMock()
        model.state_dict.return_value = {"dummy": "state"}
        p.push_model("TEST", model, {"val_accuracy": 0.75})
        model_path = p._model_dir / "dl_attention_TEST_H1.pkl"
        assert not model_path.exists()  # torch.save wasn't called on mock

    def test_run_retraining_no_data(self, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchall.return_value = []
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        report = p.run_retraining(["EURUSD"], days=1, min_samples=50, log_mlflow=False)
        assert report["status"] in ("insufficient_data", "skipped")

    def test_get_status(self, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchone.return_value = (0,)
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        status = p.get_status()
        assert "production_models" in status
        assert "backup_models" in status
        assert "journal_trades" in status
        assert "mlflow_enabled" in status
        assert "torch_available" in status


class TestMLflowTracker:
    def test_init_fallback(self):
        tracker = MLflowTracker()
        assert tracker.enabled == MLFLOW_AVAILABLE

    def test_noop_without_mlflow(self):
        tracker = MLflowTracker()
        if not tracker.enabled:
            assert not tracker.start_run(run_name="test")
            assert tracker.log_params({"a": 1}) is None
            assert tracker.log_metrics({"m": 0.5}) is None
            assert tracker.get_best_run("accuracy") is None

    def test_enabled_property(self):
        tracker = MLflowTracker()
        assert isinstance(tracker.enabled, bool)

    def test_log_autolog_noop(self):
        # Should not raise regardless of mlflow availability
        result = MLflowTracker.log_autolog()
        assert result is None
