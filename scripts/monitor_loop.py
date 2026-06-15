"""
Continuous Market Monitor — surveillance en direct du robot et du marché
Usage: python scripts/monitor_loop.py [--interval 60] [--count 10]
"""
import sys, os, json, time, argparse, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import numpy as np
import config_simple as cfg
from engine_simple.mt5_connector import MT5Connector
import MetaTrader5 as mt5

logger = logging.getLogger("monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def calc_adx(high, low, close, period=14):
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


def scan(conn):
    """Single market scan, returns dict with all key metrics."""
    result = {
        "timestamp": time.time(),
        "utc_hour": time.gmtime().tm_hour,
        "trading_window": True,  # 24/5 — toujours en session (weekend bloqué par FTMO)
    }
    
    # Account
    acct = conn.get_account_info()
    if acct:
        result["balance"] = round(acct.balance, 2)
        result["equity"] = round(acct.equity, 2)
        result["floating"] = round(acct.equity - acct.balance, 2)
    
    # Positions
    positions = conn.get_positions()
    result["num_positions"] = len(positions)
    result["total_pnl"] = round(sum(p.profit for p in positions), 2)
    result["positions"] = []
    for p in positions:
        result["positions"].append({
            "symbol": p.symbol, "ticket": p.ticket,
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": p.volume, "profit": round(p.profit, 2),
            "sl": p.sl, "tp": p.tp,
        })
    
    # Market data per symbol
    result["markets"] = {}
    for sym in cfg.SYMBOLS:
        try:
            rates = conn.get_rates(sym, mt5.TIMEFRAME_M1, 100)
        except Exception as e:
            rates = None
            logger.error(f"get_rates failed for {sym}: {e}")
        m = {"error": "no data"}
        if rates is not None and len(rates) >= 20:
                try:
                    close = np.array([r["close"] for r in rates])
                    high = np.array([r["high"] for r in rates])
                    low = np.array([r["low"] for r in rates])
                    curr = float(close[-1])
                    
                    m.pop("error", None)  # Données valides, plus d'erreur
                    m["price"] = round(curr, 5)
                    m["change_5bar"] = round((curr / close[-5] - 1) * 100, 3) if len(close) > 5 else 0
                    
                    # ATR
                    tr_vals = [max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])) for i in range(1, len(rates))]
                    atr14 = float(np.mean(tr_vals[-14:])) if len(tr_vals) >= 14 else float(np.mean(tr_vals))
                    m["atr"] = round(atr14, 5)
                    m["atr_pct"] = round(atr14 / curr * 100, 3)
                    
                    # MAs
                    m["ma20"] = round(float(np.mean(close[-20:])), 5)
                    m["ma50"] = round(float(np.mean(close[-50:])), 5) if len(close) >= 50 else 0
                    m["price_vs_ma20"] = round((curr / m["ma20"] - 1) * 100, 2)
                    
                    # ADX / Regime
                    adx_val, pdi, ndi = calc_adx(high, low, close)
                    m["adx"] = round(adx_val, 1)
                    m["pdi"] = round(pdi, 1)
                    m["ndi"] = round(ndi, 1)
                    if adx_val > 20:
                        m["regime"] = "TREND_UP" if pdi > ndi else "TREND_DOWN"
                    else:
                        m["regime"] = "RANGING"
                    
                    # Range position
                    rl, rh = float(np.min(low[-20:])), float(np.max(high[-20:]))
                    m["range_low"] = round(rl, 5)
                    m["range_high"] = round(rh, 5)
                    m["range_pos_pct"] = round((curr - rl) / (rh - rl) * 100, 0) if rh > rl else 50
                    
                    # Vol status
                    if m["atr_pct"] < 0.05:
                        m["vol_status"] = "LOW"
                    elif m["atr_pct"] > 0.15:
                        m["vol_status"] = "HIGH"
                    else:
                        m["vol_status"] = "NORMAL"
                except Exception as e:
                    logger.error(f"Market processing failed for {sym}: {e}")
                    m = {"error": f"processing error: {e}"}
        result["markets"][sym] = m
    
    return result


def print_scan(s):
    """Pretty-print a scan result."""
    ts = time.strftime("%H:%M:%S", time.localtime(s["timestamp"]))
    window_status = "🟢" if s["trading_window"] else "🔴"
    print(f"\n{'='*65}")
    print(f"  📡 MONITOR SCAN  |  {ts} UTC ({s['utc_hour']}h)  |  Window: {window_status}")
    print(f"{'='*65}")
    
    # Account
    print(f"  💰 Balance: ${s.get('balance',0):.2f}  |  Equity: ${s.get('equity',0):.2f}  |  Float: ${s.get('floating',0):+.2f}")
    
    # Market
    for sym, m in s.get("markets", {}).items():
        if "error" in m:
            print(f"  📊 {sym}: {m['error']}")
            continue
        regime_icon = {"TREND_UP": "↑", "TREND_DOWN": "↓", "RANGING": "↔"}.get(m.get("regime"), "?")
        vol_icon = {"HIGH": "⚡", "LOW": "💤", "NORMAL": ""}.get(m.get("vol_status"), "")
        print(f"  📊 {sym} {regime_icon} {m['price']:.5f}  "
              f"ADX={m['adx']}  ATR={m['atr_pct']:.2f}%{vol_icon}  "
              f"MA20={m['price_vs_ma20']:+.2f}%  "
              f"Range={m['range_pos_pct']:.0f}%")
    
    # Positions
    pos = s.get("positions", [])
    if pos:
        print(f"  📋 {s['num_positions']} positions  |  Float: ${s['total_pnl']:+.2f}")
        for p in sorted(pos, key=lambda x: x["profit"]):
            icon = "🟢" if p["profit"] > 0 else "🔴"
            print(f"    {icon} {p['symbol']} {p['type']} {p['volume']:.2f}  ${p['profit']:+.2f}  SL={p['sl']:.5f}")
    else:
        print(f"  📋 No open positions")
    
    # Alerts
    alerts = []
    for sym, m in s.get("markets", {}).items():
        if isinstance(m, dict) and m.get("vol_status") == "HIGH":
            alerts.append(f"⚠️  {sym}: High volatility ({m['atr_pct']:.2f}%)")
        if isinstance(m, dict) and m.get("adx", 0) > 50:
            alerts.append(f"⚡ {sym}: Very strong trend (ADX={m['adx']})")
    for p in pos:
        if p["profit"] < -100:
            alerts.append(f"🔴 {p['symbol']} #{p['ticket']}: Large loss ${p['profit']:.2f}")
    if s.get("floating", 0) < -500:
        alerts.append(f"🔴 CRITICAL: Floating loss ${s['floating']:.2f}")
    
    if alerts:
        print(f"  {'='*40}")
        for a in alerts:
            print(f"  {a}")
    
    return alerts


def check_logs():
    """Check robot logs for ERROR/CRITICAL lines since last check."""
    log_file = "logs/simple_robot.log"
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file) as f:
            lines = f.readlines()
        errors = []
        for line in lines[-200:]:
            if "ERROR" in line and "DEBUG" not in line:
                errors.append(line.strip()[:200])
            elif "CRITICAL" in line:
                errors.append(f"🔥 {line.strip()[:200]}")
        return errors[-5:]  # last 5 errors
    except:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60, help="Scan interval in seconds")
    parser.add_argument("--count", type=int, default=999, help="Number of scans")
    args = parser.parse_args()
    
    print(f"🚀 Continuous Market Monitor started")
    print(f"   Interval: {args.interval}s  |  Scans: {args.count}")
    print(f"   Symbols: {cfg.SYMBOLS}")
    print(f"   Risk: {cfg.RISK_PER_TRADE:.4f}  |  MaxPos: {cfg.MAX_POSITIONS}")
    
    conn = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
    if not conn.connect():
        print("ERROR: Cannot connect to MT5")
        return
    
    try:
        for i in range(args.count):
            s = scan(conn)
            alerts = print_scan(s)
            
            # Check logs for errors
            log_errors = check_logs()
            if log_errors:
                print(f"  {'='*40}")
                print(f"  🔥 LAST LOG ERRORS:")
                for e in log_errors:
                    print(f"    {e}")
            
            if i < args.count - 1:
                print(f"\n  ⏳ Next scan in {args.interval}s... (Ctrl+C to stop)")
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n🛑 Monitor stopped by user")
    finally:
        conn.disconnect()
        print("👋 Disconnected")


if __name__ == "__main__":
    main()
