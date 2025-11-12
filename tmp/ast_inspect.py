import ast
from pathlib import Path
p = Path('src') / 'utils' / 'mt5_safe.py'
text = p.read_text(encoding='utf-8')
print('len text', len(text))
mod = ast.parse(text)
print('Top-level nodes:')
for n in mod.body:
    print('  ', type(n).__name__, getattr(n, 'name', ''))
