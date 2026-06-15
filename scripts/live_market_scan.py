"""
Live market scan — analyse en temps réel des symboles actifs
Usage: python scripts/live_market_scan.py
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import numpy as np
import config_simple as cfg
from engine_simple.mt5_connector import MT5Connector
import MetaTrader5 as mt5


def calc_adx(high, low, close, period=14):
    """Calcul ADX simplifié"""
    if len(high) < period + 1:
        return 0, 0, 0
    plus_dm = np.zeros_like(high)
    minus_dm = np.zeros_like(high)
    tr_arr = np.zeros_like(high)
    for i in range(1, len(high)):
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        plus_dm[i] = max(up_move, 0) if up_move > down_move else 0
        minus_dm[i] = max(down_move, 0) if down_move > up_move else 0
        tr_arr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
    atr_val = float(np.mean(tr_arr[-period:]))
    plus_di = 100 * float(np.mean(plus_dm[-period:])) / atr_val if atr_val > 0 else 0
    minus_di = 100 * float(np.mean(minus_dm[-period:])) / atr_val if atr_val > 0 else 0
    dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 1) * 100 if (plus_di + minus_di) > 0 else 0
    return dx, plus_di, minus_di


def main():
    conn = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
    connected = conn.connect()
    print(f"MT5 connected: {connected}")

    if not connected:
        print("Cannot connect to MT5")
        return

    account = conn.get_account_info()
    if account:
        print(f"Account: #{account.login} Balance=${account.balance:.2f} Equity=${account.equity:.2f}")
        print(f"  Floating PnL: ${account.equity - account.balance:+.2f}")

    utc_hour = time.gmtime().tm_hour
    session_map = {0: "pacific", 1: "pacific", 2: "pacific", 3: "asia", 4: "asia",
                   5: "asia", 6: "asia", 7: "asia", 8: "london", 9: "london",
                   10: "london", 11: "london", 12: "london", 13: "new_york",
                   14: "new_york", 15: "new_york", 16: "new_york", 17: "new_york",
                   18: "new_york", 19: "new_york", 20: "new_york", 21: "new_york",
                   22: "pacific", 23: "pacific"}
    session = session_map.get(utc_hour, "unknown")
    in_session = 5 <= utc_hour < 18
    print(f"UTC: {utc_hour}h | Session: {session} | Trading window: {in_session}")
    print()

    # Market analysis per symbol
    for sym in cfg.SYMBOLS:
        print(f"{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")

        rates = conn.get_rates(sym, mt5.TIMEFRAME_M1, 100)
        if rates is None or len(rates) < 20:
            print(f"  No data (got {len(rates) if rates else 0} bars)")
            continue

        close = np.array([r['close'] for r in rates])
        high = np.array([r['high'] for r in rates])
        low = np.array([r['low'] for r in rates])

        curr = close[-1]
        prev_5 = close[-5] if len(close) > 5 else close[0]
        change_5 = (curr - prev_5) / prev_5 * 100
        print(f"  Price: {curr:.5f}  (5-bar Δ: {change_5:+.3f}%)")

        # ATR
        tr_vals = []
        for i in range(1, len(rates)):
            tr = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
            tr_vals.append(tr)
        atr14 = float(np.mean(tr_vals[-14:])) if len(tr_vals) >= 14 else float(np.mean(tr_vals))
        atr_pct = atr14 / curr * 100 if curr > 0 else 0
        print(f"  ATR(14): {atr14:.5f} ({atr_pct:.2f}%)")

        # MAs
        ma20 = float(np.mean(close[-20:]))
        ma50 = float(np.mean(close[-50:])) if len(close) >= 50 else ma20
        print(f"  MA20: {ma20:.5f}  MA50: {ma50:.5f}")
        print(f"  Price vs MA20: {(curr/ma20-1)*100:+.2f}%")

        # ADX / Regime
        if len(rates) >= 15:
            adx_val, pdi, ndi = calc_adx(high, low, close)
            if adx_val > 20:
                if pdi > ndi:
                    regime = "TREND_UP ↑"
                    trend_strength = "strong" if adx_val > 30 else "moderate"
                else:
                    regime = "TREND_DOWN ↓"
                    trend_strength = "strong" if adx_val > 30 else "moderate"
            else:
                regime = "RANGING ↔"
                trend_strength = "low volatility" if adx_val < 15 else "moderate"
            print(f"  ADX: {adx_val:.1f}  +DI: {pdi:.1f}  -DI: {ndi:.1f}")
            print(f"  Regime: {regime} ({trend_strength})")
        else:
            print(f"  ADX: insufficient data")

        # Range
        recent_low = float(np.min(low[-20:]))
        recent_high = float(np.max(high[-20:]))
        range_pct = (curr - recent_low) / (recent_high - recent_low) * 100 if recent_high > recent_low else 50
        print(f"  Range(20): [{recent_low:.5f}, {recent_high:.5f}]")
        print(f"  Position: {range_pct:.0f}% from low")

        # Volatility context
        if atr_pct < 0.05:
            vol_status = "LOW_VOL ⚡"
        elif atr_pct > 0.15:
            vol_status = "HIGH_VOL ⚠️"
        else:
            vol_status = "normal"
        print(f"  Volatility: {vol_status}")

        # Session-specific observation
        print(f"  Spread (approx): {(high[-1]-low[-1]):.5f}")
        print()

    # Open positions (magic=999001, comment varie: ADAPT_RAN ou FTMO_MOM20x3)
    print(f"{'='*60}")
    print(f"  OPEN POSITIONS (magic {cfg.ROBOT_MAGIC})")
    print(f"{'='*60}")
    positions = conn.get_positions()
    total_pnl = 0.0
    for pos in positions:
        total_pnl += pos.profit
        pos_time = getattr(pos, 'time', getattr(pos, 'time_create', None))
        # pos_time peut être datetime ou unix timestamp (int)
        if isinstance(pos_time, (int, float)):
            age_mins = (time.time() - pos_time) / 60
        elif pos_time:
            age_mins = (time.time() - pos_time.timestamp()) / 60
        else:
            age_mins = 0
        type_str = "BUY" if pos.type == 0 else "SELL"
        print(f"  {pos.symbol} #{pos.ticket} {type_str} "
              f"vol={pos.volume:.2f} entry={pos.price_open:.5f} "
              f"sl={pos.sl:.5f} tp={pos.tp:.5f} "
              f"PnL=${pos.profit:+.2f} age={age_mins:.0f}min "
              f"comment='{pos.comment}'")
    if not positions:
        print(f"  No open positions")
    else:
        print(f"  {'='*40}")
        print(f"  TOTAL FLOATING: ${total_pnl:+.2f}")
        print(f"  {'='*40}")
        # Win/Loss breakdown
        winners = sum(1 for p in positions if p.profit > 0)
        losers = sum(1 for p in positions if p.profit < 0)
        print(f"  Winners: {winners}  Losers: {losers}")

    conn.disconnect()


if __name__ == "__main__":
    main()
