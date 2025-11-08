#!/usr/bin/env python3
"""Check MT5 for an order/deal by id and archive details.

Usage:
  python scripts/ops/check_order.py <order_id>

Writes: artifacts/reports/order_<order_id>_<ts>.json
"""
import sys
from pathlib import Path
import json
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / 'artifacts' / 'reports'

def find_order(order_id: int):
    if not mt5.initialize():
        print('ERROR: mt5.initialize() failed')
        return None

    now = datetime.now()
    # search last 7 days
    since = now - timedelta(days=7)
    try:
        deals = mt5.history_deals_get(since, now)
    except Exception:
        deals = None

    orders = None
    try:
        orders = mt5.history_orders_get(since, now)
    except Exception:
        orders = None

    found = {'deals': [], 'orders': []}
    if deals:
        for d in deals:
            try:
                if int(getattr(d, 'order', -1)) == order_id or int(getattr(d, 'ticket', -1)) == order_id:
                    found['deals'].append(d._asdict() if hasattr(d, '_asdict') else d.__dict__)
            except Exception:
                continue

    if orders:
        for o in orders:
            try:
                if int(getattr(o, 'ticket', -1)) == order_id or int(getattr(o, 'order', -1)) == order_id:
                    found['orders'].append(o._asdict() if hasattr(o, '_asdict') else o.__dict__)
            except Exception:
                continue

    mt5.shutdown()
    return found

def main():
    if len(sys.argv) < 2:
        print('usage: check_order.py <order_id>')
        return 2
    try:
        oid = int(sys.argv[1])
    except Exception:
        print('order_id must be integer')
        return 3

    res = find_order(oid)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    out = OUT_DIR / f'order_{oid}_{ts}.json'
    with out.open('w', encoding='utf-8') as f:
        json.dump(res, f, default=str, indent=2)

    print('WROTE', out)
    print('FOUND_SUMMARY deals=%d orders=%d' % (len(res.get('deals', [])), len(res.get('orders', []))))
    return 0

if __name__ == '__main__':
    rc = main()
    sys.exit(rc)
