import MetaTrader5 as mt5

mt5.initialize()
for s in ["EURUSD", "GBPUSD", "BTCUSD", "XAUUSD", "USDCAD", "USDJPY"]:
    si = mt5.symbol_info(s)
    tick = mt5.symbol_info_tick(s)
    try:
        rates = mt5.copy_rates_from(s, mt5.TIMEFRAME_M15, 0, 10)
    except Exception:
        rates = None
    print(
        s, "symbol_info:", bool(si), "tick:", bool(tick), "m15_count:", len(rates) if rates else 0
    )
mt5.shutdown()
