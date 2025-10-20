import sys
from pathlib import Path
# Ensure repository root is on sys.path when running from tools/
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from meta_learning_system import MetaLearningTradingSystem
import pandas as pd
import numpy as np

print('Loading test dataset...')
df = pd.read_csv('data/features_sample.csv')
if 'Unnamed: 0' in df.columns:
    df = df.set_index('Unnamed: 0')
    df.index = pd.to_datetime(df.index)

meta = MetaLearningTradingSystem(max_models=3)
print('Model ensemble size:', len(meta.model_ensemble))

last = df.select_dtypes(include=[np.number]).iloc[-1:]
print('Last features columns:', last.columns.tolist())
print('Last features shape:', last.shape)

try:
    preds = meta.ensemble_predict(last)
    print('Prediction shape:', np.shape(preds))
    print('Prediction:', preds)
except Exception as e:
    print('Prediction error:', e)
    import traceback
    traceback.print_exc()

# If model available, inspect feature_name
if meta.model_ensemble and meta.model_ensemble[0].get('model') is not None:
    model = meta.model_ensemble[0]['model']
    try:
        print('Model feature_name():', model.feature_name())
    except Exception as e:
        print('Model.feature_name() error:', e)
