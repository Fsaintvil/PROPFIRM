#!/usr/bin/env python3
"""Monitor artifacts/live_trading for the first mt5_apply_*.json or active_monitor_report_*.txt and summarize it.
Writes summary JSON into artifacts/live_trading/ and prints a short summary to stdout.
"""
import time, pathlib, datetime, json, collections, sys
root = pathlib.Path('artifacts') / 'live_trading'
if not root.exists():
    print('Artifacts folder not found:', root)
    sys.exit(1)
start = datetime.datetime.utcnow()
deadline = start + datetime.timedelta(minutes=40)
seen = set()
found = None
print('Monitoring', root, 'until', deadline.isoformat())
while datetime.datetime.utcnow() < deadline:
    js = sorted(root.glob('mt5_apply_*.json'), key=lambda p: p.stat().st_mtime)
    txts = sorted(root.glob('active_monitor_report_*.txt'), key=lambda p: p.stat().st_mtime)
    candidates = js + txts
    new = [p for p in candidates if str(p) not in seen]
    if new:
        # pick the earliest new
        found = new[0]
        break
    time.sleep(10)
report = {'monitored_from': start.isoformat(), 'found': None}
if not found:
    report['status'] = 'timeout'
    print('No file found within timeout')
else:
    report['found'] = str(found)
    print('Found file:', found)
    if found.suffix == '.json':
        try:
            data = json.loads(found.read_text(encoding='utf-8'))
        except Exception as e:
            report['error'] = f'json_load_error: {e!r}'
            print(report['error'])
        else:
            entries = None
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                for k in ('results','entries','orders','applies','payloads','items'):
                    if k in data and isinstance(data[k], list):
                        entries = data[k]
                        break
                if entries is None:
                    # flatten dict values
                    flat = []
                    for v in data.values():
                        if isinstance(v, list):
                            flat.extend(v)
                        elif isinstance(v, dict):
                            flat.append(v)
                    if flat:
                        entries = flat
            if entries is None:
                report['note'] = 'no entries list discovered'
                report['raw_keys'] = list(data.keys()) if isinstance(data, dict) else None
            else:
                rc_counts = collections.Counter()
                examples = {}
                applied_count = 0
                for e in entries:
                    rc = None
                    if isinstance(e, dict):
                        for k in ('retcode','result_code','code','status'):
                            if k in e:
                                rc = e[k]
                                break
                        if rc is None and 'result' in e and isinstance(e['result'], dict):
                            rc = e['result'].get('retcode')
                        if 'applied' in e and bool(e.get('applied')):
                            applied_count += 1
                        if rc is None and 'mt5_retcode' in e:
                            rc = e.get('mt5_retcode')
                    if rc is None:
                        rc = 'unknown'
                    rc_counts[str(rc)] += 1
                    if str(rc) not in examples and isinstance(e, dict):
                        examples[str(rc)] = e
                report['retcode_counts'] = dict(rc_counts)
                report['applied_count'] = applied_count
                # store only small examples
                report['examples'] = {k: (repr(v)[:1000]) for k,v in examples.items()}
                print('retcode_counts:', report['retcode_counts'])
    elif found.suffix == '.txt':
        txt = found.read_text(encoding='utf-8', errors='ignore')
        lines = txt.splitlines()
        report['text_head'] = '\n'.join(lines[:200])
        print(report['text_head'][:1500])
# write summary
sfile = root / f'live_apply_watch_summary_{datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json'
sfile.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('WROTE SUMMARY:', sfile)
