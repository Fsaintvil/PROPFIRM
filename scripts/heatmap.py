#!/usr/bin/env python3
"""
Heatmap des performances backtest MOM20x3 — Année × Symbole.

Usage:
    python scripts/heatmap.py                          # Tous les symboles H1
    python scripts/heatmap.py --tf H4                  # Timeframe spécifique
    python scripts/heatmap.py --metric pnl             # PnL uniquement
    python scripts/heatmap.py --symbol USDCAD --all-tf # Années × TF pour 1 symbole
    python scripts/heatmap.py --show                   # Afficher à l'écran (pas de save)

Génère runtime/heatmap_{metric}_{tf}.png
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime

# Vérifier matplotlib
try:
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    import numpy as np
except ImportError:
    print("❌ matplotlib requis. Installez: pip install matplotlib")
    raise SystemExit(1)


REPORT_PATH = "runtime/backtest_report.json"
OUTPUT_DIR = "runtime"


def load_report(path=REPORT_PATH):
    """Charge le rapport JSON du backtest."""
    if not os.path.exists(path):
        # Essayer de le générer
        print(f"⚠️  {path} introuvable. Exécutez d'abord report_backtest_multi.py --json {path}")
        raise SystemExit(1)
    with open(path) as f:
        return json.load(f)


def build_year_sym_matrix(report, tf="H1", metric="pnl", top_n=None):
    """Construit une matrice Année × Symbole pour une métrique donnée."""
    # Collecter toutes les années disponibles et symboles
    all_years = set()
    all_symbols = []
    year_sym_data = {}

    for sym, sym_data in report["symbols"].items():
        if tf not in sym_data:
            continue
        years = sym_data[tf].get("years", {})
        if not years:
            continue
        all_symbols.append(sym)
        for yr_str, yr_data in years.items():
            all_years.add(int(yr_str))
            key = (sym, int(yr_str))
            if metric == "pnl":
                val = yr_data.get("pnl", 0)
            elif metric == "win_rate":
                val = yr_data.get("win_rate", 0)
            elif metric == "profit_factor":
                val = yr_data.get("profit_factor", 0)
            elif metric == "trades":
                val = yr_data.get("trades", 0)
            elif metric == "max_drawdown_pct":
                val = yr_data.get("max_drawdown_pct", 0)
            else:
                val = yr_data.get("pnl", 0)
            year_sym_data[key] = val

    if not all_symbols or not all_years:
        print(f"⚠️  Aucune donnée pour {tf}")
        return None, None, None

    all_years = sorted(all_years)
    
    # Trier les symboles par PnL total
    if top_n:
        sym_pnl = []
        for sym in all_symbols:
            total = sum(year_sym_data.get((sym, y), 0) for y in all_years)
            sym_pnl.append((sym, total))
        sym_pnl.sort(key=lambda x: -abs(x[1]) if metric != "win_rate" else -x[1])
        all_symbols = [s for s, _ in sym_pnl[:top_n]]

    # Build matrix
    n_years = len(all_years)
    n_syms = len(all_symbols)
    matrix = np.zeros((n_years, n_syms))
    for i, yr in enumerate(all_years):
        for j, sym in enumerate(all_symbols):
            matrix[i, j] = year_sym_data.get((sym, yr), 0)

    return matrix, all_years, all_symbols


def plot_heatmap(matrix, years, symbols, metric="pnl", tf="H1", show=False):
    """Dessine la heatmap et la sauvegarde."""
    if matrix is None or len(years) == 0:
        return

    fig, ax = plt.subplots(figsize=(max(10, len(symbols) * 0.8), max(6, len(years) * 0.35)))

    # Color map
    if metric in ("pnl",):
        # Red for negative, green for positive
        vmax = max(abs(matrix.min()), abs(matrix.max()))
        cmap = plt.cm.RdYlGn
        norm = mcolors.TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax)
        fmt = "${:+.0f}"
        title = "PnL ($)"
    elif metric == "win_rate":
        norm = mcolors.Normalize(vmin=40, vmax=80)
        cmap = plt.cm.YlOrRd
        fmt = "{:.1f}%"
        title = "Win Rate (%)"
    elif metric == "profit_factor":
        norm = mcolors.Normalize(vmin=0.5, vmax=1.5)
        cmap = plt.cm.RdYlGn
        fmt = "{:.2f}"
        title = "Profit Factor"
    elif metric == "trades":
        norm = None
        cmap = plt.cm.Blues
        fmt = "{:.0f}"
        title = "Trades"
    elif metric == "max_drawdown_pct":
        norm = mcolors.Normalize(vmin=0, vmax=20)
        cmap = plt.cm.Reds
        fmt = "{:.1f}%"
        title = "DD Max (%)"
    else:
        norm = None
        cmap = plt.cm.viridis
        fmt = "{:.1f}"
        title = metric

    im = ax.imshow(matrix, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")

    # Axis labels
    ax.set_xticks(range(len(symbols)))
    ax.set_xticklabels(symbols, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels([str(y) for y in years], fontsize=8)

    # Annotate cells
    for i in range(len(years)):
        for j in range(len(symbols)):
            val = matrix[i, j]
            if val == 0:
                text = "-"
            elif metric == "pnl":
                text = f"${val:+.0f}"
            elif metric == "win_rate":
                text = f"{val:.0f}%"
            elif metric == "profit_factor":
                text = f"{val:.2f}"
            else:
                text = fmt.format(val)
            if metric == "pnl":
                color = "white" if abs(val) > vmax * 0.6 else "black"
            elif metric == "win_rate":
                color = "white" if val < 55 else "black"
            else:
                color = "black"
            ax.text(j, i, text, ha="center", va="center", fontsize=7, color=color)

    ax.set_xlabel("Symboles")
    ax.set_ylabel("Années")
    ax.set_title(f"{title} — {tf}", fontsize=14, fontweight="bold")

    fig.tight_layout()

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    outpath = os.path.join(OUTPUT_DIR, f"heatmap_{metric}_{tf}.png")
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"✅ Heatmap sauvegardée: {outpath} ({matrix.shape[0]} années × {matrix.shape[1]} symboles)")

    if show:
        plt.show()
    else:
        plt.close(fig)


def plot_symbol_tf_matrix(report, symbol="USDCAD", metric="pnl", show=False):
    """Années × TF pour un seul symbole."""
    tf_data = {}
    all_years = set()
    for tf in ["H1", "H4", "D1"]:
        if symbol not in report["symbols"] or tf not in report["symbols"][symbol]:
            continue
        years = report["symbols"][symbol][tf].get("years", {})
        if not years:
            continue
        tf_data[tf] = {}
        for yr_str, yr_data in years.items():
            yr = int(yr_str)
            all_years.add(yr)
            if metric == "pnl":
                tf_data[tf][yr] = yr_data.get("pnl", 0)
            elif metric == "win_rate":
                tf_data[tf][yr] = yr_data.get("win_rate", 0)
            elif metric == "profit_factor":
                tf_data[tf][yr] = yr_data.get("profit_factor", 0)
            elif metric == "trades":
                tf_data[tf][yr] = yr_data.get("trades", 0)
            else:
                tf_data[tf][yr] = yr_data.get("pnl", 0)

    if not tf_data:
        print(f"⚠️  Aucune donnée pour {symbol}")
        return

    all_years = sorted(all_years)
    tfs = ["H1", "H4", "D1"]
    matrix = np.zeros((len(all_years), len(tfs)))
    for i, yr in enumerate(all_years):
        for j, tf in enumerate(tfs):
            matrix[i, j] = tf_data.get(tf, {}).get(yr, 0)

    fig, ax = plt.subplots(figsize=(8, max(5, len(all_years) * 0.35)))

    vmax = max(abs(matrix.min()), abs(matrix.max())) if matrix.any() else 1
    cmap = plt.cm.RdYlGn
    norm = mcolors.TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax) if matrix.min() < 0 else None

    im = ax.imshow(matrix, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")

    ax.set_xticks(range(len(tfs)))
    ax.set_xticklabels(tfs, fontsize=10)
    ax.set_yticks(range(len(all_years)))
    ax.set_yticklabels([str(y) for y in all_years], fontsize=8)

    for i in range(len(all_years)):
        for j in range(len(tfs)):
            val = matrix[i, j]
            if val == 0:
                text = "-"
            else:
                text = f"${val:+.0f}" if metric == "pnl" else f"{val:.1f}"
            ax.text(j, i, text, ha="center", va="center", fontsize=7)

    ax.set_xlabel("Timeframe")
    ax.set_ylabel("Années")
    ax.set_title(f"{symbol} — PnL par Année × Timeframe", fontsize=13, fontweight="bold")
    fig.tight_layout()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    outpath = os.path.join(OUTPUT_DIR, f"heatmap_{symbol}_{metric}.png")
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"✅ Heatmap sauvegardée: {outpath} ({len(all_years)} années × {len(tfs)} TFs)")

    if show:
        plt.show()
    else:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Heatmap backtest MOM20x3")
    parser.add_argument("--metric", choices=["pnl", "win_rate", "profit_factor", "trades", "max_drawdown_pct"],
                        default="pnl", help="Métrique à afficher")
    parser.add_argument("--tf", default="H1", help="Timeframe (H1/H4/D1)")
    parser.add_argument("--top", type=int, default=10, help="Top N symboles")
    parser.add_argument("--show", action="store_true", help="Afficher à l'écran")
    parser.add_argument("--symbol", type=str, default=None, help="Symbole unique (--all-tf)")
    parser.add_argument("--all-tf", action="store_true", help="Années × TF pour un symbole")
    args = parser.parse_args()

    report = load_report()
    
    if args.symbol and args.all_tf:
        plot_symbol_tf_matrix(report, args.symbol, args.metric, args.show)
        return

    matrix, years, symbols = build_year_sym_matrix(report, args.tf, args.metric, args.top)
    if matrix is not None:
        plot_heatmap(matrix, years, symbols, args.metric, args.tf, args.show)


if __name__ == "__main__":
    main()
