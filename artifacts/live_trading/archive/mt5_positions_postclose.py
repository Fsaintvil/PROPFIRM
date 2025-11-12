"""
Archive copy of mt5_positions_postclose.py
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
import MetaTrader5 as mt5


def main():
	if not mt5.initialize():
		print('mt5 initialize failed')
		return
	positions = mt5.positions_get()
	ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
	out = Path(f'artifacts/live_trading/monitor/MT5_POSITIONS_POSTCLOSE_{ts}.json')
	out.parent.mkdir(parents=True, exist_ok=True)
	data = {'timestamp': datetime.utcnow().isoformat() + 'Z', 'initialized': True, 'positions_count': len(positions) if positions else 0, 'positions': []}
	if positions:
		for p in positions:
			data['positions'].append({'ticket': int(p.ticket), 'symbol': p.symbol, 'volume': float(p.volume), 'price_open': float(p.price_open), 'type': int(p.type), 'time': int(p.time)})
	out.write_text(json.dumps(data, indent=2), encoding='utf-8')
	print('Wrote', out)


if __name__ == '__main__':
	main()
