#!/usr/bin/env python3
"""Récupère l'historique orders/deals côté broker pour les derniers 5 jours.
Utilise le connecteur sécurisé `src.utils.mt5_connector` pour profiter du fallback/mock
et des helpers de connexion.
"""
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.utils import mt5_connector

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "artifacts" / "live_trading"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_env_credentials(env_path: Path):
    creds = {}
    if not env_path.exists():
        return creds
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                creds[k.strip()] = v.strip()
    return creds


def main():
    env_file = ROOT / 'config' / 'mt5_credentials.env'
    creds = load_env_credentials(env_file)

    login = creds.get('MT5_ACCOUNT') or os.getenv('MT5_ACCOUNT') or os.getenv('MT5_LOGIN')
    password = creds.get('MT5_PASSWORD') or os.getenv('MT5_PASSWORD')
    server = creds.get('MT5_SERVER') or os.getenv('MT5_SERVER')

    result = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'mt5_available': mt5_connector.is_mt5_available(),
        'connected': False,
        'errors': [],
        'orders': [],
        'deals': [],
    }

    try:
        mt5 = mt5_connector.get_mt5()

        if login and password and server:
            ok = False
            try:
                ok = mt5_connector.connect_with_retry(login=login, password=password, server=server, max_retries=3, timeout=15)
            except Exception as e:
                result['errors'].append(f'connect_with_retry exception: {e}')
                ok = False

            result['connected'] = bool(ok)
            if not ok:
                result['errors'].append('mt5.connect_with_retry returned False')
        else:
            # Try initialize without login (may succeed if terminal already logged)
            try:
                ok = mt5.initialize()
            except Exception as e:
                result['errors'].append(f'mt5.initialize exception: {e}')
                ok = False
            result['connected'] = bool(ok)

        if result['connected']:
            now = datetime.utcnow()
            frm = now - timedelta(days=5)
            to = now + timedelta(minutes=10)

            try:
                orders = mt5.history_orders_get(frm, to)
            except Exception as e:
                orders = None
                result['errors'].append(f'history_orders_get exception: {e}')

            try:
                deals = mt5.history_deals_get(frm, to)
            except Exception as e:
                deals = None
                result['errors'].append(f'history_deals_get exception: {e}')

            if orders:
                for o in orders:
                    try:
                        od = o._asdict()
                    except Exception:
                        od = {k: getattr(o, k) for k in dir(o) if not k.startswith('_')}
                    result['orders'].append(od)

            if deals:
                for d in deals:
                    try:
                        dd = d._asdict()
                    except Exception:
                        dd = {k: getattr(d, k) for k in dir(d) if not k.startswith('_')}
                    result['deals'].append(dd)

            try:
                mt5.shutdown()
            except Exception:
                pass

    except Exception as e:
        result['errors'].append(f'outer exception: {e}')

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out_path = OUT_DIR / f'broker_history_5d_{ts}.json'
    with open(out_path, 'w', encoding='utf-8') as jf:
        json.dump(result, jf, indent=2, default=str)

    print(json.dumps({'status': 'done', 'json': str(out_path), 'summary': {'connected': result['connected'], 'orders': len(result['orders']), 'deals': len(result['deals']), 'errors': len(result['errors'])}}))


if __name__ == '__main__':
    main()
