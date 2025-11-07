import json
from pathlib import Path
import sys
import pandas as pd
import numpy as np
import math

# ensure repo root
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from meta_learning_system import MetaLearningTradingSystem

DATA_FILE = Path('data') / 'features_sample.csv'
REPORT_FILE = Path('artifacts') / 'backtest_realistic_report_detailed.json'
INITIAL_CAPITAL = 10000.0
RISK_PER_TRADE = 0.01
HOLD_BARS = 5
SLIPPAGE = 0.0002
COMMISSION_RATE = 0.0005
MAX_NOTIONAL_PCT = 0.05
MIN_ATR_PCT = 0.0005

print('Loading data...')
df = pd.read_csv(DATA_FILE)
if 'Unnamed: 0' in df.columns:
    df = df.set_index('Unnamed: 0')
    df.index = pd.to_datetime(df.index)

numeric_df = df.select_dtypes(include=[np.number]).fillna(method='ffill').fillna(0)

meta = MetaLearningTradingSystem(max_models=3)
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

preferred = ['close', 'volume', 'sma_1T', 'ema_15T', 'rsi_60T']
inputs = []
for i in range(len(numeric_df) - HOLD_BARS - 1):
    row = numeric_df.iloc[i:i+1]
    vals = []
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

X_all = pd.DataFrame([v for _, v in inputs], columns=[f'Column_{k}' for k in range(len(inputs[0][1]))])
try:
    preds = meta.ensemble_predict(X_all)
except Exception:
    preds = np.array([0.5] * len(X_all))

capital = INITIAL_CAPITAL
equity_curve = [capital]
trade_records = []

for idx, pred in enumerate(preds):
    i = inputs[idx][0]
    if pred <= 0.50:
        equity_curve.append(capital)
        continue

    entry_price = numeric_df['close'].iloc[i+1]
    window = 14
    if i >= window:
        vol = numeric_df['close'].pct_change().iloc[i-window+1:i+1].std()
        atr = vol * entry_price
    else:
        atr = numeric_df['close'].pct_change().iloc[:i+1].std() * entry_price
        if np.isnan(atr):
            atr = entry_price * 0.001
    atr_floor = max(atr, entry_price * MIN_ATR_PCT)
    sl = entry_price - 2 * atr_floor
    tp = entry_price + 3 * atr_floor

    risk_amount = capital * RISK_PER_TRADE
    if entry_price - sl <= 0:
        units = 0
    else:
        units = risk_amount / (entry_price - sl)
    max_notional = capital * MAX_NOTIONAL_PCT
    max_units = max_notional / entry_price if entry_price > 0 else 0
    if units > max_units:
        units = max_units

    entry_effective = entry_price + SLIPPAGE
    commission_entry = entry_effective * units * COMMISSION_RATE
    exit_price = numeric_df['close'].iloc[i+1+HOLD_BARS]
    exit_effective = exit_price - SLIPPAGE
    commission_exit = exit_effective * units * COMMISSION_RATE
    pnl = (exit_effective - entry_effective) * units - (commission_entry + commission_exit)

    capital += pnl
    equity_curve.append(capital)
    trade_records.append({'i': i, 'entry': entry_price, 'exit': exit_price, 'units': units, 'pnl': pnl, 'pred': float(pred)})

# metrics
net_pnl = capital - INITIAL_CAPITAL
total_trades = len(trade_records)
profitable = sum(1 for t in trade_records if t['pnl'] > 0)

# equity series
eq = np.array(equity_curve)
peak = np.maximum.accumulate(eq)
drawdowns = (peak - eq)
max_dd = float(np.max(drawdowns))

# annualized returns approximations: assume data frequency ~ 1 minute? using 252 trading days * 24*60 if minute -> unrealistic.
# We'll estimate annualization factor from timestamps if present
if hasattr(df.index, 'to_series'):
    try:
        total_days = (df.index[-1] - df.index[0]).days
        years = total_days / 365.25 if total_days > 0 else 1/252
        cagr = ((capital / INITIAL_CAPITAL) ** (1 / years) - 1) if years>0 else float('nan')
    except Exception:
        cagr = float('nan')
else:
    cagr = float('nan')

# daily returns from equity curve (approx)
returns = np.diff(eq) / eq[:-1]
if len(returns) > 1:
    ann_vol = np.std(returns) * math.sqrt(252)
    sharpe = (np.mean(returns) / np.std(returns)) * math.sqrt(252) if np.std(returns)>0 else float('nan')
else:
    ann_vol = float('nan')
    sharpe = float('nan')

report = {
    'initial_capital': INITIAL_CAPITAL,
    'final_capital': capital,
    'net_pnl': net_pnl,
    'total_trades': total_trades,
    'profitable_trades': profitable,
    'max_drawdown': max_dd,
    'cagr_est': cagr,
    'annualized_vol_est': ann_vol,
    'sharpe_est': sharpe,
    'equity_curve_sample': eq.tolist()[:200],
    'sample_trades': trade_records[:100]
}

REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(REPORT_FILE, 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2, default=float)

print('Detailed backtest done. Report:', REPORT_FILE)
print('Net PnL:', net_pnl, 'Trades:', total_trades, 'Profitable:', profitable, 'Max DD:', max_dd)
