#!/usr/bin/env python3
"""Fermeture directe de toutes les positions MT5 (usage maintenance).

Sécurité:
 - Fichier `control/apply_live.confirm` == 'APPLY LIVE'
 - Variable ALLOW_MT5_SEND=1
Sinon: sortie sans action.

Étapes:
 1. Initialise MetaTrader5
 2. Récupère toutes les positions
 3. Ferme chaque position par envoi d'ordre inverse (TRADE_ACTION_DEAL)
 4. Écrit artefact JSON de synthèse
"""
import os
import json
import time
from datetime import datetime

ROOT = os.path.abspath(os.path.dirname(__file__))
CTRL = (
    os.path.join(os.path.dirname(ROOT), 'control')
    if os.path.basename(ROOT) == 'tools'
    else os.path.join(ROOT, 'control')
)
CONFIRM_FILE = os.path.join(CTRL, 'apply_live.confirm')
ART_DIR = (
    os.path.join(os.path.dirname(ROOT), 'artifacts', 'live_trading')
    if os.path.basename(ROOT) == 'tools'
    else os.path.join(ROOT, 'artifacts', 'live_trading')
)
os.makedirs(ART_DIR, exist_ok=True)


def has_live_confirmation():
    """Vérifie présence + contenu du fichier de confirmation."""
    try:
        if not os.path.exists(CONFIRM_FILE):
            return False
        with open(CONFIRM_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip() == 'APPLY LIVE'
    except Exception:
        return False


def main():
    meta = {
        'timestamp': datetime.utcnow().isoformat(),
        'env_allow_send': os.getenv('ALLOW_MT5_SEND'),
        'confirmation': has_live_confirmation(),
    }
    results = []
    out_path = os.path.join(ART_DIR, f'close_all_positions_{int(time.time())}.json')

    force_file = os.path.join(CTRL, 'force_close_all')
    force_mode = os.path.exists(force_file)

    if (not has_live_confirmation() or os.getenv('ALLOW_MT5_SEND') != '1') and not force_mode:
        meta['status'] = 'skipped_safety_guard'
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'results': results}, f, indent=2)
        print('[CLOSE] Sécurité active -> aucune fermeture exécutée.')
        print(f'[CLOSE] Rapport: {out_path}')
        return 0
    elif force_mode:
        meta['force_mode'] = True
        print('[CLOSE] FORCE MODE actif (control/force_close_all) – fermeture sans garde.')

    try:
        import MetaTrader5 as mt5
    except Exception as e:
        meta['status'] = 'mt5_import_failed'
        meta['error'] = str(e)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'results': results}, f, indent=2)
        print('[CLOSE] MetaTrader5 introuvable.')
        return 2

    if not mt5.initialize():
        meta['status'] = 'mt5_initialize_failed'
        meta['init_last_error'] = mt5.last_error()
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'results': results}, f, indent=2)
        print('[CLOSE] mt5.initialize() échec.')
        return 3

    positions = mt5.positions_get()
    if not positions:
        meta['status'] = 'no_positions'
        mt5.shutdown()
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'results': results}, f, indent=2)
        print('[CLOSE] Aucune position ouverte.')
        print(f'[CLOSE] Rapport: {out_path}')
        return 0

    closed = 0
    for pos in positions:
        entry = {
            'ticket': getattr(pos, 'ticket', None),
            'symbol': getattr(pos, 'symbol', None),
            'type': getattr(pos, 'type', None),
            'volume': getattr(pos, 'volume', None),
        }
        try:
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                entry['error'] = 'tick_unavailable'
                results.append(entry)
                continue
            # type 0 = BUY, type 1 = SELL
            price = tick.bid if pos.type == 0 else tick.ask
            order_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
            close_req = {
                'action': mt5.TRADE_ACTION_DEAL,
                'symbol': pos.symbol,
                'volume': pos.volume,
                'type': order_type,
                'position': int(getattr(pos, 'ticket', 0)),
                'price': price,
                'deviation': 30,
                'comment': 'manual_close_all',
            }
            res = mt5.order_send(close_req)
            entry['retcode'] = getattr(res, 'retcode', None)
            entry['order'] = getattr(res, 'order', None)
            if getattr(res, 'retcode', None) == mt5.TRADE_RETCODE_DONE:
                closed += 1
        except Exception as e:
            entry['error'] = f'close_failed:{e}'
        results.append(entry)

    mt5.shutdown()
    meta['status'] = 'done'
    meta['closed_positions'] = closed
    meta['total_positions_before'] = len(positions)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'results': results}, f, indent=2)
    print(f'[CLOSE] Fermeture terminée: {closed}/{len(positions)} positions')
    print(f'[CLOSE] Rapport: {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
