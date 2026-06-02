"""Automated retraining pipeline — SQLite journal → walk-forward → MLflow → production

Usage:
    from engine_simple.retraining_pipeline import RetrainingPipeline
    pipeline = RetrainingPipeline(trade_journal, feature_store, dl_ensemble)
    pipeline.run_retraining(symbols=["USDCAD", "GBPUSD", "USDCHF"])
"""
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from engine_simple.ml_features import FULL_FEATURE_NAMES
from engine_simple.mlflow_tracker import MLflowTracker

logger = logging.getLogger("robot.retraining")

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    import torch.optim as optim
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None  # type: ignore[assignment,no-redef]
    nn = None     # type: ignore[no-redef]
    F = None      # type: ignore[no-redef]
    optim = None  # type: ignore[no-redef]


class RetrainingPipeline:
    """Pipeline de retraining automatique: journal SQLite → walk-forward → MLflow.

    Flux:
    1. Charge les trades fermés depuis trade_journal.db
    2. Récupère les features associées depuis FeatureStore
    3. Construit dataset avec walk-forward splits (temporel)
    4. Entraîne DL LSTM avec validation
    5. Log métriques sur MLflow
    6. Backup l'ancien modèle, push le nouveau
    """
    SEQUENCE_LENGTH = 20
    N_FEATURES = len(FULL_FEATURE_NAMES)

    def __init__(self, trade_journal, feature_store, dl_ensemble, config: dict | None = None):
        self.journal = trade_journal
        self.feature_store = feature_store
        self.dl = dl_ensemble
        self.config = config or {}
        self.mlflow = MLflowTracker(
            experiment_name="mt5_ftmo_retraining",
            tracking_uri=os.environ.get("MLFLOW_TRACKING_URI"),
        )
        self._model_dir = Path("models")
        self._model_dir.mkdir(exist_ok=True)
        self._backup_dir = self._model_dir / "backup"
        self._backup_dir.mkdir(exist_ok=True)

    def build_dataset(self, symbols: list[str], days: int = 90,
                      min_samples: int = 50) -> dict[str, dict]:
        """Charge les trades fermés + features → datasets par symbole.

        Retourne {symbol: {"X": np.ndarray, "y": np.ndarray, "tickets": list}}
        pour chaque symbole avec assez d'échantillons.
        """
        result = {}
        for symbol in symbols:
            X_list, y_list, tickets = [], [], []
            cutoff = datetime.utcnow() - timedelta(days=days)
            trades = self._get_closed_trades(symbol, cutoff)
            if len(trades) < min_samples:
                logger.info("  [RETRAIN] %s: %d trades < %d, skip", symbol, len(trades), min_samples)
                continue
            for ticket, _direction, profit in trades:
                meta = self.feature_store.load(ticket)
                features = meta.get("dl_features")
                if features is None or len(features) != self.SEQUENCE_LENGTH:
                    continue
                arr = np.array(features, dtype=np.float32)
                if arr.shape != (self.SEQUENCE_LENGTH, self.N_FEATURES):
                    continue
                X_list.append(arr)
                label = 1.0 if profit > 0 else 0.0
                y_list.append(label)
                tickets.append(ticket)
            if len(X_list) < min_samples:
                logger.info("  [RETRAIN] %s: %d valid samples < %d, skip", symbol, len(X_list), min_samples)
                continue
            X = np.array(X_list, dtype=np.float32)
            y = np.array(y_list, dtype=np.float32)
            result[symbol] = {"X": X, "y": y, "tickets": tickets, "n": len(X)}
            logger.info("  [RETRAIN] %s: %d samples loaded (%d wins, %.1f%% WR)",
                        symbol, len(X), int(y.sum()), float(y.mean() * 100))
        return result

    def _get_closed_trades(self, symbol: str, cutoff: datetime) -> list[tuple[int, str, float]]:
        """Récupère les trades fermés depuis la journal SQLite."""
        try:
            cur = self.journal.conn.execute(
                "SELECT id, direction, profit FROM trades WHERE symbol=? AND time_close>=? AND profit IS NOT NULL",
                (symbol, cutoff.isoformat()),
            )
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
        except Exception as e:
            logger.warning("  [RETRAIN] Query error for %s: %s", symbol, e)
            return []

    def walk_forward_split(self, X: np.ndarray, y: np.ndarray,
                           n_splits: int = 5) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """Walk-forward splits temporels (respecte l'ordre chronologique)."""
        n = len(X)
        fold_size = n // (n_splits + 1)
        splits = []
        for i in range(1, n_splits + 1):
            train_end = i * fold_size
            val_end = min((i + 1) * fold_size, n)
            if train_end >= n or val_end - train_end < 10:
                break
            X_train, y_train = X[:train_end], y[:train_end]
            X_val, y_val = X[train_end:val_end], y[train_end:val_end]
            splits.append((X_train, y_train, X_val, y_val))
        return splits

    def train_dl(self, symbol: str, X: np.ndarray, y: np.ndarray,
                 val_split: float = 0.2, epochs: int = 10) -> dict:
        """Entraîne le DL LSTM avec validation split. Retourne les métriques."""
        if not TORCH_AVAILABLE:
            return {"error": "PyTorch not available", "trained": False}

        # Local model definition (avoid stub from dl_ensemble when torch is unavailable)
        n_features = self.N_FEATURES
        class _SelfAttention(nn.Module):
            def __init__(self, hidden_size, num_heads=4):
                super().__init__()
                self.num_heads = num_heads
                self.head_dim = hidden_size // num_heads
                self.qkv = nn.Linear(hidden_size, hidden_size * 3)
                self.proj = nn.Linear(hidden_size, hidden_size)
            def forward(self, x):
                B, T, D = x.shape
                qkv = self.qkv(x).reshape(B, T, 3, self.num_heads, self.head_dim)
                q, k, v = qkv.unbind(2)
                attn = (q @ k.transpose(-2, -1)) * (self.head_dim ** -0.5)
                attn = F.softmax(attn, dim=-1)
                out = (attn @ v).transpose(1, 2).reshape(B, T, D)
                return self.proj(out)

        class _AttentionLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(n_features, 64, 2, batch_first=True, dropout=0.2)
                self.attention = _SelfAttention(64, 4)
                self.layer_norm = nn.LayerNorm(64)
                self.dropout = nn.Dropout(0.3)
                self.fc1 = nn.Linear(64, 32)
                self.fc2 = nn.Linear(32, 1)
                self.sigmoid = nn.Sigmoid()
            def forward(self, x):
                out, _ = self.lstm(x)
                out = self.attention(out)
                out = self.layer_norm(out)
                out = out[:, -1, :]
                out = self.dropout(out)
                out = self.fc1(out)
                out = self.fc2(out)
                return self.sigmoid(out)

        n = len(X)
        n_val = max(1, int(n * val_split))
        idx = np.arange(n)
        np.random.shuffle(idx)
        X_shuf, y_shuf = X[idx], y[idx]

        X_train, y_train = X_shuf[:-n_val], y_shuf[:-n_val]
        X_val, y_val = X_shuf[-n_val:], y_shuf[-n_val:]

        model = _AttentionLSTM()
        model.train()
        criterion = nn.BCELoss()
        optimizer = optim.Adam(model.parameters(), lr=0.001)

        best_val_loss = float("inf")
        best_state = None
        patience, patience_counter = 5, 0

        for epoch in range(epochs):
            model.train()
            perm = np.random.permutation(len(X_train))
            for i in range(0, len(perm), 32):
                batch_idx = perm[i:i + 32]
                bx = torch.FloatTensor(X_train[batch_idx])
                by = torch.FloatTensor(y_train[batch_idx]).view(-1, 1)
                optimizer.zero_grad()
                out = model(bx)
                loss = criterion(out, by)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()

            model.eval()
            with torch.no_grad():
                val_out = model(torch.FloatTensor(X_val))
                val_loss = criterion(val_out, torch.FloatTensor(y_val).view(-1, 1)).item()
                val_preds = (val_out > 0.5).float().view(-1).numpy()
                val_acc = (val_preds == y_val).mean()

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    logger.info("  [RETRAIN] %s: early stopping at epoch %d", symbol, epoch + 1)
                    break

        if best_state:
            model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            train_out = model(torch.FloatTensor(X_train))
            train_acc = ((train_out > 0.5).float().view(-1).numpy() == y_train).mean()
            all_out = model(torch.FloatTensor(X))
            all_preds = (all_out > 0.5).float().view(-1).numpy()
            all_acc = (all_preds == y).mean()

        metrics = {
            "train_loss": float(best_val_loss),
            "val_loss": float(best_val_loss),
            "train_accuracy": float(train_acc),
            "val_accuracy": float(val_acc),
            "overall_accuracy": float(all_acc),
            "n_train": len(X_train),
            "n_val": len(X_val),
            "epochs_trained": epoch + 1,
            "trained": True,
        }
        logger.info("  [RETRAIN] %s: val_acc=%.3f, overall=%.3f, early_stop=%d",
                    symbol, val_acc, all_acc, epoch + 1)
        return {"model": model, "metrics": metrics}

    def push_model(self, symbol: str, model, metrics: dict):
        """Backup l'ancien modèle, sauvegarde le nouveau."""
        if not TORCH_AVAILABLE:
            logger.warning("  [RETRAIN] PyTorch unavailable, cannot save model")
            return
        model_path = self._model_dir / f"dl_attention_{symbol}_H1.pkl"
        if model_path.exists():
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_path = self._backup_dir / f"dl_attention_{symbol}_H1_{timestamp}.pkl"
            shutil.copy2(str(model_path), str(backup_path))
            logger.info("  [RETRAIN] Backup: %s → %s", model_path.name, backup_path.name)
        torch.save(model.state_dict(), str(model_path))
        logger.info("  [RETRAIN] Saved %s (val_acc=%.3f)", model_path, metrics.get("val_accuracy", 0))

    def run_retraining(self, symbols: list[str], days: int = 90,
                       min_samples: int = 50, epochs: int = 10,
                       n_splits: int = 5, log_mlflow: bool = True) -> dict:
        """Exécute le pipeline complet. Retourne le rapport."""
        if not TORCH_AVAILABLE:
            logger.warning("PyTorch not available, retraining pipeline disabled")
            return {"status": "skipped", "reason": "no_torch"}

        report: dict = {"status": "completed", "symbols": {}, "timestamp": datetime.utcnow().isoformat()}
        run_name = f"retrain_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        if log_mlflow and self.mlflow.enabled:
            self.mlflow.start_run(run_name=run_name, tags={"purpose": "retraining", "days": str(days)})
            self.mlflow.log_params({
                "days": days, "min_samples": min_samples, "epochs": epochs,
                "n_splits": n_splits, "symbols": ",".join(symbols),
            })

        datasets = self.build_dataset(symbols, days=days, min_samples=min_samples)
        if not datasets:
            report["status"] = "insufficient_data"
            logger.info("  [RETRAIN] No symbol has enough data")
            if log_mlflow and self.mlflow.enabled:
                self.mlflow.log_metrics({"samples_total": 0})
                self.mlflow.end_run()
            return report

        total_samples = 0
        for symbol, data in datasets.items():
            total_samples += data["n"]
            logger.info("  [RETRAIN] === %s: %d samples ===", symbol, data["n"])

            splits = self.walk_forward_split(data["X"], data["y"], n_splits=n_splits)
            if splits:
                wf_accuracies = []
                for i, (Xtr, ytr, Xva, yva) in enumerate(splits):
                    dl_key = f"{symbol}_wf_{i}"
                    if len(Xtr) < 32 or len(Xva) < 10:
                        continue
                    result = self.train_dl(symbol, Xtr, ytr, val_split=0.0, epochs=epochs)
                    if result.get("trained"):
                        model = result["model"]
                        model.eval()
                        with torch.no_grad():
                            out = model(torch.FloatTensor(Xva))
                            acc = ((out > 0.5).float().view(-1).numpy() == yva).mean()
                        wf_accuracies.append(float(acc))
                        logger.info("    Fold %d: val_acc=%.3f", i, acc)
                if wf_accuracies:
                    metrics = {"wf_accuracy_mean": float(np.mean(wf_accuracies)),
                               "wf_accuracy_std": float(np.std(wf_accuracies)),
                               "wf_folds": len(wf_accuracies)}
                    logger.info("  [RETRAIN] %s: WF acc %.3f ± %.3f (%d folds)",
                                symbol, metrics["wf_accuracy_mean"], metrics["wf_accuracy_std"], len(wf_accuracies))

            result = self.train_dl(symbol, data["X"], data["y"], epochs=epochs)
            if not result.get("trained"):
                report["symbols"][symbol] = {"status": "train_failed"}
                continue

            model = result["model"]
            self.push_model(symbol, model, result["metrics"])

            if log_mlflow and self.mlflow.enabled:
                self.mlflow.log_metrics({
                    f"{symbol}_val_accuracy": result["metrics"]["val_accuracy"],
                    f"{symbol}_overall_accuracy": result["metrics"]["overall_accuracy"],
                    f"{symbol}_n_train": result["metrics"]["n_train"],
                })

            sym_report = {
                "status": "trained",
                "samples": data["n"],
                "val_accuracy": result["metrics"]["val_accuracy"],
                "overall_accuracy": result["metrics"]["overall_accuracy"],
            }
            report["symbols"][symbol] = sym_report

            if "wf_accuracies" in locals() and wf_accuracies:
                sym_report["wf_accuracy_mean"] = float(np.mean(wf_accuracies))
                sym_report["wf_accuracy_std"] = float(np.std(wf_accuracies))

        if log_mlflow and self.mlflow.enabled:
            self.mlflow.log_metrics({"samples_total": total_samples, "symbols_trained": len(report["symbols"])})
            self.mlflow.log_dict(report, "retraining_report.json")
            self.mlflow.end_run()

        report["total_samples"] = total_samples
        report["symbols_trained"] = len(report["symbols"])
        return report

    def get_status(self) -> dict:
        """Retourne l'état actuel du pipeline."""
        import glob
        models = sorted(glob.glob("models/dl_attention_*.pkl") + glob.glob("models/dl_lstm_*.pkl"))
        backups = sorted(glob.glob("models/backup/dl_*.pkl"))
        journal_count = 0
        try:
            cur = self.journal.conn.execute("SELECT COUNT(*) FROM trades")
            journal_count = cur.fetchone()[0]
        except (sqlite3.Error, AttributeError):
            pass
        return {
            "production_models": [os.path.basename(m) for m in models],
            "backup_models": len(backups),
            "journal_trades": journal_count,
            "mlflow_enabled": self.mlflow.enabled,
            "torch_available": TORCH_AVAILABLE,
        }
