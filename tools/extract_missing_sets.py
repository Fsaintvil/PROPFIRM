"""Extract missing sets from match report and sample broker entries.
Writes:
 - artifacts/live_trading/broker_not_in_local_YYYYMMDDTHHMMSS.csv
 - artifacts/live_trading/local_not_on_broker_YYYYMMDDTHHMMSS.csv
 - artifacts/live_trading/samples_broker_not_in_local_YYYYMMDDTHHMMSS.json
 - artifacts/live_trading/samples_local_not_on_broker_YYYYMMDDTHHMMSS.json

Usage: run from project root: python tools/extract_missing_sets.py
"""
from __future__ import annotations
import csv
import json
import os
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "live_trading"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

# attempt to find latest match_report_*.json in artifacts/live_trading
reports = sorted(ARTIFACTS.glob('match_report_*.json'))
if not reports:
    raise SystemExit('No match_report_*.json found in artifacts/live_trading')
match_json_path = reports[-1]
with open(match_json_path, 'r', encoding='utf-8') as f:
    match_meta = json.load(f)

csv_path = Path(match_meta.get('csv') or (ARTIFACTS / 'match_report.csv'))
broker_json_path = Path(match_meta.get('broker_json') or (ARTIFACTS / 'broker_history.json'))

if not csv_path.exists():
    raise SystemExit(f'match CSV not found: {csv_path}')
if not broker_json_path.exists():
    print(f'Warning: broker JSON not found at {broker_json_path} — will still produce CSV filters without broker samples')

# read match CSV
rows = []
with open(csv_path, newline='', encoding='utf-8') as fh:
    rdr = csv.DictReader(fh)
    for r in rdr:
        # normalize fields
        r['ticket'] = r.get('ticket','').strip()
        r['in_local'] = (r.get('in_local','').strip() or 'N')
        r['in_broker'] = (r.get('in_broker','').strip() or 'N')
        rows.append(r)

# filters
missing_local = [r for r in rows if r['in_local'] in ('N','n') and r['in_broker'] in ('Y','y')]
missing_broker = [r for r in rows if r['in_local'] in ('Y','y') and r['in_broker'] in ('N','n')]

stamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
file_broker_not_in_local = ARTIFACTS / f'broker_not_in_local_{stamp}.csv'
file_local_not_on_broker = ARTIFACTS / f'local_not_on_broker_{stamp}.csv'

# write CSVs
fieldnames = list(rows[0].keys()) if rows else ['ticket','in_local','local_files','local_count','in_broker','sample_broker_entry']
with open(file_broker_not_in_local, 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=fieldnames)
    w.writeheader()
    for r in missing_local:
        w.writerow(r)

with open(file_local_not_on_broker, 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=fieldnames)
    w.writeheader()
    for r in missing_broker:
        w.writerow(r)

# load broker JSON and collect sample entries for missing_local tickets
samples_broker = []
samples_local = []
if broker_json_path.exists():
    with open(broker_json_path, 'r', encoding='utf-8') as fh:
        broker = json.load(fh)
    # common layouts: top-level 'orders' and/or 'deals', or list-of-objects
    candidates = []
    if isinstance(broker, dict):
        for k in ('orders','deals','orders_history','history'):
            if k in broker and isinstance(broker[k], list):
                candidates.extend(broker[k])
        # if no named arrays, look for list values
        if not candidates:
            for v in broker.values():
                if isinstance(v, list):
                    candidates.extend(v)
    elif isinstance(broker, list):
        candidates = broker

    # build map by ticket (as str)
    ticket_map = {}
    for item in candidates:
        # try common keys
        t = None
        if isinstance(item, dict):
            for key in ('ticket','order','deal','ticket_id'):
                if key in item:
                    t = str(item[key])
                    break
            # fallback: if there's 'magic' numeric id in keys
            if t is None:
                # attempt to find any int-ish value under keys named like 'id'
                for key,val in item.items():
                    if key.lower() in ('id','unique_id'):
                        t = str(val); break
        if t is None:
            continue
        ticket_map.setdefault(t, []).append(item)

    # for each missing_local take first matching items
    for r in missing_local[:50]:
        t = r['ticket']
        matches = ticket_map.get(t)
        if matches:
            samples_broker.append({'ticket': t, 'entries': matches})
        else:
            # try numeric cast
            tt = str(int(t)) if t.isdigit() else t
            if ticket_map.get(tt):
                samples_broker.append({'ticket': t, 'entries': ticket_map[tt]})
            else:
                samples_broker.append({'ticket': t, 'entries': []})

    # for missing_broker, we don't have broker lookup data: just sample local rows
    for r in missing_broker[:50]:
        samples_local.append(r)

# write sample files (top 10 each)
sample_broker_file = ARTIFACTS / f'samples_broker_not_in_local_{stamp}.json'
sample_local_file = ARTIFACTS / f'samples_local_not_on_broker_{stamp}.json'
with open(sample_broker_file, 'w', encoding='utf-8') as fh:
    json.dump(samples_broker[:10], fh, indent=2, ensure_ascii=False)
with open(sample_local_file, 'w', encoding='utf-8') as fh:
    json.dump(samples_local[:10], fh, indent=2, ensure_ascii=False)

# summary print
print('WROTE:')
print(' -', file_broker_not_in_local)
print(' -', file_local_not_on_broker)
print(' -', sample_broker_file)
print(' -', sample_local_file)
print('\nSTATS:')
print(' total_rows_in_match_csv=', len(rows))
print(' missing_local (broker ∧ ¬local)=', len(missing_local))
print(' missing_broker (local ∧ ¬broker)=', len(missing_broker))
print('\nYou can inspect the CSV/JSON files in the artifacts/live_trading folder.')
