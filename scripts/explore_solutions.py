"""Analyse comparative des 3 solutions pour FTMO."""

import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

from scripts.backtest_full import *

RISK_PER_TRADE = 0.008
INITIAL_BALANCE = 200000.0
MIN_TRADES = 30
MIN_BARS = 80

# ── Symboles testés ──
FOREX = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"]
CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD"]
INDICES = ["US500.cash", "US30.cash", "US100.cash", "JP225.cash"]
COMMOD = ["XAUUSD", "XAGUSD", "USOIL.cash"]
ALL_SYM = FOREX + CRYPTO + INDICES + COMMOD


# SimTrade sans trailing ni partial
class SimTradeBasic(SimTrade):
    def update_trailing(self, atr_v):
        pass

    def check_partial(self, atr_v, current_price=None):
        pass


# ── Solution A: MOM20x3 optimisé (high threshold, no trailing) ──
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
    """MOM20x3 avec thresh=4.0/3.5, SL=1.5, TP=6.0, NO trailing, NO partial."""
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

    # ADX slope filter
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

    # DI override
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

    # Pullback filter
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

    signal = {
        "action": action,
        "score": final_score,
        "atr": atr_v,
        "adx": adx_v,
        "regime": regime,
        "sl_atr": 1.5,
        "tp_atr": 6.0,
        "threshold_value": threshold_value,
    }
    return signal, raw_score


# ── Solution B: Mean Reversion ──
def sol_b_signal(
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
    """Mean reversion: buy oversold, sell overbought in ranging markets."""
    atr_v = atr_arr[i]
    adx_v = adx_arr[i]
    if np.isnan(atr_v) or atr_v <= 0 or np.isnan(adx_v):
        return None, None
    ev = ema20[i]
    if np.isnan(ev) or ev <= 0:
        return None, None
    # Only trade in ranging/no-trend regime (ADX < 20)
    if adx_v >= 20:
        return None, None
    dist_atr = (close[i] - ev) / max(atr_v, 1e-10)
    score = min(0.95, 0.5 + abs(dist_atr) * 0.08)
    if dist_atr < -2.0:  # oversold
        return {"action": "BUY", "score": score, "atr": atr_v, "regime": "RANGING", "sl_atr": 1.5, "tp_atr": 2.0}, score
    if dist_atr > 2.0:  # overbought
        return {
            "action": "SELL",
            "score": score,
            "atr": atr_v,
            "regime": "RANGING",
            "sl_atr": 1.5,
            "tp_atr": 2.0,
        }, score
    return None, None


# ── Solution C: Structure Break exits ──
def sol_c_signal(
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
    """MOM20x3 + structure-based exits (BOS/CHoCH) instead of ATR trailing."""
    from engine_simple.structure_analyzer import structure_exit_signal

    signal, raw = sol_a_signal(
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
    )
    if signal is None:
        return None, None
    # Store structure data for exit logic
    h1h = high[max(0, i - 30) : i + 1]
    h1l = low[max(0, i - 30) : i + 1]
    h1c = close[max(0, i - 30) : i + 1]
    if len(h1h) >= 15:
        should_exit, reason, idx = structure_exit_signal(0 if signal["action"] == "BUY" else 1, h1h, h1l, h1c, window=5)
        signal["_struct_break"] = should_exit
    else:
        signal["_struct_break"] = False
    # Still use fixed SL/TP but tighter SL
    signal["sl_atr"] = 1.2
    signal["tp_atr"] = 5.0
    return signal, raw


# ── Backtest engine ──
def run_backtest(symbol, signal_fn, timeframe="H1", use_trailing=False):
    fp = Path(f"data/historical/{symbol}_{timeframe}.parquet")
    if not fp.exists():
        return []
    df = pd.read_parquet(fp)
    if len(df) < MIN_BARS:
        return []
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
    if len(df) < MIN_BARS:
        return []

    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    volume = df.get("volume", df.get("tick_volume", np.ones(len(close)))).values.astype(float)
    times = df["timestamp"].values
    n = len(close)
    times_dt = pd.to_datetime(times)

    atr_arr, adx_arr, pdi, ndi, ema20, rvol_arr, cmf_arr, obv_type_arr, obv_div_arr = precalc_indicators(
        high, low, close, volume
    )
    ol_emu = OnlineLearnerEmu()
    trades = []
    open_trades = []
    bars_since_last = 999
    SimClass = SimTrade if use_trailing else SimTradeBasic

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0
        still_open = []
        for t in open_trades:
            t.update_peak(high[i], low[i])
            if use_trailing:
                t.check_partial(atr_v, current_price=close[i])
                t.update_trailing(atr_v)
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
        open_trades = still_open
        bars_since_last += 1
        if atr_v <= 0:
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
        t = SimClass(symbol, signal["action"], close[i], sp, tp, atr_v, signal["regime"], i, times[i], INITIAL_BALANCE)
        trades.append(t)
        open_trades.append(t)
        bars_since_last = 0
    return trades


def run_multi_tf(symbol, signal_fn, use_trailing=False):
    all_t = []
    for tf in ("H1", "H4", "D1"):
        t = run_backtest(symbol, signal_fn, tf, use_trailing)
        all_t.extend(t)
    return all_t


def print_results(name, symbol_results):
    """Affiche les resultats tries par PnL."""
    print(f"\n  ═══ {name} ═══")
    ranked = sorted(symbol_results.items(), key=lambda x: x[1]["pnl"], reverse=True)
    for sym, r in ranked:
        n = r["n"]
        wr = r["wr"]
        pnl = r["pnl"]
        pf = r["pf"]
        dd = r["dd"]
        emoji = "✅" if r["survives"] else "⚠️" if wr > 50 else "❌"
        print(f"  {emoji} {sym:12s}  {n:>5d} trades  WR={wr:>5.1f}%  PnL=${pnl:>+9.2f}  PF={pf:>5.2f}  DD={dd:>5.1f}%")


def analyze_solution(name, signal_fn, symbols, use_trailing=False):
    results = {}
    for sym in symbols:
        all_t = run_multi_tf(sym, signal_fn, use_trailing)
        closed = [t for t in all_t if t.closed]
        if len(closed) < MIN_TRADES:
            continue
        m = compute_metrics(closed)
        results[sym] = {
            "n": m["n"],
            "wr": m["win_rate"],
            "pnl": m["total_pnl"],
            "pf": m["profit_factor"],
            "dd": m["max_drawdown_pct"],
            "significant": m.get("significant", False),
            "survives": m.get("significant", False) and m["win_rate"] > 50 and m["total_pnl"] > 0,
        }
    print_results(name, results)
    return results


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
print("=" * 110)
print("  ANALYSE COMPARATIVE — 3 Solutions pour FTMO")
print(f"  Risk: {RISK_PER_TRADE * 100:.2f}%/trade, Balance: ${INITIAL_BALANCE:,.0f}")
print("=" * 110)

all_results = {}

# Solution A: MOM20x3 optimisé
print(f"\n{'─' * 110}")
print("  SOLUTION A — MOM20x3 OPTIMISÉ (thresh=4.0/3.5, SL=1.5, TP=6.0, NO trailing)")
print("  Concept: Signaux forts uniquement, SL serré, TP large, Pas de trailing")
print(f"{'─' * 110}")
all_results["A"] = analyze_solution("A - MOM20x3 optimisé", sol_a_signal, ALL_SYM)

# Solution B: Mean Reversion
print(f"\n{'─' * 110}")
print("  SOLUTION B — MEAN REVERSION (ADX<20, dist>2.0×ATR de EMA20, SL=1.5, TP=2.0)")
print("  Concept: Vendre les extremes, acheter les oversolds en range")
print(f"{'─' * 110}")
all_results["B"] = analyze_solution("B - Mean Reversion", sol_b_signal, ALL_SYM)

# Solution C: Structure exits
print(f"\n{'─' * 110}")
print("  SOLUTION C — STRUCTURE BREAK (MOM20x3 + BOS/CHoCH exits)")
print("  Concept: MOM20x3 entries, structure-based exits (pas de trailing ATR)")
print(f"{'─' * 110}")
all_results["C"] = analyze_solution("C - Structure Break", sol_c_signal, ALL_SYM)

# ── Synthèse ──
print(f"\n{'═' * 110}")
print("  SYNTHÈSE COMPARATIVE")
print(f"{'═' * 110}")
print(
    f"  {'Solution':20s} {'Symbole':12s} {'Trades':>6s} {'WR':>5s}  {'PnL':>10s}  {'PF':>5s}  {'DD':>5s}  {'FTMO':>5s}"
)
print(f"  {'-' * 75}")


def ftmo_verdict(r):
    dd = r["dd"]
    pf = r["pf"]
    if dd < 8 and pf > 1.10:
        return "✅"
    if dd < 15 and pf > 1.05:
        return "🟡"
    return "❌"


for sol_name, results in sorted(all_results.items()):
    ranked = sorted(results.items(), key=lambda x: x[1]["pnl"], reverse=True)
    for sym, r in ranked[:4]:  # top 4 per solution
        ftmo = ftmo_verdict(r)
        print(
            f"  {f'SOLUTION {sol_name}':20s} {sym:12s} {r['n']:>6d} {r['wr']:>4.1f}%  ${r['pnl']:>+9.2f} {r['pf']:>5.2f} {r['dd']:>5.1f}% {ftmo:>5s}"
        )
    print()

# ── Recommandation finale ──
print(f"{'═' * 110}")
print("  RECOMMANDATION FINALE")
print(f"{'═' * 110}")

# Find best FTMO-viable combo
viable = []
for sol_name, results in all_results.items():
    for sym, r in results.items():
        if r["dd"] < 8 and r["pf"] > 1.10 and r["n"] >= 50:
            viable.append((sol_name, sym, r["pf"], r["dd"], r["pnl"], r["n"]))

if viable:
    viable.sort(key=lambda x: x[4], reverse=True)
    print(f"\n  ✅ Configurations FTMO-viables (DD<8%, PF>1.10, 50+ trades):")
    for sol, sym, pf, dd, pnl, n in viable:
        print(f"     Solution {sol} — {sym:12s}  {n:>4d} trades  PF={pf:.2f}  DD={dd:.1f}%  PnL=${pnl:>+.0f}")
else:
    print(f"\n  ⚠️  Aucune configuration seule ne passe les critères FTMO.")
    print(f"     Suggestions: combiner plusieurs strategies ou reduire le risk.")

# Best non-FTMO options
print(f"\n  📊 TOP 5 configurations (toutes solutions confondues):")
all_entries = []
for sol_name, results in all_results.items():
    for sym, r in results.items():
        all_entries.append((sol_name, sym, r["pf"], r["dd"], r["pnl"], r["n"], r["wr"]))
all_entries.sort(key=lambda x: x[4], reverse=True)
for i, (sol, sym, pf, dd, pnl, n, wr) in enumerate(all_entries[:10], 1):
    print(
        f"     {i}. Solution {sol} — {sym:12s}  {n:>5d}tr  WR={wr:.1f}%  PF={pf:.2f}  DD={dd:.1f}%  PnL=${pnl:>+9.2f}"
    )

# Save results
out = {"timestamp": datetime.utcnow().isoformat(), "results": all_results}
with open("runtime/solutions_comparison.json", "w") as f:
    json.dump(out, f, indent=2, default=str)
print(f"\n  Rapport: runtime/solutions_comparison.json")
