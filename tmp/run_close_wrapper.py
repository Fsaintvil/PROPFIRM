# Wrapper to run scripts/close_all_positions.py with repository root on sys.path
import sys
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
# Ensure repo root is first on sys.path
if str(repo) not in sys.path:
    sys.path.insert(0, str(repo))
# Execute target script
script = repo / 'scripts' / 'close_all_positions.py'
with open(script, 'r', encoding='utf-8') as f:
    code = f.read()
# Execute in a clean globals dict
_globals = {
    '__name__': '__main__',
    '__file__': str(script),
}
exec(compile(code, str(script), 'exec'), _globals)
