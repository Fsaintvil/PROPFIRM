"""
Archive copy of tmp/close_positions_autonomous.py
"""

# Original content from tmp/close_positions_autonomous.py
from __future__ import annotations
import time
import json
import sys
from pathlib import Path
from datetime import datetime
import math

import MetaTrader5 as mt5


def find_latest(path_pattern: str):
	import glob
	files = sorted(glob.glob(path_pattern), key=lambda p: Path(p).stat().st_mtime, reverse=True)
	return files[0] if files else None


def quantize_volume(vol: float, step: float) -> float:
	if step is None or step <= 0:
		return vol
	# floor to nearest step
	steps = math.floor(vol / step + 1e-12)
	return round(steps * step, 8)


def load_symbols_snapshot():
	p = find_latest('artifacts/live_trading/monitor/MT5_SYMBOLS_INFO_*.json')
	if not p:
		return {}
	with open(p, 'r', encoding='utf-8') as f:
		j = json.load(f)
	return j.get('symbols', {})


def remove_kill_switch():
	ks = Path('control/disable_trading')
	if ks.exists():
		ks.unlink()
		return True
	return False


def send_close_for_position(pos, symmap):
	ticket = int(pos.ticket)
	symbol = pos.symbol
	volume = float(pos.volume)
	ptype = int(pos.type)  # 0 buy, 1 sell

	syminfo = symmap.get(symbol, {}).get('symbol_info') if symmap else None
	vmin = float(syminfo.get('volume_min', 0.0) or 0.0) if syminfo else 0.0
	vstep = float(syminfo.get('volume_step', 0.0) or 0.0) if syminfo else 0.0
	adj_vol = quantize_volume(volume, vstep) if vstep else volume
	if adj_vol < vmin:
		return {'ticket': ticket, 'status': 'skipped', 'reason': 'volume below min after quantize', 'adj_volume': adj_vol}

	# decide order type: to close a buy -> send sell, and vice‑versa
	order_type = mt5.ORDER_TYPE_SELL if ptype == 0 else mt5.ORDER_TYPE_BUY

	# price selection
	tick = mt5.symbol_info_tick(symbol)
	if not tick:
		return {'ticket': ticket, 'status': 'error', 'reason': 'no tick for symbol'}
	price = float(tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid)

	request = {
		'action': mt5.TRADE_ACTION_DEAL,
		'symbol': symbol,
		'volume': float(adj_vol),
		'type': int(order_type),
		'position': ticket,
		'price': price,
		'deviation': 20,
		'magic': 424242,
		'comment': 'auto_close_autonomous',
	}

	attempts = []
	max_attempts = 5
	backoff = 1.0
	for attempt in range(1, max_attempts + 1):
		res = mt5.order_send(request)
		now = datetime.utcnow().isoformat() + 'Z'
		entry = {'attempt': attempt, 'time': now}
		if res is None:
			entry.update({'retcode': None, 'comment': 'order_send returned None'})
			attempts.append(entry)
			time.sleep(backoff)
			backoff *= 2
			continue
		# result has retcode field
		entry.update({'retcode': int(res.retcode), 'order': int(getattr(res, 'order', 0)), 'deal': int(getattr(res, 'deal', 0)), 'comment': getattr(res, 'comment', '')})
		attempts.append(entry)
		if int(res.retcode) == 10009 or int(res.retcode) == 0:
			# 10009 = Request completed? (broker vary). treat 0 as success
			status = 'closed'
			return {'ticket': ticket, 'status': status, 'attempts': attempts}
		# else, log and retry
		time.sleep(backoff)
		backoff *= 2

	return {'ticket': ticket, 'status': 'failed', 'attempts': attempts}


def main():
	ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
	outp = Path(f'artifacts/live_trading/close_positions_run_{ts}.json')
	outp.parent.mkdir(parents=True, exist_ok=True)

	# initialize mt5
	if not mt5.initialize():
		print('MT5 initialize failed', file=sys.stderr)
		return

	acct = mt5.account_info()
	acct_login = getattr(acct, 'login', None)

	# optional: compare with snapshot if exists
	snap = find_latest('artifacts/live_trading/monitor/MT5_ACCOUNT_INFO_*.json')
	snapshot_login = None
	if snap:
		try:
			with open(snap, 'r', encoding='utf-8') as f:
				aj = json.load(f)
				snapshot_login = aj.get('account_info', {}).get('login')
		except Exception:
			snapshot_login = None

	if snapshot_login and acct_login and int(snapshot_login) != int(acct_login):
		# mismatch — abort to be safe
		res = {'error': 'account_mismatch', 'snapshot_login': snapshot_login, 'current_login': acct_login}
		outp.write_text(json.dumps(res, indent=2), encoding='utf-8')
		print('Account mismatch between snapshot and current MT5. Aborting. See', outp)
		return

	# remove kill switch (user previously authorized)
	ks_removed = remove_kill_switch()

	# load symbol info
	symmap = load_symbols_snapshot()

	positions = mt5.positions_get()
	if not positions:
		res = {'info': 'no positions', 'positions_count': 0}
		outp.write_text(json.dumps(res, indent=2), encoding='utf-8')
		print('No positions to close. Wrote', outp)
		return

	results = {'generated_at': datetime.utcnow().isoformat() + 'Z', 'account_login': acct_login, 'kill_switch_removed': ks_removed, 'positions_count': len(positions), 'results': []}

	for pos in positions:
		r = send_close_for_position(pos, symmap)
		results['results'].append(r)

	outp.write_text(json.dumps(results, indent=2), encoding='utf-8')
	print('Done. Results written to', outp)


if __name__ == '__main__':
	main()
