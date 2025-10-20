"""Inspecte le modèle LightGBM et affiche les feature names attendues.
Usage: python tools/inspect_lightgbm_model.py
"""
from pathlib import Path
import json

art = Path('artifacts') / 'auto_improve'
models = list(art.glob('best_lightgbm*.txt')) if art.exists() else []
print('models found:', models)

if not models:
    raise SystemExit('No model files found')

import lightgbm as lgb
for m in models:
    try:
        booster = lgb.Booster(model_file=str(m))
        print('\nMODEL:', m)
        try:
            fn = booster.feature_name()
            print('feature_names:', fn)
            print('num features:', len(fn))
        except Exception as e:
            print('error getting feature_name():', e)
    except Exception as e:
        print('failed to load', m, 'error:', e)
