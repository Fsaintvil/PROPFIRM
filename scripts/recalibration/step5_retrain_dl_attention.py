"""Step 5: Retrain Attention-LSTM (ALFA-style) sur les 967 trades propres"""
import logging
import os
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
logger = logging.getLogger("retrain_attn")

n_features = len(FULL_FEATURE_NAMES)
print(f"Feature count: {n_features}")
print(f"Features: {FULL_FEATURE_NAMES}")

# Load clean trades
with open(r"C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\trades_clean.pkl", "rb") as f:
    trades = pickle.load(f, encoding="utf-8")
print(f"Loaded {len(trades)} trades")

by_symbol = defaultdict(list)
for t in trades:
    by_symbol[t["symbol"]].append(t)

for s, ts in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
    print(f"  {s}: {len(ts)} trades")

connector = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
connector.connect()
fe = FeatureEngine()

all_sequences = []
all_labels = []
per_symbol_labels = defaultdict(list)
per_symbol_seqs = defaultdict(list)

for sym, sym_trades in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
    if len(sym_trades) < 5:
        print(f"\nSkip {sym}: only {len(sym_trades)} trades")
        continue

    print(f"\n{sym}: {len(sym_trades)} trades")
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 2000)
    if rates is None or len(rates) < 200:
        print(f"  Skip: insufficient H1 data ({len(rates) if rates is not None else 0})")
        continue

    rates_list = [tuple(r) for r in rates]
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

# Shuffle and split train/val
np.random.seed(42)
idx = np.random.permutation(len(all_sequences))
split = int(len(idx) * 0.8)
train_idx, val_idx = idx[:split], idx[split:]

X_all = np.array(all_sequences, dtype=np.float32)
y_all = np.array(all_labels, dtype=np.float32)
X_train, y_train = X_all[train_idx], y_all[train_idx]
X_val, y_val = X_all[val_idx], y_all[val_idx]

print(f"X shape: {X_all.shape}, y shape: {y_all.shape}")
print(f"Win rate: {y_all.mean()*100:.1f}%")
print(f"Train: {len(X_train)}, Val: {len(X_val)}")

def train_model(model, X_tr, y_tr, X_va, y_va, epochs=20, name="model"):
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=3, factor=0.5)
    batch_size = min(32, len(X_tr))
    dataset = torch.utils.data.TensorDataset(torch.FloatTensor(X_tr), torch.FloatTensor(y_tr).view(-1, 1))
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

    best_val_loss = float('inf')
    best_state = None
    patience = 5
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
                print(f"  {name} Early stopping at epoch {epoch+1} (val_loss={val_loss:.4f}, best={best_val_loss:.4f})")
                break

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        preds = model(torch.FloatTensor(X_tr))
        tr_acc = ((preds.numpy() > 0.5).astype(int).flatten() == y_tr).mean()
        preds_val = model(torch.FloatTensor(X_va))
        va_acc = ((preds_val.numpy() > 0.5).astype(int).flatten() == y_va).mean()
    print(f"  {name} Best: train_acc={tr_acc*100:.1f}% val_acc={va_acc*100:.1f}% (val_loss={best_val_loss:.4f})")
    return best_val_loss

print("\n=== Training ALL-SYMBOL Attention-LSTM ===")
model_all = AttentionLSTMNet(n_features)
train_model(model_all, X_train, y_train, X_val, y_val, epochs=20, name="ALL")
torch.save(model_all.state_dict(), "models/dl_attention_all.pkl")
print(f"Saved models/dl_attention_all.pkl ({os.path.getsize('models/dl_attention_all.pkl')} bytes)")

# USDCAD-specific model
usdcad_seqs = per_symbol_seqs.get("USDCAD", [])
usdcad_labels = per_symbol_labels.get("USDCAD", [])
if len(usdcad_seqs) >= 32:
    X_usd = np.array(usdcad_seqs, dtype=np.float32)
    y_usd = np.array(usdcad_labels, dtype=np.float32)
    np.random.seed(7)
    idx_usd = np.random.permutation(len(X_usd))
    sp_usd = int(len(idx_usd) * 0.8)
    X_usd_tr, y_usd_tr = X_usd[idx_usd[:sp_usd]], y_usd[idx_usd[:sp_usd]]
    X_usd_va, y_usd_va = X_usd[idx_usd[sp_usd:]], y_usd[idx_usd[sp_usd:]]
    print(f"\n=== Training USDCAD Attention-LSTM ({len(X_usd)} trades, WR={y_usd.mean()*100:.1f}%) ===")
    model_usdcad = AttentionLSTMNet(n_features)
    train_model(model_usdcad, X_usd_tr, y_usd_tr, X_usd_va, y_usd_va, epochs=20, name="USDCAD")
    torch.save(model_usdcad.state_dict(), "models/dl_attention_USDCAD.pkl")
    print(f"Saved models/dl_attention_USDCAD.pkl ({os.path.getsize('models/dl_attention_USDCAD.pkl')} bytes)")
else:
    print(f"\nNot enough USDCAD sequences: {len(usdcad_seqs)} (need >= 32)")

# Per-symbol validation
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
        scores = np.maximum(preds.numpy(), 1 - preds.numpy()).flatten()
        high_conf = scores >= 0.60
        if high_conf.sum() > 0:
            hc_acc = ((preds.numpy()[high_conf] > 0.5).astype(int).flatten() == ys[high_conf]).mean()
            print(f"  {sym}: {len(seqs)}t, {acc*100:.1f}% | high-conf({high_conf.sum()}): {hc_acc*100:.1f}%")
        else:
            print(f"  {sym}: {len(seqs)}t, {acc*100:.1f}%")

connector.disconnect()
print("\n=== Done ===")
