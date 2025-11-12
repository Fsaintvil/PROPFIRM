import sys, json
print('python', sys.version)
print(json.dumps(sys.path, indent=2))
try:
    import src
    print('import src OK')
except Exception as e:
    print('import src FAIL:', type(e).__name__, e)
try:
    import MetaTrader5 as mt5
    print('import mt5 OK', getattr(mt5, '__version__', 'unknown'))
except Exception as e:
    print('import mt5 FAIL:', type(e).__name__, e)
