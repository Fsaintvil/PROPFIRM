import importlib, traceback
try:
    m = importlib.import_module('src.utils.mt5_safe')
    print('module imported OK')
    print(sorted([n for n in dir(m) if not n.startswith('_')]))
except Exception:
    traceback.print_exc()
