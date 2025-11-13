#!/usr/bin/env python3
import json
import sys
try:
    import MetaTrader5 as mt5
except Exception as e:
    print(json.dumps({'error':'mt5_import_failed','detail':str(e)}))
    sys.exit(0)
ok = mt5.initialize()
res = {'initialized': bool(ok)}
try:
    pos = mt5.positions_get()
    res['positions'] = []
    if pos:
        for p in list(pos):
            try:
                res['positions'].append({'ticket': int(p.ticket), 'symbol': p.symbol, 'volume': float(p.volume), 'type': int(p.type)})
            except Exception:
                res['positions'].append(str(p))
    else:
        res['positions'] = []
except Exception as e:
    res['positions_error'] = str(e)
try:
    le = mt5.last_error()
    res['last_error'] = le
except Exception as e:
    res['last_error_exception'] = str(e)
try:
    mt5.shutdown()
except Exception:
    pass
print(json.dumps(res, indent=2))
