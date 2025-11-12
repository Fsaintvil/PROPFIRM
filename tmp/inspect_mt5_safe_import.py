import sys
import traceback
import importlib
from pathlib import Path

print('cwd=', Path('.').resolve())
print('sys.executable=', sys.executable)
print('sys.path[:6]=')
for p in sys.path[:10]:
    print('  ', p)

try:
    m = importlib.import_module('src.utils.mt5_safe')
    print('\nImported module:', m)
    print('module file:', getattr(m, '__file__', None))
    print('dir(module)[:80]=', dir(m)[:80])
    # show whether send_order exists
    print('has send_order =', hasattr(m, 'send_order'))
    if hasattr(m, 'send_order'):
        print('send_order object:', m.send_order)
except Exception:
    print('\nException during import:')
    traceback.print_exc()

# Now try direct "from ... import"
try:
    from src.utils.mt5_safe import send_order, Mt5OrderError
    print('\nFrom-import succeeded:', send_order, Mt5OrderError)
except Exception:
    print('\nException during from-import:')
    traceback.print_exc()
