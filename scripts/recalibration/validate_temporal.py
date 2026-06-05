"""Validation temporelle: train sur trades anciens, test sur trades recents (simule walk-forward)."""
import logging
import pickle
import sys
from collections import defaultdict
from datetime import datetime

import MetaTrader5 as mt5
import numpy as np

sys.path.insert(0, r"C:\Users\saint\Documents\MT5_FTMO_IA.7")
import torch
import torch.nn as nn
import torch.optim as optim

import config_simple as cfg
from engine_simple.dl_ensemble import AttentionLSTMNet
from engine_simple.ml_features import FULL_FEATURE_NAMES, FeatureEngine
from engine_simple.mt5_connector import MT5Connector

logging.basicConfig(level=logging.INFO, format="%(message)s")

n_features = len(FULL_FEATURE_NAMES)
print(f"Features: {n_features}")

with open(r"C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\trades_clean.pkl", "rb") as f:
    trades = pickle.load(f, encoding="utf-8")

# Sort trades by time
def parse_time(t):
    ts = t["time_open"].replace(".", "-")[:19]
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return datetime.min

for t in trades:
    t["_dt"] = parse_time(t)
trades.sort(key=lambda t: t["_dt"])

connector = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
connector.connect()
fe = FeatureEngine()

def build_sequences(sym_trades):
    """Build feature sequences for a list of trades."""
    if not sym_trades:
        return [], []
    sym = sym_trades[0]["symbol"]
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 2000)
    if rates is None or len(rates) < 200:
        return [], []
    rates_list = [tuple(r) for r in rates]
    bar_features = [None] * 20
    for j in range(20, len(rates_list)):
        window = rates_list[:j+1]
        try:
            feat = fe.compute_features(window)
            bar_features.append(feat)
        except (ValueError, TypeError, IndexError):
            bar_features.append(None)
    seqs, labels = [], []
    for t in sym_trades:
        trade_ts = t["_dt"].timestamp()
        bar_idx = -1
        for j in range(len(rates_list) - 1, -1, -1):
            if rates_list[j][0] < trade_ts:
                bar_idx = j
                break
        if bar_idx < 21 or bar_idx >= len(bar_features):
            continue
        seq = []
        for j in range(bar_idx - 20, bar_idx):
            feat = bar_features[j]
            if feat is None:
                break
            fv = [feat.get(n, 0.5) for n in FULL_FEATURE_NAMES]
            seq.append(fv)
        if len(seq) == 20:
            seqs.append(seq)
            labels.append(1 if t["won"] else 0)
    return seqs, labels

# Build all sequences (USDCAD only for focused test)
usdcad_trades = [t for t in trades if t["symbol"] == "USDCAD"]
print(f"USDCAD trades: {len(usdcad_trades)}")
seqs, labels = build_sequences(usdcad_trades)
print(f"USDCAD sequences: {len(seqs)}")

if len(seqs) < 64:
    print("Not enough sequences")
    connector.disconnect()
    sys.exit(0)

# Time-based split: 80% earliest, 20% latest
split = int(len(seqs) * 0.8)
X_train = np.array(seqs[:split], dtype=np.float32)
y_train = np.array(labels[:split], dtype=np.float32)
X_test = np.array(seqs[split:], dtype=np.float32)
y_test = np.array(labels[split:], dtype=np.float32)

print(f"Train: {len(X_train)} trades (WR={y_train.mean()*100:.1f}%)")
print(f"Test:  {len(X_test)} trades (WR={y_test.mean()*100:.1f}%)")
print(f"Train period: {usdcad_trades[0]['_dt']} -> {usdcad_trades[split-1]['_dt']}")
print(f"Test period:  {usdcad_trades[split]['_dt']} -> {usdcad_trades[-1]['_dt']}")

# Also test per-symbol on the full set
by_sym = defaultdict(list)
for t in trades:
    by_sym[t["symbol"]].append(t)

# Train Attention-LSTM on train set
model = AttentionLSTMNet(n_features)
criterion = nn.BCELoss()
optimizer = optim.Adam(model.parameters(), lr=0.001)
batch_size = 32
dataset = torch.utils.data.TensorDataset(torch.FloatTensor(X_train), torch.FloatTensor(y_train).view(-1, 1))
loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

print("\nTraining...")
for epoch in range(15):
    model.train()
    losses = []
    for bx, by in loader:
        optimizer.zero_grad()
        out = model(bx)
        loss = criterion(out, by)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        losses.append(loss.item())
    if (epoch + 1) % 5 == 0:
        print(f"  Epoch {epoch+1}: loss={np.mean(losses):.4f}")

# Evaluate
model.eval()
with torch.no_grad():
    train_preds = model(torch.FloatTensor(X_train))
    test_preds = model(torch.FloatTensor(X_test))
    train_acc = ((train_preds.numpy() > 0.5).astype(int).flatten() == y_train).mean()
    test_acc = ((test_preds.numpy() > 0.5).astype(int).flatten() == y_test).mean()
    # High-confidence on test
    test_scores = np.maximum(test_preds.numpy(), 1 - test_preds.numpy()).flatten()
    hc_mask = test_scores >= 0.60
    hc_acc = (
        ((test_preds.numpy()[hc_mask] > 0.5).astype(int).flatten() == y_test[hc_mask]).mean()
        if hc_mask.sum() > 0 else 0
    )

print("\n=== USDCAD Temporal Validation ===")
print(f"  Train accuracy: {train_acc*100:.1f}%")
print(f"  Test accuracy:  {test_acc*100:.1f}%")
print(f"  Test high-conf (n={hc_mask.sum()}): {hc_acc*100:.1f}%")

# Test on other symbols (using model trained only on USDCAD)
print("\n=== Cross-symbol generalisation ===")
model.eval()
with torch.no_grad():
    for sym in sorted(by_sym.keys()):
        if sym == "USDCAD" or len(by_sym[sym]) < 10:
            continue
        sym_seqs, sym_labels = build_sequences(by_sym[sym])
        if len(sym_seqs) < 10:
            continue
        Xs = np.array(sym_seqs, dtype=np.float32)
        ys = np.array(sym_labels, dtype=np.float32)
        preds = model(torch.FloatTensor(Xs))
        acc = ((preds.numpy() > 0.5).astype(int).flatten() == ys).mean()
        print(f"  {sym}: {len(Xs)}t, {acc*100:.1f}% (model trained on USDCAD only)")

connector.disconnect()
print("\n=== Done ===")
