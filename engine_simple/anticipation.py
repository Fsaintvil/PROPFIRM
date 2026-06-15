"""
Anticipation Engine v2 — Prédiction multi-timeframe 100+ features
=================================================================
Entraîne les modèles DL sur 16 ans × 4 timeframes pour anticiper
les mouvements de prix. Combine :
  - DL LSTM + Attention (100+ features, 60 timesteps)
  - Pattern Matching DTW + patterns chartistes
  - Contexte de structure multi-timeframe
  - Early stopping + LR scheduling + dropout tuning
"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("anticipation")

DATA_DIR = Path("data")
FEATURES_DIR = DATA_DIR / "features"
MODELS_DIR = Path("models")
CACHE_DIR = DATA_DIR / "cache"

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ============================================================
# 1. DATA PREPARATION — Multi-timeframe + 100+ features
# ============================================================

class AnticipationData:
    """Prépare les données pour l'entraînement DL (multi-timeframe, 100+ features)."""

    def __init__(self, symbol: str, sequence_length: int = 60, tf: str = "H1"):
        self.symbol = symbol
        self.seq_len = sequence_length
        self.tf = tf
        self.features_df: pd.DataFrame | None = None
        self._means = None
        self._stds = None

    def load(self) -> "AnticipationData":
        """Charge les features depuis le fichier parquet du timeframe choisi."""
        path = FEATURES_DIR / f"{self.symbol}_{self.tf}_features.parquet"
        if not path.exists():
            logger.warning(f"Fichier features introuvable: {path}, fallback H1")
            path = FEATURES_DIR / f"{self.symbol}_H1_features.parquet"
            if not path.exists():
                logger.error(f"Aucun fichier features trouvé pour {self.symbol}")
                return self
        self.features_df = pd.read_parquet(path)
        logger.debug(f"  {self.symbol} {self.tf}: {len(self.features_df)} candles chargées, "
                     f"{len(self.features_df.columns)} colonnes")
        return self

    def prepare_target(self, horizon: int = 12) -> pd.DataFrame:
        """
        Prépare la target : direction du prix dans `horizon` bougies.
        horizon=12 pour H1 = 12h, pour M15 = 3h, pour H4 = 48h
        """
        df = self.features_df.copy()
        future_close = df["close"].shift(-horizon)
        df["target"] = (future_close > df["close"]).astype(int)
        df["target_return"] = (future_close - df["close"]) / df["close"] * 100
        df["target"] = df["target"].fillna(0).astype(int)
        self.features_df = df
        return df

    def get_feature_columns(self) -> list[str]:
        """Colonnes à utiliser comme features (exclut les non-numériques)."""
        exclude = {"timestamp", "symbol", "target", "target_return",
                   "open", "high", "low", "close", "volume", "spread", "real_volume",
                   "trend_strength", "month", "week", "year", "day", "price_bucket"}
        return [c for c in self.features_df.columns if c not in exclude]

    def to_sequences(self, max_samples: int = 30000) -> tuple[np.ndarray, np.ndarray]:
        """Convertit en séquences pour le LSTM avec échantillonnage intelligent."""
        if self.features_df is None or "target" not in self.features_df.columns:
            self.prepare_target()

        df = self.features_df.dropna().reset_index(drop=True)
        feat_cols = self.get_feature_columns()
        data = df[feat_cols].values.astype(np.float32)
        targets = df["target"].values

        # Normalisation z-score robuste
        self._means = np.nanmean(data, axis=0)
        self._stds = np.nanstd(data, axis=0)
        self._stds[self._stds < 1e-10] = 1.0
        data = (data - self._means) / self._stds
        data = np.clip(data, -10, 10)

        # Construire les séquences avec échantillonnage uniforme
        n_possible = len(data) - self.seq_len
        if n_possible <= 0:
            return np.array([]), np.array([])

        # Déterminer le step pour atteindre ~max_samples
        step = max(1, n_possible // max_samples)

        X, y = [], []
        for i in range(0, n_possible, step):
            X.append(data[i:i + self.seq_len])
            y.append(targets[i + self.seq_len])

        # Si trop peu de séquences, prendre aussi par la fin
        if len(X) < max_samples // 2 and step > 1:
            extra_start = max(0, n_possible - max_samples // 2)
            for i in range(extra_start, n_possible, 1):
                if i % step == 0:
                    continue  # déjà pris
                X.append(data[i:i + self.seq_len])
                y.append(targets[i + self.seq_len])
                if len(X) >= max_samples:
                    break

        return np.array(X), np.array(y)

    def get_recent_sequence(self, n: int = 60) -> np.ndarray | None:
        """Dernière séquence pour prédiction en temps réel."""
        if self.features_df is None or len(self.features_df) < n + 1:
            return None
        df = self.features_df.tail(n + 5).dropna()
        if len(df) < n:
            return None
        feat_cols = self.get_feature_columns()
        data = df[feat_cols].values.astype(np.float32)[-n:]
        if self._means is not None and self._stds is not None:
            data = (data - self._means) / self._stds
            data = np.clip(data, -10, 10)
        return np.expand_dims(data, axis=0)


# ============================================================
# 2. DL TRAINER — Entraînement avec Early Stopping + Tuning
# ============================================================

class DLModelWrapper:
    """Wrapper DL avec entraînement optimisé (early stopping, LR sched, dropout tuning)."""

    def __init__(self, symbol: str, input_size: int = 100):
        self.symbol = symbol
        self.input_size = input_size
        self.model = None
        self._trained = False
        self._accuracy = 0.0
        self._best_threshold = 0.5

    def build(self, hidden_size: int = 96, num_layers: int = 3, dropout: float = 0.3):
        """Construit le modèle LSTM + Attention avec architecture optimisée."""
        if not TORCH_AVAILABLE:
            logger.warning("PyTorch non dispo")
            return self

        from engine_simple.dl_ensemble import AttentionLSTMNet
        self.model = AttentionLSTMNet(
            input_size=self.input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            num_heads=6,
            dropout=dropout,
        )
        return self

    def train(self, X: np.ndarray, y: np.ndarray, epochs: int = 30,
              batch_size: int = 512, val_split: float = 0.2,
              use_early_stopping: bool = True) -> dict:
        """
        Entraîne avec early stopping, LR scheduling, et optimisation du seuil.
        """
        if not TORCH_AVAILABLE or self.model is None:
            return {"trained": False, "reason": "PyTorch non dispo"}

        from torch.utils.data import DataLoader, TensorDataset

        n_val = int(len(X) * val_split)
        indices = np.random.permutation(len(X))
        train_idx, val_idx = indices[n_val:], indices[:n_val]

        X_train, y_train = X[train_idx], y[train_idx]
        X_val, y_val = X[val_idx], y[val_idx]

        X_train_t = torch.FloatTensor(X_train)
        y_train_t = torch.FloatTensor(y_train).unsqueeze(1)
        X_val_t = torch.FloatTensor(X_val)
        y_val_t = torch.FloatTensor(y_val).unsqueeze(1)

        train_dataset = TensorDataset(X_train_t, y_train_t)
        val_dataset = TensorDataset(X_val_t, y_val_t)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=0)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, num_workers=0)

        # AdamW avec weight decay pour régularisation
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=0.002, weight_decay=5e-5)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", patience=4, factor=0.5, min_lr=1e-5
        )
        criterion = nn.BCELoss()

        best_val_loss = float("inf")
        best_accuracy = 0.0
        patience_counter = 0
        max_patience = 8
        history = {"train_loss": [], "val_loss": [], "val_acc": [], "lr": []}

        logger.info(f"  {self.symbol}: {len(X_train)} séquences, {epochs} epochs max, "
                    f"{self.input_size} features")

        for epoch in range(epochs):
            # Train
            self.model.train()
            train_loss = 0.0
            for Xb, yb in train_loader:
                optimizer.zero_grad()
                y_pred = self.model(Xb)
                loss = criterion(y_pred, yb)
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                train_loss += loss.item()

            # Validation
            self.model.eval()
            val_loss = 0.0
            correct = 0
            total = 0
            with torch.no_grad():
                for Xb, yb in val_loader:
                    y_pred = self.model(Xb)
                    loss = criterion(y_pred, yb)
                    val_loss += loss.item()
                    predicted = (y_pred > 0.5).float()
                    correct += (predicted == yb).sum().item()
                    total += yb.size(0)

            train_loss /= len(train_loader)
            val_loss /= len(val_loader)
            val_acc = correct / total if total > 0 else 0
            current_lr = optimizer.param_groups[0]["lr"]
            scheduler.step(val_loss)

            history["train_loss"].append(train_loss)
            history["val_loss"].append(val_loss)
            history["val_acc"].append(val_acc)
            history["lr"].append(current_lr)

            if (epoch + 1) % 5 == 0 or epoch == 0:
                logger.info(f"    Epoch {epoch+1}/{epochs}: loss={train_loss:.4f}, "
                            f"val_loss={val_loss:.4f}, val_acc={val_acc:.4f}, lr={current_lr:.6f}")

            # Early stopping
            if use_early_stopping:
                if val_loss < best_val_loss - 1e-4:
                    best_val_loss = val_loss
                    best_accuracy = val_acc
                    patience_counter = 0
                else:
                    patience_counter += 1
                    if patience_counter >= max_patience:
                        logger.info(f"    Early stopping à l'epoch {epoch+1}")
                        break

        # Optimisation du seuil de décision sur la validation
        self._optimize_threshold(X_val_t, y_val_t)
        self._accuracy = best_accuracy if best_accuracy > 0 else history["val_acc"][-1]
        self._trained = True

        logger.info(f"  ✓ {self.symbol}: accuracy={self._accuracy:.4f}, "
                    f"best_threshold={self._best_threshold:.4f}")

        return {
            "trained": True,
            "accuracy": float(self._accuracy),
            "best_threshold": float(self._best_threshold),
            "history": {k: [float(v) for v in vals] for k, vals in history.items()},
            "epochs_completed": int(epoch + 1),
        }

    def _optimize_threshold(self, X_val: torch.Tensor, y_val: torch.Tensor):
        """Trouve le meilleur seuil de décision (0.3-0.7) sur la validation."""
        self.model.eval()
        with torch.no_grad():
            probs = self.model(X_val).numpy().flatten()

        best_acc = 0.0
        best_thresh = 0.5
        for thresh in np.arange(0.3, 0.71, 0.02):
            preds = (probs > thresh).astype(float)
            acc = float(np.mean(preds == y_val.numpy().flatten()))
            if acc > best_acc:
                best_acc = acc
                best_thresh = float(thresh)

        self._best_threshold = best_thresh
        self._accuracy = max(self._accuracy, best_acc)

    def predict(self, X_seq: np.ndarray) -> dict:
        """Prédit la direction future avec seuil optimisé."""
        if not TORCH_AVAILABLE or self.model is None or not self._trained:
            return {"direction": "NEUTRE", "confidence": 0.0}

        self.model.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X_seq)
            prob = self.model(X_t).item()

        threshold = max(0.5, self._best_threshold)  # symétrique: jamais <0.5 pour éviter biais HAUSSE
        direction = "HAUSSE" if prob > threshold else "BAISSE" if prob < (1 - threshold) else "NEUTRE"
        confidence = max(abs(prob - 0.5) * 2, 0.0) if direction != "NEUTRE" else 0.0

        return {
            "direction": direction,
            "confidence": round(min(confidence, 1.0), 4),
            "probability": round(prob, 4),
            "threshold": round(threshold, 3),
        }

    def save(self, path: str | None = None, norm_stats: dict | None = None):
        """Sauvegarde le modèle + stats + seuil.

        Toutes les valeurs sont converties en types Python natifs
        pour compatibilité avec torch.load(weights_only=True).
        """
        if not TORCH_AVAILABLE or self.model is None:
            return
        path = path or str(MODELS_DIR / f"anticipation_v2_{self.symbol}.pkl")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        save_dict = {
            "model_state": self.model.state_dict(),
            "input_size": int(self.input_size),
            "accuracy": float(self._accuracy),
            "best_threshold": float(self._best_threshold),
        }
        if norm_stats:
            # Convertir tous les tableaux numpy en listes Python
            clean_stats = {}
            for k, v in norm_stats.items():
                if hasattr(v, "tolist"):
                    clean_stats[k] = v.tolist()
                elif isinstance(v, list):
                    clean_stats[k] = v
                else:
                    clean_stats[k] = v
            save_dict["norm_stats"] = clean_stats
        torch.save(save_dict, path)
        logger.info(f"  Modèle v2 sauvegardé: {path} (acc={self._accuracy:.4f})")

    def load(self, path: str | None = None):
        """Charge un modèle v2 sauvegardé."""
        if not TORCH_AVAILABLE:
            return self
        path = path or str(MODELS_DIR / f"anticipation_v2_{self.symbol}.pkl")
        # Fallback sur l'ancien nom
        if not Path(path).exists():
            path_old = str(MODELS_DIR / f"anticipation_{self.symbol}.pkl")
            if Path(path_old).exists():
                path = path_old
            else:
                logger.warning(f"Modèle introuvable: {path}")
                return self

        try:
            saved = torch.load(path, map_location="cpu", weights_only=True)
        except Exception:
            # Fallback pickle sécurisé : ces modèles sont générés par notre propre pipeline
            # de retraining et stockés localement. Les modèles legacy contiennent des types
            # numpy (scalar, dtype, ndarray, etc.) non supportés par weights_only=True.
            # TODO: migrer le saving pipeline pour utiliser des types Python natifs.
            logger.debug(f"Fallback pickle pour {path} (numpy types non supportés par weights_only)")
            saved = torch.load(path, map_location="cpu", weights_only=False)
        if isinstance(saved, dict):
            self.input_size = saved.get("input_size", self.input_size)
            self._accuracy = saved.get("accuracy", 0.0)
            self._best_threshold = saved.get("best_threshold", 0.5)
            self.build(hidden_size=96, num_layers=3, dropout=0.3)
            self.model.load_state_dict(saved["model_state"])
            self._norm_stats = saved.get("norm_stats")
        else:
            self.build(hidden_size=64, num_layers=2, dropout=0.3)
            self.model.load_state_dict(saved)
        self._trained = True
        logger.info(f"  Modèle chargé: {path} (acc={self._accuracy:.4f})")
        return self


# ============================================================
# 3. ANTICIPATION ENGINE — Intégration finale v2
# ============================================================

class AnticipationEngine:
    """
    Moteur d'anticipation complet v2.
    Combine DL + Pattern DTW + Chartistes + Structure multi-TF.
    """

    def __init__(self):
        self.dl_models: dict[str, DLModelWrapper] = {}
        self._features_cache: dict[str, AnticipationData] = {}  # évite rechargement parquet à chaque cycle
        self._initialized = False

    def initialize(self, retrain: bool = False):
        """Initialise ou entraîne les modèles pour tous les symboles."""
        logger.info("Initialisation de l'Anticipation Engine v2...")

        from engine_simple.market_memory import MarketMemory
        self.market_memory = MarketMemory().load_all()

        for symbol in SYMBOLS:
            data = AnticipationData(symbol, sequence_length=60).load()
            if data.features_df is None:
                continue

            # Cache pour éviter le rechargement du parquet à chaque cycle
            self._features_cache[symbol] = data

            feat_cols = data.get_feature_columns()
            logger.info(f"  {symbol}: {len(feat_cols)} features disponibles")
            model = DLModelWrapper(symbol, input_size=len(feat_cols))

            model_path = MODELS_DIR / f"anticipation_v2_{symbol}.pkl"
            old_path = MODELS_DIR / f"anticipation_{symbol}.pkl"
            if (model_path.exists() or old_path.exists()) and not retrain:
                model.build().load(str(model_path) if model_path.exists() else str(old_path))
            else:
                logger.info(f"  Entraînement nouveau modèle v2 pour {symbol}...")
                data.prepare_target(horizon=12)
                X, y = data.to_sequences(max_samples=30000)
                if len(X) > 1000:
                    model.build(hidden_size=96, num_layers=3, dropout=0.3)
                    model.train(X, y, epochs=30)
                    norm_stats = {
                        "means": data._means.tolist() if hasattr(data, "_means") else None,
                        "stds": data._stds.tolist() if hasattr(data, "_stds") else None,
                    }
                    model.save(norm_stats=norm_stats)
                else:
                    logger.warning(f"  Pas assez de données pour {symbol}: {len(X)} séquences")

            self.dl_models[symbol] = model

        self._initialized = True
        logger.info(f"Anticipation Engine v2 prêt: {len(self.dl_models)} symboles")

        # Afficher un résumé des accuracies
        for s, m in self.dl_models.items():
            if m._trained:
                logger.info(f"  {s}: accuracy={m._accuracy:.4f}, threshold={m._best_threshold:.3f}")

    def anticipate(self, symbol: str, current_price: float,
                   recent_h1: pd.DataFrame | None = None,
                   use_dtw: bool = False,
                   tf: str = "H1") -> dict:
        """
        Anticipe le mouvement à venir.
        Retourne un score d'anticipation complet (DL + Pattern + Structure).
        """
        dl_signal = {"direction": "NEUTRE", "confidence": 0}
        pattern_signal = {"signal": "NEUTRE", "confidence": 0}
        structure_context = {"niveaux_proches": [], "mtf_alignment": {}}

        # 1. DL Prediction (100+ features × 60 timesteps)
        model = self.dl_models.get(symbol)
        if model and model._trained:
            # Utiliser le cache plutôt que recharger le parquet à chaque cycle
            data = self._features_cache.get(symbol)
            if data is None:
                data = AnticipationData(symbol, tf=tf).load()
            if data and data.features_df is not None:
                seq = data.get_recent_sequence(60)
                if seq is not None:
                    dl_signal = model.predict(seq)

        # 2. Pattern Matching v2 (DTW + chartistes)
        if hasattr(self, "market_memory") and recent_h1 is not None:
            pattern_result = self.market_memory.get_pattern_context(
                symbol, recent_h1, use_dtw=use_dtw, tf=tf
            )
            pattern_signal = {
                "signal": pattern_result.get("signal", "NEUTRE"),
                "confidence": pattern_result.get("confidence", 0),
                "chart_patterns": pattern_result.get("chart_patterns", []),
                "boosted": pattern_result.get("boosted", False),
            }

        # 3. Structure multi-timeframe
        if hasattr(self, "market_memory"):
            structure_context = {
                "niveaux_proches": self.market_memory.get_nearby_levels(symbol, current_price, 0.3),
                "mtf_alignment": self.market_memory.get_mtf_alignment(symbol, current_price),
            }

        # 4. Consensus scoring v2 (3 sources)
        directions = []
        weights = []

        if dl_signal["direction"] != "NEUTRE":
            directions.append(dl_signal["direction"])
            weights.append(dl_signal["confidence"] * 2.5)

        if pattern_signal["signal"] != "NEUTRE":
            directions.append(pattern_signal["signal"])
            w = pattern_signal["confidence"]
            if pattern_signal.get("boosted"):
                w *= 1.3  # Boost si accord avec patterns chartistes
            weights.append(w)

        # Vote pondéré
        if directions:
            hausse_weight = sum(w for d, w in zip(directions, weights) if d == "HAUSSE")
            baisse_weight = sum(w for d, w in zip(directions, weights) if d == "BAISSE")
            total_weight = hausse_weight + baisse_weight

            if total_weight > 0:
                if hausse_weight > baisse_weight:
                    consensus_dir = "HAUSSE"
                    consensus_conf = hausse_weight / total_weight
                elif baisse_weight > hausse_weight:
                    consensus_dir = "BAISSE"
                    consensus_conf = baisse_weight / total_weight
                else:
                    consensus_dir = "NEUTRE"
                    consensus_conf = 0
            else:
                consensus_dir = "NEUTRE"
                consensus_conf = 0
        else:
            consensus_dir = "NEUTRE"
            consensus_conf = 0

        return {
            "symbol": symbol,
            "current_price": current_price,
            "consensus": {
                "direction": consensus_dir,
                "confidence": round(consensus_conf, 3),
                "strength": "FORT" if consensus_conf > 0.7 else
                           "MOYEN" if consensus_conf > 0.55 else "FAIBLE",
            },
            "dl": dl_signal,
            "pattern": pattern_signal,
            "structure": structure_context,
        }

    def get_summary(self) -> dict:
        """Résumé de l'état de l'anticipation."""
        return {
            "symbols": list(self.dl_models.keys()),
            "trained": {s: m._trained for s, m in self.dl_models.items()},
            "accuracies": {s: m._accuracy for s, m in self.dl_models.items() if m._trained},
            "thresholds": {s: m._best_threshold for s, m in self.dl_models.items() if m._trained},
        }
