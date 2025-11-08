import os
import py_compile
import traceback

fails = []
SKIP_DIR_KEYWORDS = {'.venv', 'venv', 'env', 'site-packages', '.git', 'node_modules', 'tmp', 'tmp_lgb', 'artifacts', 'backups', 'archive'}

for dirpath, dirnames, filenames in os.walk('.'):
    norm_dir = dirpath.replace('\\', '/').lower()
    if any(k in norm_dir for k in SKIP_DIR_KEYWORDS):
        # skip virtualenvs, large vendor dirs and caches where we don't want to write .pyc
        continue

    for fname in filenames:
        if not fname.endswith('.py'):
            continue
        path = os.path.join(dirpath, fname)
        try:
            py_compile.compile(path, doraise=True)
        except Exception:
            tb = traceback.format_exc()
            fails.append((path, tb))

if not fails:
    print('OK')
    raise SystemExit(0)

print(f'FAILURES: {len(fails)}')
for path, tb in fails:
    print('\n---', path)
    print(tb)

# exit with non-zero so CI can detect
raise SystemExit(1)
