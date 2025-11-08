"""
Scan logs modified in the last N hours and extract JSON objects that represent live orders.
Produces:
 - logs/recent_live_orders_summary_<ts>.json
 - artifacts/ready_for_apply/replay_from_recent_<ts>.ndjson  (one per found order set)

This is read-only on logs; it will create staging NDJSONs in artifacts/ready_for_apply for operator review.
"""
from pathlib import Path
import json
import datetime


NOW = datetime.datetime.utcnow()
HOURS = 72
ROOT = Path('.')
LOGS = ROOT / 'logs'
ART = ROOT / 'artifacts' / 'ready_for_apply'
# ensure output dirs exist
LOGS.mkdir(parents=True, exist_ok=True)
ART.mkdir(parents=True, exist_ok=True)

cutoff = NOW - datetime.timedelta(hours=HOURS)
found = []

for f in LOGS.rglob('*'):
    if not f.is_file():
        continue
    try:
        mtime = datetime.datetime.utcfromtimestamp(f.stat().st_mtime)
    except Exception:
        continue
    if mtime < cutoff:
        continue
    text = None
    try:
        text = f.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        continue
    # try to parse as JSON array/object
    records = []
    parsed = False
    try:
        j = json.loads(text)
        # if top-level is dict with 'files' or 'orders' etc, try to traverse
        if isinstance(j, dict):
            # flatten potential lists
            # collect any dicts inside recursively
            stack = [j]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    # if this dict looks like an order
                    if cur.get('mode') == 'live' or (cur.get('symbol') and cur.get('mode') == 'live'):
                        records.append(cur)
                    for v in cur.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(cur, list):
                    for item in cur:
                        if isinstance(item, (dict, list)):
                            stack.append(item)
        elif isinstance(j, list):
            for item in j:
                if isinstance(item, dict) and item.get('mode') == 'live':
                    records.append(item)
        parsed = True
    except Exception:
        parsed = False
    # fallback: try NDJSON lines
    if not parsed:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if '"mode"' in line and '"live"' in line:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
                except Exception:
                    # leave it
                    pass
    if records:
        for r in records:
            r['_source_file'] = str(f)
            r['_file_mtime'] = mtime.isoformat() + 'Z'
            found.append(r)

# Summarize and write outputs
ts = NOW.strftime('%Y%m%d_%H%M%S')
summary_path = LOGS / f'recent_live_orders_summary_{ts}.json'
with summary_path.open('w', encoding='utf-8') as out:
    json.dump(
        {
            'scanned_after': cutoff.isoformat() + 'Z',
            'generated_at': NOW.isoformat() + 'Z',
            'count': len(found),
            'orders': found,
        },
        out,
        ensure_ascii=False,
        indent=2,
    )

# create NDJSON replay file (one file containing all found orders, normalized minimal fields)
replay_path = ART / f'replay_from_recent_{ts}.ndjson'
with replay_path.open('w', encoding='utf-8') as rp:
    for o in found:
        # normalize common fields
        rec = {}
        for k in (
            'mode',
            'symbol',
            'action',
            'side',
            'price',
            'volume',
            'sl',
            'tp',
            'comment',
            'type',
            'type_hint',
            'deviation',
            'magic',
        ):
            if k in o:
                rec[k] = o[k]
        # if mode missing but symbol present, force live
        if 'mode' not in rec and 'symbol' in rec:
            rec['mode'] = 'live'
        rp.write(json.dumps(rec, ensure_ascii=False) + '\n')

print('Scanned logs after', cutoff.isoformat() + 'Z')
print('Found', len(found), 'live-like records; summary ->', summary_path)
print('Replay NDJSON ->', replay_path)
