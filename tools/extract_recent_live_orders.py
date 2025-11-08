#!/usr/bin/env python3
"""
Scan logs modified within the last 72 hours, extract JSON objects with 'mode'=='live' or equivalent,
produce an NDJSON replay file under artifacts/ready_for_apply/replay_from_recent_<ts>.ndjson
and a summary JSON in logs/replay_summary_<ts>.json
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / 'logs'
OUT_DIR = ROOT / 'artifacts' / 'ready_for_apply'
OUT_DIR.mkdir(parents=True, exist_ok=True)

now = datetime.utcnow()
cutoff = now - timedelta(hours=72)

collected = []
seen = set()

def find_live_in_obj(obj):
    results = []
    if isinstance(obj, dict):
        # heuristic: exact 'mode' == 'live' OR presence of fields typical of orders and mode missing but comments indicate 'live'
        if obj.get('mode') == 'live':
            results.append(obj)
        else:
            # also consider objects with 'action' or 'type' and 'mode' missing but 'type_hint' or 'comment' includes 'live' or 'canary'
            if ('action' in obj or 'type' in obj or 'price' in obj) and obj.get('mode') is None:
                # treat as live if comment/magic indicates
                c = obj.get('comment','') or obj.get('type_hint','') or obj.get('mode','')
                if isinstance(c, str) and ( 'live' in c or 'canary' in c or 'periodic' in c or 'retry' in c or 'manual' in c):
                    results.append(obj)
        # recurse
        for v in obj.values():
            results.extend(find_live_in_obj(v))
    elif isinstance(obj, list):
        for it in obj:
            results.extend(find_live_in_obj(it))
    return results


def process_file(p: Path):
    try:
        mtime = datetime.utcfromtimestamp(p.stat().st_mtime)
    except Exception:
        return []
    if mtime < cutoff:
        return []
    text = p.read_text(encoding='utf-8', errors='ignore')
    items = []
    # try full JSON
    try:
        data = json.loads(text)
        items = find_live_in_obj(data)
    except Exception:
        # try NDJSON (one JSON obj per line)
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                items.extend(find_live_in_obj(obj))
            except Exception:
                # fallback: search for '"mode"\s*:\s*"live"' and attempt to extract nearby braces
                if '"mode"' in ln and 'live' in ln:
                    try:
                        # attempt to find JSON substring
                        start = ln.find('{')
                        end = ln.rfind('}')
                        if start != -1 and end != -1 and end>start:
                            sub = ln[start:end+1]
                            obj = json.loads(sub)
                            items.extend(find_live_in_obj(obj))
                    except Exception:
                        pass
    # normalize items (ensure dicts)
    normalized = []
    for obj in items:
        if not isinstance(obj, dict):
            continue
        # create a canonical key to dedupe
        key = json.dumps({k: obj.get(k) for k in sorted(obj.keys()) if k in ('symbol','price','volume','action','type','comment')}, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(obj)
    return normalized


if __name__ == '__main__':
    files = list(LOGS.glob('*'))
    files = [f for f in files if f.is_file()]
    total_found = 0
    for f in files:
        found = process_file(f)
        if found:
            for o in found:
                collected.append({'source': str(f), 'obj': o})
            total_found += len(found)
    ts = now.strftime('%Y%m%d_%H%M%S')
    replay_path = OUT_DIR / f'replay_from_recent_{ts}.ndjson'
    summary_path = LOGS / f'replay_summary_{ts}.json'
    # write NDJSON
    with replay_path.open('w', encoding='utf-8') as out:
        for rec in collected:
            out.write(json.dumps(rec['obj'], ensure_ascii=False) + '\n')
    summary = {
        'generated_at': now.isoformat()+'Z',
        'cutoff': cutoff.isoformat()+'Z',
        'total_sources_scanned': len(files),
        'total_collected': len(collected),
        'replay_file': str(replay_path),
        'samples': []
    }
    for rec in collected[:20]:
        summary['samples'].append({'source': rec['source'], 'symbol': rec['obj'].get('symbol'), 'price': rec['obj'].get('price'), 'comment': rec['obj'].get('comment')})
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Scanned {len(files)} log files, collected {len(collected)} live-order objects.')
    print('Replay NDJSON:', replay_path)
    print('Summary JSON:', summary_path)
