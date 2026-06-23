"""Retrain Attention-LSTM on 17k trades with walk-forward validation (v2 - optimized)."""
import logging
import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Users\saint\Documents\MT5_FTMO_IA.7")
import torch
import torch.nn as nn
import torch.optim as optim

from engine_simple.dl_ensemble import AttentionLSTMNet
from engine_simple.ml_features import FULL_FEATURE_NAMES, FeatureEngine

logging.basicConfig(level=logging.INFO, format="%(message)s")

n_features = len(FULL_FEATURE_NAMES)
print(f"Feature count: {n_features}")

# Load clean trades
with open("runtime/all_trades_clean.pkl", "rb") as f:
    trades = pickle.load(f, encoding="utf-8")

MIN_TRADES = 10
sym_counts: dict = defaultdict(int)
for t in trades:
    sym_counts[t["symbol"]] += 1
valid_symbols = {s for s, c in sym_counts.items() if c >= MIN_TRADES}
trades = [t for t in trades if t["symbol"] in valid_symbols]
print(f"Loaded {len(trades)} trades across {len(valid_symbols)} symbols")

by_symbol = defaultdict(list)
for t in trades:
    by_symbol[t["symbol"]].append(t)
for s, ts in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
    wr = sum(1 for t in ts if t["won"]) / len(ts) * 100
    print(f"  {s:12s}: {len(ts):5d}t, WR={wr:.1f}%")

# Load H1 data
H1_DIR = "runtime/market_h1_2026"
h1_data = {}
for sym in valid_symbols:
    fname = os.path.join(H1_DIR, f"{sym}_H1.csv")
    if not os.path.exists(fname):
        print(f"WARNING: No H1 data for {sym}")
        continue
    df = pd.read_csv(fname, parse_dates=["time"])
    df = df.sort_values("time")
    df = df.reset_index(drop=True)
    h1_data[sym] = df
    print(f"  {sym}: {len(df)} bars ({df['time'].min()} to {df['time'].max()})")

fe = FeatureEngine()
LOOKBACK = 60
SEQ_LEN = 20

# Pre-compute features for ALL bars of ALL symbols
print("\nPre-computing features for all bars...")
symbol_features = {}
for sym, df in h1_data.items():
    rates_list = []
    for _, row in df.iterrows():
        t = row["time"].timestamp()
        rates_list.append((t, row["open"], row["high"], row["low"], row["close"],
                           int(row["tick_volume"]), int(row["spread"]), int(row["real_volume"])))

    bar_features = [None] * 20  # first 20 bars need >50 bars lookback
    total = len(rates_list)
    for j in range(20, total):
        window = rates_list[max(0, j-LOOKBACK+1):j+1]
        if len(window) < 50:
            bar_features.append(None)
            continue
        try:
            feat = fe.compute_features(window)
            bar_features.append(feat)
        except (ValueError, TypeError, IndexError):
            bar_features.append(None)

    symbol_features[sym] = bar_features
    print(f"  {sym}: {len(bar_features)} feature bars computed ({total} raw bars)")

def parse_trade_time(t):
    ts = t["time_open"].replace(".", "-")[:19]
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"]:
        try:
            return datetime.strptime(ts, fmt).timestamp()
        except ValueError:
            continue
    return None

def get_bar_idx_for_trade(t, df_h1):
    ts = parse_trade_time(t)
    if ts is None:
        return None
    trade_ts = pd.Timestamp(datetime.fromtimestamp(ts)).tz_localize(None)
    bars_before = df_h1[df_h1["time"] <= trade_ts]
    if len(bars_before) < SEQ_LEN + LOOKBACK + 1:
        return None
    return bars_before.index[-1]

# Build sequences from pre-computed features
print("\nBuilding sequences...")
all_sequences = []
all_labels = []
all_timestamps = []
per_symbol: dict = defaultdict(lambda: {"seqs": [], "labels": [], "times": []})

skipped = {"no_data": 0, "oob": 0, "no_feat": 0}
built = 0

for t in trades:
    sym = t["symbol"]
    df = h1_data.get(sym)
    feat_list = symbol_features.get(sym)
    if df is None or feat_list is None:
        skipped["no_data"] += 1
        continue

    bar_idx = get_bar_idx_for_trade(t, df)
    if bar_idx is None:
        skipped["oob"] += 1
        continue
    if bar_idx >= len(feat_list) or bar_idx < SEQ_LEN:
        skipped["oob"] += 1
        continue

    seq: list = []
    ok = True
    for j in range(bar_idx - SEQ_LEN + 1, bar_idx + 1):
        feat = feat_list[j]
        if feat is None:
            ok = False
            break
        fv = [feat.get(n, 0.5) for n in FULL_FEATURE_NAMES]
        seq.append(fv)

    if ok and len(seq) == SEQ_LEN:
        all_sequences.append(seq)
        all_labels.append(1 if t["won"] else 0)
        ts_val = pd.Timestamp(datetime.fromtimestamp(parse_trade_time(t))).tz_localize(None)
        all_timestamps.append(ts_val)
        per_symbol[sym]["seqs"].append(seq)
        per_symbol[sym]["labels"].append(1 if t["won"] else 0)
        per_symbol[sym]["times"].append(ts_val)
        built += 1
    else:
        skipped["no_feat"] += 1

print(f"\nBuilt {built} sequences from {len(trades)} trades")
print(f"  Skipped: {skipped}")
print(f"  Win rate: {np.mean(all_labels)*100:.1f}%" if built > 0 else "  No sequences!")

if built < 32:
    print("Not enough sequences, aborting")
    sys.exit(1)

# Sort by time, walk-forward split
timestamps = np.array(all_timestamps)
labels = np.array(all_labels, dtype=np.float32)
sequences = np.array(all_sequences, dtype=np.float32)

sort_idx = np.argsort(timestamps)
sequences = sequences[sort_idx]
labels = labels[sort_idx]
timestamps = timestamps[sort_idx]

split_idx = int(len(sequences) * 0.70)
X_train, y_train = sequences[:split_idx], labels[:split_idx]
X_val, y_val = sequences[split_idx:], labels[split_idx:]

print(f"\nTrain: {len(X_train)} (up to {timestamps[split_idx-1]})")
print(f"Val:   {len(X_val)} (from {timestamps[split_idx]})")
print(f"Train WR: {y_train.mean()*100:.1f}%, Val WR: {y_val.mean()*100:.1f}%")

def train_model(model, X_tr, y_tr, X_va, y_va, epochs=30, name="model"):
    criterion = nn.BCELoss()
    optimizer = optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=4, factor=0.5)
    batch_size = min(64, len(X_tr))
    dataset = torch.utils.data.TensorDataset(torch.FloatTensor(X_tr), torch.FloatTensor(y_tr).view(-1, 1))
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

    best_val_loss = float('inf')
    best_state = None
    patience = 7
    patience_counter = 0

    for epoch in range(epochs):
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

        model.eval()
        with torch.no_grad():
            val_preds = model(torch.FloatTensor(X_va))
            val_loss = criterion(val_preds, torch.FloatTensor(y_va).view(-1, 1)).item()
            train_preds = model(torch.FloatTensor(X_tr))
            train_loss = criterion(train_preds, torch.FloatTensor(y_tr).view(-1, 1)).item()
            val_acc = ((val_preds.numpy() > 0.5).astype(int).flatten() == y_va).mean()
            train_acc = ((train_preds.numpy() > 0.5).astype(int).flatten() == y_tr).mean()

        scheduler.step(val_loss)
        print(f"  {name} E{epoch+1:2d}: train_loss={train_loss:.4f} val_loss={val_loss:.4f} "
              f"train_acc={train_acc*100:.1f}% val_acc={val_acc*100:.1f}% "
              f"lr={optimizer.param_groups[0]['lr']:.6f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"  {name} Early stopping at epoch {epoch+1}")
                break

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        tr_acc = ((model(torch.FloatTensor(X_tr)).numpy() > 0.5).astype(int).flatten() == y_tr).mean()
        va_acc = ((model(torch.FloatTensor(X_va)).numpy() > 0.5).astype(int).flatten() == y_va).mean()
    print(f"  {name} Best: train_acc={tr_acc*100:.1f}% val_acc={va_acc*100:.1f}%")
    return best_val_loss

# Train ALL-symbol model
print("\n=== Training ALL-SYMBOL Attention-LSTM (walk-forward) ===")
model_all = AttentionLSTMNet(n_features)
train_model(model_all, X_train, y_train, X_val, y_val, epochs=30, name="ALL")

os.makedirs("models", exist_ok=True)
torch.save(model_all.state_dict(), "models/dl_attention_all_v2.pkl")
print(f"\nSaved models/dl_attention_all_v2.pkl ({os.path.getsize('models/dl_attention_all_v2.pkl')} bytes)")

# Per-symbol validation
print("\n=== Per-symbol validation (ALL model) ===")
model_all.eval()
with torch.no_grad():
    for sym in sorted(per_symbol.keys()):
        seqs = np.array(per_symbol[sym]["seqs"], dtype=np.float32)
        labs = np.array(per_symbol[sym]["labels"], dtype=np.float32)
        if len(seqs) < 10:
            continue
        preds = model_all(torch.FloatTensor(seqs))
        acc = ((preds.numpy() > 0.5).astype(int).flatten() == labs).mean()
        scores = np.maximum(preds.numpy(), 1 - preds.numpy()).flatten()
        high_conf = scores >= 0.60
        if high_conf.sum() > 0:
            hc_acc = ((preds.numpy()[high_conf] > 0.5).astype(int).flatten() == labs[high_conf]).mean()
            print(f"  {sym:12s}: {len(seqs):5d}t, {acc*100:.1f}% | high-conf({high_conf.sum()}): {hc_acc*100:.1f}%")
        else:
            print(f"  {sym:12s}: {len(seqs):5d}t, {acc*100:.1f}%")

# Per-symbol models
print("\n=== Per-symbol specialized models ===")
for sym in sorted(per_symbol.keys()):
    seqs = np.array(per_symbol[sym]["seqs"], dtype=np.float32)
    labs = np.array(per_symbol[sym]["labels"], dtype=np.float32)
    if len(seqs) < 100:
        print(f"  {sym}: skip ({len(seqs)} trades, need >= 100)")
        continue
    times = np.array(per_symbol[sym]["times"])
    sort_i = np.argsort(times)
    seqs_s = seqs[sort_i]
    labs_s = labs[sort_i]
    sp = int(len(seqs_s) * 0.70)
    Xs_tr, ys_tr = seqs_s[:sp], labs_s[:sp]
    Xs_va, ys_va = seqs_s[sp:], labs_s[sp:]
    if len(Xs_tr) < 32:
        continue
    print(f"\n  {sym}: {len(seqs)} trades, WR={labs.mean()*100:.1f}%")
    model_sym = AttentionLSTMNet(n_features)
    train_model(model_sym, Xs_tr, ys_tr, Xs_va, ys_va, epochs=20, name=sym)
    torch.save(model_sym.state_dict(), f"models/dl_attention_{sym}.pkl")
    print(f"  Saved models/dl_attention_{sym}.pkl")

print("\n=== Done ===")
