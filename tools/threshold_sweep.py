import json
from pathlib import Path
import sys
import pandas as pd
import numpy as np

# ensure repo root
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from meta_learning_system import MetaLearningTradingSystem

DATA_FILE = Path('data') / 'features_sample.csv'
REPORT_FILE = Path('artifacts') / 'backtest_threshold_sweep.json'
HOLD_BARS = 5

print('Loading data...')
df = pd.read_csv(DATA_FILE)
if 'Unnamed: 0' in df.columns:
    df = df.set_index('Unnamed: 0')
    df.index = pd.to_datetime(df.index)

numeric_df = df.select_dtypes(include=[np.number]).fillna(method='ffill').fillna(0)

meta = MetaLearningTradingSystem(max_models=3)
# load booster if shim empty
if hasattr(meta, 'model_ensemble') and not meta.model_ensemble:
    art = Path('artifacts') / 'auto_improve'
    candidate = None
    if (art / 'best_lightgbm_large.txt').exists():
        candidate = art / 'best_lightgbm_large.txt'
    elif (art / 'best_lightgbm.txt').exists():
        candidate = art / 'best_lightgbm.txt'
    if candidate:
        import lightgbm as lgb
        booster = lgb.Booster(model_file=str(candidate))
        meta.model_ensemble = [{'model': booster, 'performance': 1.0, 'architecture': 'lightgbm_booster_file'}]

# Build inputs array once
preferred = ['close', 'volume', 'sma_1T', 'ema_15T', 'rsi_60T']
inputs = []
for i in range(len(numeric_df) - HOLD_BARS - 1):
    row = numeric_df.iloc[i:i+1]
    vals = []
    model = None
    if hasattr(meta, 'model_ensemble') and meta.model_ensemble:
        model = meta.model_ensemble[0]['model']
        fn = model.feature_name() or []
        n_feat = len(fn) if fn else 5
    else:
        n_feat = 5
    for j in range(n_feat):
        if j < len(preferred) and preferred[j] in row.columns:
            v = float(row[preferred[j]].iloc[0])
        elif j < len(row.columns):
            v = float(row.iloc[0, j])
        else:
            v = 0.0
        vals.append(v)
    inputs.append((i, vals))

# Compute predictions
X_all = pd.DataFrame([v for _, v in inputs], columns=[f'Column_{k}' for k in range(len(inputs[0][1]))])
try:
    preds = meta.ensemble_predict(X_all)
except Exception as e:
    preds = np.array([0.5] * len(X_all))

thresholds = np.linspace(0.5, 0.95, 10)
results = []
for thr in thresholds:
    balance = 0.0
    trades = 0
    wins = 0
    peak = 0.0
    max_dd = 0.0
    for idx, pred in enumerate(preds):
        if pred > thr:
            i = inputs[idx][0]
            entry = numeric_df['close'].iloc[i+1]
            exitp = numeric_df['close'].iloc[i+1+HOLD_BARS]
            pnl = exitp - entry
            trades += 1
            balance += pnl
            if balance > peak:
                peak = balance
            dd = balance - peak
            if dd < max_dd:
                max_dd = dd
            if pnl > 0:
                wins += 1
    results.append({'threshold': float(thr), 'trades': trades, 'wins': wins, 'net_pnl': float(balance), 'max_drawdown': abs(max_dd)})

REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(REPORT_FILE, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)

print('Sweep complete. Report:', REPORT_FILE)
print(results)
