"""Send AI-decided orders immediately (uses MetaTrader5).

Safety: this script will send only if control/apply_live.auto.confirm exists
and the environment variable ALLOW_MT5_SEND is set to '1'.

Run with:
  $env:PYTHONPATH='.'; $env:ALLOW_MT5_SEND='1'; C:/.../python.exe tools\send_ai_orders_now.py
"""
import os
import json
import MetaTrader5 as mt5

SYMBOLS = ['BTCUSD','ETHUSD','XAUUSD','USDCAD','AUDNZD','EURJPY','GBPCHF','NZDJPY','EURUSD','EURAUD','US500.cash','JP225.cash']

def main():
    if not mt5.initialize():
        print('mt5.initialize() FAILED', mt5.last_error())
        return 2

    orders = []
    for s in SYMBOLS:
        info = mt5.symbol_info(s)
        tick = mt5.symbol_info_tick(s)
        rates = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M15, 0, 20)
        if rates is None or len(rates) < 5 or tick is None or info is None:
            print(f'{s}: insufficient data -> skip')
            continue
        closes = [float(r['close']) for r in rates]
        avg = sum(closes) / len(closes)
        last = closes[-1]
        side = 'BUY' if last > avg else 'SELL'
        price = float(tick.ask) if side == 'BUY' else float(tick.bid)
        sl = price * (0.99 if side == 'BUY' else 1.01)
        tp = price * (1.02 if side == 'BUY' else 0.98)
        digits = info.digits if hasattr(info, 'digits') else 5
        orders.append({
            'symbol': s,
            'side': side,
            'volume': 0.01,
            'price': round(price, digits),
            'sl': round(sl, digits),
            'tp': round(tp, digits),
            'comment': 'ai_mtf_m15'
        })

    print('Prepared orders:\n', json.dumps(orders, indent=2))

    auto_conf = os.path.join('control','apply_live.auto.confirm')
    if os.path.exists(auto_conf) and os.getenv('ALLOW_MT5_SEND') == '1' and orders:
        print('Auto-confirm present and ALLOW_MT5_SEND=1 -> sending orders')
        results = []
        for o in orders:
            try:
                order_type = mt5.ORDER_TYPE_BUY if o['side']=='BUY' else mt5.ORDER_TYPE_SELL
                req = {
                    'action': mt5.TRADE_ACTION_DEAL,
                    'symbol': o['symbol'],
                    'volume': float(o['volume']),
                    'type': order_type,
                    'price': float(o['price']),
                    'deviation': 20,
                    'sl': float(o['sl']),
                    'tp': float(o['tp']),
                    'comment': o.get('comment','ai_auto'),
                }
                res = mt5.order_send(req)
                results.append({'order': o, 'result': str(res)})
                print('sent', o['symbol'], '->', res)
            except Exception as e:
                results.append({'order': o, 'error': str(e)})
                print('send error for', o['symbol'], e)
        print('All send results:\n', json.dumps(results, indent=2))
    else:
        print('Not sending: missing auto-confirm or ALLOW_MT5_SEND!=1 or no orders')

    mt5.shutdown()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
