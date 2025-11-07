import json
from pathlib import Path
import sys
import pandas as pd
import numpy as np
from datetime import timedelta

# ensure repo root is importable
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

try:
    from meta_learning_system import MetaLearningTradingSystem
except Exception as e:
    print("Impossible d'importer meta_learning_system:", e)
    raise

# Config
DATA_FILE = Path('data') / 'features_sample.csv'
REPORT_FILE = Path('artifacts') / 'backtest_quick_report.json'
THRESHOLD = 0.50
HOLD_BARS = 5  # horizon de sortie

print('Loading data...', DATA_FILE)
df = pd.read_csv(DATA_FILE)
if 'Unnamed: 0' in df.columns:
    df = df.set_index('Unnamed: 0')
    df.index = pd.to_datetime(df.index)

# Use numeric columns
num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
print('Numeric cols:', num_cols[:10])

meta = MetaLearningTradingSystem(max_models=3)
# If shim didn't load any model automatically, try to load candidate
if hasattr(meta, 'model_ensemble') and not meta.model_ensemble:
    art = Path('artifacts') / 'auto_improve'
    candidate = None
    if (art / 'best_lightgbm_large.txt').exists():
        candidate = art / 'best_lightgbm_large.txt'
    elif (art / 'best_lightgbm.txt').exists():
        candidate = art / 'best_lightgbm.txt'
    if candidate:
        try:
            import lightgbm as lgb
            booster = lgb.Booster(model_file=str(candidate))
            meta.model_ensemble = [{'model': booster, 'performance': 1.0, 'architecture': 'lightgbm_booster_file'}]
            print('Loaded booster for backtest:', candidate)
        except Exception as e:
            print('Failed loading booster:', e)

# Prepare results
trades = []
balance = 0.0
equity = 0.0
peak = 0.0
max_dd = 0.0

# Iterate rows and simulate
numeric_df = df.select_dtypes(include=[np.number]).fillna(method='ffill').fillna(0)
for i in range(len(numeric_df) - HOLD_BARS - 1):
    row = numeric_df.iloc[i:i+1]
    # Build mapped input like engine: prefer close, volume, sma_1T, ema_15T, rsi_60T
    preferred = ['close', 'volume', 'sma_1T', 'ema_15T', 'rsi_60T']
    vals = []
    model = None
    try:
        if hasattr(meta, 'model_ensemble') and meta.model_ensemble:
            model = meta.model_ensemble[0]['model']
            fn = model.feature_name() or []
            n_feat = len(fn) if fn else 5
        else:
            n_feat = 5
    except Exception:
        n_feat = 5

    for j in range(n_feat):
        if j < len(preferred) and preferred[j] in row.columns:
            v = float(row[preferred[j]].iloc[0])
        elif j < len(row.columns):
            v = float(row.iloc[0, j])
        else:
            v = 0.0
        vals.append(v)

    X = pd.DataFrame([vals], columns=[f'Column_{k}' for k in range(len(vals))])

    # Predict
    try:
        if hasattr(meta, 'ensemble_predict'):
            pred = float(meta.ensemble_predict(X)[0])
        else:
            pred = 0.5
    except Exception as e:
        # fallback neutral
        pred = 0.5

    if pred > THRESHOLD:
        # enter long at next bar close
        entry_price = numeric_df['close'].iloc[i+1]
        exit_price = numeric_df['close'].iloc[i+1+HOLD_BARS]
        pnl = exit_price - entry_price
        trades.append({'i': i, 'entry': entry_price, 'exit': exit_price, 'pnl': pnl, 'pred': pred})
        balance += pnl
        equity = balance
        if equity > peak:
            peak = equity
        dd = (equity - peak)
        if dd < max_dd:
            max_dd = dd

# Summarize
total = len(trades)
profit_trades = sum(1 for t in trades if t['pnl'] > 0)
net_pnl = sum(t['pnl'] for t in trades)
avg_pnl = (net_pnl / total) if total > 0 else 0.0
max_drawdown = abs(max_dd)

report = {
    'dataset_rows': len(numeric_df),
    'threshold': THRESHOLD,
    'hold_bars': HOLD_BARS,
    'total_trades': total,
    'profitable_trades': profit_trades,
    'net_pnl': net_pnl,
    'avg_pnl': avg_pnl,
    'max_drawdown': max_drawdown,
    'sample_trades': trades[:20]
}

REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(REPORT_FILE, 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, default=float)

print('Backtest complete. Report:', REPORT_FILE)
print('Summary:', report)
