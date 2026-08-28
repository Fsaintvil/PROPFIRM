"""
MT5 Data Quality Check — All Active Symbols
Checks: availability, spread, bars, data freshness, tick info, Market Watch status.
"""
import sys
import MetaTrader5 as mt5
from datetime import datetime, timezone

# Config: active symbols and their max_spread_points from default.yaml
SYMBOLS = {
    "US100.cash":  {"max_spread_pts": 200,  "adx_thresh": 22, "max_spread_atr_ratio": None},
    "US30.cash":   {"max_spread_pts": 300,  "adx_thresh": 22, "max_spread_atr_ratio": None},
    "JP225.cash":  {"max_spread_pts": 1200, "adx_thresh": 22, "max_spread_atr_ratio": None},
    "SOLUSD":      {"max_spread_pts": 120,  "adx_thresh": 20, "max_spread_atr_ratio": 0.25},
    "BTCUSD":      {"max_spread_pts": 150,  "adx_thresh": 20, "max_spread_atr_ratio": None},
    "XAUUSD":      {"max_spread_pts": 60,   "adx_thresh": 22, "max_spread_atr_ratio": None},
    "EURUSD":      {"max_spread_pts": 40,   "adx_thresh": 22, "max_spread_atr_ratio": None},
    "GBPUSD":      {"max_spread_pts": 50,   "adx_thresh": 22, "max_spread_atr_ratio": None},
    "USDJPY":      {"max_spread_pts": 45,   "adx_thresh": 22, "max_spread_atr_ratio": None},
    "USDCAD":      {"max_spread_pts": 45,   "adx_thresh": 22, "max_spread_atr_ratio": None},
    "AUDUSD":      {"max_spread_pts": 45,   "adx_thresh": 22, "max_spread_atr_ratio": 0.15},
    "NZDUSD":      {"max_spread_pts": 40,   "adx_thresh": 22, "max_spread_atr_ratio": None},
    "USDCHF":      {"max_spread_pts": 40,   "adx_thresh": 22, "max_spread_atr_ratio": None},
}

TIMEFRAMES = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

def calc_atr_14(symbol: str, tf, count=100):
    """Calculate ATR(14) from raw bars."""
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) < 16:
        return None
    trs = []
    for i in range(1, len(rates)):
        h, l, pc = rates[i]['high'], rates[i]['low'], rates[i-1]['close']
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < 14:
        return None
    atr = sum(trs[-14:]) / 14.0
    return atr

def main():
    print("=" * 120)
    print("MT5 DATA QUALITY CHECK — ALL ACTIVE SYMBOLS")
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 120)

    # Initialize MT5
    if not mt5.initialize():
        print(f"MT5 initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    info = mt5.account_info()
    if info:
        print(f"Account: {info.login} | Server: {info.server} | Balance: ${info.balance:,.2f} | Equity: ${info.equity:,.2f}")
    print()

    now_utc = datetime.now(timezone.utc)
    results = []

    for sym_name, cfg in SYMBOLS.items():
        row = {
            "symbol": sym_name,
            "available": False,
            "visible": False,
            "spread_pts": 0,
            "max_spread_pts": cfg["max_spread_pts"],
            "spread_atr_pct": None,
            "bars_h1": 0,
            "bars_h4": 0,
            "bars_d1": 0,
            "last_bar_h1": None,
            "last_bar_h4": None,
            "tick_bid": 0,
            "tick_ask": 0,
            "tick_last": 0,
            "tick_time": None,
            "tick_fresh_sec": None,
            "atr_h1": None,
            "point": 0,
            "digits": 0,
            "trade_mode": None,
            "error": None,
        }

        # 1. Check symbol info
        si = mt5.symbol_info(sym_name)
        if si is None:
            row["error"] = "NOT_FOUND"
            results.append(row)
            continue

        row["available"] = True
        row["visible"] = si.visible
        row["digits"] = si.digits
        row["point"] = si.point
        row["trade_mode"] = si.trade_mode
        row["spread_pts"] = si.spread

        # Ensure symbol selected in Market Watch
        if not si.visible:
            mt5.symbol_select(sym_name, True)

        # 2. Get ATR(14) on H4 (primary timeframe for most symbols)
        atr_h4 = calc_atr_14(sym_name, mt5.TIMEFRAME_H4, 50)
        atr_h1 = calc_atr_14(sym_name, mt5.TIMEFRAME_H1, 50)

        # Use H4 for crypto/XAUUSD, H1 for forex/indices
        if sym_name in ("BTCUSD", "SOLUSD", "XAUUSD"):
            atr_ref = atr_h4
        else:
            atr_ref = atr_h1

        if atr_ref and atr_ref > 0:
            row["atr_h1"] = atr_h1
            # spread_pts * point = actual spread value; ATR is in price units
            spread_value = si.spread * si.point
            row["spread_atr_pct"] = (spread_value / atr_ref) * 100.0

        # 3. Count bars on multiple timeframes
        for tf_name, tf_const in [("H1", mt5.TIMEFRAME_H1), ("H4", mt5.TIMEFRAME_H4), ("D1", mt5.TIMEFRAME_D1)]:
            rates = mt5.copy_rates_from_pos(sym_name, tf_const, 0, 1)
            if rates is not None and len(rates) > 0:
                if tf_name == "H1":
                    row["bars_h1"] = mt5.copy_rates_from_pos(sym_name, tf_const, 0, 5000).__len__() if mt5.copy_rates_from_pos(sym_name, tf_const, 0, 5000) is not None else 0
                elif tf_name == "H4":
                    row["bars_h4"] = mt5.copy_rates_from_pos(sym_name, tf_const, 0, 5000).__len__() if mt5.copy_rates_from_pos(sym_name, tf_const, 0, 5000) is not None else 0
                elif tf_name == "D1":
                    row["bars_d1"] = mt5.copy_rates_from_pos(sym_name, tf_const, 0, 5000).__len__() if mt5.copy_rates_from_pos(sym_name, tf_const, 0, 5000) is not None else 0

        # Get last bar times
        for tf_name, tf_const in [("H1", mt5.TIMEFRAME_H1), ("H4", mt5.TIMEFRAME_H4)]:
            rates = mt5.copy_rates_from_pos(sym_name, tf_const, 0, 1)
            if rates is not None and len(rates) > 0:
                ts = rates[0]['time']
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                if tf_name == "H1":
                    row["last_bar_h1"] = dt
                elif tf_name == "H4":
                    row["last_bar_h4"] = dt

        # 4. Get tick info
        tick = mt5.symbol_info_tick(sym_name)
        if tick is not None:
            row["tick_bid"] = tick.bid
            row["tick_ask"] = tick.ask
            row["tick_last"] = tick.last
            tick_dt = datetime.fromtimestamp(tick.time, tz=timezone.utc)
            row["tick_time"] = tick_dt
            row["tick_fresh_sec"] = (now_utc - tick_dt).total_seconds()

        results.append(row)

    # Free MT5
    mt5.shutdown()

    # === OUTPUT TABLE 1: Overview ===
    print("─" * 120)
    print("TABLE 1: SYMBOL AVAILABILITY & MARKET WATCH STATUS")
    print("─" * 120)
    hdr = f"{'Symbol':<14} {'Avail':>5} {'Visible':>7} {'Digits':>6} {'Point':>12} {'TradeMode':>10} {'Error':>12}"
    print(hdr)
    print("─" * 120)
    for r in results:
        avail = "YES" if r["available"] else "NO"
        vis = "YES" if r["visible"] else "NO"
        tm = str(r["trade_mode"]) if r["trade_mode"] is not None else "N/A"
        pt_str = f"{r['point']:.8f}" if r["point"] < 1 else f"{r['point']:.4f}"
        err = r["error"] or ""
        print(f"{r['symbol']:<14} {avail:>5} {vis:>7} {r['digits']:>6} {pt_str:>12} {tm:>10} {err:>12}")
    print()

    # === OUTPUT TABLE 2: Spread & ATR ===
    print("─" * 120)
    print("TABLE 2: SPREAD ANALYSIS (points + % of ATR)")
    print("─" * 120)
    hdr = f"{'Symbol':<14} {'Sprd_pts':>8} {'MaxSprd':>8} {'Sprd/ATR%':>10} {'ATR_H1':>12} {'ATR_ref':>12} {'ATR_max%':>10} {'SPRD_OK':>8}"
    print(hdr)
    print("─" * 120)
    for r in results:
        if not r["available"]:
            print(f"{r['symbol']:<14} {'N/A':>8} {'N/A':>8} {'N/A':>10} {'N/A':>12} {'N/A':>12} {'N/A':>10} {'N/A':>8}")
            continue
        sprd_pct = f"{r['spread_atr_pct']:.2f}%" if r["spread_atr_pct"] is not None else "N/A"
        atr_h1_str = f"{r['atr_h1']:.6f}" if r["atr_h1"] is not None else "N/A"
        # Calculate ATR reference used
        if r["symbol"] in ("BTCUSD", "SOLUSD", "XAUUSD"):
            atr_ref = calc_atr_14(r["symbol"], mt5.TIMEFRAME_H4, 50)  # Already shutdown, use cached
            atr_ref = r["atr_h1"]  # approximate
        else:
            atr_ref = r["atr_h1"]
        atr_ref_str = f"{atr_ref:.6f}" if atr_ref else "N/A"

        cfg = SYMBOLS[r["symbol"]]
        max_pct_str = f"{cfg['max_spread_atr_ratio']*100:.1f}%" if cfg['max_spread_atr_ratio'] else "N/A"

        sprd_ok = "OK"
        if r["spread_pts"] > cfg["max_spread_pts"]:
            sprd_ok = "OVER"
        if cfg["max_spread_atr_ratio"] and r["spread_atr_pct"] is not None:
            if r["spread_atr_pct"] > cfg["max_spread_atr_ratio"] * 100:
                sprd_ok = "OVER"
        if r["spread_atr_pct"] is None and r["spread_pts"] > cfg["max_spread_pts"]:
            sprd_ok = "OVER"

        print(f"{r['symbol']:<14} {r['spread_pts']:>8} {cfg['max_spread_pts']:>8} {sprd_pct:>10} {atr_h1_str:>12} {atr_ref_str:>12} {max_pct_str:>10} {sprd_ok:>8}")
    print()

    # === OUTPUT TABLE 3: Bar Data ===
    print("─" * 120)
    print("TABLE 3: BAR COUNTS & DATA FRESHNESS")
    print("─" * 120)
    hdr = f"{'Symbol':<14} {'Bars_H1':>8} {'Bars_H4':>8} {'Bars_D1':>8} {'LastBar_H1':>22} {'LastBar_H4':>22} {'Fresh_H1':>12} {'Fresh_H4':>12}"
    print(hdr)
    print("─" * 120)
    for r in results:
        if not r["available"]:
            print(f"{r['symbol']:<14} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>22} {'N/A':>22} {'N/A':>12} {'N/A':>12}")
            continue
        lb_h1 = r["last_bar_h1"].strftime("%Y-%m-%d %H:%M") if r["last_bar_h1"] else "N/A"
        lb_h4 = r["last_bar_h4"].strftime("%Y-%m-%d %H:%M") if r["last_bar_h4"] else "N/A"
        fresh_h1 = ""
        fresh_h4 = ""
        if r["last_bar_h1"]:
            delta_h1 = (now_utc - r["last_bar_h1"]).total_seconds() / 3600.0
            fresh_h1 = f"{delta_h1:.1f}h"
        if r["last_bar_h4"]:
            delta_h4 = (now_utc - r["last_bar_h4"]).total_seconds() / 3600.0
            fresh_h4 = f"{delta_h4:.1f}h"
        print(f"{r['symbol']:<14} {r['bars_h1']:>8} {r['bars_h4']:>8} {r['bars_d1']:>8} {lb_h1:>22} {lb_h4:>22} {fresh_h1:>12} {fresh_h4:>12}")
    print()

    # === OUTPUT TABLE 4: Tick Data ===
    print("─" * 120)
    print("TABLE 4: TICK DATA (Bid/Ask/Last + Freshness)")
    print("─" * 120)
    hdr = f"{'Symbol':<14} {'Bid':>14} {'Ask':>14} {'Last':>14} {'Spread_$':>10} {'TickTime_UTC':>22} {'Fresh_sec':>10} {'FRESH?':>7}"
    print(hdr)
    print("─" * 120)
    for r in results:
        if not r["available"] or r["tick_time"] is None:
            print(f"{r['symbol']:<14} {'N/A':>14} {'N/A':>14} {'N/A':>14} {'N/A':>10} {'N/A':>22} {'N/A':>10} {'N/A':>7}")
            continue
        spread_val = r["tick_ask"] - r["tick_bid"]
        tt = r["tick_time"].strftime("%Y-%m-%d %H:%M:%S")
        fresh = "YES" if r["tick_fresh_sec"] < 10 else ("STALE" if r["tick_fresh_sec"] > 60 else "OK")
        print(f"{r['symbol']:<14} {r['tick_bid']:>14.6f} {r['tick_ask']:>14.6f} {r['tick_last']:>14.6f} {spread_val:>10.6f} {tt:>22} {r['tick_fresh_sec']:>10.1f} {fresh:>7}")
    print()

    # === SUMMARY ===
    print("=" * 120)
    print("SUMMARY")
    print("=" * 120)
    total = len(results)
    avail = sum(1 for r in results if r["available"])
    visible = sum(1 for r in results if r["visible"])
    spread_ok = sum(1 for r in results if r["available"] and r["spread_pts"] <= SYMBOLS[r["symbol"]]["max_spread_pts"])
    fresh_tick = sum(1 for r in results if r["tick_fresh_sec"] is not None and r["tick_fresh_sec"] < 10)
    stale_tick = sum(1 for r in results if r["tick_fresh_sec"] is not None and r["tick_fresh_sec"] > 60)

    print(f"  Symbols checked:      {total}")
    print(f"  Available in MT5:     {avail}/{total}")
    print(f"  Visible in MW:        {visible}/{total}")
    print(f"  Spread within limit:  {spread_ok}/{avail}")
    print(f"  Fresh ticks (<10s):   {fresh_tick}/{total}")
    print(f"  Stale ticks (>60s):   {stale_tick}/{total}")

    # Flags
    issues = []
    for r in results:
        if not r["available"]:
            issues.append(f"  [CRITICAL] {r['symbol']}: NOT AVAILABLE in MT5")
        elif not r["visible"]:
            issues.append(f"  [WARNING]  {r['symbol']}: Not visible in Market Watch")
        if r["available"] and r["spread_pts"] > SYMBOLS[r["symbol"]]["max_spread_pts"]:
            issues.append(f"  [SPREAD]   {r['symbol']}: spread {r['spread_pts']}pts > max {SYMBOLS[r['symbol']]['max_spread_pts']}pts")
        if r["available"] and SYMBOLS[r["symbol"]]["max_spread_atr_ratio"] and r["spread_atr_pct"] is not None:
            if r["spread_atr_pct"] > SYMBOLS[r["symbol"]]["max_spread_atr_ratio"] * 100:
                issues.append(f"  [ATR_SPRD] {r['symbol']}: spread/ATR {r['spread_atr_pct']:.2f}% > max {SYMBOLS[r['symbol']]['max_spread_atr_ratio']*100:.1f}%")
        if r["tick_fresh_sec"] is not None and r["tick_fresh_sec"] > 60:
            issues.append(f"  [STALE]    {r['symbol']}: tick is {r['tick_fresh_sec']:.0f}s old")
        if r["available"] and r["last_bar_h1"]:
            delta_h = (now_utc - r["last_bar_h1"]).total_seconds() / 3600
            if delta_h > 8:
                issues.append(f"  [DATA]     {r['symbol']}: H1 bar {delta_h:.1f}h old (data stale?)")
        if r["bars_h1"] > 0 and r["bars_h1"] < 100:
            issues.append(f"  [DATA]     {r['symbol']}: Only {r['bars_h1']} H1 bars (insufficient history)")

    if issues:
        print(f"\n  ISSUES FOUND ({len(issues)}):")
        for iss in issues:
            print(iss)
    else:
        print("\n  NO ISSUES FOUND — all symbols green.")

    print("\n" + "=" * 120)

if __name__ == "__main__":
    main()
