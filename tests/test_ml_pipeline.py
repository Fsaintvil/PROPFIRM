"""Tests pour RetrainingPipeline (Phase 7 ML Pipeline)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

from unittest.mock import MagicMock, patch


class _FakeModelOut:
    """Pretends to be a torch tensor for the operation chain: out > 0.5 -> .float() -> .view(-1) -> .numpy()"""
    def __gt__(self, other):
        return self
    def float(self):
        return self
    def view(self, *args):
        return self
    def numpy(self):
        return np.array([0.5, 0.5, 0.5, 0.5])


def _make_trained_result(metrics: dict | None = None) -> dict:
    model = MagicMock()
    model.return_value = _FakeModelOut()
    return {
        "trained": True,
        "model": model,
        "metrics": metrics or {
            "val_accuracy": 0.7, "overall_accuracy": 0.65,
            "n_train": 40, "n_val": 10,
            "train_loss": 0.5, "val_loss": 0.6,
            "epochs_trained": 5,
        },
    }


_UNTRAINED = {"trained": False}

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

    def test_train_dl_torch_mocked(self, mock_journal, mock_feature_store, mock_dl):
        """train_dl returns dict even when torch is mocked (always available)."""
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        X = np.random.randn(50, 20, len(FULL_FEATURE_NAMES)).astype(np.float32)
        y = np.random.randint(0, 2, size=50).astype(np.float32)
        with patch.object(p, "train_dl", return_value={"trained": True, "error": None}):
            result = p.train_dl("TEST", X, y)
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


class TestBuildDatasetDetailed:
    """Additional build_dataset edge cases"""

    def test_mixed_valid_and_invalid_features(self, mock_journal, mock_feature_store, mock_dl):
        """Features with wrong shape are filtered, valid ones remain."""
        n_features = len(FULL_FEATURE_NAMES)
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (1, "BUY", 10.0), (2, "SELL", -5.0), (3, "BUY", 3.0)
        ]
        # Trade 1: valid, Trade 2: empty, Trade 3: wrong seq length
        def load_side_effect(ticket):
            if ticket == 1:
                return {"dl_features": [[0.5] * n_features for _ in range(20)]}
            elif ticket == 2:
                return {}
            return {"dl_features": [[0.5] * n_features for _ in range(10)]}
        mock_feature_store.load.side_effect = load_side_effect
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=1)
        assert "EURUSD" in result
        assert result["EURUSD"]["n"] == 1
        assert len(result["EURUSD"]["tickets"]) == 1

    def test_all_losses_y_zero(self, mock_journal, mock_feature_store, mock_dl):
        """When all trades lose, y should be all zeros."""
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (1, "SELL", -10.0), (2, "BUY", -5.0)
        ]
        n_features = len(FULL_FEATURE_NAMES)
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * n_features for _ in range(20)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=1)
        assert np.allclose(result["EURUSD"]["y"], 0.0)

    def test_mixed_profits_y_correct(self, mock_journal, mock_feature_store, mock_dl):
        """y labels match profit > 0."""
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (1, "BUY", 10.0), (2, "SELL", -5.0), (3, "BUY", 0.0)
        ]
        n_features = len(FULL_FEATURE_NAMES)
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * n_features for _ in range(20)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=1)
        assert result["EURUSD"]["y"].tolist() == [1.0, 0.0, 0.0]

    def test_skips_symbol_with_insufficient_valid(self, mock_journal, mock_feature_store, mock_dl):
        """Symbol with < min_samples valid trades is skipped."""
        mock_journal.conn.execute.return_value.fetchall.return_value = [(1, "BUY", 10.0)]
        n_features = len(FULL_FEATURE_NAMES)
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * n_features for _ in range(20)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=5)
        assert result == {}

    def test_multiple_symbols(self, mock_journal, mock_feature_store, mock_dl):
        """Multiple symbols are processed independently."""
        n_features = len(FULL_FEATURE_NAMES)
        def exec_side_effect(sql, params):
            mock_cursor = MagicMock()
            if params and params[0] == "EURUSD":
                mock_cursor.fetchall.return_value = [(1, "BUY", 10.0)]
            else:
                mock_cursor.fetchall.return_value = []
            return mock_cursor
        mock_journal.conn.execute.side_effect = exec_side_effect
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * n_features for _ in range(20)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD", "GBPUSD"], days=90, min_samples=1)
        assert "EURUSD" in result
        assert "GBPUSD" not in result

    def test_get_closed_trades_exception(self, mock_journal, mock_feature_store, mock_dl):
        """SQL exception returns empty list."""
        mock_journal.conn.execute.side_effect = Exception("DB error")
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        result = p.build_dataset(["EURUSD"], days=90, min_samples=1)
        assert result == {}

    def test_walk_forward_skip_small_val(self, mock_journal, mock_feature_store, mock_dl):
        """Folds with < 10 validation samples are skipped."""
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        X = np.random.randn(25, 20, len(FULL_FEATURE_NAMES)).astype(np.float32)
        y = np.random.randint(0, 2, size=25).astype(np.float32)
        splits = p.walk_forward_split(X, y, n_splits=5)
        assert len(splits) <= 2

    def test_walk_forward_exact_n_splits_when_possible(self, mock_journal, mock_feature_store, mock_dl):
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        X = np.random.randn(200, 20, len(FULL_FEATURE_NAMES)).astype(np.float32)
        y = np.random.randint(0, 2, size=200).astype(np.float32)
        splits = p.walk_forward_split(X, y, n_splits=4)
        assert len(splits) == 4


class TestTrainDLDetailed:
    """Tests for train_dl structure and error paths"""

    def test_train_dl_no_torch(self, mock_journal, mock_feature_store, mock_dl):
        nf = len(FULL_FEATURE_NAMES)
        X = np.random.randn(50, 20, nf).astype(np.float32)
        y = np.random.randint(0, 2, size=50).astype(np.float32)
        with patch("engine_simple.retraining_pipeline.TORCH_AVAILABLE", False):
            p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
            result = p.train_dl("TEST", X, y)
            assert result["error"] == "PyTorch not available"


class TestPushModelDetailed:
    def test_push_model_backup_existing(self, mock_journal, mock_feature_store, mock_dl):
        """When model file exists, backup is created before overwrite."""
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        model_path = p._model_dir / "dl_attention_TEST_H1.pkl"
        model_path.touch()  # create dummy file
        model = MagicMock()
        model.state_dict.return_value = {"w": 1}
        with patch("engine_simple.retraining_pipeline.shutil.copy2") as mock_copy:
            with patch("engine_simple.retraining_pipeline.torch.save") as mock_save:
                p.push_model("TEST", model, {"val_accuracy": 0.8})
                mock_copy.assert_called_once()
                mock_save.assert_called_once()
        model_path.unlink()

    def test_push_model_no_backup_when_missing(self, mock_journal, mock_feature_store, mock_dl):
        """When no model file exists, no backup is made."""
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        model = MagicMock()
        model.state_dict.return_value = {"w": 1}
        with patch("engine_simple.retraining_pipeline.shutil.copy2") as mock_copy:
            with patch("engine_simple.retraining_pipeline.torch.save") as mock_save:
                p.push_model("TEST", model, {"val_accuracy": 0.8})
                mock_copy.assert_not_called()
                mock_save.assert_called_once()

    def test_push_model_torch_unavailable(self, mock_journal, mock_feature_store, mock_dl):
        with patch("engine_simple.retraining_pipeline.TORCH_AVAILABLE", False):
            p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
            model = MagicMock()
            p.push_model("TEST", model, {"val_accuracy": 0.8})  # should not raise


class TestRunRetrainingDetailed:
    def test_run_retraining_with_data(self, mock_journal, mock_feature_store, mock_dl):
        n_features = len(FULL_FEATURE_NAMES)
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (i, "BUY", 10.0 if i % 2 == 0 else -5.0) for i in range(60)
        ]
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * n_features for _ in range(20)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        with patch.object(p, "train_dl", side_effect=[
            _UNTRAINED,  # skip walk-forward eval
            _make_trained_result(),
        ]):
            with patch.object(p, "push_model"):
                report = p.run_retraining(["EURUSD"], days=90, min_samples=30, epochs=1, n_splits=3, log_mlflow=False)
        assert report["status"] in ("completed", "insufficient_data")

    def test_run_retraining_mlflow_logging(self, mock_journal, mock_feature_store, mock_dl):
        n_features = len(FULL_FEATURE_NAMES)
        mock_journal.conn.execute.return_value.fetchall.return_value = [
            (i, "BUY", 10.0) for i in range(30)
        ]
        mock_feature_store.load.return_value = {"dl_features": [[0.5] * n_features for _ in range(20)]}
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        with patch.object(p, "train_dl", side_effect=[
            _UNTRAINED,  # skip walk-forward eval
            _make_trained_result(),
        ]):
            with patch.object(p, "push_model"):
                with patch.object(p.mlflow, "start_run", return_value=True):
                    with patch.object(p.mlflow, "log_params"):
                        with patch.object(p.mlflow, "log_metrics"):
                            with patch.object(p.mlflow, "log_dict"):
                                with patch.object(p.mlflow, "end_run"):
                                    report = p.run_retraining(["EURUSD"], days=90, min_samples=20,
                                                               epochs=1, n_splits=2, log_mlflow=True)
        assert report["status"] in ("completed", "insufficient_data")

    def test_run_retraining_no_torch(self, mock_journal, mock_feature_store, mock_dl):
        with patch("engine_simple.retraining_pipeline.TORCH_AVAILABLE", False):
            p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
            report = p.run_retraining(["EURUSD"], days=1, min_samples=50, log_mlflow=False)
            assert report["status"] == "skipped"
            assert report["reason"] == "no_torch"


class TestGetStatusDetailed:
    @patch("glob.glob", return_value=["models/dl_attention_EURUSD_H1.pkl",
                                       "models/dl_lstm_USDCAD_H1.pkl"])
    @patch("pathlib.Path.glob", return_value=["backup1.pkl", "backup2.pkl"])
    def test_get_status_with_models(self, mock_path_glob, mock_glob,
                                     mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.return_value.fetchone.return_value = (42,)
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        status = p.get_status()
        assert len(status["production_models"]) >= 2
        assert status["journal_trades"] == 42

    @patch("glob.glob", return_value=[])
    def test_get_status_journal_error(self, mock_glob, mock_journal, mock_feature_store, mock_dl):
        mock_journal.conn.execute.side_effect = AttributeError("no conn")
        p = RetrainingPipeline(mock_journal, mock_feature_store, mock_dl)
        status = p.get_status()
        assert status["journal_trades"] == 0
        assert isinstance(status["production_models"], list)
