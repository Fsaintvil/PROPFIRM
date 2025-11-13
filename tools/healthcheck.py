#!/usr/bin/env python3
"""Healthcheck: verify MT5 connectivity, env vars and kill-switch files.

Writes a small artifact `artifacts/live_trading/healthcheck.json` with results.
"""
import json
import os
import time

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


def main():
    out = 'artifacts/live_trading/healthcheck.json'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    results = {'timestamp': time.time(), 'env': {}, 'mt5': {}, 'kill_switch': {}}

    # env checks
    keys = ['ALLOW_MT5_SEND','TRADE_INTERVAL_SECONDS','SL_RETRY_MAX','AI_VOLUME']
    for k in keys:
        results['env'][k] = os.getenv(k)

    # kill-switch files
    results['kill_switch']['disable_trading'] = os.path.exists('control/disable_trading')
    results['kill_switch']['emergency_stop'] = os.path.exists('control/emergency_stop')

    # mt5 connectivity
    if mt5 is None:
        results['mt5']['ok'] = False
        results['mt5']['error'] = 'mt5_not_installed'
    else:
        try:
            ok = mt5.initialize()
            results['mt5']['ok'] = bool(ok)
            results['mt5']['last_error'] = mt5.last_error()
            if ok:
                mt5.shutdown()
        except Exception as e:
            results['mt5']['ok'] = False
            results['mt5']['error'] = str(e)

    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

    print('Wrote', out)


if __name__ == '__main__':
    main()
