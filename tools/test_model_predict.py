"""Test non invasif: charger le modèle LightGBM et prédire sur une entrée 1x5.
Sauvegarde le résultat dans artifacts/test_model_predict.json
"""
from pathlib import Path
import json
import pandas as pd
import numpy as np

art = Path('artifacts') / 'auto_improve'
model_file = art / 'best_lightgbm_large.txt'
if not model_file.exists():
    model_file = art / 'best_lightgbm.txt'

if not model_file.exists():
    raise SystemExit('No model found')

import lightgbm as lgb
booster = lgb.Booster(model_file=str(model_file))

# Lire sample
df = pd.read_csv('data/features_sample.csv', index_col=0)
# Choisir features candidates
cols = ['close','volume','sma_1T','ema_15T','rsi_60T']
for c in cols:
    if c not in df.columns:
        raise SystemExit(f'missing feature {c} in sample')

row = df.iloc[-1][cols].astype(float).values.reshape(1, -1)

# Prédire avec override de shape check
try:
    preds = booster.predict(row, predict_disable_shape_check=True)
except TypeError:
    preds = booster.predict(row)

report = {
    'model': str(model_file),
    'input_shape': row.shape,
    'prediction': preds.tolist()
}

Path('artifacts').mkdir(exist_ok=True)
with open('artifacts/test_model_predict.json','w',encoding='utf-8') as f:
    json.dump(report, f, indent=2)

print('wrote artifacts/test_model_predict.json')
print(report)
