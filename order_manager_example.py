# order_manager_example.py
# Snippet: calcule SL/TP, taille de position, et prépare ordre MT5 (dry-run safe)
import json
from math import floor

# helper functions (à adapter avec ton code MT5 wrapper)

def load_env_map():
    import os
    return {
        'DEFAULT_SL_PTS': float(os.getenv('DEFAULT_SL_PTS','0')),
        'DEFAULT_TP_PTS': float(os.getenv('DEFAULT_TP_PTS','0')),
        'DEFAULT_RR': float(os.getenv('DEFAULT_RR','2.0')),
        'TRAILING_STOP_ENABLE': os.getenv('TRAILING_STOP_ENABLE','0') == '1',
        'TRAILING_STOP_PTS': float(os.getenv('TRAILING_STOP_PTS','0')),
        'PER_SYMBOL_SL_JSON': json.loads(os.getenv('PER_SYMBOL_SL_JSON','{}')),
        'SL_AS_ATR_MULT': float(os.getenv('SL_AS_ATR_MULT','1.5')),
        'RISK_PER_TRADE_PCT': float(os.getenv('RISK_PER_TRADE_PCT','0.1'))/100.0,
        'SL_MAX_PCT_ACCOUNT': float(os.getenv('SL_MAX_PCT_ACCOUNT','0.5'))/100.0,
    }


def compute_sl_tp_and_lots(symbol, side, atr, account_balance, tick_value, tick_size):
    # side: 'buy' or 'sell'
    env = load_env_map()

    # 1) determine SL pts
    per_sym = env['PER_SYMBOL_SL_JSON'].get(symbol)
    if per_sym and per_sym > 0:
        sl_pts = float(per_sym)
    elif env['DEFAULT_SL_PTS'] > 0:
        sl_pts = env['DEFAULT_SL_PTS']
    else:
        # ATR provided in pts
        sl_pts = env['SL_AS_ATR_MULT'] * float(atr)

    # 2) determine TP pts
    if env['DEFAULT_TP_PTS'] > 0:
        tp_pts = env['DEFAULT_TP_PTS']
    else:
        tp_pts = sl_pts * env['DEFAULT_RR']

    # 3) compute pip/tick value per lot to derive lots from risk
    # tick_value: $ per tick for 1 lot, tick_size: pts per tick (ex: 0.0001 for FX)
    # risk allowed
    risk_usd = account_balance * env['RISK_PER_TRADE_PCT']
    # cost if 1 lot = sl_pts / tick_size * tick_value
    cost_per_lot = (sl_pts / tick_size) * tick_value
    if cost_per_lot <= 0:
        lots = 0.0
    else:
        lots = risk_usd / cost_per_lot

    # enforce SL_MAX_PCT_ACCOUNT (monetary cap) if needed
    max_risk_usd = account_balance * env['SL_MAX_PCT_ACCOUNT']
    max_lots = max_risk_usd / cost_per_lot if cost_per_lot>0 else lots
    if lots * cost_per_lot > max_risk_usd:
        lots = max_lots

    # round lots to allowed step (example: 0.01)
    step = 0.01
    lots = floor(lots/step) * step
    if lots < step:
        lots = 0.0

    result = {
        'symbol': symbol,
        'side': side,
        'sl_pts': sl_pts,
        'tp_pts': tp_pts,
        'lots': lots,
        'risk_usd': lots * cost_per_lot,
        'trailing': env['TRAILING_STOP_ENABLE'],
        'trailing_start_pts': env['TRAILING_STOP_PTS'],
    }
    return result

# Example usage (dry-run)
if __name__ == '__main__':
    # fake market data (adapter à ton environnement)
    example = compute_sl_tp_and_lots('EURUSD','buy', atr=10, account_balance=100000, tick_value=10, tick_size=0.0001)
    print('Proposal:', example)

# Integrer: appeler compute_sl_tp_and_lots() depuis la logique d'ordre avant send_order_mt5()
# send_order_mt5(symbol, side, lots, sl_points, tp_points, comment='auto')
