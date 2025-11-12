import json
import MetaTrader5 as mt5
symbols = ['BTCUSD','ETHUSD','XAUUSD','USDCAD','AUDNZD','EURJPY','GBPCHF','NZDJPY','EURUSD','EURAUD','US500.cash','JP225.cash']
res = {}
init_ok = False
try:
    init_ok = mt5.initialize()
except Exception as e:
    res['error'] = str(e)
acc = None
if init_ok:
    try:
        ai = mt5.account_info()
        if ai is not None:
            acc = {'login': int(ai.login), 'server': str(ai.server)}
    except Exception as e:
        res['account_error'] = str(e)
syminfo = {}
for s in symbols:
    try:
        info = mt5.symbol_info(s)
        syminfo[s] = (info is not None)
    except Exception as e:
        syminfo[s] = False
        res.setdefault('symbol_errors', {})[s] = str(e)
res.update({'mt5_initialized': bool(init_ok), 'account': acc, 'symbols': syminfo})
print(json.dumps(res))
if init_ok:
    try:
        mt5.shutdown()
    except:
        pass
