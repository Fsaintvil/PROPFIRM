"""Afficher un petit diff entre le NDJSON original et le NDJSON patché pour JP225."""
from pathlib import Path
import json

orig = Path('artifacts/tmp_send_20251104T013642Z/send_JP225.cash.ndjson')
patched = Path('artifacts/ready_for_apply/send_JP225.cash.patched.ndjson')
if not orig.exists() or not patched.exists():
    print('Fichiers manquants. Assurez-vous que les chemins existent.')
    raise SystemExit(1)

orig_lines = [json.loads(l) for l in orig.read_text(encoding='utf-8').splitlines() if l.strip()]
pat_lines = [json.loads(l) for l in patched.read_text(encoding='utf-8').splitlines() if l.strip()]

diffs = []
for i, (o, p) in enumerate(zip(orig_lines, pat_lines)):
    if o.get('sl') != p.get('sl') or o.get('tp') != p.get('tp'):
        diffs.append((i, o, p))

print(f'Found {len(diffs)} diffs (showing up to 12):')
for i, o, p in diffs[:12]:
    print('---')
    print(f'line {i+1}')
    print('before:', {'price': o.get('price'), 'sl': o.get('sl'), 'tp': o.get('tp'), 'type_hint': o.get('type_hint')})
    print(' after:', {'price': p.get('price'), 'sl': p.get('sl'), 'tp': p.get('tp'), 'type_hint': p.get('type_hint')})

if len(diffs) > 12:
    print('...')
