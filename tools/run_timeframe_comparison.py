"""Runner rapide: compare M1, M5, M15 en mode simulation
Charge best_lightgbm model et data/features_sample.csv (déjà en M1)
Resample pour M5/M15 en prenant la dernière 'close' et somme des volumes
Simule trading en utilisant la logique de optimize_threshold.py
"""

from pathlib import Path
import pandas as pd
import numpy as np
import json
import lightgbm as lgb

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data' / 'features_sample.csv'
ART = BASE / 'artifacts' / 'auto_improve'
MODEL_FILE = ART / 'best_lightgbm.txt'
BEST_JSON = ART / 'best.json'

OUT_DIR = BASE / 'artifacts' / 'auto_improve' / 'timeframe_comparison'
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_model():
    if not MODEL_FILE.exists():
        raise FileNotFoundError(str(MODEL_FILE))
    model = lgb.Booster(model_file=str(MODEL_FILE))
    return model


def load_data():
    df = pd.read_csv(DATA, parse_dates=[0], index_col=0)
    if df.index.tzinfo is None:
        df.index = pd.to_datetime(df.index)
    return df


def resample_df(df, minutes):
    if minutes == 1:
        return df.copy()
    rule = f'{minutes}min'
    # For close take last, for volume sum, for indicators take last
    agg = {
        'close': 'last',
        'volume': 'sum'
    }
    # other numeric columns -> last
    other_cols = [c for c in df.columns if c not in ['close', 'volume']]
    for c in other_cols:
        agg[c] = 'last'
    res = df.resample(rule).agg(agg).dropna()
    return res


def prepare_X(df, model):
    # try to get feature names from model if available
    try:
        fn = model.feature_name() or []
    except Exception:
        fn = []
    # if fn non vide, try to align, else use numeric columns
    if fn:
        X = df.reindex(columns=fn).fillna(method='ffill').fillna(0)
    else:
        X = df.select_dtypes(include=[np.number])
        X = X.fillna(method='ffill').fillna(0)
    return X


def simulate(df, model, threshold=0.68, horizon=15):
    X = prepare_X(df, model)
    preds = model.predict(X.values)
    # simulation: when pred > threshold enter long at next bar close,
    # exit after horizon bars
    returns = []
    positions = []
    for i in range(len(df)-horizon-1):
        if preds[i] > threshold:
            entry = df['close'].iloc[i+1]
            exit_p = df['close'].iloc[i+1+horizon]
            ret = (exit_p - entry) / entry
            returns.append(ret)
            positions.append(1)
        else:
            returns.append(0)
            positions.append(0)
    returns = np.array(returns)
    num_trades = int(np.sum(np.array(positions) == 1))
    total_return = float(np.sum(returns))
    win_rate = (
        float(np.sum(returns > 0) / num_trades) if num_trades > 0 else 0.0
    )
    avg_return = (
        float(np.mean(returns[returns != 0])) if num_trades > 0 else 0.0
    )
    sharpe = (
        float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 24))
        if np.std(returns) > 0
        else 0.0
    )
    # drawdown
    cumulative = np.cumsum(returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    max_dd = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0
    return {
        'num_trades': num_trades,
        'total_return': total_return,
        'win_rate': win_rate,
        'avg_return': avg_return,
        'sharpe': sharpe,
        'max_drawdown': max_dd
    }


def main():
    model = load_model()
    with open(BEST_JSON, 'r', encoding='utf-8') as f:
        best = json.load(f)
    horizon = int(best.get('horizon', 15))

    df = load_data()
    results = {}
    for minutes in [1, 5, 15]:
        df_r = resample_df(df, minutes)
        res = simulate(df_r, model, threshold=0.68, horizon=horizon)
        res['rows'] = len(df_r)
        results[f'{minutes}min'] = res
        print(f'{minutes}min ->', res)
    with open(
        OUT_DIR / 'timeframe_comparison.json', 'w', encoding='utf-8'
    ) as f:
        json.dump({'horizon': horizon, 'results': results}, f, indent=2)
    print('Saved results to', OUT_DIR / 'timeframe_comparison.json')
 

if __name__ == '__main__':
    main()
