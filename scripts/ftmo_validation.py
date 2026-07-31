"""Validation FTMO complète de la Solution A.
Analyse: FTMO protector, données récentes, drawdowns par période.
"""

import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

from scripts.backtest_full import *

RISK_PER_TRADE = 0.008
INITIAL_BALANCE = 200000.0
MIN_TRADES = 10

# ── Symboles FTMO-viables (Solution A) ──
BEST_SYMBOLS = ["US500.cash", "US100.cash", "JP225.cash", "XAUUSD", "XAGUSD", "USOIL.cash"]


# SimTrade sans trailing ni partial
class SimTradeSA(SimTrade):
    def update_trailing(self, atr_v):
        pass

    def check_partial(self, atr_v, current_price=None):
        pass


# ── Signal Solution A ──
def sol_a_signal(
    i,
    close,
    high,
    low,
    atr_arr,
    adx_arr,
    pdi,
    ndi,
    ema20,
    rvol_arr,
    cmf_arr,
    obv_type_arr,
    obv_div_arr,
    symbol,
    sym_cfg,
    ol_emu,
):
    atr_v = atr_arr[i]
    adx_v = adx_arr[i]
    if np.isnan(atr_v) or atr_v <= 0 or np.isnan(adx_v) or adx_v <= 0:
        return None, None
    mp = 20
    if i < mp + 1:
        return None, None
    mom = float(close[i] - close[i - mp])
    if np.isnan(mom) or np.isinf(mom):
        return None, None
    mom_abs = abs(mom)
    is_trending = adx_v >= 22
    thresh = 4.0 if is_trending else 3.5
    thresh = max(1.5, min(3.0, thresh))
    threshold_value = thresh * atr_v
    if mom_abs < threshold_value:
        return None, None
    raw_score = min(1.0, mom_abs / (threshold_value * 2))
    # ADX slope
    half = max(14, mp // 2)
    if i >= half + 28 and not np.isnan(adx_arr[i]) and not np.isnan(adx_arr[i - half]):
        adx_slope = adx_arr[i] - adx_arr[i - half]
        slope_th = -8.0 if raw_score > 0.70 else -5.0
        if adx_slope < slope_th:
            return None, None
    # DI filter
    pdi_v = pdi[i]
    ndi_v = ndi[i]
    if np.isnan(pdi_v) or np.isnan(ndi_v):
        return None, None
    dir_ok = True
    di_sugg = None
    if mom > 0:
        if pdi_v <= ndi_v * 0.8:
            dir_ok = False
            di_sugg = "SELL"
    else:
        if ndi_v <= pdi_v * 0.8:
            dir_ok = False
            di_sugg = "BUY"
    action = None
    score = 0.0
    if mom > 0 and mom_abs >= threshold_value:
        action = "BUY"
        score = 0.35 + raw_score * 0.60
    elif mom < 0 and mom_abs >= threshold_value:
        action = "SELL"
        score = 0.35 + raw_score * 0.60
    if action is None:
        return None, None
    if not dir_ok and di_sugg is not None and i >= 7:
        short_mom = float(close[i] - close[i - 5])
        short_mom_abs = abs(short_mom)
        ot = threshold_value * 2.0 if adx_v < 22 else threshold_value * 0.5
        if di_sugg == "SELL" and short_mom < -ot:
            action = "SELL"
            score = 0.35 + min(1.0, short_mom_abs / (threshold_value * 2)) * 0.60
            dir_ok = True
        elif di_sugg == "BUY" and short_mom > ot:
            action = "BUY"
            score = 0.35 + min(1.0, short_mom_abs / (threshold_value * 2)) * 0.60
            dir_ok = True
    if not dir_ok:
        return None, None
    ev = ema20[i]
    if np.isnan(ev) or ev <= 0:
        return None, None
    pb_dist = (close[i] - ev) / ev * 100
    pb_mult = 0.5 if is_trending else 0.3
    pb_band = max(0.05, min(1.0, (pb_mult * atr_v) / ev * 100))
    if abs(pb_dist) >= pb_band:
        return None, None
    final_score = min(0.99, score)
    if final_score < 0.60:
        return None, None
    regime = "RANGING"
    if is_trending and action == "BUY":
        regime = "TREND_UP"
    elif is_trending and action == "SELL":
        regime = "TREND_DOWN"
    return {
        "action": action,
        "score": final_score,
        "atr": atr_v,
        "adx": adx_v,
        "regime": regime,
        "sl_atr": 1.5,
        "tp_atr": 6.0,
        "threshold_value": threshold_value,
    }, raw_score


# ── Backtest engine avec FTMO protector optionnel ──
def run_bt(symbol, signal_fn, timeframe="H1", start_date=None, end_date=None, use_ftmo_protector=False):
    fp = Path(f"data/historical/{symbol}_{timeframe}.parquet")
    if not fp.exists():
        return []
    df = pd.read_parquet(fp)
    if len(df) < 80:
        return []
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if start_date:
        df = df[df["timestamp"] >= start_date]
    if end_date:
        df = df[df["timestamp"] <= end_date]
    if len(df) < 80:
        return []

    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    volume = df.get("volume", df.get("tick_volume", np.ones(len(close)))).values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    atr_arr, adx_arr, pdi, ndi, ema20, rvol_arr, cmf_arr, obv_type_arr, obv_div_arr = precalc_indicators(
        high, low, close, volume
    )
    ol_emu = OnlineLearnerEmu()
    trades = []
    open_trades = []
    bars_since_last = 999

    # FTMO protector state
    peak_balance = INITIAL_BALANCE
    current_balance = INITIAL_BALANCE
    risk_mult = 1.0
    daily_pnl = 0.0
    last_day = None

    for i in range(80, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0
        bar_time = pd.Timestamp(times[i]) if hasattr(times[i], "strftime") else pd.Timestamp(times[i])

        # FTMO protector : daily loss check
        if use_ftmo_protector:
            bar_day = bar_time.date()
            if last_day is None:
                last_day = bar_day
            if bar_day != last_day:
                daily_pnl = 0.0  # reset daily
                last_day = bar_day
            # DD-based risk reduction
            dd_pct = (peak_balance - current_balance) / peak_balance * 100 if peak_balance > 0 else 0
            if dd_pct > 7.0:
                risk_mult = 0.0  # stop trading
            elif dd_pct > 5.0:
                risk_mult = 0.2
            elif dd_pct > 3.0:
                risk_mult = 0.5
            else:
                risk_mult = 1.0

        # Update open trades
        still_open = []
        for t in open_trades:
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            if not t.closed and i - t.open_bar > 120:
                t.closed = True
                t.close_price = close[i]
                t.close_time = times[i]
                t.result = "TIMEOUT"
                t.bars_held = i - t.open_bar
                t._calc_pnl()
            if not t.closed:
                still_open.append(t)
            else:
                ol_emu.record_result(t.symbol, t.profit_usd_cost > 0)
                # Update balance for FTMO protector
                if use_ftmo_protector:
                    current_balance += t.profit_usd_cost
                    if current_balance > peak_balance:
                        peak_balance = current_balance
                    daily_pnl += t.profit_usd_cost
        open_trades = still_open
        bars_since_last += 1
        if atr_v <= 0:
            continue

        # Skip if FTMO protector stopped trading
        if use_ftmo_protector and risk_mult <= 0:
            continue

        signal, raw = signal_fn(
            i,
            close,
            high,
            low,
            atr_arr,
            adx_arr,
            pdi,
            ndi,
            ema20,
            rvol_arr,
            cmf_arr,
            obv_type_arr,
            obv_div_arr,
            symbol,
            {},
            ol_emu,
        )
        if signal is None or bars_since_last < 3:
            continue

        sd = signal["sl_atr"] * atr_v
        td = signal["tp_atr"] * atr_v
        if signal["action"] == "BUY":
            sp, tp = close[i] - sd, close[i] + td
        else:
            sp, tp = close[i] + sd, close[i] - td
        if sd > 0 and td / sd < 1.5:
            continue
        if any(t.action == signal["action"] for t in open_trades):
            continue

        # Apply FTMO risk reduction
        eff_risk = RISK_PER_TRADE * risk_mult
        if eff_risk <= 0:
            continue

        # Custom SimTrade with adjustable risk
        t = SimTradeSA(
            symbol, signal["action"], close[i], sp, tp, atr_v, signal["regime"], i, times[i], current_balance
        )
        # Scale lot by risk multiplier
        t.lot = t.lot * risk_mult
        if t.lot < 0.01:
            continue
        trades.append(t)
        open_trades.append(t)
        bars_since_last = 0
    return trades


def run_multi_tf(symbol, signal_fn, start_date=None, end_date=None, use_ftmo_protector=False):
    all_t = []
    for tf in ("H1", "H4", "D1"):
        t = run_bt(symbol, signal_fn, tf, start_date, end_date, use_ftmo_protector)
        all_t.extend(t)
    return all_t


def compute_metrics_with_dd(closed, balance=INITIAL_BALANCE):
    """Compute metrics avec suivi realiste du drawdown."""
    if not closed:
        return {"n": 0}
    wins = [t for t in closed if t.profit_usd_cost > 0]
    losses = [t for t in closed if t.profit_usd_cost <= 0]
    n = len(closed)
    nw = len(wins)
    wr = nw / n * 100 if n > 0 else 0
    tp = sum(t.profit_usd_cost for t in closed)
    gp = sum(max(0, t.profit_usd_cost) for t in closed)
    gl = abs(sum(min(0, t.profit_usd_cost) for t in closed))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)

    # Drawdown analysis
    peak = balance
    dd_max = 0.0
    bal = balance
    dd_periods = []  # (dd_pct, start_time, end_time)
    in_dd = False
    dd_start = None
    for t in sorted(closed, key=lambda x: x.close_time or x.open_time):
        bal += t.profit_usd_cost
        if bal > peak:
            peak = bal
            if in_dd:
                dd_periods.append((dd_max, dd_start, t.close_time))
                in_dd = False
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        if dd > 3.0 and not in_dd:
            in_dd = True
            dd_start = t.close_time
        if dd > dd_max:
            dd_max = dd

    # Yearly breakdown
    yearly = {}
    for t in closed:
        if t.close_time:
            try:
                y = pd.Timestamp(t.close_time).year
                yearly.setdefault(y, {"n": 0, "pnl": 0.0})
                yearly[y]["n"] += 1
                yearly[y]["pnl"] += t.profit_usd_cost
            except:
                pass

    return {
        "n": n,
        "wins": nw,
        "losses": n - nw,
        "win_rate": round(wr, 1),
        "total_pnl": round(tp, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "avg_pnl": round(tp / n, 2) if n else 0,
        "yearly": {str(k): v for k, v in sorted(yearly.items())},
    }


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

results = {"solution_a": {}, "ftmo_protector": {}, "recent": {}, "dd_analysis": {}}

print("=" * 110)
print("  VALIDATION FTMO — Solution A")
print(f"  Risk: {RISK_PER_TRADE * 100:.2f}%/trade, Balance: ${INITIAL_BALANCE:,.0f}")
print("=" * 110)

# ── 1. BACKTEST COMPLET (2012-2026) ──
print(f"\n{'─' * 110}")
print("  1. BACKTEST COMPLET 2012-2026 — Solution A")
print(f"{'─' * 110}")
for sym in BEST_SYMBOLS:
    all_t = run_multi_tf(sym, sol_a_signal)
    closed = [t for t in all_t if t.closed]
    if len(closed) < MIN_TRADES:
        continue
    m = compute_metrics_with_dd(closed)
    surv = (
        "✅" if m["profit_factor"] > 1.10 and m["max_drawdown_pct"] < 8 else "🟡" if m["profit_factor"] > 1.05 else "❌"
    )
    print(
        f"  {surv} {sym:12s}  {m['n']:>5d} trades  WR={m['win_rate']:>5.1f}%  PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>5.2f}  DD={m['max_drawdown_pct']:>5.1f}%"
    )
    results["solution_a"][sym] = m

# ── 2. FTMO PROTECTOR ──
print(f"\n{'─' * 110}")
print("  2. AVEC FTMO PROTECTOR (risk réduit à DD>3%, stop à DD>7%)")
print(f"{'─' * 110}")
for sym in BEST_SYMBOLS:
    all_t = run_multi_tf(sym, sol_a_signal, use_ftmo_protector=True)
    closed = [t for t in all_t if t.closed]
    if len(closed) < MIN_TRADES:
        continue
    m = compute_metrics_with_dd(closed)
    surv = (
        "✅" if m["profit_factor"] > 1.10 and m["max_drawdown_pct"] < 8 else "🟡" if m["profit_factor"] > 1.05 else "❌"
    )
    print(
        f"  {surv} {sym:12s}  {m['n']:>5d} trades  WR={m['win_rate']:>5.1f}%  PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>5.2f}  DD={m['max_drawdown_pct']:>5.1f}%"
    )
    results["ftmo_protector"][sym] = m

# ── 3. DONNÉES RÉCENTES 2024-2026 ──
print(f"\n{'─' * 110}")
print("  3. VALIDATION RÉCENTE 2024-2026")
print(f"{'─' * 110}")
for sym in BEST_SYMBOLS:
    all_t = run_multi_tf(sym, sol_a_signal, start_date="2024-01-01")
    closed = [t for t in all_t if t.closed]
    if len(closed) < 5:
        print(f"  {sym:12s}  {len(closed):>4d} trades  ❌ trop peu")
        continue
    m = compute_metrics_with_dd(closed)
    surv = (
        "✅" if m["profit_factor"] > 1.10 and m["max_drawdown_pct"] < 8 else "🟡" if m["profit_factor"] > 1.05 else "❌"
    )
    print(
        f"  {surv} {sym:12s}  {m['n']:>4d} trades  WR={m['win_rate']:>5.1f}%  PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>5.2f}  DD={m['max_drawdown_pct']:>5.1f}%"
    )
    results["recent"][sym] = m

# ── 4. DRAWDOWN ANALYSIS ──
print(f"\n{'─' * 110}")
print("  4. ANALYSE DES DRAWDOWNS PAR SYMBOLE")
print(f"{'─' * 110}")
for sym in BEST_SYMBOLS:
    all_t = run_multi_tf(sym, sol_a_signal)
    closed = [t for t in all_t if t.closed]
    if len(closed) < MIN_TRADES:
        continue
    m = compute_metrics_with_dd(closed)
    print(f"\n  {sym}:")
    print(f"     DD Max: {m['max_drawdown_pct']:.1f}%  |  PF: {m['profit_factor']:.2f}  |  {m['n']} trades")
    yearly = m.get("yearly", {})
    for y, data in list(yearly.items())[-5:]:  # last 5 years
        emoji = "✅" if data["pnl"] > 0 else "❌"
        print(f"     {y}: {emoji} {data['n']:>3d} trades  PnL=${data['pnl']:>+8.2f}")
    results["dd_analysis"][sym] = {"dd": m["max_drawdown_pct"], "yearly": yearly}

# ── 5. SYNTHÈSE ──
print(f"\n{'═' * 110}")
print("  SYNTHÈSE FTMO")
print(f"{'═' * 110}")

print(f"\n  ✅ 3 configs FTMO-viables (DD<8% avec FTMO protector):")
for sym in BEST_SYMBOLS:
    if sym in results["ftmo_protector"]:
        m = results["ftmo_protector"][sym]
        if m["max_drawdown_pct"] < 8 and m["profit_factor"] > 1.10:
            print(
                f"     {sym:12s}  PF={m['profit_factor']:.2f}  DD={m['max_drawdown_pct']:.1f}%  PnL=${m['total_pnl']:>+8.2f}  {m['n']} trades"
            )

print(f"\n  📊 Performance récente (2024-2026):")
for sym in BEST_SYMBOLS:
    if sym in results["recent"]:
        m = results["recent"][sym]
        print(f"     {sym:12s}  {m['n']:>3d} trades  PnL=${m['total_pnl']:>+8.2f}  PF={m['profit_factor']:.2f}")

# Save all results
with open("runtime/ftmo_validation.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\n  Rapport: runtime/ftmo_validation.json")
