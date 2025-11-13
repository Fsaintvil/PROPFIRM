"""
Check tradability of remaining positions listed in
artifacts/live_trading/close_all_positions_result.json
Writes artifacts/live_trading/remaining_tradability.json and prints a compact report.
"""
import os
import json
import time

from pathlib import Path

OUT_DIR = Path('artifacts') / 'live_trading'
OUT_DIR.mkdir(parents=True, exist_ok=True)

infile = Path('artifacts') / 'live_trading' / 'close_all_positions_result.json'
if not infile.exists():
    print('INPUT_NOT_FOUND:', infile)
    raise SystemExit(1)

j = json.loads(infile.read_text(encoding='utf-8'))
closed = j.get('closed', [])
not_closed = [r for r in closed if not (r.get('order_result', {}).get('deal'))]
symbols = sorted({r.get('symbol') for r in not_closed})

report = {
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'symbols_checked': [],
}

# try to import mt5
try:
    import MetaTrader5 as mt5
except Exception as e:
    print('MT5_IMPORT_FAILED:', e)
    # still write a simple report
    for s in symbols:
        report['symbols_checked'].append({'symbol': s, 'trade_allowed': None, 'note': 'mt5_import_failed'})
    out = OUT_DIR / 'remaining_tradability.json'
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(1)

# load credentials from config if present
creds_path = Path(__file__).parent.parent / 'config' / 'mt5_credentials.env'
if creds_path.exists():
    for line in creds_path.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k,v = line.split('=',1)
        os.environ.setdefault(k.strip(), v.strip())

init_kwargs = {}
login = os.getenv('MT5_LOGIN') or os.getenv('MT5_ACCOUNT')
password = os.getenv('MT5_PASSWORD') or os.getenv('MT5_PWD')
server = os.getenv('MT5_SERVER')
if login:
    try:
        init_kwargs['login'] = int(login)
    except Exception:
        init_kwargs['login'] = login
if password:
    init_kwargs['password'] = password
if server:
    init_kwargs['server'] = server

ok = mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()
if not ok:
    print('MT5_INITIALIZE_FAILED')

for sym in symbols:
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    entry = {'symbol': sym, 'trade_allowed': None, 'visible': None, 'volume_step': None, 'volume_min': None, 'digits': None, 'tick': None, 'note': ''}
    if info is None:
        entry['note'] = 'no_symbol_info'
    else:
        entry['trade_allowed'] = bool(info.trade_allowed) if hasattr(info, 'trade_allowed') else None
        entry['visible'] = bool(info.visible) if hasattr(info, 'visible') else None
        try:
            entry['volume_step'] = float(info.volume_step) if info.volume_step is not None else None
            entry['volume_min'] = float(info.volume_min) if info.volume_min is not None else None
            entry['digits'] = int(info.digits) if info.digits is not None else None
        except Exception:
            pass
    if tick is not None:
        try:
            entry['tick'] = {'bid': float(tick.bid), 'ask': float(tick.ask)}
        except Exception:
            entry['tick'] = None
    report['symbols_checked'].append(entry)

out = OUT_DIR / 'remaining_tradability.json'
out.write_text(json.dumps(report, indent=2), encoding='utf-8')

# print compact
print('checked', len(report['symbols_checked']), 'symbols')
for e in report['symbols_checked']:
    s=e['symbol']
    ta = e['trade_allowed']
    vis = e['visible']
    note = e.get('note','')
    vs = e.get('volume_step')
    vm = e.get('volume_min')
    digits = e.get('digits')
    tick = e.get('tick')
    print(f"{s}: trade_allowed={ta} visible={vis} step={vs} min={vm} digits={digits} tick={tick} note={note}")

mt5.shutdown()
print('\nWrote', out)
