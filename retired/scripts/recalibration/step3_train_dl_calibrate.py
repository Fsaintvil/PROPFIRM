"""Step 3: Train DL LSTM + Calibrate Meta-Learner + Adjust Configuration"""
import sys

sys.path.insert(0, r"C:\Users\saint\Documents\MT5_FTMO_IA.7")
import logging
import pickle
from collections import defaultdict
from datetime import datetime

import MetaTrader5 as mt5_module
import numpy as np

import config_simple as cfg
from engine_simple.dl_ensemble import DLEnsemble
from engine_simple.ml_features import FULL_FEATURE_NAMES, FeatureEngine
from engine_simple.mt5_connector import MT5Connector

logging.basicConfig(level=logging.INFO)

# Load validation data
with open(r"C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\ml_validation_batch.pkl", "rb") as f:
    validation = pickle.load(f, encoding="utf-8")
print(f"Loaded {len(validation)} validated trades")

# Connect MT5
connector = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
connector.connect()

fe = FeatureEngine()

# ============================================================
# PART 1: Build DL training sequences
# ============================================================
print("\n=== Building DL Training Sequences ===")

# Group trades by symbol to process efficiently
by_symbol = defaultdict(list)
for r in validation:
    by_symbol[r["symbol"]].append(r)

all_sequences = []
all_labels = []
SYMBOLS = ["EURUSD", "GBPUSD", "USDCAD", "USDCHF", "GBPJPY", "USOIL.cash", "BTCUSD", "ETHUSD"]

for sym in SYMBOLS:
    trades_sym = by_symbol.get(sym, [])
    if len(trades_sym) < 10:
        continue

    print(f"\n{sym}: {len(trades_sym)} trades")

    # Get all H1 rates for this symbol from the full history period
    # Fetch a large block of H1 data
    rates = mt5_module.copy_rates_from_pos(sym, mt5_module.TIMEFRAME_H1, 0, 1000)
    if rates is None or len(rates) < 100:
        print("  Skip: insufficient H1 data")
        continue

    # Convert to list for easier handling
    rates_list = [tuple(r) for r in rates]

    # Compute features for every H1 bar
    print(f"  Computing features for {len(rates_list)} bars...")
    bar_features: list = []
    for j in range(len(rates_list)):
        # Need 20 bars before current for meaningful features
        if j < 20:
            bar_features.append(None)
            continue
        window = rates_list[:j+1]
        try:
            feat = fe.compute_features(window)
            bar_features.append(feat)
        except (ValueError, TypeError, IndexError):
            bar_features.append(None)

    # For each trade, find the matching bar and build sequence
    seq_count = 0
    for r in trades_sym:
        try:
            ts = r["time_open"].replace(".", "-")[:19]
            t_open = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            continue

        # Convert trade time to timestamp and find closest bar
        trade_ts = t_open.timestamp()

        # Find the bar index just BEFORE the trade
        bar_idx = -1
        for j in range(len(rates_list) - 1, -1, -1):
            bar_time = rates_list[j][0]  # timestamp
            if bar_time < trade_ts:
                bar_idx = j
                break

        if bar_idx < 21:
            continue

        # Build sequence of 20 feature vectors
        seq_features = []
        for j in range(bar_idx - 20, bar_idx):
            feat = bar_features[j]
            if feat is None:
                break
            fv = [feat.get(n, 0.5) for n in FULL_FEATURE_NAMES]
            seq_features.append(fv)

        if len(seq_features) == 20:
            all_sequences.append(seq_features)
            all_labels.append(1 if r["won"] else 0)
            seq_count += 1

    print(f"  Built {seq_count} sequences")

print(f"\nTotal DL sequences: {len(all_sequences)}")
if len(all_sequences) >= 32:
    X = np.array(all_sequences, dtype=np.float32)
    y = np.array(all_labels, dtype=np.float32)
    print(f"X shape: {X.shape}, y shape: {y.shape}")
    print(f"Win rate in training: {y.mean()*100:.1f}%")

    # Train DL LSTM
    print("\n=== Training DL LSTM ===")
    dl = DLEnsemble()
    if dl.available:
        # Create a single model for all symbols
        model_key = "all_symbols_H1"
        from engine_simple.dl_ensemble import LSTMNet
        model = LSTMNet(len(FULL_FEATURE_NAMES))

        # Train
        import torch
        import torch.nn as nn
        import torch.optim as optim

        criterion = nn.BCELoss()
        optimizer = optim.Adam(model.parameters(), lr=0.001)
        batch_size = min(32, len(X))

        print(f"Training for 10 epochs, batch_size={batch_size}...")
        dataset = torch.utils.data.TensorDataset(torch.FloatTensor(X), torch.FloatTensor(y).view(-1, 1))
        loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

        for epoch in range(10):
            losses = []
            for bx, by in loader:
                optimizer.zero_grad()
                out = model(bx)
                loss = criterion(out, by)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                losses.append(loss.item())
            print(f"  Epoch {epoch+1}: loss={np.mean(losses):.4f}")

        # Save model
        torch.save(model.state_dict(), "models/dl_lstm_all.pkl")
        print("Model saved to models/dl_lstm_all.pkl")

        # Validate
        model.eval()
        with torch.no_grad():
            preds = model(torch.FloatTensor(X))
            pred_labels = (preds.numpy() > 0.5).astype(int).flatten()
            accuracy = (pred_labels == y.numpy()).mean()
            print(f"Training accuracy: {accuracy*100:.1f}%")
    else:
        print("PyTorch not available, skip DL training")
else:
    print("Not enough sequences for DL training (need >= 32)")

# ============================================================
# PART 2: Calibrate Meta-Learner
# ============================================================
print("\n=== Calibrating Meta-Learner ===")

# Based on validation data, calculate per-symbol ML accuracy
ml_accuracy = {}
for r in validation:
    ml = r.get("ml")
    if not ml or not isinstance(ml, dict):
        continue
    action = ml.get("action", "HOLD")
    if action == "HOLD":
        continue
    sym = r["symbol"]
    if sym not in ml_accuracy:
        ml_accuracy[sym] = {"correct": 0, "total": 0, "score_sum": 0}
    trade_buy = r["direction"] == "BUY"
    ml_buy = action == "BUY"
    if ml_buy == trade_buy:
        ml_accuracy[sym]["correct"] += 1
    ml_accuracy[sym]["total"] += 1
    ml_accuracy[sym]["score_sum"] += ml.get("score", 0.5)

print("ML per-symbol accuracy (calibration data):")
for sym in sorted(ml_accuracy):
    d = ml_accuracy[sym]
    acc = d["correct"] / d["total"] * 100 if d["total"] > 0 else 0
    avg_score = d["score_sum"] / d["total"] if d["total"] > 0 else 0.5
    print(f"  {sym}: {d['correct']}/{d['total']} = {acc:.1f}% (avg_score={avg_score:.2f})")

# Create calibrated meta-learner config
print("\n=== Generating Calibrated Config ===")
calibration = {
    "ml_accuracy": {sym: round(d["correct"]/max(d["total"],1), 3) for sym, d in ml_accuracy.items()},
    "ml_is_anti_predictive": True,
    "recommendation": "ML should NOT gate trades. Use ML score as weak secondary signal only.",
    "strategy_adjustments": {
        "EURUSD": {"thresh": 2.0, "risk_mult": 1.0},
        "GBPUSD": {"thresh": 2.0, "risk_mult": 1.0},
        "USDJPY": {"thresh": 2.0, "risk_mult": 0.8},
        "USDCAD": {"thresh": 2.0, "risk_mult": 1.0},
        "USDCHF": {"thresh": 2.0, "risk_mult": 1.0},
        "AUDUSD": {"thresh": 2.0, "risk_mult": 0.9},
        "NZDUSD": {"thresh": 2.0, "risk_mult": 0.8},
        "EURJPY": {"thresh": 2.0, "risk_mult": 0.8},
        "GBPJPY": {"thresh": 2.0, "risk_mult": 0.9},
        "XAUUSD": {"thresh": 2.5, "risk_mult": 0.5},
        "ETHUSD": {"thresh": 2.5, "risk_mult": 0.5},
        "USOIL.cash": {"thresh": 2.5, "risk_mult": 0.7},
    }
}

with open("runtime/ml_calibration.pkl", "wb") as f:
    pickle.dump(calibration, f)
print("Calibration saved to runtime/ml_calibration.pkl")

connector.disconnect()
print("\n=== Step 3 Complete ===")
print(f"DL sequences: {len(all_sequences)}")
print(f"Calibrated {len(ml_accuracy)} symbols")
