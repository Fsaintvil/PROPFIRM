"""MLflow experiment tracking — optionnel, graceful fallback si mlflow non installé"""
import contextlib
import logging
import os
import tempfile

logger = logging.getLogger("robot.mlflow")

try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False


class MLflowTracker:
    """Wraps MLflow pour logger expériences, métriques, modèles.
    Graceful degradation: toutes les méthodes sont no-ops si mlflow absent.
    """
    def __init__(self, experiment_name: str = "mt5_ftmo", tracking_uri: str | None = None):
        self.experiment_name = experiment_name
        self._active_run = None
        if MLFLOW_AVAILABLE:
            uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI", "file:./mlruns")
            mlflow.set_tracking_uri(uri)
            try:
                mlflow.set_experiment(experiment_name)
            except Exception as e:
                logger.warning("MLflow set_experiment failed: %s", e)

    @property
    def enabled(self) -> bool:
        return MLFLOW_AVAILABLE

    def start_run(self, run_name: str | None = None, tags: dict | None = None) -> bool:
        if not MLFLOW_AVAILABLE:
            return False
        try:
            self._active_run = mlflow.start_run(run_name=run_name)
            if tags:
                mlflow.set_tags(tags)
            return True
        except Exception as e:
            logger.warning("MLflow start_run failed: %s", e)
            return False

    def end_run(self):
        if self._active_run and MLFLOW_AVAILABLE:
            with contextlib.suppress(Exception):
                mlflow.end_run()
            self._active_run = None

    def log_params(self, params: dict):
        if not MLFLOW_AVAILABLE:
            return
        try:
            mlflow.log_params(params)
        except Exception as e:
            logger.warning("MLflow log_params failed: %s", e)

    def log_metrics(self, metrics: dict, step: int | None = None):
        if not MLFLOW_AVAILABLE:
            return
        try:
            mlflow.log_metrics(metrics, step=step)
        except Exception as e:
            logger.warning("MLflow log_metrics failed: %s", e)

    def log_model(self, model, artifact_path: str, signature=None, input_example=None):
        """Log un modèle sklearn/pytorch. Type auto-détecté."""
        if not MLFLOW_AVAILABLE:
            return
        try:
            import mlflow.pytorch
            import mlflow.sklearn
            import sklearn
            import torch
            if isinstance(model, torch.nn.Module):
                mlflow.pytorch.log_model(model, artifact_path, signature=signature, input_example=input_example)
            elif isinstance(model, (sklearn.base.BaseEstimator,)):
                mlflow.sklearn.log_model(model, artifact_path, signature=signature, input_example=input_example)
            else:
                with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
                    import joblib
                    joblib.dump(model, f.name)
                    mlflow.log_artifact(f.name, artifact_path)
                    os.unlink(f.name)
        except Exception as e:
            logger.warning("MLflow log_model failed: %s", e)

    def log_artifact(self, local_path: str):
        if not MLFLOW_AVAILABLE:
            return
        try:
            mlflow.log_artifact(local_path)
        except Exception as e:
            logger.warning("MLflow log_artifact failed: %s", e)

    def log_dict(self, dictionary: dict, artifact_file: str):
        if not MLFLOW_AVAILABLE:
            return
        try:
            mlflow.log_dict(dictionary, artifact_file)
        except Exception as e:
            logger.warning("MLflow log_dict failed: %s", e)

    def get_best_run(self, metric_name: str, mode: str = "max") -> dict | None:
        if not MLFLOW_AVAILABLE:
            return None
        try:
            from mlflow.tracking import MlflowClient
            client = MlflowClient()
            exp = client.get_experiment_by_name(self.experiment_name)
            if not exp:
                return None
            runs = client.search_runs(
                experiment_ids=[exp.experiment_id],
                order_by=[f"metrics.{metric_name} {'DESC' if mode == 'max' else 'ASC'}"],
                max_results=1,
            )
            if runs:
                return dict(runs[0].data.metrics)
            return None
        except Exception:
            return None

    @staticmethod
    def log_autolog():
        """Active l'autologging pytorch + sklearn si disponible."""
        if MLFLOW_AVAILABLE:
            try:
                import mlflow.pytorch
                import mlflow.sklearn
                mlflow.pytorch.autolog()
                mlflow.sklearn.autolog()
            except Exception:
                pass
