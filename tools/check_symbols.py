"""Check availability and ticks for a list of symbols via MetaTrader5.

Usage: python tools/check_symbols.py
"""
import MetaTrader5 as mt5
import sys

SYMBOLS = [
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "USDCAD",
    "AUDNZD",
    "EURJPY",
    "GBPCHF",
    "NZDJPY",
    "EURUSD",
    "EURAUD",
    "US500.cash",
    "JP225.cash",
]


def main():
    if not mt5.initialize():
        print("mt5.initialize() FAILED", mt5.last_error())
        sys.exit(2)

    for s in SYMBOLS:
        info = mt5.symbol_info(s)
        tick = mt5.symbol_info_tick(s)
        if info is None:
            print(f"{s}: NOT FOUND in Market Watch")
        else:
            print(f"{s}: visible=True trade_mode={info.trade_mode} digits={info.digits}")
        print(f"  tick={tick}")

    mt5.shutdown()


if __name__ == '__main__':
    main()
