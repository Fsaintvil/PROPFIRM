#!/usr/bin/env python3
"""Fetch broker history for last 5 days using explicit mt5.initialize with typed login.
This bypasses connect_with_retry to avoid type coercion issues.
"""
import sys
from pathlib import Path
import json
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import mt5_connector


def main():
    creds_path = ROOT / 'config' / 'mt5_credentials.env'
    creds = {}
    if creds_path.exists():
        for ln in creds_path.read_text(encoding='utf-8').splitlines():
            if '=' in ln:
                k, v = ln.split('=', 1)
                creds[k.strip()] = v.strip()

    login = creds.get('MT5_ACCOUNT')
    password = creds.get('MT5_PASSWORD')
    server = creds.get('MT5_SERVER')

    mt5 = mt5_connector.get_mt5()
    print('MT5 impl:', type(mt5).__name__)

    connected = False
    try:
        if login and password and server:
            try:
                l = int(login)
            except Exception:
                l = login
            connected = mt5.initialize(login=l, password=password, server=server, timeout=15)
        else:
            connected = mt5.initialize()
    except Exception as e:
        print('initialize exception', e)
        connected = False

    print('connected=', connected)

    result = {'timestamp': datetime.utcnow().isoformat() + 'Z', 'connected': bool(connected), 'orders': [], 'deals': [], 'errors': []}

    if connected:
        now = datetime.utcnow()
        frm = now - timedelta(days=5)
        to = now + timedelta(minutes=10)
        try:
            orders = mt5.history_orders_get(frm, to)
            if orders:
                for o in orders:
                    try:
                        od = o._asdict()
                    except Exception:
                        od = {k: getattr(o, k) for k in dir(o) if not k.startswith('_')}
                    result['orders'].append(od)
        except Exception as e:
            result['errors'].append(f'history_orders_get exception: {e}')

        try:
            deals = mt5.history_deals_get(frm, to)
            if deals:
                for d in deals:
                    try:
                        dd = d._asdict()
                    except Exception:
                        dd = {k: getattr(d, k) for k in dir(d) if not k.startswith('_')}
                    result['deals'].append(dd)
        except Exception as e:
            result['errors'].append(f'history_deals_get exception: {e}')

        try:
            mt5.shutdown()
        except Exception:
            pass

    out = Path('artifacts') / 'live_trading' / f'broker_history_direct_{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    print('wrote', out)


if __name__ == '__main__':
    main()
