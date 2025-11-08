# migration: try import safe sender (fail-open)
try:
    from src.utils.mt5_safe import send_order as _mt5_send_safe
except Exception:
    _mt5_send_safe = None

#!/usr/bin/env python3
"""Send a single EURUSD market order (small helper script).
This script is intended to be run from the repository root.
"""
import sys
try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

SYMBOL = "EURUSD"
VOLUME = 0.01

def main():
    if not mt5.initialize():
        print("ERROR: mt5.initialize() failed")
        return 2

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print("ERROR: tick unavailable for", SYMBOL)
        mt5.shutdown()
        return 3

    price = tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "deviation": 20,
        "magic": 234000,
        "comment": "Manual EURUSD extra order",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    try:
        from src.utils.mt5_safe import send_order
    except Exception:
        send_order = None

    if send_order is not None:
        try:
            res = send_order(request, logger=None, mt5_module=mt5)
        except Exception as e:
            print("SEND_ERROR", e)
            mt5.shutdown()
            return 1
    else:
        res = _mt5_send_safe(request)

    try:
        # Print the important fields if available
        print("ORDER_RET", getattr(res, 'retcode', None), "ORDER_ID", getattr(res, 'order', None), "COMMENT", getattr(res, 'comment', None))
    except Exception:
        print("ORDER_RESULT_OBJ", res)

    mt5.shutdown()
    return 0

if __name__ == '__main__':
    rc = main()
    sys.exit(rc)
