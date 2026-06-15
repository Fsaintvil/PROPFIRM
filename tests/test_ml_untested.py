"""Smoke tests pour les 3 modules ML non testés.

Ces modules sont marqués comme non-fonctionnels (ML désactivé), mais on vérifie
au minimum que les imports fonctionnent sans crash.
"""
import pytest


class TestAnticipationSmoke:
    """Smoke tests pour anticipation.py — constructeur sans argument."""

    def test_module_importable(self):
        import engine_simple.anticipation
        assert engine_simple.anticipation is not None

    def test_anticipation_class_accessible(self):
        from engine_simple.anticipation import AnticipationEngine
        assert AnticipationEngine is not None

    def test_anticipation_init(self):
        """Constructeur AnticipationEngine() — pas de paramètres."""
        from engine_simple.anticipation import AnticipationEngine
        try:
            engine = AnticipationEngine()
            assert engine is not None
            assert engine._initialized is False
        except Exception as e:
            if "No module named 'torch'" in str(e):
                pytest.skip("PyTorch not installed — skip")
            raise


class TestMlflowTrackerSmoke:
    """Smoke tests pour mlflow_tracker.py."""

    def test_module_importable(self):
        import engine_simple.mlflow_tracker
        assert engine_simple.mlflow_tracker is not None

    def test_mlflow_tracker_class(self):
        from engine_simple.mlflow_tracker import MLflowTracker
        assert MLflowTracker is not None

    def test_mlflow_tracker_init(self):
        """Constructeur: MLflowTracker(experiment_name, tracking_uri)."""
        from engine_simple.mlflow_tracker import MLflowTracker
        try:
            tracker = MLflowTracker(experiment_name="test_exp", tracking_uri="")
            assert tracker is not None
        except Exception as e:
            if "No module named 'mlflow'" in str(e):
                pytest.skip("MLflow not installed — skip")
            raise

    def test_mlflow_tracker_logging(self):
        """Vérifie que les méthodes ne crashent pas même sans mlflow."""
        from engine_simple.mlflow_tracker import MLflowTracker
        try:
            tracker = MLflowTracker(experiment_name="test_exp", tracking_uri="")
            tracker.log_params({"test": 1})
            tracker.log_metrics({"test": 1.0})
            assert tracker is not None
        except Exception as e:
            if "No module named 'mlflow'" in str(e):
                pytest.skip("MLflow not installed — skip")
            raise


class TestRetrainingPipelineSmoke:
    """Smoke tests pour retraining_pipeline.py."""

    def test_module_importable(self):
        import engine_simple.retraining_pipeline
        assert engine_simple.retraining_pipeline is not None

    def test_retraining_pipeline_class(self):
        from engine_simple.retraining_pipeline import RetrainingPipeline
        assert RetrainingPipeline is not None

    def test_retraining_pipeline_init(self):
        """Constructeur: RetrainingPipeline(trade_journal, feature_store, dl_ensemble, config)."""
        from engine_simple.retraining_pipeline import RetrainingPipeline
        try:
            pipeline = RetrainingPipeline(
                trade_journal=None,
                feature_store=None,
                dl_ensemble=None,
                config={},
            )
            assert pipeline is not None
        except Exception as e:
            if "No module named 'torch'" in str(e) or "No module named 'mlflow'" in str(e):
                pytest.skip("PyTorch/MLflow not installed — skip")
            raise
