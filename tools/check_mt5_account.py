import MetaTrader5 as mt5
import json

res = {'initialized': False, 'account': None}
if mt5.initialize():
    res['initialized'] = True
    ai = mt5.account_info()
    try:
        res['account'] = ai._asdict()
    except Exception:
        res['account'] = str(ai)
    mt5.shutdown()
print(json.dumps(res))
