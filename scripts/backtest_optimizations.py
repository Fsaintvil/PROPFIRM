"""
Backtest Optimisations — Comparaison paramétrique des 3 optimisations issues de la veille externe.

Tests (dans l'ordre):
  1. SL ATR forex : 2.0x (baseline) vs 2.5x vs 3.0x  [TP ajusté pour garder RR ≈ 2.5]
  2. Partial TP : 50% (baseline) vs 25% vs 75%       [fraction fermée au seuil 0.65 du chemin TP]
  3. Filtre de session forex : OFF vs London-NY overlap (13h-17h GMT)

Méthode : réutilise le moteur de backtest_full.py (prod replica, coûts, trailing,
partial TP) en monkey-patchant get_sym_config (SL/TP) et SimTrade.check_partial
(fraction partial) + filtre de session via wrapper de gen_signal_bar.

Usage:
    python scripts/backtest_optimizations.py --symbols EURUSD,GBPUSD,AUDUSD --tf H1
    python scripts/backtest_optimizations.py   # forex majeurs H1 par défaut
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import backtest_full as bt

# ─── Paramètres du test ────────────────────────────────────────────
FOREX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD"]
DEFAULT_TF = "H1"

# Test 1 : SL ATR multiples (TP scale pour RR ≈ 2.5)
SL_TP_VARIANTS = [
    ("SL_2.0x", 2.0, 5.0, 1.5, 4.0),   # baseline prod
    ("SL_2.5x", 2.5, 6.25, 1.875, 5.0),
    ("SL_3.0x", 3.0, 7.5, 2.25, 6.0),
]

# Test 2 : fraction du partial TP (fermée au seuil progress 0.65 du TP)
PARTIAL_VARIANTS = [("PTP_50pct", 0.50), ("PTP_25pct", 0.25), ("PTP_75pct", 0.75)]

# Test 3 : filtre de session forex (heures GMT inclusives)
SESSION_VARIANTS = [
    ("SES_OFF", None),
    ("SES_LDN_NY", (13, 17)),  # London-NY overlap 13h-17h GMT
]

FOREX_PREFIXES = ("EUR", "GBP", "USD", "AUD", "NZD", "JPY")
FOREX_SET = set(FOREX_SYMBOLS)


# ─── Monkey-patches ────────────────────────────────────────────────

class Patches:
    sl_trend = 2.0
    tp_trend = 5.0
    sl_range = 1.5
    tp_range = 4.0
    partial_frac = 0.50
    session = None  # (start_hour, end_hour) GMT


def patched_get_sym_config(symbol):
    cfg = bt.get_symbol_full_config(symbol)
    cfg = dict(cfg)
    cfg["sl_atr_trending"] = Patches.sl_trend
    cfg["tp_atr_trending"] = Patches.tp_trend
    cfg["sl_atr_ranging"] = Patches.sl_range
    cfg["tp_atr_ranging"] = Patches.tp_range
    return cfg


_orig_gen_signal = bt.gen_signal_bar


def patched_gen_signal_bar(*args, **kwargs):
    if Patches.session is not None:
        # gen_signal_bar(i, close, high, low, volume, times_dt, ...) → times_dt = args[5]
        times_dt = args[5]
        i = args[0]
        hour = times_dt[i].hour
        start, end = Patches.session
        if not (start <= hour <= end):
            return None, None
    return _orig_gen_signal(*args, **kwargs)


_orig_check_partial = bt.SimTrade.check_partial


def patched_check_partial(self, atr_v, current_price=None):
    """Reproduction de check_partial avec fraction configurable."""
    if self.closed or self.partial_closed or atr_v <= 0:
        return
    price = current_price if current_price is not None else self.peak_price
    if self.direction == 0:
        if price <= self.entry:
            return
        prog = (price - self.entry) / max(self.tp - self.entry, 1e-10)
    else:
        if price >= self.entry:
            return
        prog = (self.entry - price) / max(self.entry - self.tp, 1e-10)
    if prog < 0.65:
        return

    self.partial_closed = True
    frac = Patches.partial_frac  # fraction fermée (ex: 0.50 = on ferme 50%)
    self.remaining_vol_ratio = 1.0 - frac

    pv = self._pip_value_corrected(price)
    usdpp_frac = self.lot * frac * pv
    if self.direction == 0:
        pips_partial = (price - self.entry) / self._pip_size
    else:
        pips_partial = (self.entry - price) / self._pip_size
    self.partial_locked_profit = pips_partial * usdpp_frac

    # Coût sur la fraction fermée
    notional_frac = self._notional_usd() * frac
    commission_frac = (notional_frac / 100_000) * bt.COMMISSION_PER_100K
    pips_cost_partial = pips_partial - self.cost_pips * frac
    self.partial_locked_profit_cost = pips_cost_partial * usdpp_frac - commission_frac

    # SL → BE+ buffer pour la fraction restante
    be_mult = bt.get_be_buffer_for_symbol(self.symbol, self.regime)
    be_buffer = be_mult * atr_v
    if self.direction == 0:
        be_sl = self.entry + be_buffer
        if be_sl > self.trailing_sl:
            self.trailing_sl = be_sl
    else:
        be_sl = self.entry - be_buffer
        if be_sl < self.trailing_sl:
            self.trailing_sl = be_sl


def apply_patches(sl_trend, tp_trend, sl_range, tp_range, partial_frac, session):
    Patches.sl_trend = sl_trend
    Patches.tp_trend = tp_trend
    Patches.sl_range = sl_range
    Patches.tp_range = tp_range
    Patches.partial_frac = partial_frac
    Patches.session = session


# ─── Run ───────────────────────────────────────────────────────────

def run_symbol(symbol, tf, df):
    """Backtest un symbole avec les patches courants."""
    bt.get_sym_config = patched_get_sym_config
    bt.SimTrade.check_partial = patched_check_partial
    bt.gen_signal_bar = patched_gen_signal_bar

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
    if len(df) < bt.MIN_BARS:
        return None
    trades = bt.backtest_symbol(symbol, tf, df)
    closed = [t for t in trades if t.closed]
    if len(closed) < 30:
        return None
    m = bt.compute_metrics(closed)
    costs = bt.avg_costs(closed) if hasattr(bt, "avg_costs") else {}
    return {"metrics": m, "costs": costs, "n": len(closed)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest des 3 optimisations (SL / partial / session)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Symboles à tester (défaut: forex majeurs)")
    parser.add_argument("--tf", choices=["H1", "H4", "D1"], default=DEFAULT_TF)
    parser.add_argument("--out", type=str, default="runtime/backtest_optimizations.json")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else FOREX_SYMBOLS
    data_dir = Path("data/historical")

    print("=" * 110)
    print("  BACKTEST OPTIMISATIONS — Veille externe (SL / Partial TP / Session)")
    print(f"  Symboles: {', '.join(symbols)} | TF: {args.tf} | Coûts: spread+slippage+commission")
    print("=" * 110)

    report = {"metadata": {"date": datetime.utcnow().isoformat(), "tf": args.tf, "symbols": symbols}, "tests": {}}

    # ═══ TEST 1: SL ATR multiples ═══
    print("\n─── TEST 1: SL ATR forex (TP scale pour RR≈2.5, partial 50% baseline) ───")
    print(f"  {'Variant':10s} {'Trades':>7s} {'WR':>6s} {'PnL$':>10s} {'PF':>6s} {'DD%':>6s} {'Cost$':>7s}")
    t1_results = {}
    for name, sl_t, tp_t, sl_r, tp_r in SL_TP_VARIANTS:
        apply_patches(sl_t, tp_t, sl_r, tp_r, 0.50, None)
        agg = {"n": 0, "pnl": 0.0, "wins": 0, "pf": 0.0, "dd": 0.0, "cost": 0.0}
        per_sym = {}
        for sym in symbols:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            r = run_symbol(sym, args.tf, df)
            if not r:
                continue
            m = r["metrics"]
            per_sym[sym] = m
            agg["n"] += m["n"]
            agg["pnl"] += m["total_pnl"]
            agg["wins"] += m["wins"]
            agg["pf"] += m["profit_factor"] if m["profit_factor"] != float("inf") else 0
            agg["dd"] = max(agg["dd"], m["max_drawdown_pct"])
            agg["cost"] += r.get("costs", {}).get("avg_total_cost_usd", 0) or 0
        n = max(agg["n"], 1)
        wr = agg["wins"] / n * 100
        pf = agg["pf"] / max(len(per_sym), 1)
        print(f"  {name:10s} {agg['n']:>7d} {wr:>5.1f}% {agg['pnl']:>10.2f} {pf:>6.2f} {agg['dd']:>6.1f} {agg['cost']/max(len(per_sym),1):>7.2f}")
        t1_results[name] = {"trades": agg["n"], "win_rate": round(wr, 1),
                            "pnl": round(agg["pnl"], 2), "profit_factor": round(pf, 2),
                            "max_dd": round(agg["dd"], 1), "per_symbol": per_sym}
    report["tests"]["1_sl_atr"] = t1_results

    # ═══ TEST 2: Partial TP fraction ═══
    print("\n─── TEST 2: Fraction partial TP (SL 2.0x baseline, session OFF) ───")
    print(f"  {'Variant':10s} {'Trades':>7s} {'WR':>6s} {'PnL$':>10s} {'PF':>6s} {'DD%':>6s}")
    t2_results = {}
    for name, frac in PARTIAL_VARIANTS:
        apply_patches(2.0, 5.0, 1.5, 4.0, frac, None)
        agg = {"n": 0, "pnl": 0.0, "wins": 0, "pf": 0.0, "dd": 0.0}
        per_sym = {}
        for sym in symbols:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            r = run_symbol(sym, args.tf, df)
            if not r:
                continue
            m = r["metrics"]
            per_sym[sym] = m
            agg["n"] += m["n"]
            agg["pnl"] += m["total_pnl"]
            agg["wins"] += m["wins"]
            agg["pf"] += m["profit_factor"] if m["profit_factor"] != float("inf") else 0
            agg["dd"] = max(agg["dd"], m["max_drawdown_pct"])
        n = max(agg["n"], 1)
        wr = agg["wins"] / n * 100
        pf = agg["pf"] / max(len(per_sym), 1)
        print(f"  {name:10s} {agg['n']:>7d} {wr:>5.1f}% {agg['pnl']:>10.2f} {pf:>6.2f} {agg['dd']:>6.1f}")
        t2_results[name] = {"trades": agg["n"], "win_rate": round(wr, 1),
                            "pnl": round(agg["pnl"], 2), "profit_factor": round(pf, 2),
                            "max_dd": round(agg["dd"], 1), "per_symbol": per_sym}
    report["tests"]["2_partial_tp"] = t2_results

    # ═══ TEST 3: Filtre de session ═══
    print("\n─── TEST 3: Filtre de session forex (SL 2.0x, partial 50%) ───")
    print(f"  {'Variant':12s} {'Trades':>7s} {'WR':>6s} {'PnL$':>10s} {'PF':>6s} {'DD%':>6s}")
    t3_results = {}
    for name, session in SESSION_VARIANTS:
        apply_patches(2.0, 5.0, 1.5, 4.0, 0.50, session)
        agg = {"n": 0, "pnl": 0.0, "wins": 0, "pf": 0.0, "dd": 0.0}
        per_sym = {}
        for sym in symbols:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            r = run_symbol(sym, args.tf, df)
            if not r:
                continue
            m = r["metrics"]
            per_sym[sym] = m
            agg["n"] += m["n"]
            agg["pnl"] += m["total_pnl"]
            agg["wins"] += m["wins"]
            agg["pf"] += m["profit_factor"] if m["profit_factor"] != float("inf") else 0
            agg["dd"] = max(agg["dd"], m["max_drawdown_pct"])
        n = max(agg["n"], 1)
        wr = agg["wins"] / n * 100
        pf = agg["pf"] / max(len(per_sym), 1)
        print(f"  {name:12s} {agg['n']:>7d} {wr:>5.1f}% {agg['pnl']:>10.2f} {pf:>6.2f} {agg['dd']:>6.1f}")
        t3_results[name] = {"trades": agg["n"], "win_rate": round(wr, 1),
                            "pnl": round(agg["pnl"], 2), "profit_factor": round(pf, 2),
                            "max_dd": round(agg["dd"], 1), "per_symbol": per_sym}
    report["tests"]["3_session"] = t3_results

    # ═══ TEST 4: Combinaison complète (SL 3.0x + PTP 75% + session LDN-NY) ═══
    print("\n─── TEST 4: Combo SL 3.0x + PTP 75% + session LDN-NY ───")
    print(f"  {'Variant':18s} {'Trades':>7s} {'WR':>6s} {'PnL$':>10s} {'PF':>6s} {'DD%':>6s}")
    combos = [
        ("SL2_PTP50_SESOFF", 2.0, 5.0, 1.5, 4.0, 0.50, None),
        ("SL3_PTP75_SESON", 3.0, 7.5, 2.25, 6.0, 0.75, (13, 17)),
        ("SL3_PTP50_SESON", 3.0, 7.5, 2.25, 6.0, 0.50, (13, 17)),
        ("SL3_PTP75_SESOFF", 3.0, 7.5, 2.25, 6.0, 0.75, None),
    ]
    t4_results = {}
    for name, sl_t, tp_t, sl_r, tp_r, frac, session in combos:
        apply_patches(sl_t, tp_t, sl_r, tp_r, frac, session)
        agg = {"n": 0, "pnl": 0.0, "wins": 0, "pf": 0.0, "dd": 0.0}
        per_sym = {}
        for sym in symbols:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            r = run_symbol(sym, args.tf, df)
            if not r:
                continue
            m = r["metrics"]
            per_sym[sym] = m
            agg["n"] += m["n"]
            agg["pnl"] += m["total_pnl"]
            agg["wins"] += m["wins"]
            agg["pf"] += m["profit_factor"] if m["profit_factor"] != float("inf") else 0
            agg["dd"] = max(agg["dd"], m["max_drawdown_pct"])
        n = max(agg["n"], 1)
        wr = agg["wins"] / n * 100
        pf = agg["pf"] / max(len(per_sym), 1)
        print(f"  {name:18s} {agg['n']:>7d} {wr:>5.1f}% {agg['pnl']:>10.2f} {pf:>6.2f} {agg['dd']:>6.1f}")
        t4_results[name] = {"trades": agg["n"], "win_rate": round(wr, 1),
                            "pnl": round(agg["pnl"], 2), "profit_factor": round(pf, 2),
                            "max_dd": round(agg["dd"], 1), "per_symbol": per_sym}
    report["tests"]["4_combo"] = t4_results

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Rapport sauvegardé: {out}")


if __name__ == "__main__":
    main()