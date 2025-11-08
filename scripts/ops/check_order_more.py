#!/usr/bin/env python3
"""Extra MT5 probes to find an order by id using multiple APIs.

Usage: python scripts/ops/check_order_more.py <order_id>
"""
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('ERROR import MT5', e)
    sys.exit(2)

def main():
    if len(sys.argv) < 2:
        print('usage')
        return 2
    oid = int(sys.argv[1])
    if not mt5.initialize():
        print('mt5.init failed')
        return 3

    try:
        o = mt5.orders_get(ticket=oid)
        print('orders_get ->', o)
    except Exception as e:
        print('orders_get error', e)

    try:
        p = mt5.positions_get(ticket=oid)
        print('positions_get ->', p)
    except Exception as e:
        print('positions_get error', e)

    try:
        since = datetime.now() - timedelta(days=30)
        deals = mt5.history_deals_get(since, datetime.now())
        print('history_deals_get last30 count ->', len(deals) if deals else 0)
        if deals:
            for d in deals:
                if int(getattr(d,'order', -1)) == oid or int(getattr(d,'ticket',-1)) == oid:
                    print('FOUND DEAL:', d)
    except Exception as e:
        print('history_deals_get error', e)

    mt5.shutdown()
    return 0

if __name__ == '__main__':
    sys.exit(main())
