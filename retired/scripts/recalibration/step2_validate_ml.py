"""Step 2: Fetch MT5 rates for each historical trade and compute features"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pickle
from collections import defaultdict
from datetime import datetime, timedelta

import MetaTrader5 as mt5_module

from engine_simple.ml_features import FeatureEngine

try:
    from engine_simple.ml_ensemble import MLEnsemble
    _ML_AVAILABLE = True
except ImportError:
    MLEnsemble = None
    _ML_AVAILABLE = False
import contextlib

import config_simple as cfg
from engine_simple.mt5_connector import MT5Connector

# Load trades
with open("runtime/historical_trades.pkl", "rb") as f:
    trades = pickle.load(f, encoding="utf-8")
print(f"Loaded {len(trades)} trades")

# Filter to symbols the robot currently trades
ROBOT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF",
                 "AUDUSD", "NZDUSD", "EURJPY", "GBPJPY",
                 "XAUUSD", "ETHUSD", "USOIL.cash"]
trades = [t for t in trades if t['symbol'] in ROBOT_SYMBOLS]
print(f"After symbol filter: {len(trades)} trades")

# Group by (symbol, date) for efficient fetching
grouped = defaultdict(list)
for t in trades:
    date_key = t['time_open'][:10]
    grouped[(t['symbol'], date_key)].append(t)

print(f"Unique (symbol, date) groups: {len(grouped)}")

# Initialize MT5

connector = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
connector.connect()
TIMEFRAMES = {"H1": mt5_module.TIMEFRAME_H1, "M15": mt5_module.TIMEFRAME_M15, "M5": mt5_module.TIMEFRAME_M5}
TF_NAMES = ["H1", "M15", "M5"]

# Cache: rates_cache[(symbol, tf)] = {'rates': np.array, 'from_time': datetime}
rates_cache: dict = {}

def get_rates_before(symbol, tf_name, before_time, n_bars=300):
    """Get n_bars of rates before the given time"""
    key = (symbol, tf_name)
    if key in rates_cache:
        cached = rates_cache[key]
        # Check if cache covers the requested time (compare timestamps)
        cached_from = datetime.fromtimestamp(cached['from_time'])
        if cached_from <= before_time:
            return cached['rates']

    # Fetch from MT5
    mt5_tf = TIMEFRAMES[tf_name]
    # Fetch 300 bars back from before_time
    dt = before_time - timedelta(hours=1)
    rates = mt5_module.copy_rates_from(symbol, mt5_tf, dt, n_bars)
    if rates is not None and len(rates) > 0:
        rates_cache[key] = {'rates': rates, 'from_time': rates[0][0]}
        return rates
    return None

# Initialize ML
print("Loading ML Ensemble...")
ml = None
if _ML_AVAILABLE:
    try:
        ml = MLEnsemble()
        print("ML loaded")
    except Exception as e:
        print(f"ML load failed: {e}")
else:
    print("ML Ensemble not available (module disabled)")

feature_engine = FeatureEngine()

# Process trades
results = []
batch_size = 50
total = len(trades)
batch_num = 0
count_no_rates = 0
count_feature_err = 0
count_ok = 0

for i, trade in enumerate(trades):
    if i % 100 == 0:
        print(f"Processing {i}/{total} (ok={count_ok}, no_rates={count_no_rates}, feat_err={count_feature_err})...")

    symbol = trade['symbol']
    direction = trade['direction']
    won = trade['won']
    profit = trade['profit']

    # Parse time_open (support both . and - separators)
    try:
        ts = trade['time_open'].replace('.', '-')
        t_open = datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')
    except (ValueError, KeyError):
        continue

    rates_dict = {}
    for tf in TF_NAMES:
        rates = get_rates_before(symbol, tf, t_open, 300)
        if rates is not None and len(rates) >= 50:
            rates_dict[tf] = rates

    if not rates_dict or "H1" not in rates_dict:
        count_no_rates += 1
        continue

    # Compute features
    h1 = rates_dict["H1"]
    if len(h1) < 50:
        count_no_rates += 1
        continue

    try:
        features = feature_engine.compute_features(h1)
        if features is None:
            count_feature_err += 1
            continue
    except Exception as e:
        count_feature_err += 1
        if count_feature_err <= 5:
            print(f"  Feature error {symbol}@{trade['time_open']}: {e}")
        continue

    # Run ML prediction
    ml_result = None
    if ml is not None:
        with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError):
            ml_result = ml.predict(symbol, rates_dict)

    r = {
        'symbol': symbol,
        'direction': direction,
        'won': won,
        'profit': profit,
        'time_open': trade['time_open'],
        'features': features,
        'ml': ml_result,
    }
    results.append(r)
    count_ok += 1

    # Batch save
    if len(results) % batch_size == 0:
        with open("runtime/ml_validation_batch.pkl", "wb") as batch_f:
            pickle.dump(results, batch_f)
        batch_num += 1

# Final save
with open("runtime/ml_validation.pkl", "wb") as final_f:
    pickle.dump(results, final_f)
print(f"\nSaved {len(results)} validated trades "
      f"(ok={count_ok}, no_rates={count_no_rates}, "
      f"feat_err={count_feature_err})")

# Quick stats
if results:
    total_ml = sum(1 for r in results if r['ml'] is not None)
    print(f"Trades with ML predictions: {total_ml}/{len(results)}")

    # ML accuracy if available
    correct = 0
    total_pred = 0
    for r in results:
        ml = r['ml']
        if ml and ml.get('action') != 'HOLD':
            total_pred += 1
            ml_buy = ml['action'] == 'BUY'
            trade_buy = r['direction'] == 'BUY'
            if ml_buy == trade_buy:
                correct += 1
    if total_pred > 0:
        print(f"ML direction accuracy: {correct}/{total_pred} = {correct/total_pred*100:.1f}%")
