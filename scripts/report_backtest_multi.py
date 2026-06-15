"""
Generate per-year, per-month reports from backtest trades.
Groups by (symbol, timeframe, year, month) and computes:
  Trades, WR, PnL, PF, DD Max, Edge

Usage:
    python scripts/report_backtest_multi.py
    python scripts/report_backtest_multi.py --top 5   # Top 5 best symbols only
    python scripts/report_backtest_multi.py --json     # JSON export
"""
import argparse
import json
import pickle
import sys
from collections import defaultdict
from datetime import datetime
from math import erf, sqrt
from pathlib import Path

INITIAL_BALANCE = 200_000.0


class TradeDictWrapper:
    """Wrapper to make dict trades behave like SimTrade objects."""
    def __init__(self, d):
        self.symbol = d["symbol"]
        self.timeframe = d["timeframe"]
        self.action = d["action"]
        self.entry = d["entry"]
        self.sl = d["sl"]
        self.tp = d["tp"]
        self.close_price = d["close_price"]
        self.result = d["result"]
        self.profit_usd = d["profit_usd"]
        self.profit_pct = d["profit_pct"]
        self.bars_held = d["bars_held"]
        self.partial_tp = d["partial_tp"]
        self.lot = d["lot"]
        self.open_time = d["open_time"]
        self.close_time = d["close_time"]
        self.closed = self.result is not None


def load_trades(path="runtime/trades_backtest.pkl"):
    with open(path, "rb") as f:
        data = pickle.load(f)

    # Detect if dicts or objects
    result = {}
    for key, tlist in data.items():
        if tlist and hasattr(tlist[0], 'symbol'):
            # Direct SimTrade objects
            result[key] = tlist
        else:
            # Dicts
            result[key] = [TradeDictWrapper(t) for t in tlist]
    return result


def compute_metrics(trades):
    """Compute metrics for a list of SimTrade objects."""
    closed = [t for t in trades if t.closed]
    if not closed:
        return {"n": 0}

    wins = [t for t in closed if t.profit_usd > 0]
    losses = [t for t in closed if t.profit_usd <= 0]
    n = len(closed)
    n_wins = len(wins)
    wr = n_wins / n * 100 if n > 0 else 0
    total_pnl = sum(t.profit_usd for t in closed)
    gross_profit = sum(max(0, t.profit_usd) for t in closed)
    gross_loss = abs(sum(min(0, t.profit_usd) for t in closed))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0)

    # Max drawdown
    peak = INITIAL_BALANCE
    dd_max = 0.0
    balance = INITIAL_BALANCE
    for t in sorted(closed, key=lambda x: (x.open_time is None, x.open_time or "")):
        balance += t.profit_usd
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)

    # p-value
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    else:
        p = 1.0

    return {
        "n": n, "wins": n_wins, "losses": n - n_wins,
        "win_rate": round(wr, 1), "total_pnl": round(total_pnl, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "p_value": round(p, 4), "significant": p < 0.05,
    }


def extract_year_month(open_time):
    """Extract year and month from a trade's open_time string."""
    if open_time is None or open_time == "":
        return None, None
    try:
        s = str(open_time)[:19].replace("T", " ")
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.year, dt.month
    except (ValueError, TypeError):
        return None, None


def group_by_period(trades, by_month=True):
    """Group trades by (symbol, timeframe, year, month)."""
    groups = defaultdict(list)
    for t in trades:
        if not t.closed:
            continue
        year, month = extract_year_month(t.open_time)
        if year is None:
            continue
        if by_month:
            key = (t.symbol, t.timeframe, year, month)
        else:
            key = (t.symbol, t.timeframe, year)
        groups[key].append(t)
    return groups


def print_summary_table(all_trades, top_n=None):
    """Print the global summary table (all symbols × timeframes)."""
    print(f"\n{'='*80}")
    print(f"  BACKTEST MOM20x3 — GLOBAL SUMMARY")
    print(f"{'='*80}")
    print(f"  {'Symbol':12s} {'TF':3s} {'Trades':>7s} {'WR':>7s} {'PnL':>11s} "
          f"{'PF':>6s} {'DD Max':>7s} {'Edge':>6s}")
    print(f"  {'-'*60}")

    # Sort by PnL descending
    rows = []
    for key, trades in all_trades.items():
        if not trades:
            continue
        m = compute_metrics(trades)
        sym, tf = key.split("_")
        edge = "✅" if m.get("significant") and m["win_rate"] > 50 else "❌"
        rows.append((sym, tf, m, edge))

    rows.sort(key=lambda r: r[2]["total_pnl"], reverse=True)

    if top_n:
        rows = rows[:top_n]

    total_n = 0
    total_pnl = 0.0
    for sym, tf, m, edge in rows:
        dd = m.get("max_drawdown_pct", 0)
        print(f"  {sym:12s} {tf:3s} {m['n']:>7d} {m['win_rate']:>6.1f}% "
              f"${m['total_pnl']:>+9.2f} {m['profit_factor']:>5.2f} "
              f"{dd:>5.1f}% {edge:>6s}")
        total_n += m["n"]
        total_pnl += m["total_pnl"]

    print(f"  {'-'*60}")
    print(f"  {'TOTAL':12s}       {total_n:>7d}               ${total_pnl:>+9.2f}")


def print_period_report(all_trades, symbol=None, timeframe=None, top_symbols=None):
    """Print per-year and per-month breakdown."""
    all_closed = []
    for trades in all_trades.values():
        all_closed.extend([t for t in trades if t.closed])

    # Group by symbol+tf+year+month
    monthly_groups = group_by_period(all_closed, by_month=True)
    yearly_groups = group_by_period(all_closed, by_month=False)

    # Filter
    if symbol:
        monthly_groups = {k: v for k, v in monthly_groups.items() if k[0] == symbol}
        yearly_groups = {k: v for k, v in yearly_groups.items() if k[0] == symbol}
    if timeframe:
        monthly_groups = {k: v for k, v in monthly_groups.items() if k[1] == timeframe}
        yearly_groups = {k: v for k, v in yearly_groups.items() if k[1] == timeframe}

    # Determine which symbols to show
    if top_symbols:
        # Show per-symbol PnL ranking
        by_sym_tf = defaultdict(list)
        for t in all_closed:
            by_sym_tf[(t.symbol, t.timeframe)].append(t)
        ranked = sorted(by_sym_tf.items(), key=lambda x: compute_metrics(x[1])["total_pnl"], reverse=True)
        selected = ranked[:top_symbols]
    else:
        # Show all
        by_sym_tf = defaultdict(list)
        for t in all_closed:
            by_sym_tf[(t.symbol, t.timeframe)].append(t)
        selected = sorted(by_sym_tf.items(), key=lambda x: compute_metrics(x[1])["total_pnl"], reverse=True)

    # Get unique years from yearly_groups
    years = sorted(set(k[2] for k in yearly_groups.keys()))

    for (sym, tf), trades in selected:
        # Filter groups for this symbol+tf
        sym_yearly = {k: v for k, v in yearly_groups.items()
                      if k[0] == sym and k[1] == tf}
        sym_monthly = {k: v for k, v in monthly_groups.items()
                       if k[0] == sym and k[1] == tf}

        if not sym_yearly:
            continue

        print(f"\n{'='*80}")
        print(f"  {sym} — {tf}")
        print(f"{'='*80}")

        # Yearly table
        print(f"\n  Year   Trades    WR      PnL        PF   DD Max  Edge")
        print(f"  {'-'*57}")
        yearly_total_n = 0
        yearly_total_pnl = 0.0
        for year in years:
            key = (sym, tf, year)
            if key not in sym_yearly:
                continue
            m = compute_metrics(sym_yearly[key])
            edge = "✅" if m["significant"] and m["win_rate"] > 50 else "❌"
            print(f"  {year:>4d}  {m['n']:>6d}  {m['win_rate']:>5.1f}%  "
                  f"${m['total_pnl']:>+9.2f}  {m['profit_factor']:>5.2f}  "
                  f"{m['max_drawdown_pct']:>5.1f}%  {edge}")
            yearly_total_n += m["n"]
            yearly_total_pnl += m["total_pnl"]

        total_m = compute_metrics([t for t in trades if t.closed])
        print(f"  {'-'*57}")
        print(f"  TOTAL {yearly_total_n:>6d}  {total_m['win_rate']:>5.1f}%  "
              f"${yearly_total_pnl:>+9.2f}  {total_m['profit_factor']:>5.2f}  "
              f"{total_m['max_drawdown_pct']:>5.1f}%")

        # Monthly table (only recent years to avoid huge output)
        print(f"\n  Monthly breakdown (by year):")
        for year in years[-5:]:  # Last 5 years
            key = (sym, tf, year)
            if key not in sym_yearly:
                continue
            m_y = compute_metrics(sym_yearly[key])
            print(f"\n  --- {year} ({m_y['n']} trades, ${m_y['total_pnl']:+.0f}) ---")
            print(f"  {'Mon':4s} {'Trades':>7s} {'WR':>7s} {'PnL':>11s} "
                  f"{'PF':>6s} {'DD':>6s}")
            for month in range(1, 13):
                mkey = (sym, tf, year, month)
                if mkey not in sym_monthly:
                    continue
                m = compute_metrics(sym_monthly[mkey])
                if m["n"] == 0:
                    continue
                months = "JanFebMarAprMayJunJulAugSepOctNovDec"
                mon_str = months[(month-1)*3:(month)*3] if 1 <= month <= 12 else f"{month:02d}"
                print(f"  {mon_str:4s} {m['n']:>7d} {m['win_rate']:>6.1f}% "
                      f"${m['total_pnl']:>+9.2f} {m['profit_factor']:>5.2f} "
                      f"{m['max_drawdown_pct']:>5.1f}%")


def export_to_json(all_trades, outpath):
    """Export all metrics to JSON."""
    all_closed = []
    for trades in all_trades.values():
        all_closed.extend([t for t in trades if t.closed])

    monthly_groups = group_by_period(all_closed, by_month=True)
    yearly_groups = group_by_period(all_closed, by_month=False)

    report = {
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "symbols": {}
    }

    by_sym_tf = defaultdict(list)
    for t in all_closed:
        by_sym_tf[(t.symbol, t.timeframe)].append(t)

    for (sym, tf), trades_sel in by_sym_tf.items():
        if sym not in report["symbols"]:
            report["symbols"][sym] = {}
        total_m = compute_metrics(trades_sel)

        # Yearly
        years_data = {}
        sym_yearly = {k: v for k, v in yearly_groups.items() if k[0] == sym and k[1] == tf}
        sym_monthly = {k: v for k, v in monthly_groups.items() if k[0] == sym and k[1] == tf}

        for (_, _, year), tlist in sorted(sym_yearly.items()):
            m = compute_metrics(tlist)
            years_data[str(year)] = {
                "trades": m["n"], "win_rate": m["win_rate"],
                "pnl": m["total_pnl"], "profit_factor": m["profit_factor"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "significant": m["significant"],
            }

        report["symbols"][sym][tf] = {
            "total_trades": total_m["n"],
            "win_rate": total_m["win_rate"],
            "pnl": total_m["total_pnl"],
            "profit_factor": total_m["profit_factor"],
            "max_drawdown_pct": total_m["max_drawdown_pct"],
            "significant": total_m["significant"],
            "years": years_data,
        }

    with open(outpath, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nExported to {outpath}")


def export_to_csv(all_trades, outpath):
    """Export all metrics to CSV (flat format)."""
    all_closed = []
    for trades in all_trades.values():
        all_closed.extend([t for t in trades if t.closed])

    monthly_groups = group_by_period(all_closed, by_month=True)
    yearly_groups = group_by_period(all_closed, by_month=False)

    import csv

    with open(outpath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Symbol", "Timeframe", "Period", "Type", "Trades",
                         "WR", "PnL", "PF", "DD_Max", "Significant"])

        # Yearly
        for (sym, tf, year), tlist in sorted(yearly_groups.items()):
            m = compute_metrics(tlist)
            writer.writerow([sym, tf, str(year), "year", m["n"],
                             f"{m['win_rate']:.1f}%", round(m["total_pnl"], 2),
                             m["profit_factor"], m["max_drawdown_pct"],
                             m["significant"]])

        # Monthly
        for (sym, tf, year, month), tlist in sorted(monthly_groups.items()):
            m = compute_metrics(tlist)
            period = f"{year}-{month:02d}"
            writer.writerow([sym, tf, period, "month", m["n"],
                             f"{m['win_rate']:.1f}%", round(m["total_pnl"], 2),
                             m["profit_factor"], m["max_drawdown_pct"],
                             m["significant"]])

    print(f"Exported to {outpath}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=None, help="Top N symbols only")
    parser.add_argument("--symbol", type=str, default=None, help="Filter symbol")
    parser.add_argument("--tf", type=str, default=None, help="Filter timeframe")
    parser.add_argument("--json", type=str, default=None, help="Export to JSON file")
    parser.add_argument("--csv", type=str, default=None, help="Export to CSV file")
    parser.add_argument("--summary", action="store_true", help="Summary only")
    args = parser.parse_args()

    trades = load_trades()
    print(f"Loaded {sum(len(v) for v in trades.values())} trades "
          f"({len(trades)} symbol×tf combinations)")

    if not args.json and not args.csv:
        # Console mode only
        pass
    else:
        if args.json:
            export_to_json(trades, args.json)
        if args.csv:
            export_to_csv(trades, args.csv)
        return

    # Summary table
    print_summary_table(trades, top_n=args.top)

    if not args.summary:
        # Per-period report
        print_period_report(trades, symbol=args.symbol, timeframe=args.tf,
                            top_symbols=args.top)


if __name__ == "__main__":
    main()
