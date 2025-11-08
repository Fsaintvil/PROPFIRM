#!/usr/bin/env python3
"""Collect a per-symbol MT5 audit and archive it to artifacts/reports.

Usage:
  python scripts/ops/mt5_audit_and_archive.py [--label pre|post]

Produces: artifacts/reports/mt5_audit_<ts>_<label>.csv
"""
from pathlib import Path
import json
import sys
from datetime import datetime

try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
SYMBOLS_FILE = ROOT / 'config' / 'symbols_live.json'
OUT_DIR = ROOT / 'artifacts' / 'reports'

def load_symbols():
    try:
        return json.loads(SYMBOLS_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        print('ERROR reading symbols:', e)
        return []

def audit(symbols):
    if not mt5.initialize():
        print('ERROR: mt5.initialize() failed')
        return None

    rows = []
    total_pos = 0
    total_pending = 0
    for s in symbols:
        pos = mt5.positions_get(symbol=s)
        pending = mt5.orders_get(symbol=s)
        npos = len(pos) if pos is not None else 0
        npend = len(pending) if pending is not None else 0
        rows.append((s, npos, npend))
        total_pos += npos
        total_pending += npend

    mt5.shutdown()
    return rows, (total_pos, total_pending)

def write_csv(rows, totals, label):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    label = label or 'audit'
    out = OUT_DIR / f'mt5_audit_{ts}_{label}.csv'
    with out.open('w', encoding='utf-8') as f:
        f.write('symbol,positions,pending_orders\n')
        for s, p, o in rows:
            f.write(f'{s},{p},{o}\n')
        f.write(f'TOTALS,{totals[0]},{totals[1]}\n')
    return out

def main():
    label = None
    if len(sys.argv) > 1:
        if sys.argv[1].startswith('--label'):
            parts = sys.argv[1].split('=')
            if len(parts) == 2:
                label = parts[1]
        else:
            label = sys.argv[1]

    symbols = load_symbols()
    if not symbols:
        print('No symbols found, aborting')
        return 3

    result = audit(symbols)
    if result is None:
        print('Audit failed')
        return 4

    rows, totals = result
    out = write_csv(rows, totals, label)
    print('WROTE', out)
    return 0

if __name__ == '__main__':
    rc = main()
    sys.exit(rc)
