from meta_learning_system import MetaLearningTradingSystem
import lightgbm as lgb
from pathlib import Path
import pandas as pd, numpy as np

print('--- START TEST: MetaLearning + explicit booster load')
# instantiate shim
m = MetaLearningTradingSystem()
print('shim.loaded_model_path =', getattr(m, 'loaded_model_path', None))
me = getattr(m, 'model_ensemble', None)
print('shim.model_ensemble_len =', len(me) if me is not None else 'NO_ATTR')

# If ensemble empty, try to load booster explicitly
if not me:
    cand = Path('artifacts') / 'auto_improve' / 'best_lightgbm.txt'
    print('candidate path exists =', cand.exists(), str(cand))
    booster = None
    try:
        booster = lgb.Booster(model_file=str(cand))
        print('booster loaded, n_features =', len(booster.feature_name() or []))
        print('booster.feature_name() sample =', (booster.feature_name() or [])[:10])
    except Exception as e:
        print('booster load error:', e)

    # Build DataFrame using booster.feature_name()
    fn = booster.feature_name() if booster is not None else []
    if not fn:
        fn = ['label_order_success','close','volume','sma_1T','ema_15T']
        print('Using fallback feature list =', fn)

    row = {}
    for name in fn:
        nl = name.lower()
        if 'close' in nl:
            row[name] = 1.15311
        elif 'volume' in nl:
            row[name] = 1000
        elif 'label' in nl:
            row[name] = 0
        elif 'sma' in nl or 'ema' in nl:
            row[name] = 1.152
        elif 'rsi' in nl:
            row[name] = 50
        else:
            row[name] = 0.0

    df = pd.DataFrame([row])
    print('Constructed DataFrame with columns:', df.columns.tolist())

    # If we have booster but shim didn't set ensemble, attach it
    if booster is not None and (not getattr(m,'model_ensemble', None)):
        try:
            m.model_ensemble = [{'model': booster, 'performance':1.0, 'architecture':'lightgbm_booster_file'}]
            m.loaded_model_path = str(cand)
            print('Attached booster to shim.model_ensemble and set loaded_model_path')
        except Exception as e:
            print('Failed to attach booster to shim:', e)

    # Try ensemble_predict
    try:
        preds = m.ensemble_predict(df)
        print('m.ensemble_predict ->', preds)
    except Exception as e:
        print('m.ensemble_predict raised ->', repr(e))

    # Try booster.predict directly
    try:
        arr = df.values
        try:
            preds2 = booster.predict(arr, predict_disable_shape_check=True)
        except TypeError:
            preds2 = booster.predict(arr)
        print('booster.predict ->', preds2)
    except Exception as e:
        print('booster.predict error ->', repr(e))
else:
    print('shim already had model_ensemble - trying ensemble_predict directly')
    try:
        import pandas as pd
        fn = m.model_ensemble[0]['model'].feature_name()
        df = pd.DataFrame([{c:1.0 for c in (fn[:5] or ['close','volume','sma_1T','ema_15T','rsi_60T'])}])
        print('calling m.ensemble_predict on df cols', df.columns.tolist())
        print('->', m.ensemble_predict(df))
    except Exception as e:
        print('direct ensemble_predict failed ->', e)

print('--- END TEST')
