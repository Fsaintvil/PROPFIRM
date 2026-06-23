#!/usr/bin/env python3
"""
Analyse complète du portefeuille FTMO 6 symboles.
1. Périodes de DD > 10%
2. Fenêtres glissantes 30 jours (FTMO réaliste)
3. Optimisation risk_per_trade
4. Circuit breaker daily loss
5. Analyse daily loss 2.05%
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

from engine_simple.backtest_core.ftmo import FTMOChallengeSimulator, FTMOConfig, FTMOPortfolioSimulator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("analyze")
logging.getLogger("backtest_core").setLevel(logging.WARNING)

OUTPUT_DIR = Path("backtest/results")
CAPITAL = 200_000
SYMBOLS = ["EURUSD", "USDCAD", "EURJPY", "GBPJPY", "XAUUSD", "BTCUSD"]

# ─── 1. Charger tous les trades ──────────────────────────────────────────


def load_all_trades():
    """Load all trades from pickle files, return chronologically sorted list."""
    all_trades = []
    for sym in SYMBOLS:
        pkl_path = OUTPUT_DIR / f"bt_result_{sym}.pkl"
        if not pkl_path.exists():
            logger.warning(f"Pickle introuvable: {pkl_path}")
            continue
        with open(pkl_path, "rb") as f:
            result = pickle.load(f)
        trades = [t for t in result.trades if hasattr(t, "closed") and t.closed]
        all_trades.extend(trades)
        pnl = sum(getattr(t, "profit_usd_cost", 0) for t in trades)
        logger.info(f"  {sym}: {len(trades)} trades, PnL ${pnl:+.2f}")

    # Sort chronologically
    all_trades.sort(key=lambda t: getattr(t, "close_time", None) or datetime.min)
    logger.info(f"\nTotal: {len(all_trades)} trades combinés")
    return all_trades


# ─── 2. Analyser DD > 10% ───────────────────────────────────────────────


def analyze_dd_periods(trades):
    """Identify periods where DD exceeded 10%."""
    logger.info("\n" + "=" * 70)
    logger.info("ANALYSE DES PÉRIODES DE DD > 10%")
    logger.info("=" * 70)

    running = CAPITAL
    peak = CAPITAL
    max_dd = 0.0
    dd_periods = []
    in_dd = False
    dd_start = None
    dd_peak_val = CAPITAL

    for t in trades:
        pnl = getattr(t, "profit_usd_cost", 0)
        close_time = getattr(t, "close_time", None)
        running += pnl

        if running > peak:
            peak = running
            if in_dd:
                # End of DD period
                dd_pct = (dd_peak_val - running) / CAPITAL * 100
                dd_periods.append(
                    {
                        "start": dd_start,
                        "end": close_time,
                        "peak_val": dd_peak_val,
                        "nadir_val": running,
                        "dd_pct": round(dd_pct, 2),
                        "recovered": True,
                    }
                )
                in_dd = False

        dd = (peak - running) / CAPITAL * 100
        if dd > max_dd:
            max_dd = dd

        if dd > 10 and not in_dd:
            in_dd = True
            dd_start = close_time
            dd_peak_val = peak
        elif dd > 10 and in_dd:
            # Update the worst point
            if running < dd_peak_val:
                dd_peak_val = peak  # Keep the peak
            pass

    # If still in DD at the end
    if in_dd:
        dd_pct = (dd_peak_val - running) / CAPITAL * 100
        dd_periods.append(
            {
                "start": dd_start,
                "end": trades[-1].close_time if trades else None,
                "peak_val": dd_peak_val,
                "nadir_val": running,
                "dd_pct": round(dd_pct, 2),
                "recovered": False,
            }
        )

    logger.info(f"  Max DD global: {max_dd:.2f}%")
    logger.info(f"  Périodes de DD > 10%: {len(dd_periods)}")

    for i, p in enumerate(dd_periods):
        severity = "🔴" if p["dd_pct"] > 15 else "🟠"
        start_str = str(p["start"].date()) if p["start"] and hasattr(p["start"], "date") else str(p["start"])
        end_str = str(p["end"].date()) if p["end"] and hasattr(p["end"], "date") else str(p["end"])
        logger.info(
            f"  {severity} #{i + 1}: {start_str} → {end_str} | DD {p['dd_pct']:.2f}% | Peak=${p['peak_val']:.0f}"
        )

    return dd_periods


# ─── 3. Fenêtres glissantes 30 jours ────────────────────────────────────


def rolling_30d_ftmo(trades):
    """Simulate FTMO challenge on every rolling 30-day window."""
    logger.info("\n" + "=" * 70)
    logger.info("SIMULATION FTMO — FENÊTRES GLISSANTES 30 JOURS")
    logger.info("=" * 70)

    # Group trades by day
    daily_pnl = defaultdict(float)
    for t in trades:
        ct = getattr(t, "close_time", None)
        if ct and hasattr(ct, "date"):
            day = ct.date()
            daily_pnl[day] += getattr(t, "profit_usd_cost", 0)

    sorted_days = sorted(daily_pnl.keys())
    if len(sorted_days) < 30:
        logger.warning("Pas assez de jours pour fenêtre glissante")
        return

    ftmo_config = FTMOConfig(account_size=CAPITAL)
    results = []

    for i in range(len(sorted_days) - 20):  # Min 20 trading days
        window_end = sorted_days[i + 20]
        window_start = window_end - timedelta(days=30)

        # Get trades in this 30-day window
        window_pnl = 0.0
        window_peak = CAPITAL
        window_balance = CAPITAL
        window_max_dd = 0.0
        window_max_daily_loss = 0.0
        window_days = 0
        window_profit_target = CAPITAL * 0.05  # $10K

        for day in sorted_days:
            if day < window_start or day > window_end:
                continue
            day_pnl_val = daily_pnl[day]
            window_pnl += day_pnl_val
            window_days += 1

            # Daily loss check
            if day_pnl_val < 0:
                day_loss_pct = abs(day_pnl_val) / CAPITAL * 100
                if day_loss_pct > window_max_daily_loss:
                    window_max_daily_loss = day_loss_pct

            # Running balance + DD
            window_balance += day_pnl_val
            if window_balance > window_peak:
                window_peak = window_balance
            dd = (window_peak - window_balance) / CAPITAL * 100
            if dd > window_max_dd:
                window_max_dd = dd

        # Check FTMO rules
        passed = True
        fail_reason = ""
        if window_pnl < window_profit_target:
            passed = False
            fail_reason = "profit_target"
        if window_max_dd > 10:
            passed = False
            fail_reason = "max_dd"
        if window_max_daily_loss > 2:
            passed = False
            fail_reason = "daily_loss"

        results.append(
            {
                "window_start": window_start,
                "window_end": window_end,
                "trading_days": window_days,
                "pnl": window_pnl,
                "max_dd": window_max_dd,
                "max_daily_loss": window_max_daily_loss,
                "passed": passed,
                "fail_reason": fail_reason,
            }
        )

    # Stats
    passes = sum(1 for r in results if r["passed"])
    total_windows = len(results)
    pass_rate = passes / total_windows * 100 if total_windows > 0 else 0

    # Find which rules cause failure
    dd_fails = sum(1 for r in results if r["fail_reason"] == "max_dd")
    dl_fails = sum(1 for r in results if r["fail_reason"] == "daily_loss")
    pt_fails = sum(1 for r in results if r["fail_reason"] == "profit_target")

    # Best and worst windows
    best = max(results, key=lambda r: r["pnl"])
    worst = min(results, key=lambda r: r["max_dd"])

    logger.info(f"  Fenêtres analysées: {total_windows}")
    logger.info(f"  Taux de PASS: {pass_rate:.1f}% ({passes}/{total_windows})")
    logger.info(f"  Échecs par DD: {dd_fails} ({dd_fails / total_windows * 100:.1f}%)")
    logger.info(f"  Échecs par Daily Loss: {dl_fails} ({dl_fails / total_windows * 100:.1f}%)")
    logger.info(f"  Échecs par Profit Target: {pt_fails} ({pt_fails / total_windows * 100:.1f}%)")

    # Recent 2024-2025 analysis
    recent_results = [r for r in results if r["window_start"].year >= 2024]
    if recent_results:
        recent_passes = sum(1 for r in recent_results if r["passed"])
        logger.info(
            f"\n  2024-2025: {recent_passes}/{len(recent_results)} PASS ({recent_passes / len(recent_results) * 100:.1f}%)"
        )
        avg_dd = np.mean([r["max_dd"] for r in recent_results])
        avg_pnl = np.mean([r["pnl"] for r in recent_results])
        logger.info(f"  DD moyen: {avg_dd:.2f}% | PnL moyen: ${avg_pnl:.2f}")

    # Return stats for optimization
    return {
        "pass_rate": pass_rate,
        "total_windows": total_windows,
        "passes": passes,
        "dd_fails": dd_fails,
        "dl_fails": dl_fails,
        "pt_fails": pt_fails,
        "avg_dd": np.mean([r["max_dd"] for r in results]),
        "p95_dd": np.percentile([r["max_dd"] for r in results], 95),
        "max_dd_all": max(r["max_dd"] for r in results),
        "avg_daily_loss": np.mean([r["max_daily_loss"] for r in results]),
        "p95_daily_loss": np.percentile([r["max_daily_loss"] for r in results], 95),
    }


# ─── 4. Optimisation risk_per_trade ─────────────────────────────────────


def optimize_risk(trades, risk_levels=None):
    """Test different risk_per_trade levels for FTMO compliance."""
    logger.info("\n" + "=" * 70)
    logger.info("OPTIMISATION RISK PER TRADE")
    logger.info("=" * 70)

    if risk_levels is None:
        risk_levels = [0.0020, 0.0025, 0.0030, 0.0035, 0.0040, 0.0044, 0.0050, 0.0060]

    # Extract raw PnL per trade (before risk scaling)
    raw_pnls = []
    for t in trades:
        pnl = getattr(t, "profit_usd_cost", 0)
        lot = getattr(t, "lot", 1.0)
        # Normalize by lot size to get "unit PnL"
        raw_pnls.append(
            {
                "pnl": pnl,
                "lot": lot,
                "close_time": getattr(t, "close_time", None),
            }
        )

    logger.info(f"  Analyse sur {len(raw_pnls)} trades")
    logger.info(f"\n  {'Risk':>8} | {'PnL':>12} | {'DD max':>8} | {'DL max':>8} | {'30j PASS':>10}")
    logger.info(f"  {'-' * 8}-+-{'-' * 12}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 10}")

    results = []
    for risk in risk_levels:
        scaling = risk / 0.0044  # Scale relative to current 0.44%

        # Apply scaling to all trades
        daily_pnl = defaultdict(float)
        for r in raw_pnls:
            scaled_pnl = r["pnl"] * scaling
            ct = r["close_time"]
            if ct and hasattr(ct, "date"):
                daily_pnl[ct.date()] += scaled_pnl

        sorted_days = sorted(daily_pnl.keys())

        # Full-period metrics
        running = CAPITAL
        peak = CAPITAL
        max_dd = 0.0
        max_daily_loss = 0.0
        total_pnl = sum(daily_pnl.values())

        for day in sorted_days:
            day_pnl = daily_pnl[day]
            running += day_pnl
            if running > peak:
                peak = running
            dd = (peak - running) / CAPITAL * 100
            if dd > max_dd:
                max_dd = dd
            if day_pnl < 0:
                dl = abs(day_pnl) / CAPITAL * 100
                if dl > max_daily_loss:
                    max_daily_loss = dl

        # Rolling 30-day pass rate
        if len(sorted_days) >= 20:
            passes_30 = 0
            total_30 = 0
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
                    dp = daily_pnl[day]
                    w_pnl += dp
                    w_balance += dp
                    if w_balance > w_peak:
                        w_peak = w_balance
                    w_dd = (w_peak - w_balance) / CAPITAL * 100
                    if w_dd > w_max_dd:
                        w_max_dd = w_dd
                    if dp < 0:
                        w_dl = abs(dp) / CAPITAL * 100
                        if w_dl > w_max_dl:
                            w_max_dl = w_dl

                if w_pnl >= CAPITAL * 0.05 and w_max_dd <= 10 and w_max_dl <= 2:
                    passes_30 += 1
                total_30 += 1

            pass_rate_30 = passes_30 / total_30 * 100 if total_30 > 0 else 0
        else:
            pass_rate_30 = 0.0

        results.append(
            {
                "risk": risk,
                "risk_pct": risk * 100,
                "total_pnl": total_pnl,
                "max_dd": max_dd,
                "max_daily_loss": max_daily_loss,
                "pass_rate_30": pass_rate_30,
            }
        )

        logger.info(
            f"  {risk * 100:>6.2f}% | ${total_pnl:>+10,.0f} | {max_dd:>7.2f}% | {max_daily_loss:>7.2f}% | {pass_rate_30:>8.1f}%"
        )

    # Find best risk level (max PnL where DD < 10% AND daily loss < 2%)
    valid = [r for r in results if r["max_dd"] <= 10 and r["max_daily_loss"] <= 2]
    if valid:
        best = max(valid, key=lambda r: r["total_pnl"])
        logger.info(f"\n  ✅ Meilleur risk: {best['risk_pct']:.2f}%")
        logger.info(f"     PnL: ${best['total_pnl']:+,.0f}")
        logger.info(f"     DD: {best['max_dd']:.2f}%")
        logger.info(f"     Daily Loss: {best['max_daily_loss']:.2f}%")
        logger.info(f"     PASS rate 30j: {best['pass_rate_30']:.1f}%")
    else:
        logger.info("\n  ❌ Aucun niveau de risk ne satisfait DD < 10% ET Daily Loss < 2%")
        # Find closest
        best = max(results, key=lambda r: r["total_pnl"])
        logger.info(
            f"  Meilleur candidat: {best['risk_pct']:.2f}% (DD {best['max_dd']:.2f}%, DL {best['max_daily_loss']:.2f}%)"
        )

    return results


# ─── 5. Analyse daily loss 2025 ──────────────────────────────────────────


def analyze_daily_loss_2025(trades):
    """Analyze daily loss in 2025 specifically."""
    logger.info("\n" + "=" * 70)
    logger.info("ANALYSE DAILY LOSS 2025")
    logger.info("=" * 70)

    # Filter 2025 trades
    trades_2025 = [
        t
        for t in trades
        if getattr(t, "close_time", None) and hasattr(t.close_time, "year") and t.close_time.year == 2025
    ]

    if not trades_2025:
        logger.warning("Pas de trades en 2025")
        return

    # Group by day
    daily_pnl = defaultdict(float)
    daily_trades = defaultdict(int)
    for t in trades_2025:
        day = t.close_time.date()
        daily_pnl[day] += getattr(t, "profit_usd_cost", 0)
        daily_trades[day] += 1

    logger.info(f"  Trades 2025: {len(trades_2025)} sur {len(daily_pnl)} jours")

    # Find worst days
    worst_days = sorted(daily_pnl.items(), key=lambda x: x[1])[:10]

    logger.info(f"\n  Pire 10 jours (2025):")
    logger.info(f"  {'Date':>12} | {'PnL':>12} | {'% Capital':>10} | {'Trades':>7}")
    logger.info(f"  {'-' * 12}-+-{'-' * 12}-+-{'-' * 10}-+-{'-' * 7}")
    for day, pnl in worst_days:
        pct = abs(pnl) / CAPITAL * 100
        n = daily_trades[day]
        logger.info(f"  {str(day):>12} | ${pnl:>+9.2f} | {pct:>8.2f}% | {n:>5}")

    # Daily loss > 2% days
    bad_days = [(d, p) for d, p in daily_pnl.items() if p < 0 and abs(p) / CAPITAL * 100 > 1.5]
    logger.info(f"\n  Jours avec perte > 1.5% du capital: {len(bad_days)}")
    for d, p in bad_days:
        pct = abs(p) / CAPITAL * 100
        logger.info(f"    {d}: ${p:+.2f} ({pct:.2f}%)")

    logger.info(f"\n  Stats daily loss 2025:")
    losses = [abs(p) / CAPITAL * 100 for d, p in daily_pnl.items() if p < 0]
    if losses:
        logger.info(f"    Moyenne: {np.mean(losses):.2f}%")
        logger.info(f"    Médiane: {np.median(losses):.2f}%")
        logger.info(f"    P95: {np.percentile(losses, 95):.2f}%")
        logger.info(f"    Max: {max(losses):.2f}%")
        logger.info(f"    N jours négatifs: {len(losses)}/{len(daily_pnl)} ({len(losses) / len(daily_pnl) * 100:.1f}%)")

    # Also analyze 2024
    trades_2024 = [
        t
        for t in trades
        if getattr(t, "close_time", None) and hasattr(t.close_time, "year") and t.close_time.year == 2024
    ]
    if trades_2024:
        daily_pnl_2024 = defaultdict(float)
        for t in trades_2024:
            day = t.close_time.date()
            daily_pnl_2024[day] += getattr(t, "profit_usd_cost", 0)
        losses_2024 = [abs(p) / CAPITAL * 100 for d, p in daily_pnl_2024.items() if p < 0]
        logger.info(f"\n  Stats daily loss 2024 ({len(trades_2024)} trades):")
        if losses_2024:
            logger.info(f"    Max: {max(losses_2024):.2f}%")
            logger.info(f"    P95: {np.percentile(losses_2024, 95):.2f}%")
            logger.info(f"    Moyenne: {np.mean(losses_2024):.2f}%")


# ─── 6. Circuit breaker simulation ──────────────────────────────────────


def simulate_circuit_breaker(trades, threshold=0.018):
    """Simulate what would happen with a daily loss circuit breaker at threshold."""
    logger.info("\n" + "=" * 70)
    logger.info(f"SIMULATION CIRCUIT BREAKER (stop à {threshold * 100:.1f}% perte quotidienne)")
    logger.info("=" * 70)

    # Group all trades by day
    trades_by_day = defaultdict(list)
    for t in trades:
        ct = getattr(t, "close_time", None)
        if ct and hasattr(ct, "date"):
            trades_by_day[ct.date()].append(t)

    # Simulate without CB
    running_no_cb = CAPITAL
    peak_no_cb = CAPITAL
    max_dd_no_cb = 0.0

    # With CB: stop trading for the day after threshold reached
    running_cb = CAPITAL
    peak_cb = CAPITAL
    max_dd_cb = 0.0
    stopped_days = 0

    sorted_days = sorted(trades_by_day.keys())

    for day in sorted_days:
        day_trades = trades_by_day[day]

        # Without CB: all trades execute
        for t in day_trades:
            pnl = getattr(t, "profit_usd_cost", 0)
            running_no_cb += pnl
            if running_no_cb > peak_no_cb:
                peak_no_cb = running_no_cb
            dd = (peak_no_cb - running_no_cb) / CAPITAL * 100
            if dd > max_dd_no_cb:
                max_dd_no_cb = dd

        # With CB: stop after threshold reached
        day_pnl_cb = 0.0
        for t in day_trades:
            pnl = getattr(t, "profit_usd_cost", 0)
            if day_pnl_cb < 0 and abs(day_pnl_cb + pnl) / CAPITAL > threshold:
                # This trade would push beyond threshold — skip it
                stopped_days += 1
                break
            day_pnl_cb += pnl
            running_cb += pnl
            if running_cb > peak_cb:
                peak_cb = running_cb
            dd = (peak_cb - running_cb) / CAPITAL * 100
            if dd > max_dd_cb:
                max_dd_cb = dd

    logger.info(f"  {'':>20} | {'Sans CB':>10} | {'Avec CB':>10}")
    logger.info(f"  {'-' * 20}-+-{'-' * 10}-+-{'-' * 10}")
    logger.info(f"  {'Max DD':>20} | {max_dd_no_cb:>9.2f}% | {max_dd_cb:>9.2f}%")
    logger.info(f"  {'PnL final':>20} | ${running_no_cb - CAPITAL:>+8,.0f} | ${running_cb - CAPITAL:>+8,.0f}")
    logger.info(f"  {'Jours stoppés':>20} | {'N/A':>10} | {stopped_days:>9}")

    # Also test with 1.5% threshold
    threshold2 = 0.015
    running_cb2 = CAPITAL
    peak_cb2 = CAPITAL
    max_dd_cb2 = 0.0
    stopped_days2 = 0

    for day in sorted_days:
        day_trades = trades_by_day[day]
        day_pnl_cb = 0.0
        for t in day_trades:
            pnl = getattr(t, "profit_usd_cost", 0)
            if day_pnl_cb < 0 and abs(day_pnl_cb + pnl) / CAPITAL > threshold2:
                stopped_days2 += 1
                break
            day_pnl_cb += pnl
            running_cb2 += pnl
            if running_cb2 > peak_cb2:
                peak_cb2 = running_cb2
            dd = (peak_cb2 - running_cb2) / CAPITAL * 100
            if dd > max_dd_cb2:
                max_dd_cb2 = dd

    logger.info(f"  {'Avec CB 1.5%':>20} | {'':>10} | {max_dd_cb2:>9.2f}%")
    logger.info(f"  {'PnL final':>20} | {'':>10} | ${running_cb2 - CAPITAL:>+8,.0f}")
    logger.info(f"  {'Jours stoppés':>20} | {'':>10} | {stopped_days2:>9}")

    return {
        "no_cb": {"max_dd": max_dd_no_cb, "pnl": running_no_cb - CAPITAL},
        "cb_1_8": {"max_dd": max_dd_cb, "pnl": running_cb - CAPITAL, "stopped": stopped_days},
        "cb_1_5": {"max_dd": max_dd_cb2, "pnl": running_cb2 - CAPITAL, "stopped": stopped_days2},
    }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Chargement des trades...")
    trades = load_all_trades()

    if not trades:
        logger.error("Aucun trade chargé. Lancez d'abord les backtests.")
        sys.exit(1)

    # 1. DD periods
    dd_periods = analyze_dd_periods(trades)

    # 2. Rolling 30-day windows
    rolling_stats = rolling_30d_ftmo(trades)

    # 3. Risk optimization
    opt_results = optimize_risk(trades)

    # 4. Circuit breaker
    cb_results = simulate_circuit_breaker(trades)

    # 5. Daily loss 2025
    analyze_daily_loss_2025(trades)

    logger.info("\n" + "=" * 70)
    logger.info("RÉSUMÉ EXÉCUTIF")
    logger.info("=" * 70)
    logger.info(f"  Périodes DD > 10%: {len(dd_periods)}")
    logger.info(f"  Rolling 30j PASS rate: {rolling_stats['pass_rate']:.1f}%")
    logger.info(f"  DD 95e percentile (30j): {rolling_stats['p95_dd']:.2f}%")
    logger.info(f"  Daily Loss 95e percentile: {rolling_stats['p95_daily_loss']:.2f}%")
    logger.info(f"  Circuit breaker 1.8%: DD {cb_results['cb_1_8']['max_dd']:.2f}%")
    logger.info("=" * 70)
    logger.info("✅ Analyse terminée")
