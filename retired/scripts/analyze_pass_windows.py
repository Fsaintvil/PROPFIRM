#!/usr/bin/env python3
"""
Analyse des fenêtres 30j qui PASSENT le challenge FTMO.
1. Caractéristiques des fenêtres PASS vs FAIL
2. Conditions de marché gagnantes
3. Optimisation des paramètres MOM20x3
"""

import logging
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("analyze_pass")
logging.getLogger("backtest_core").setLevel(logging.WARNING)

OUTPUT_DIR = Path("backtest/results")
CAPITAL = 200_000
SYMBOLS = ["EURUSD", "USDCAD", "EURJPY", "GBPJPY", "XAUUSD", "BTCUSD"]


def load_all_trades():
    all_trades = []
    for sym in SYMBOLS:
        pkl_path = OUTPUT_DIR / f"bt_result_{sym}.pkl"
        if not pkl_path.exists():
            continue
        with open(pkl_path, "rb") as f:
            result = pickle.load(f)
        trades = [t for t in result.trades if hasattr(t, "closed") and t.closed]
        all_trades.extend(trades)
    all_trades.sort(key=lambda t: getattr(t, "close_time", None) or datetime.min)
    return all_trades


def analyze_passing_windows(trades):
    """Analyze characteristics of passing vs failing 30-day windows."""
    logger.info("=" * 70)
    logger.info("ANALYSE DES FENÊTRES 30 JOURS — PASS vs FAIL")
    logger.info("=" * 70)

    # Group by day
    daily_data = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "symbols": set()})
    for t in trades:
        ct = getattr(t, "close_time", None)
        if ct and hasattr(ct, "date"):
            day = ct.date()
            pnl = getattr(t, "profit_usd_cost", 0)
            daily_data[day]["pnl"] += pnl
            daily_data[day]["trades"] += 1
            daily_data[day]["symbols"].add(getattr(t, "symbol", "?"))
            if pnl > 0:
                daily_data[day]["wins"] += 1
            else:
                daily_data[day]["losses"] += 1

    sorted_days = sorted(daily_data.keys())

    # Build windows
    windows_data = []
    for i in range(len(sorted_days) - 20):
        window_end = sorted_days[i + 20]
        window_start = window_end - timedelta(days=30)

        w_trades = 0
        w_wins = 0
        w_losses = 0
        w_pnl = 0.0
        w_balance = CAPITAL
        w_peak = CAPITAL
        w_max_dd = 0.0
        w_max_dl = 0.0
        w_active_syms = set()
        w_daily_pnls = []

        for day in sorted_days:
            if day < window_start or day > window_end:
                continue
            dd = daily_data[day]
            w_trades += dd["trades"]
            w_wins += dd["wins"]
            w_losses += dd["losses"]
            w_pnl += dd["pnl"]
            w_balance += dd["pnl"]
            w_active_syms.update(dd["symbols"])
            w_daily_pnls.append(dd["pnl"])

            if w_balance > w_peak:
                w_peak = w_balance
            dd_pct = (w_peak - w_balance) / CAPITAL * 100
            if dd_pct > w_max_dd:
                w_max_dd = dd_pct
            if dd["pnl"] < 0:
                dl_pct = abs(dd["pnl"]) / CAPITAL * 100
                if dl_pct > w_max_dl:
                    w_max_dl = dl_pct

        wr = w_wins / max(w_trades, 1) * 100
        target = CAPITAL * 0.05
        passed = w_pnl >= target and w_max_dd <= 10 and w_max_dl <= 2

        windows_data.append(
            {
                "start": window_start,
                "end": window_end,
                "trades": w_trades,
                "wins": w_wins,
                "losses": w_losses,
                "wr": wr,
                "pnl": w_pnl,
                "max_dd": w_max_dd,
                "max_dl": w_max_dl,
                "active_symbols": len(w_active_syms),
                "passed": passed,
                "daily_pnls": w_daily_pnls,
            }
        )

    pass_windows = [w for w in windows_data if w["passed"]]
    fail_windows = [w for w in windows_data if not w["passed"]]

    logger.info(f"  Fenêtres PASS: {len(pass_windows)}")
    logger.info(f"  Fenêtres FAIL: {len(fail_windows)}")

    # Compare characteristics
    logger.info(f"\n  {'Caractéristique':<25} | {'PASS':>12} | {'FAIL':>12} | {'Ratio':>8}")
    logger.info(f"  {'-' * 25}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 8}")

    stats = [
        ("Trades/jour", lambda w: w["trades"] / max(1, (w["end"] - w["start"]).days) * 30, "trades/mois"),
        ("Trades total", lambda w: w["trades"], "trades"),
        ("Win Rate", lambda w: w["wr"], "%"),
        ("Symboles actifs", lambda w: w["active_symbols"], "n"),
        ("Max DD", lambda w: w["max_dd"], "%"),
        ("Max Daily Loss", lambda w: w["max_dl"], "%"),
        ("PnL", lambda w: w["pnl"], "$"),
    ]

    for name, func, unit in stats:
        pass_vals = [func(w) for w in pass_windows]
        fail_vals = [func(w) for w in fail_windows]
        pass_mean = np.mean(pass_vals)
        fail_mean = np.mean(fail_vals)
        ratio = pass_mean / max(fail_mean, 0.001)
        logger.info(f"  {name:<25} | {pass_mean:>9.1f}{unit:>3} | {fail_mean:>9.1f}{unit:>3} | {ratio:>7.2f}x")

    # Distribution temporelle
    logger.info(f"\n  Distribution temporelle des fenêtres PASS:")
    years = defaultdict(int)
    for w in pass_windows:
        years[w["start"].year] += 1
    for y in sorted(years.keys()):
        total_that_year = sum(1 for w in windows_data if w["start"].year == y)
        rate = years[y] / max(total_that_year, 1) * 100
        bar = "█" * int(rate / 5)
        logger.info(f"    {y}: {years[y]:>3}/{total_that_year:<4} ({rate:.0f}%) {bar}")

    # WR threshold analysis
    logger.info(f"\n  Impact du Win Rate sur le PASS:")
    wr_buckets = [(w, w["wr"]) for w in windows_data]
    for wr_thresh in [60, 65, 70, 75]:
        subset = [w for w in windows_data if w["wr"] >= wr_thresh]
        pass_subset = [w for w in subset if w["passed"]]
        rate = len(pass_subset) / max(len(subset), 1) * 100
        logger.info(f"    WR >= {wr_thresh}%: {len(pass_subset)}/{len(subset)} PASS ({rate:.1f}%)")

    # Trades per month analysis
    logger.info(f"\n  Impact du nombre de trades sur le PASS:")
    for t_thresh in [20, 30, 40, 50, 60]:
        subset = [w for w in windows_data if w["trades"] >= t_thresh]
        pass_subset = [w for w in subset if w["passed"]]
        rate = len(pass_subset) / max(len(subset), 1) * 100
        logger.info(f"    Trades >= {t_thresh}: {len(pass_subset)}/{len(subset)} PASS ({rate:.1f}%)")

    # Monthly profit distribution
    pnls_pass = [w["pnl"] for w in pass_windows]
    pnls_fail = [w["pnl"] for w in fail_windows]
    logger.info(f"\n  PnL moyen PASS: ${np.mean(pnls_pass):.2f}")
    logger.info(f"  PnL moyen FAIL: ${np.mean(pnls_fail):.2f}")
    logger.info(f"  PnL median PASS: ${np.median(pnls_pass):.2f}")
    logger.info(f"  PnL median FAIL: ${np.median(pnls_fail):.2f}")

    # What about PnL from failing windows (but close)?
    close_windows = [w for w in fail_windows if w["pnl"] > CAPITAL * 0.03]  # > 3% but < 5%
    logger.info(f"\n  Fenêtres 'presque' (PnL 3-5%): {len(close_windows)}")
    if close_windows:
        logger.info(f"    PnL moyen: ${np.mean([w['pnl'] for w in close_windows]):.2f}")
        logger.info(f"    Trades moyen: {np.mean([w['trades'] for w in close_windows]):.0f}")
        logger.info(f"    WR moyen: {np.mean([w['wr'] for w in close_windows]):.1f}%")

    return windows_data, pass_windows, fail_windows


def simulate_optimized_mom20x3(trades):
    """
    Simule l'impact de modifier les paramètres MOM20x3
    en scaling les lots et en ajustant la fréquence.
    """
    logger.info("\n" + "=" * 70)
    logger.info("SIMULATION D'OPTIMISATION MOM20x3")
    logger.info("=" * 70)

    # Current config: 0.44% risk, 6 symbols, H1
    # Strategies to test:
    scenarios = {
        "Actuel (0.44%, 6 sym, H1)": {"risk_mult": 1.0, "trade_mult": 1.0},
        "Risk ×1.5 (0.66%)": {"risk_mult": 1.5, "trade_mult": 1.0},
        "Risk ×2.0 (0.88%)": {"risk_mult": 2.0, "trade_mult": 1.0},
        "Risk ×1.5 + Trades ×1.3": {"risk_mult": 1.5, "trade_mult": 1.3},
        "Risk ×1.5 + Trades ×1.5": {"risk_mult": 1.5, "trade_mult": 1.5},
        "Risk ×2.0 + Trades ×1.5": {"risk_mult": 2.0, "trade_mult": 1.5},
    }

    # Group by day
    daily_pnl = defaultdict(float)
    daily_trades = defaultdict(int)
    for t in trades:
        ct = getattr(t, "close_time", None)
        if ct and hasattr(ct, "date"):
            day = ct.date()
            pnl = getattr(t, "profit_usd_cost", 0)
            daily_pnl[day] += pnl
            daily_trades[day] += 1

    sorted_days = sorted(daily_pnl.keys())

    logger.info(f"\n  {'Scénario':<35} | {'PnL/mois':>10} | {'PASS':>8} | {'DD':>6} | {'DL':>6}")
    logger.info(f"  {'-' * 35}-+-{'-' * 10}-+-{'-' * 8}-+-{'-' * 6}-+-{'-' * 6}")

    results = []
    for name, params in scenarios.items():
        risk_mult = params["risk_mult"]
        trade_mult = params["trade_mult"]

        passes = 0
        total_windows = 0
        all_pnl = []
        all_dd = []
        all_dl = []

        for i in range(len(sorted_days) - 20):
            window_end = sorted_days[i + 20]
            window_start = window_end - timedelta(days=30)

            w_balance = CAPITAL
            w_peak = CAPITAL
            w_max_dd = 0.0
            w_max_dl = 0.0
            w_pnl = 0.0
            w_trades = 0

            for day in sorted_days:
                if day < window_start or day > window_end:
                    continue

                base_pnl = daily_pnl[day]
                base_trades = daily_trades[day]

                # Apply multipliers
                scaled_pnl = base_pnl * risk_mult * trade_mult
                w_pnl += scaled_pnl
                w_trades += int(base_trades * trade_mult)

                w_balance += scaled_pnl
                if w_balance > w_peak:
                    w_peak = w_balance
                dd = (w_peak - w_balance) / CAPITAL * 100
                if dd > w_max_dd:
                    w_max_dd = dd

                if scaled_pnl < 0:
                    dl = abs(scaled_pnl) / CAPITAL * 100
                    if dl > w_max_dl:
                        w_max_dl = dl

            if w_pnl >= CAPITAL * 0.05 and w_max_dd <= 10 and w_max_dl <= 2:
                passes += 1
            total_windows += 1
            all_pnl.append(w_pnl)
            all_dd.append(w_max_dd)
            all_dl.append(w_max_dl)

        pass_rate = passes / max(total_windows, 1) * 100
        avg_pnl = np.mean(all_pnl)
        p95_dd = np.percentile(all_dd, 95)
        p95_dl = np.percentile(all_dl, 95)

        results.append(
            {
                "name": name,
                "pass_rate": pass_rate,
                "passes": passes,
                "total": total_windows,
                "avg_pnl": avg_pnl,
                "p95_dd": p95_dd,
                "p95_dl": p95_dl,
            }
        )

        logger.info(f"  {name:<35} | ${avg_pnl:>+8,.0f} | {pass_rate:>6.1f}% | {p95_dd:>5.1f} | {p95_dl:>5.1f}")

    # Summary
    logger.info(f"\n  {'-' * 70}")
    logger.info(f"  Meilleurs scénarios:")
    sorted_results = sorted(results, key=lambda r: r["pass_rate"], reverse=True)
    for r in sorted_results[:3]:
        logger.info(
            f"    🥇 {r['name']}: PASS {r['pass_rate']:.1f}%, PnL ${r['avg_pnl']:+.0f}, DD p95 {r['p95_dd']:.1f}%"
        )

    return results


def analyze_trade_characteristics(trades):
    """Analyze trade-level characteristics to find optimization levers."""
    logger.info("\n" + "=" * 70)
    logger.info("ANALYSE DES CARACTÉRISTIQUES DES TRADES")
    logger.info("=" * 70)

    # Per-symbol stats
    logger.info(f"\n  {'Symbole':<10} | {'Trades':>8} | {'WR':>6} | {'PnL/trade':>11} | {'RR':>6} | {'Lot moy':>8}")
    logger.info(f"  {'-' * 10}-+-{'-' * 8}-+-{'-' * 6}-+-{'-' * 11}-+-{'-' * 6}-+-{'-' * 8}")

    by_symbol = defaultdict(list)
    for t in trades:
        by_symbol[getattr(t, "symbol", "?")].append(t)

    for sym in sorted(by_symbol.keys()):
        sym_trades = by_symbol[sym]
        n = len(sym_trades)
        wins = sum(1 for t in sym_trades if getattr(t, "profit_usd_cost", 0) > 0)
        wr = wins / n * 100
        avg_pnl = np.mean([getattr(t, "profit_usd_cost", 0) for t in sym_trades])
        avg_lot = np.mean([getattr(t, "lot", 0) for t in sym_trades])

        # Estimate RR
        avg_win = (
            np.mean([getattr(t, "profit_usd_cost", 0) for t in sym_trades if getattr(t, "profit_usd_cost", 0) > 0]) or 1
        )
        avg_loss = (
            abs(
                np.mean([getattr(t, "profit_usd_cost", 0) for t in sym_trades if getattr(t, "profit_usd_cost", 0) <= 0])
            )
            or 1
        )
        rr = avg_win / max(avg_loss, 0.01)

        logger.info(f"  {sym:<10} | {n:>8} | {wr:>5.1f}% | ${avg_pnl:>+9.2f} | {rr:>5.2f} | {avg_lot:>7.4f}")

    # Average trade frequency
    dates = sorted(
        set(t.close_time.date() for t in trades if hasattr(t, "close_time") and hasattr(t.close_time, "date"))
    )

    if len(dates) >= 2:
        total_days = (dates[-1] - dates[0]).days
        trades_per_day = len(trades) / max(total_days, 1)
        logger.info(f"\n  Fréquence: {trades_per_day:.2f} trades/jour ({len(trades)} trades sur {total_days} jours)")
        logger.info(f"  Trades par mois: {trades_per_day * 30:.1f}")

    # Win rate vs PnL distribution
    pnls = [getattr(t, "profit_usd_cost", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    if wins and losses:
        logger.info(f"\n  Distribution des PnL:")
        logger.info(f"    Win moyen: ${np.mean(wins):.2f}")
        logger.info(f"    Win median: ${np.median(wins):.2f}")
        logger.info(f"    Loss moyen: ${np.mean(losses):.2f}")
        logger.info(f"    Loss median: ${np.median(losses):.2f}")
        logger.info(f"    Plus grand win: ${max(wins):.2f}")
        logger.info(f"    Plus grande loss: ${min(losses):.2f}")

    # Monthly profit variability
    monthly_pnl = defaultdict(float)
    for t in trades:
        ct = getattr(t, "close_time", None)
        if ct and hasattr(ct, "month"):
            key = (ct.year, ct.month)
            monthly_pnl[key] += getattr(t, "profit_usd_cost", 0)

    monthly_values = list(monthly_pnl.values())
    if monthly_values:
        logger.info(f"\n  PnL mensuel (sur {len(monthly_values)} mois):")
        logger.info(f"    Moyen: ${np.mean(monthly_values):.2f}")
        logger.info(f"    Median: ${np.median(monthly_values):.2f}")
        logger.info(f"    Écart-type: ${np.std(monthly_values):.2f}")
        logger.info(f"    Min: ${min(monthly_values):.2f}")
        logger.info(f"    Max: ${max(monthly_values):.2f}")
        pct_positive = sum(1 for v in monthly_values if v > 0) / len(monthly_values) * 100
        logger.info(f"    Mois positifs: {pct_positive:.1f}%")


def rolling_ftmo_with_extra_symbols(trades, extra_pairs=None):
    """Simulate what adding more forex pairs would do."""
    logger.info("\n" + "=" * 70)
    logger.info("SIMULATION AJOUT DE SYMBOLES SUPPLÉMENTAIRES")
    logger.info("=" * 70)

    # Current daily PnL from our 6 symbols
    daily_pnl_6 = defaultdict(float)
    for t in trades:
        ct = getattr(t, "close_time", None)
        if ct and hasattr(ct, "date"):
            daily_pnl_6[ct.date()] += getattr(t, "profit_usd_cost", 0)

    sorted_days = sorted(daily_pnl_6.keys())

    if extra_pairs is None:
        extra_pairs = ["AUDUSD", "GBPUSD", "USDCHF", "NZDUSD"]

    # Estimate extra PnL: assume similar WR and PnL per trade as existing forex
    # EURUSD gives ~$17.68/trade, USDCAD gives ~$14.09/trade
    # Average forex pair: ~$16/trade with ~70% WR

    # Assume each extra symbol adds ~30% of EURUSD volume (since they're correlated)
    # This is a rough estimate
    forex_avg_pnl = 16.0  # per trade
    forex_trades_per_day = 0.6  # trades/day/symbol from our data

    logger.info(f"  Estimation basée sur les paires forex existantes:")
    logger.info(f"  PnL moyen par trade forex: ${forex_avg_pnl:.2f}")
    logger.info(f"  Trades/jour/symbole: {forex_trades_per_day:.2f}")
    logger.info(f"")

    for n_extra in [2, 4, 6, 8]:
        extra_trades_per_day = n_extra * forex_trades_per_day
        extra_pnl_per_day = extra_trades_per_day * forex_avg_pnl

        passes = 0
        total = 0

        for i in range(len(sorted_days) - 20):
            window_end = sorted_days[i + 20]
            window_start = window_end - timedelta(days=30)

            w_balance = CAPITAL
            w_peak = CAPITAL
            w_max_dd = 0.0
            w_max_dl = 0.0
            w_pnl = 0.0

            for day in sorted_days:
                if day < window_start or day > window_end:
                    continue
                pnl = daily_pnl_6[day] + extra_pnl_per_day
                w_pnl += pnl
                w_balance += pnl
                if w_balance > w_peak:
                    w_peak = w_balance
                dd = (w_peak - w_balance) / CAPITAL * 100
                if dd > w_max_dd:
                    w_max_dd = dd
                if pnl < 0:
                    dl = abs(pnl) / CAPITAL * 100
                    if dl > w_max_dl:
                        w_max_dl = dl

            if w_pnl >= CAPITAL * 0.05 and w_max_dd <= 10 and w_max_dl <= 2:
                passes += 1
            total += 1

        rate = passes / max(total, 1) * 100
        avg_pnl_month = (
            extra_pnl_per_day * 30
            + np.mean(
                [
                    daily_pnl_6[d]
                    for d in sorted_days
                    if d >= sorted_days[0] and d <= sorted_days[min(29, len(sorted_days) - 1)]
                ]
            )
            * 30
        )
        logger.info(f"  +{n_extra:<2} symboles: {rate:>5.1f}% PASS | +${extra_pnl_per_day * 30:.0f}/mois estimé")

    return


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Chargement des trades...")
    trades = load_all_trades()
    logger.info(f"  {len(trades)} trades chargés")

    # 1. Analyze trade characteristics
    analyze_trade_characteristics(trades)

    # 2. Analyze passing windows
    windows_data, pass_windows, fail_windows = analyze_passing_windows(trades)

    # 3. Simulate MOM20x3 optimization
    opt_results = simulate_optimized_mom20x3(trades)

    # 4. Extra symbols simulation
    rolling_ftmo_with_extra_symbols(trades)

    logger.info("\n" + "=" * 70)
    logger.info("RÉSUMÉ DES PISTES D'OPTIMISATION")
    logger.info("=" * 70)
    logger.info(f"  1. Augmenter la taille des positions (risk_mult ×1.5-2.0)")
    logger.info(f"  2. Augmenter le nombre de trades (plus de symboles)")
    logger.info(f"  3. Combiner les deux pour maximiser le PASS rate")
    logger.info(f"  4. Réduire les seuils MOM20x3 pour plus de signaux")
    logger.info("=" * 70)
    logger.info("✅ Analyse terminée")
