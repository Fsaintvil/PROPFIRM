#!/usr/bin/env python3
"""Surveille le répertoire artifacts/live_trading pour le prochain fichier mt5_apply_*.json
créé après l'instant d'exécution. Timeout 60 minutes, poll toutes les 10s.
Écrit un résumé JSON dans le même dossier avec le préfixe
`live_apply_watch_next_summary_YYYYmmddTHHMMSSZ.json`.
"""
import time
import pathlib
import datetime
import json
import collections


def main():
    root = pathlib.Path('artifacts') / 'live_trading'
    root.mkdir(parents=True, exist_ok=True)
    start = datetime.datetime.utcnow()
    threshold = start.timestamp()
    deadline = start + datetime.timedelta(minutes=60)
    print('Watch started at', start.isoformat())
    found = None
    while datetime.datetime.utcnow() < deadline:
        candidates = sorted(root.glob('mt5_apply_*.json'), key=lambda p: p.stat().st_mtime)
        new = [p for p in candidates if p.stat().st_mtime > threshold]
        if new:
            found = new[0]
            break
        time.sleep(10)

    summary = {'watched_from': start.isoformat(), 'found': None}
    if not found:
        summary['status'] = 'timeout'
        print('No new mt5_apply_*.json within 60 minutes')
    else:
        summary['found'] = str(found)
        print('Found new file:', found)
        try:
            text = found.read_text(encoding='utf-8')
            data = json.loads(text)
        except Exception as e:
            summary['error'] = repr(e)
        else:
            entries = None
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                for k in ('results', 'entries', 'orders', 'applies', 'payloads', 'items'):
                    if k in data and isinstance(data[k], list):
                        entries = data[k]
                        break
                if entries is None:
                    flat = []
                    for v in data.values():
                        if isinstance(v, list):
                            flat.extend(v)
                        elif isinstance(v, dict):
                            flat.append(v)
                    if flat:
                        entries = flat

            if entries is None:
                summary['note'] = 'no entries list discovered'
                summary['raw_keys'] = list(data.keys()) if isinstance(data, dict) else None
            else:
                rc_counts = collections.Counter()
                examples = {}
                applied = 0
                for e in entries:
                    rc = None
                    if isinstance(e, dict):
                        for k in ('retcode', 'result', 'code', 'status'):
                            if k in e:
                                v = e[k]
                                if k == 'result' and isinstance(v, dict):
                                    rc = v.get('retcode')
                                else:
                                    rc = v
                                break
                        if rc is None and e.get('applied'):
                            applied += 1
                        if rc is None and 'mt5_retcode' in e:
                            rc = e.get('mt5_retcode')
                    if rc is None:
                        rc = 'unknown'
                    rc_counts[str(rc)] += 1
                    if str(rc) not in examples and isinstance(e, dict):
                        examples[str(rc)] = e
                summary['retcode_counts'] = dict(rc_counts)
                summary['applied_count'] = applied
                # keep small examples only
                summary['examples'] = {k: (repr(v)[:800]) for k, v in examples.items()}

    out = root / f'live_apply_watch_next_summary_{datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('WROTE', out)


if __name__ == '__main__':
    main()
