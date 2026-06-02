import logging
import os
import threading
from collections import deque

import numpy as np

from engine_simple.ml_features import FULL_FEATURE_NAMES, FeatureEngine

logger = logging.getLogger("dl_ensemble")

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    import torch.optim as optim
    TORCH_AVAILABLE = True

    class MultiHeadSelfAttention(nn.Module):
        """Multi-head self-attention over LSTM outputs."""
        def __init__(self, hidden_size, num_heads=4):
            super().__init__()
            assert hidden_size % num_heads == 0, f"hidden_size {hidden_size} must be divisible by num_heads {num_heads}"
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

    class AttentionLSTMNet(nn.Module):
        """"LSTM + Multi-Head Self-Attention (ALFA-style architecture)."""
        def __init__(self, input_size, hidden_size=64, num_layers=2, num_heads=4):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                                batch_first=True, dropout=0.2, bidirectional=False)
            self.attention = MultiHeadSelfAttention(hidden_size, num_heads)
            self.layer_norm = nn.LayerNorm(hidden_size)
            self.dropout = nn.Dropout(0.3)
            self.fc1 = nn.Linear(hidden_size, 32)
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

    class LSTMNet(nn.Module):
        """Legacy LSTM (backward compat pour anciens modeles)."""
        def __init__(self, input_size, hidden_size=64, num_layers=2):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.2)
            self.dropout = nn.Dropout(0.3)
            self.fc1 = nn.Linear(hidden_size, 32)
            self.fc2 = nn.Linear(32, 1)
            self.sigmoid = nn.Sigmoid()

        def forward(self, x):
            out, _ = self.lstm(x)
            out = out[:, -1, :]
            out = self.dropout(out)
            out = self.fc1(out)
            out = self.fc2(out)
            return self.sigmoid(out)

except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available, DL ensemble disabled")

    class AttentionLSTMNet:  # type: ignore[no-redef]
        """Stub — torch not available"""
        def __init__(self, *args, **kwargs): pass

    class LSTMNet:  # type: ignore[no-redef]
        """Stub — torch not available"""
        def __init__(self, *args, **kwargs): pass


class DLEnsemble:
    SEQUENCE_LENGTH = 20
    BATCH_SIZE = 32
    EPOCHS = 5
    LR = 0.001

    def __init__(self):
        self.models = {}
        self.feature_engine = FeatureEngine()
        self.training_buffer = {}  # dict[symbol] -> deque of (features, label)
        self.trade_outcomes = {}
        self._training_thread = None
        self._model_lock = threading.Lock()
        self.available = TORCH_AVAILABLE
        if self.available:
            self._load_pretrained()
            logger.info("DL Ensemble ready (PyTorch LSTM)")

    def _load_pretrained(self):
        import glob as glob_mod
        n_features = len(FULL_FEATURE_NAMES)
        # Load legacy LSTM models (dl_lstm_*.pkl)
        for fpath in glob_mod.glob("models/dl_lstm_*.pkl"):
            try:
                key = os.path.basename(fpath).replace("dl_lstm_", "").replace(".pkl", "")
                sd = torch.load(fpath, map_location='cpu', weights_only=True)
                lstm_weight = sd.get("lstm.weight_ih_l0")
                if lstm_weight is not None and lstm_weight.shape[1] != n_features:
                    logger.info(f"  [DL] Skipping {fpath}: expected {n_features} features, got {lstm_weight.shape[1]}")
                    continue
                # Detect architecture: if attention keys present, use AttentionLSTMNet
                has_attention = any("attention" in k for k in sd)
                ModelClass = AttentionLSTMNet if has_attention else LSTMNet
                model = ModelClass(n_features)
                model.load_state_dict(sd, strict=False)
                model.eval()
                with self._model_lock:
                    self.models[f"{key}_H1"] = model
                lbl = "Attention-" if has_attention else ""
                logger.info(f"  [DL] Loaded {lbl}LSTM {key} "
                            f"({os.path.getsize(fpath)} bytes)")
            except Exception as e:
                logger.warning(f"  [DL] Cannot load {fpath}: {e}")
        # Load attention models (dl_attention_*.pkl)
        for fpath in glob_mod.glob("models/dl_attention_*.pkl"):
            try:
                key = os.path.basename(fpath).replace("dl_attention_", "").replace(".pkl", "")
                sd = torch.load(fpath, map_location='cpu', weights_only=True)
                model = AttentionLSTMNet(n_features)
                model.load_state_dict(sd, strict=False)
                model.eval()
                with self._model_lock:
                    self.models[f"{key}_H1"] = model
                logger.info(f"  [DL] Loaded Attention-LSTM {key} ({os.path.getsize(fpath)} bytes)")
            except Exception as e:
                logger.warning(f"  [DL] Cannot load attention {fpath}: {e}")

    def _get_model(self, key):
        with self._model_lock:
            if key not in self.models:
                if not self.available:
                    return None
                for pref in ["all_H1", "USDCAD_H1"]:
                    if pref in self.models:
                        return self.models[pref]
                try:
                    self.models[key] = AttentionLSTMNet(len(FULL_FEATURE_NAMES))
                    logger.debug(f"  [DL] New Attention-LSTM model for {key}")
                except Exception as e:
                    logger.warning(f"  [DL] Cannot create model for {key}: {e}")
                    return None
            return self.models[key]

    LOOKBACK = 60  # bars needed for feature computation (compute_features needs >=50)

    def _build_sequence(self, rates):
        if rates is None or len(rates) < self.SEQUENCE_LENGTH + self.LOOKBACK:
            return None
        if hasattr(rates, 'dtype'):
            rates = rates.tolist()
        features_list = []
        for i in range(len(rates) - self.SEQUENCE_LENGTH, len(rates)):
            window = rates[max(0, i - self.LOOKBACK + 1):i + 1]
            if len(window) < 50:
                continue
            feat = self.feature_engine.compute_features(window)
            fv = [feat.get(n, 0.5) for n in FULL_FEATURE_NAMES]
            features_list.append(fv)
        if len(features_list) < self.SEQUENCE_LENGTH:
            return None
        arr = np.array(features_list[-self.SEQUENCE_LENGTH:], dtype=np.float32)
        arr = np.nan_to_num(arr, nan=0.5)
        return arr

    def predict(self, symbol, rates_dict):
        if not self.available:
            return None
        tf_scores = []
        for tf in ["H1", "M15", "M5"]:
            rates = rates_dict.get(tf) if tf in rates_dict else None
            if rates is None or len(rates) < self.SEQUENCE_LENGTH + self.LOOKBACK:
                continue
            seq = self._build_sequence(rates)
            if seq is None:
                continue
            key = f"{symbol}_{tf}"
            model = self._get_model(key)
            if model is None:
                continue
            try:
                with self._model_lock:
                    model.eval()
                    with torch.no_grad():
                        tensor = torch.FloatTensor(seq).unsqueeze(0)
                        prob = model(tensor).item()
                buy_prob = prob
                score = max(buy_prob, 1 - buy_prob)
                tf_scores.append({"tf": tf, "buy_prob": buy_prob, "score": score})
            except Exception as e:
                logger.debug(f"  [DL] {key} predict error: {e}")
        if not tf_scores:
            return None
        weights = {"H1": 0.6, "M15": 0.3, "M5": 0.1}
        total_w = sum(weights.get(s["tf"], 0.1) for s in tf_scores)
        if total_w == 0:
            return None
        avg_direction = sum(s["buy_prob"] * weights.get(s["tf"], 0.1) for s in tf_scores) / total_w
        avg_score = sum(s["score"] * weights.get(s["tf"], 0.1) for s in tf_scores) / total_w
        action = "BUY" if avg_direction > 0.50 else "SELL"
        return {
            "action": action, "score": round(avg_score, 3),
            "buy_prob": round(avg_direction, 3), "tfs": len(tf_scores),
        }

    def record_trade(self, symbol, features_before, profit_r):
        if not self.available:
            return
        if symbol not in self.training_buffer:
            self.training_buffer[symbol] = deque(maxlen=500)
        self.training_buffer[symbol].append((features_before, 1 if profit_r > 0 else 0))
        self.trade_outcomes.setdefault(symbol, []).append(profit_r)

    def _train_step(self, model, X, y):
        try:
            model.train()
            criterion = nn.BCELoss()
            optimizer = optim.Adam(model.parameters(), lr=self.LR)
            dataset = torch.utils.data.TensorDataset(
                torch.FloatTensor(X), torch.FloatTensor(y).view(-1, 1))
            loader = torch.utils.data.DataLoader(dataset, batch_size=min(self.BATCH_SIZE, len(X)), shuffle=True)
            for _ in range(self.EPOCHS):
                for bx, by in loader:
                    optimizer.zero_grad()
                    out = model(bx)
                    loss = criterion(out, by)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
            return True
        except Exception as e:
            logger.debug(f"  [DL] train error: {e}")
            return False

    @property
    def is_training(self):
        return self._training_thread is not None and self._training_thread.is_alive()

    def train_all(self):
        if not self.available or self.is_training:
            return
        self._training_thread = threading.Thread(target=self._train_all_sync, daemon=True)
        self._training_thread.start()

    def _train_all_sync(self):
        if not self.available:
            return
        for key, model in self.models.items():
            # Model key is "{symbol}_H1", buffer key is "{symbol}"
            buf_key = key.replace("_H1", "")
            buf = self.training_buffer.get(buf_key, [])
            if len(buf) < 32:
                logger.debug(f"  [DL] {key}: only {len(buf)} samples, skip training")
                continue
            X_list, y_list = zip(*buf, strict=False)
            X = np.array(X_list, dtype=np.float32)
            y = np.array(y_list, dtype=np.float32)
            if X.ndim != 3:
                X = X.reshape(-1, self.SEQUENCE_LENGTH, len(FULL_FEATURE_NAMES))
            with self._model_lock:
                self._train_step(model, X, y)
            logger.info(f"  [DL] Trained {key} on {len(X)} samples")
        # Save trained model to disk (attention prefix for attention models)
        try:
            for key, model in self.models.items():
                is_attention = isinstance(model, AttentionLSTMNet)
                prefix = "dl_attention" if is_attention else "dl_lstm"
                path = f"models/{prefix}_{key}.pkl"
                torch.save(model.state_dict(), path)
                logger.info(f"  [DL] Saved model to {path} ({os.path.getsize(path)} bytes)")
        except Exception as e:
            logger.debug(f"  [DL] Save model failed: {e}")
