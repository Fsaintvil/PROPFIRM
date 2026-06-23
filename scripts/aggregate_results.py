#!/usr/bin/env python3
"""
Aggregate Backtest Results — Rapport consolidé multi-symboles.

Scanne les CSVs dans backtest/results/ et produit un tableau comparatif.

Usage :
    python scripts/aggregate_results.py                          # Tableau complet
    python scripts/aggregate_results.py --group major_fx         # Filtrer par groupe
    python scripts/aggregate_results.py --json                   # Export JSON
    python scripts/aggregate_results.py --min-trades 50          # Min trades pour inclusion
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# ─── Groupes de symboles ──────────────────────────────────────────────────

SYMBOL_GROUPS: dict[str, list[str]] = {
    "major_fx": ["EURUSD", "USDCAD"],
    "minor_fx": ["EURJPY", "GBPJPY"],
    "metals": ["XAUUSD"],
    "crypto": ["BTCUSD"],
}

ALL_SYMBOLS = [s for group in SYMBOL_GROUPS.values() for s in group]

# Mapping nom court → groupe
SYMBOL_TO_GROUP: dict[str, str] = {}
for group_name, symbols in SYMBOL_GROUPS.items():
    for s in symbols:
        SYMBOL_TO_GROUP[s] = group_name


def get_symbol_short(symbol: str) -> str:
    """Retourne le nom court d'un symbole."""
    return symbol.replace(".cash", "")


def parse_csv(path: Path) -> dict | None:
    """Parse un fichier CSV de trades et retourne les métriques clés."""
    try:
        df = pd.read_csv(path)
        if df.empty or "pnl_usd" not in df.columns:
            return None

        n = len(df)

        # pnl_cost = profit AFTER costs (net PnL utilisé par le moteur de backtest)
        # pnl_usd = profit BEFORE costs (raw PnL)
        pnl_col = "pnl_cost" if "pnl_cost" in df.columns else "pnl_usd"
        raw_col = "pnl_usd" if "pnl_usd" in df.columns else pnl_col

        wins = df[df[pnl_col] > 0]
        losses = df[df[pnl_col] < 0]
        n_wins = len(wins)
        n_losses = len(losses)

        if n == 0:
            return None

        win_rate = n_wins / n * 100
        total_pnl = df[pnl_col].sum()
        total_raw = df[raw_col].sum()
        total_cost = round(total_raw - total_pnl, 2)
        gross_profit = wins[raw_col].sum() if n_wins > 0 else 0
        gross_loss = abs(losses[raw_col].sum()) if n_losses > 0 else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        avg_win = wins[raw_col].mean() if n_wins > 0 else 0
        avg_loss = abs(losses[raw_col].mean()) if n_losses > 0 else 0
        avg_rr = avg_win / avg_loss if avg_loss > 0 else 0

        max_dd_pct = None
        if "cum_pnl" in df.columns:
            cum = df["cum_pnl"].values
            peak = cum[0]
            dd = 0
            for v in cum:
                if v > peak:
                    peak = v
                dd_val = (peak - v) / peak * 100 if peak > 0 else 0
                if dd_val > dd:
                    dd = dd_val
            max_dd_pct = round(dd, 2)

        avg_lot = df["lot"].mean()
        max_lot = df["lot"].max()

        return {
            "symbol": df.iloc[0].get("symbol", path.stem.replace("trades_", "").rsplit("_", 2)[0]),
            "strategy": path.stem.split("_")[-1] if "_mom20x3" in path.stem else "unknown",
            "timeframe": path.stem.split("_")[-2] if len(path.stem.split("_")) >= 3 else "H1",
            "total_trades": n,
            "win_rate": round(win_rate, 1),
            "n_wins": n_wins,
            "n_losses": n_losses,
            "net_profit": round(total_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_rr": round(avg_rr, 2),
            "avg_lot": round(avg_lot, 4),
            "max_lot": round(max_lot, 4),
            "max_dd_pct": max_dd_pct,
            "total_cost": total_cost,
            "source_file": path.name,
        }
    except Exception as e:
        return {"error": str(e), "source_file": path.name}


def scan_results(directory: str = "backtest/results") -> list[dict]:
    """Scanne tous les CSVs de trades dans le dossier results."""
    results_dir = Path(directory)
    if not results_dir.exists():
        print(f"❌ Dossier introuvable : {results_dir}")
        return []

    csv_files = sorted(results_dir.glob("trades_*.csv"))
    if not csv_files:
        print(f"⚠️  Aucun fichier trades_*.csv trouvé dans {results_dir}")
        return []

    results = []
    for path in csv_files:
        metrics = parse_csv(path)
        if metrics and "error" not in metrics:
            results.append(metrics)
        elif metrics and "error" in metrics:
            print(f"  ⚠️  Erreur {path.name}: {metrics['error']}")

    return results


def filter_by_group(results: list[dict], group: str) -> list[dict]:
    """Filtre les résultats par groupe de symboles."""
    if group not in SYMBOL_GROUPS:
        print(f"⚠️  Groupe inconnu: {group}. Groupes: {', '.join(SYMBOL_GROUPS.keys())}")
        return results

    allowed = SYMBOL_GROUPS[group]
    return [r for r in results if r["symbol"] in allowed]


def print_table(results: list[dict], title: str = "Rapport Consolidé Multi-Symboles"):
    """Affiche un tableau comparatif formaté."""
    if not results:
        print("Aucun résultat à afficher.")
        return

    # Trier par net_profit décroissant
    sorted_results = sorted(results, key=lambda r: r["net_profit"], reverse=True)

    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)
    print()

    # En-tête
    header = f"{'Symbole':20s} {'TF':4s} {'Trades':>7s} {'WR':>6s} {'PnL':>12s} {'PF':>6s} {'DD':>7s} {'RR':>5s} {'Avg$':>8s} {'Lot':>6s}"
    print(header)
    print("-" * len(header))

    total_trades = 0
    total_pnl = 0
    total_wins = 0

    for r in sorted_results:
        symbol_short = get_symbol_short(r["symbol"])
        dd_str = f"{r['max_dd_pct']:.1f}%" if r["max_dd_pct"] is not None else "N/A"
        pnl_str = f"${r['net_profit']:>+,.2f}"
        avg_str = f"${r['net_profit'] / r['total_trades']:>+,.2f}" if r["total_trades"] > 0 else "$0.00"

        print(
            f"{symbol_short:20s} {r['timeframe']:4s} {r['total_trades']:>7d}"
            f" {r['win_rate']:>5.1f}% {pnl_str:>12s}"
            f" {r['profit_factor']:>6.2f} {dd_str:>7s}"
            f" {r['avg_rr']:>5.2f} {avg_str:>8s}"
            f" {r['avg_lot']:>6.2f}"
        )

        total_trades += r["total_trades"]
        total_pnl += r["net_profit"]
        total_wins += r["n_wins"]

    # Total
    total_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    print("-" * len(header))
    print(f"{'TOTAL':20s} {'':4s} {total_trades:>7d} {total_wr:>5.1f}% ${total_pnl:>+9,.2f}")
    print()

    # Résumé exécutif
    n_positive = sum(1 for r in results if r["net_profit"] > 0)
    n_negative = sum(1 for r in results if r["net_profit"] < 0)
    n_profitable_pf = sum(1 for r in results if r["profit_factor"] >= 1.0)

    print(f"  ✅ Symboles positifs  : {n_positive}/{len(results)}")
    print(f"  ✅ PF >= 1.0          : {n_profitable_pf}/{len(results)}")
    print(f"  ❌ Symboles négatifs  : {n_negative}/{len(results)}")
    print(f"  📊 PnL total          : ${total_pnl:+,.2f}")
    print(f"  📊 WR moyenne         : {total_wr:.1f}%")
    print()


def export_json(results: list[dict], output: str = "backtest/results/consolidated_report.json"):
    """Exporte les résultats en JSON."""
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "total_symbols": len(results),
        "total_trades": sum(r["total_trades"] for r in results),
        "total_pnl": round(sum(r["net_profit"] for r in results), 2),
        "symbols": {r["symbol"]: r for r in results},
    }

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"📄 Rapport exporté : {output_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Rapport consolidé multi-symboles des backtests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--group",
        type=str,
        default=None,
        choices=list(SYMBOL_GROUPS.keys()) + ["all"],
        help="Filtrer par groupe de symboles",
    )
    parser.add_argument("--json", action="store_true", help="Exporter en JSON")
    parser.add_argument(
        "--output", type=str, default="backtest/results/consolidated_report.json", help="Fichier JSON de sortie"
    )
    parser.add_argument(
        "--min-trades", type=int, default=10, help="Nombre minimum de trades pour inclusion (défaut: 10)"
    )
    parser.add_argument(
        "--dir", type=str, default="backtest/results", help="Dossier contenant les CSVs (défaut: backtest/results)"
    )

    args = parser.parse_args()

    # Scanner les résultats
    results = scan_results(args.dir)

    if not results:
        print("❌ Aucun résultat trouvé.")
        sys.exit(1)

    # Filtrer par nombre minimum de trades
    results = [r for r in results if r["total_trades"] >= args.min_trades]

    # Filtrer par groupe
    if args.group and args.group != "all":
        results = filter_by_group(results, args.group)
        title = f"Rapport Consolidé — Groupe {args.group}"
    else:
        title = "Rapport Consolidé Multi-Symboles"

    if not results:
        print(f"⚠️  Aucun résultat après filtrage (min {args.min_trades} trades).")
        sys.exit(0)

    # Afficher le tableau
    print_table(results, title)

    # Exporter en JSON
    if args.json:
        export_json(results, args.output)


if __name__ == "__main__":
    main()
