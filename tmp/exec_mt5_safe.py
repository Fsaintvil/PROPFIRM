from pathlib import Path
import traceback
p = Path('src') / 'utils' / 'mt5_safe.py'
print('execing', p.resolve())
code = p.read_text(encoding='utf-8')
_g = {'__name__':'__main__', '__file__':str(p)}
try:
    exec(compile(code, str(p), 'exec'), _g)
    print('exec succeeded, keys:', sorted([k for k in _g.keys() if not k.startswith('__')])[:50])
except Exception:
    print('exec failed:')
    traceback.print_exc()
