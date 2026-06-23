"""Step 4: Retrain DL LSTM with 31 features (selection optimisée) + per-symbol USDCAD"""
import logging
import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime

import MetaTrader5 as mt5
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, r"C:\Users\saint\Documents\MT5_FTMO_IA.7")
import config_simple as cfg
from engine_simple.ml_features import FULL_FEATURE_NAMES, FeatureEngine
from engine_simple.mt5_connector import MT5Connector

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("retrain")

print(f"Feature count: {len(FULL_FEATURE_NAMES)}")
print(f"Features: {FULL_FEATURE_NAMES}")

# Load historical trades
with open(r"C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\historical_trades.pkl", "rb") as f:
    trades = pickle.load(f, encoding="utf-8")
print(f"Loaded {len(trades)} trades")

# Group by symbol
by_symbol = defaultdict(list)
for t in trades:
    by_symbol[t["symbol"]].append(t)

for s, ts in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
    print(f"  {s}: {len(ts)} trades")

# Connect MT5
connector = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
connector.connect()

fe = FeatureEngine()

# Build sequences per symbol
all_sequences = []
all_labels = []
per_symbol_labels = defaultdict(list)
per_symbol_seqs = defaultdict(list)

for sym, sym_trades in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
    if len(sym_trades) < 5:
        print(f"\nSkip {sym}: only {len(sym_trades)} trades")
        continue

    print(f"\n{sym}: {len(sym_trades)} trades")

    # Fetch enough H1 data: go back 2000 bars to cover all trades
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 2000)
    if rates is None or len(rates) < 200:
        print(f"  Skip: insufficient H1 data ({len(rates) if rates is not None else 0})")
        continue

    rates_list = [tuple(r) for r in rates]

    # Pre-compute features for every bar
    bar_features = [None] * 20
    for j in range(20, len(rates_list)):
        window = rates_list[:j+1]
        try:
            feat = fe.compute_features(window)
            bar_features.append(feat)
        except (ValueError, TypeError, IndexError):
            bar_features.append(None)
    print(f"  Computed features for {len(bar_features)} bars")

    seq_count = 0
    for t in sym_trades:
        ts = t["time_open"].replace(".", "-")[:19]
        try:
            t_open = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            try:
                t_open = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        trade_ts = t_open.timestamp()

        # Find bar just BEFORE trade
        bar_idx = -1
        for j in range(len(rates_list) - 1, -1, -1):
            if rates_list[j][0] < trade_ts:
                bar_idx = j
                break

        if bar_idx < 21 or bar_idx >= len(bar_features):
            continue

        seq_features: list = []
        for j in range(bar_idx - 20, bar_idx):
            feat = bar_features[j]
            if feat is None:
                break
            fv = [feat.get(n, 0.5) for n in FULL_FEATURE_NAMES]
            seq_features.append(fv)

        if len(seq_features) == 20:
            all_sequences.append(seq_features)
            all_labels.append(1 if t["won"] else 0)
            per_symbol_seqs[sym].append(seq_features)
            per_symbol_labels[sym].append(1 if t["won"] else 0)
            seq_count += 1

    print(f"  Built {seq_count} sequences")

print(f"\nTotal DL sequences: {len(all_sequences)}")

if len(all_sequences) < 32:
    print("Not enough sequences for training")
    connector.disconnect()
    sys.exit(0)

X_all = np.array(all_sequences, dtype=np.float32)
y_all = np.array(all_labels, dtype=np.float32)
print(f"X shape: {X_all.shape}, y shape: {y_all.shape}")
print(f"Win rate: {y_all.mean()*100:.1f}%")

# ============================================================
# TRAINING
# ============================================================


class LSTMNet(nn.Module):
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

def train_model(model, X, y, epochs=10, name="model"):
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    batch_size = min(32, len(X))
    dataset = torch.utils.data.TensorDataset(torch.FloatTensor(X), torch.FloatTensor(y).view(-1, 1))
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

    for epoch in range(epochs):
        losses = []
        for bx, by in loader:
            optimizer.zero_grad()
            out = model(bx)
            loss = criterion(out, by)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(loss.item())
        print(f"  {name} Epoch {epoch+1}: loss={np.mean(losses):.4f}")

    model.eval()
    with torch.no_grad():
        preds = model(torch.FloatTensor(X))
        acc = ((preds.numpy() > 0.5).astype(int).flatten() == y).mean()
        print(f"  {name} Training accuracy: {acc*100:.1f}%")
    return acc

# 1. Train all-symbol model
print("\n=== Training ALL-SYMBOL model (31 features) ===")
model_all = LSTMNet(len(FULL_FEATURE_NAMES))
train_model(model_all, X_all, y_all, epochs=10, name="ALL")
torch.save(model_all.state_dict(), "models/dl_lstm_all.pkl")
print(f"Saved models/dl_lstm_all.pkl ({os.path.getsize('models/dl_lstm_all.pkl')} bytes)")

# 2. Train USDCAD-specific model
usdcad_seqs = per_symbol_seqs.get("USDCAD", [])
usdcad_labels = per_symbol_labels.get("USDCAD", [])
if len(usdcad_seqs) >= 32:
    X_usdcad = np.array(usdcad_seqs, dtype=np.float32)
    y_usdcad = np.array(usdcad_labels, dtype=np.float32)
    print(f"\n=== Training USDCAD-specific model ({len(usdcad_seqs)} trades, WR={y_usdcad.mean()*100:.1f}%) ===")
    model_usdcad = LSTMNet(len(FULL_FEATURE_NAMES))
    train_model(model_usdcad, X_usdcad, y_usdcad, epochs=10, name="USDCAD")
    torch.save(model_usdcad.state_dict(), "models/dl_lstm_USDCAD.pkl")
    print(f"Saved models/dl_lstm_USDCAD.pkl ({os.path.getsize('models/dl_lstm_USDCAD.pkl')} bytes)")
else:
    print(f"\nNot enough USDCAD sequences: {len(usdcad_seqs)} (need >= 32)")

# 3. Validate per-symbol accuracy for all symbols
print("\n=== Per-symbol validation ===")
model_all.eval()
with torch.no_grad():
    for sym in sorted(per_symbol_seqs.keys()):
        seqs = per_symbol_seqs[sym]
        labs = per_symbol_labels[sym]
        if len(seqs) < 10:
            continue
        Xs = np.array(seqs, dtype=np.float32)
        ys = np.array(labs, dtype=np.float32)
        preds = model_all(torch.FloatTensor(Xs))
        acc = ((preds.numpy() > 0.5).astype(int).flatten() == ys).mean()
        # High-confidence analysis
        scores = np.maximum(preds.numpy(), 1 - preds.numpy()).flatten()
        high_conf = scores >= 0.60
        if high_conf.sum() > 0:
            hc_acc = ((preds.numpy()[high_conf] > 0.5).astype(int).flatten() == ys[high_conf]).mean()
            print(f"  {sym}: {len(seqs)}t, {acc*100:.1f}% | high-conf({high_conf.sum()}): {hc_acc*100:.1f}%")
        else:
            print(f"  {sym}: {len(seqs)}t, {acc*100:.1f}%")

connector.disconnect()
print("\n=== Done ===")
