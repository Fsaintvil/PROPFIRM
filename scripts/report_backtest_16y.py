"""
Rapport détaillé du backtest MOM20x3 16 ans — 5 symboles actifs.
Analyse par symbole, par timeframe, par année, par régime.

Lit les données de runtime/backtest_16y_full.pkl et runtime/backtest_16y_metrics.json.

Usage:
    python scripts/report_backtest_16y.py
    python scripts/report_backtest_16y.py --summary
    python scripts/report_backtest_16y.py --symbol XAUUSD
    python scripts/report_backtest_16y.py --export
"""
import os
import sys
import json
import pickle
from collections import defaultdict
from pathlib import Path
from math import sqrt, erf

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ============================================================================
# METRICS COMPUTATION
# ============================================================================
def compute_metrics(trades):
    """Compute comprehensive metrics from a list of trade dicts."""
    if not trades:
        return None

    n = len(trades)
    wins = [t for t in trades if t.get("profit_usd", 0) > 0]
    losses = [t for t in trades if t.get("profit_usd", 0) <= 0]
    n_wins = len(wins)
    wr = n_wins / n * 100 if n > 0 else 0

    total_pnl = sum(t.get("profit_usd", 0) for t in trades)
    gross_profit = sum(max(0, t.get("profit_usd", 0)) for t in trades)
    gross_loss = abs(sum(min(0, t.get("profit_usd", 0)) for t in trades))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0)

    # Drawdown
    peak = 200_000.0
    dd_max = 0.0
    balance = 200_000.0
    for t in sorted(trades, key=lambda x: x.get("open_time", "")):
        balance += t.get("profit_usd", 0)
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)

    # Avg win/loss
    avg_win = np.mean([t.get("profit_usd", 0) for t in wins]) if wins else 0
    avg_loss = np.mean([t.get("profit_usd", 0) for t in losses]) if losses else 0

    # Consecutive
    max_consec_wins = 0
    max_consec_losses = 0
    curr_wins = 0
    curr_losses = 0
    for t in trades:
        if t.get("profit_usd", 0) > 0:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)
        else:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)

    # Partial TP
    partial_tp = sum(1 for t in trades if t.get("partial_tp", False))

    # Statistical significance
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    else:
        p = 1.0

    return {
        "n": n, "wins": n_wins, "losses": n - n_wins,
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "partial_tp_count": partial_tp,
        "p_value": round(p, 4),
        "significant": p < 0.05,
    }


# ============================================================================
# REPORT GENERATION
# ============================================================================
def generate_report(all_trades, summary_only=False, symbol_filter=None, export=False):
    """Generate comprehensive backtest report."""
    print("=" * 100)
    print("  RAPPORT BACKTEST MOM20x3 — 16 ANS — 5 SYMBOLES ACTIFS")
    print("  Trailing ATR multi-niveaux + Partial TP à 60% + BE Buffer")
    print("=" * 100)

    # ── 1. Summary by symbol × TF ──
    print("\n" + "─" * 100)
    print("  1. RÉSUMÉ PAR SYMOLE × TIMEFRAME")
    print("─" * 100)
    print(f"  {'Symbol_TF':16s} {'Trades':>7s} {'WR':>7s} {'PnL':>12s} {'PF':>6s} "
          f"{'DD Max':>7s} {'Avg Win':>10s} {'Avg Loss':>10s} {'Sig':>5s}")
    print(f"  {'-'*85}")

    symbol_agg = defaultdict(list)
    tf_agg = defaultdict(list)
    all_closed = []

    for key, trades in sorted(all_trades.items()):
        if not trades:
            continue
        if symbol_filter and not key.startswith(symbol_filter):
            continue

        m = compute_metrics(trades)
        if m is None:
            continue

        print(f"  {key:16s} {m['n']:>7d} {m['win_rate']:>6.1f}% ${m['total_pnl']:>+11.2f} "
              f"{m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>6.1f}% "
              f"${m['avg_win']:>+9.2f} ${m['avg_loss']:>+9.2f} "
              f"{'  ✓' if m['significant'] else '  ✗'}")

        parts = key.split("_")
        symbol = parts[0]
        tf = parts[-1]
        symbol_agg[symbol].extend(trades)
        tf_agg[tf].extend(trades)
        all_closed.extend(trades)

    # ── 2. Summary by symbol ──
    print(f"\n{'─' * 100}")
    print("  2. RÉSUMÉ PAR SYMOLE (tous TF cumulés)")
    print("─" * 100)
    print(f"  {'Symbol':12s} {'Trades':>7s} {'WR':>7s} {'PnL':>12s} {'PF':>6s} "
          f"{'DD Max':>7s} {'Sig':>5s}")
    print(f"  {'-'*60}")

    for sym, trades in sorted(symbol_agg.items()):
        m = compute_metrics(trades)
        if m:
            print(f"  {sym:12s} {m['n']:>7d} {m['win_rate']:>6.1f}% ${m['total_pnl']:>+11.2f} "
                  f"{m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>6.1f}% "
                  f"{'  ✓' if m['significant'] else '  ✗'}")

    # ── 3. Summary by TF ──
    if not summary_only:
        print(f"\n{'─' * 100}")
        print("  3. RÉSUMÉ PAR TIMEFRAME (tous symboles cumulés)")
        print("─" * 100)
        print(f"  {'TF':5s} {'Trades':>7s} {'WR':>7s} {'PnL':>12s} {'PF':>6s} "
              f"{'DD Max':>7s} {'Sig':>5s}")
        print(f"  {'-'*50}")

        for tf, trades in sorted(tf_agg.items()):
            m = compute_metrics(trades)
            if m:
                print(f"  {tf:5s} {m['n']:>7d} {m['win_rate']:>6.1f}% ${m['total_pnl']:>+11.2f} "
                      f"{m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>6.1f}% "
                      f"{'  ✓' if m['significant'] else '  ✗'}")

    # ── 4. Detailed symbol analysis ──
    if not summary_only:
        print(f"\n{'─' * 100}")
        print("  4. ANALYSE DÉTAILLÉE PAR SYMOLE")
        print("─" * 100)

        for sym, trades in sorted(symbol_agg.items()):
            if symbol_filter and sym != symbol_filter:
                continue

            m = compute_metrics(trades)
            if m is None:
                continue

            print(f"\n  ═══ {sym} ═══")
            print(f"  Trades: {m['n']} | WR: {m['win_rate']}% | PnL: ${m['total_pnl']:+.2f}")
            print(f"  PF: {m['profit_factor']} | DD Max: {m['max_drawdown_pct']}%")
            print(f"  Avg Win: ${m['avg_win']:+.2f} | Avg Loss: ${m['avg_loss']:+.2f}")
            print(f"  Max Consec Wins: {m['max_consec_wins']} | Max Consec Losses: {m['max_consec_losses']}")
            print(f"  Partial TP: {m['partial_tp_count']} ({m['partial_tp_count']/m['n']*100:.1f}%)")
            print(f"  Statistical Significance: {'YES' if m['significant'] else 'NO'} (p={m['p_value']})")

            # Win rate by action
            buy_trades = [t for t in trades if t.get("action") == "BUY"]
            sell_trades = [t for t in trades if t.get("action") == "SELL"]
            if buy_trades:
                bm = compute_metrics(buy_trades)
                if bm:
                    print(f"  BUY:  {bm['n']:>5d} trades, WR {bm['win_rate']}%, PnL ${bm['total_pnl']:+.2f}")
            if sell_trades:
                sm = compute_metrics(sell_trades)
                if sm:
                    print(f"  SELL: {sm['n']:>5d} trades, WR {sm['win_rate']}%, PnL ${sm['total_pnl']:+.2f}")

            # Win rate by regime
            regimes = defaultdict(list)
            for t in trades:
                regimes[t.get("regime", "UNKNOWN")].append(t)
            if regimes:
                print(f"  Régimes:")
                for regime, rtrades in sorted(regimes.items()):
                    rm = compute_metrics(rtrades)
                    if rm:
                        print(f"    {regime:12s}: {rm['n']:>5d} trades, WR {rm['win_rate']}%, PnL ${rm['total_pnl']:+.2f}")

    # ── 5. Total ──
    print(f"\n{'═' * 100}")
    print("  5. TOTAL")
    print("═" * 100)
    total_m = compute_metrics(all_closed)
    if total_m:
        print(f"  Total Trades: {total_m['n']}")
        print(f"  Win Rate: {total_m['win_rate']}%")
        print(f"  Total PnL: ${total_m['total_pnl']:+.2f}")
        print(f"  Profit Factor: {total_m['profit_factor']}")
        print(f"  Max Drawdown: {total_m['max_drawdown_pct']}%")
        print(f"  Avg Win: ${total_m['avg_win']:+.2f}")
        print(f"  Avg Loss: ${total_m['avg_loss']:+.2f}")
        print(f"  Max Consec Wins: {total_m['max_consec_wins']}")
        print(f"  Max Consec Losses: {total_m['max_consec_losses']}")
        print(f"  Statistical Significance: {'YES' if total_m['significant'] else 'NO'} (p={total_m['p_value']})")

    # ── 6. Risk Analysis ──
    if not summary_only:
        print(f"\n{'─' * 100}")
        print("  6. ANALYSE DE RISQUE FTMO")
        print("─" * 100)

        # Check FTMO compliance
        dd_ok = total_m['max_drawdown_pct'] <= 10.0
        pf_ok = total_m['profit_factor'] >= 1.0
        wr_ok = total_m['win_rate'] >= 50.0

        print(f"  DD Max ≤ 10%: {'✅ PASS' if dd_ok else '❌ FAIL'} ({total_m['max_drawdown_pct']}%)")
        print(f"  PF ≥ 1.0: {'✅ PASS' if pf_ok else '❌ FAIL'} ({total_m['profit_factor']})")
        print(f"  WR ≥ 50%: {'✅ PASS' if wr_ok else '❌ FAIL'} ({total_m['win_rate']}%)")

        # Per-symbol FTMO check
        print(f"\n  Per-symbol DD check:")
        for sym, trades in sorted(symbol_agg.items()):
            m = compute_metrics(trades)
            if m:
                status = "✅" if m['max_drawdown_pct'] <= 10.0 else "❌"
                print(f"    {status} {sym}: DD {m['max_drawdown_pct']}%")

    # ── 7. Recommendations ──
    if not summary_only:
        print(f"\n{'─' * 100}")
        print("  7. RECOMMANDATIONS")
        print("─" * 100)

        # Find best/worst symbols
        sym_pnl = {}
        for sym, trades in symbol_agg.items():
            m = compute_metrics(trades)
            if m:
                sym_pnl[sym] = m['total_pnl']

        if sym_pnl:
            best = max(sym_pnl, key=sym_pnl.get)
            worst = min(sym_pnl, key=sym_pnl.get)
            print(f"  Meilleur symbole: {best} (${sym_pnl[best]:+.2f})")
            print(f"  Pire symbole: {worst} (${sym_pnl[worst]:+.2f})")

        # Find best/worst TF
        tf_pnl = {}
        for tf, trades in tf_agg.items():
            m = compute_metrics(trades)
            if m:
                tf_pnl[tf] = m['total_pnl']

        if tf_pnl:
            best_tf = max(tf_pnl, key=tf_pnl.get)
            worst_tf = min(tf_pnl, key=tf_pnl.get)
            print(f"  Meilleur TF: {best_tf} (${tf_pnl[best_tf]:+.2f})")
            print(f"  Pire TF: {worst_tf} (${tf_pnl[worst_tf]:+.2f})")

        # Warning about backtest limitations
        print(f"\n  ⚠️  AVERTISSEMENTS:")
        print(f"  - Backtest sans spread/slippage réel")
        print(f"  - WR uniforme (69-78%) → suspicion de biais")
        print(f"  - Performance live sera inférieure de 10-20%")
        print(f"  - Valider avec walk-forward avant production")

    print(f"\n{'═' * 100}")

    # Export
    if export:
        export_path = Path("runtime/backtest_16y_report.json")
        report = {
            "total": total_m,
            "by_symbol": {},
            "by_tf": {},
        }
        for sym, trades in symbol_agg.items():
            m = compute_metrics(trades)
            if m:
                report["by_symbol"][sym] = m
        for tf, trades in tf_agg.items():
            m = compute_metrics(trades)
            if m:
                report["by_tf"][tf] = m
        with open(export_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n  Exported report to {export_path}")


# ============================================================================
# MAIN
# ============================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rapport backtest 16y")
    parser.add_argument("--summary", action="store_true", help="Résumé seulement")
    parser.add_argument("--symbol", type=str, default=None, help="Filtrer par symbole")
    parser.add_argument("--export", action="store_true", help="Exporter JSON")
    args = parser.parse_args()

    # Load data
    pkl_path = Path("runtime/backtest_16y_full.pkl")
    if not pkl_path.exists():
        print(f"ERROR: {pkl_path} not found. Run backtest_16y_full.py first.")
        return

    with open(pkl_path, "rb") as f:
        all_trades = pickle.load(f)

    print(f"Loaded {sum(len(v) for v in all_trades.values())} trades from {pkl_path}\n")

    generate_report(all_trades, summary_only=args.summary, symbol_filter=args.symbol, export=args.export)


if __name__ == "__main__":
    main()
