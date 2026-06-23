#!/usr/bin/env python3
"""Extract real trades from FTMO ReportHistory Excel and feed into OnlineLearner.

Reads trades from ReportHistory-1513621052.xlsx (and optionally all in data/reporthistory/),
computes ATR(14) and R-multiple from H1 parquet data at entry time,
appends them to runtime/ol_state.json["online_history"] with regime=IMPORT.

Usage:
    python scripts/feed_excel_to_ol.py                          # Root file only
    python scripts/feed_excel_to_ol.py --all                     # All files in data/reporthistory/
    python scripts/feed_excel_to_ol.py --dry-run                 # Simulation sans écriture
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "runtime" / "ol_state.json"
HISTORICAL_DIR = ROOT / "data" / "historical"
REPORTHISTORY_DIR = ROOT / "data" / "reporthistory"

CONTRACT_MULTIPLIER = {
    "XAUUSD": 100,
    "BTCUSD": 1,
    "ETHUSD": 1,
    "EURUSD": 100000,
    "GBPUSD": 100000,
    "USDJPY": 1000,
    "AUDUSD": 100000,
    "USDCAD": 100000,
    "NZDUSD": 100000,
    "USDCHF": 100000,
    "US500.cash": 1,
    "LNKUSD": 1,
}


def compute_atr_wilder(df_slice, period=14):
    """Compute Wilder's ATR(14) and ADX from a slice of OHLC data."""
    high = df_slice["high"].values
    low = df_slice["low"].values
    close = df_slice["close"].values
    prev_close = close[:-1]

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )

    if len(tr) < period:
        return None, None, None

    atr = float(np.mean(tr[:period]))
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period

    # +DI / -DI calculation
    plus_dm = np.maximum(high[1:] - high[:-1], 0)
    minus_dm = np.maximum(low[:-1] - low[1:], 0)
    plus_dm = np.where((plus_dm > minus_dm), plus_dm, 0)
    minus_dm = np.where((minus_dm > plus_dm), minus_dm, 0)

    di_period = min(period, len(tr))
    if atr > 0:
        plus_di = 100 * np.mean(plus_dm[-di_period:]) / atr
        minus_di = 100 * np.mean(minus_dm[-di_period:]) / atr
    else:
        plus_di = minus_di = 0

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    adx = dx

    return atr, adx, {"plus_di": plus_di, "minus_di": minus_di}


def load_parquet(symbol):
    """Load H1 parquet data for a symbol."""
    path = HISTORICAL_DIR / f"{symbol}_H1.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def detect_regime(adx, trend_pct):
    """Classify market regime based on ADX + trend."""
    if adx is None:
        return "UNKNOWN"
    if adx >= 22:
        if trend_pct > 0.002:
            return "TREND_UP"
        elif trend_pct < -0.002:
            return "TREND_DOWN"
        else:
            return "RANGING"
    elif adx < 18:
        return "RANGING"
    else:
        return "RANGING"  # 18-22 hysteresis → ranging


def parse_excel(path):
    """Parse an FTMO ReportHistory Excel file and return valid trades."""
    try:
        df = pd.read_excel(path, skiprows=6)
    except Exception as e:
        print(f"  ⚠️  Erreur lecture {path.name}: {e}")
        return None

    # Clean column names
    df.columns = [str(c).strip() for c in df.columns]

    # Find the relevant columns
    expected = {"Symbole", "Type", "Volume", "Profit", "Heure", "Prix", "Prix.1"}
    missing = expected - set(df.columns)
    if missing:
        print(f"  ⚠️  Colonnes manquantes dans {path.name}: {missing}")
        return None

    # Drop non-data rows (header repetitions, empty)
    df = df[df["Symbole"].notna()].copy()
    df = df[~df["Symbole"].isin(["Symbole", "Symbol"])].copy()

    # Parse numeric fields
    df["Profit"] = pd.to_numeric(df["Profit"], errors="coerce")
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    df = df.dropna(subset=["Profit", "Volume"])

    # Parse entry time
    df["entry_time"] = pd.to_datetime(df["Heure"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    df["exit_price"] = pd.to_numeric(df["Prix.1"], errors="coerce")
    df["entry_price"] = pd.to_numeric(df["Prix"], errors="coerce")

    # Parse direction
    df["direction"] = df["Type"].str.upper().str.strip()
    df = df[df["direction"].isin(["BUY", "SELL"])].copy()
    df = df.dropna(subset=["entry_time"])

    return df.reset_index(drop=True)


def enricher(trades_df, symbol, hist):
    """Enrich trades with ATR, ADX, regime at entry time."""
    multiplier = CONTRACT_MULTIPLIER.get(symbol, 100000)
    enriched = []

    for _, t in trades_df.iterrows():
        entry_ts = t["entry_time"]
        idx = int(hist["timestamp"].searchsorted(entry_ts, side="right")) - 1
        if idx < 15:
            continue

        atr_bars = hist.iloc[idx - 15 : idx]
        atr, adx, di = compute_atr_wilder(atr_bars, period=14)
        if atr is None or atr <= 0:
            continue

        close_ago = atr_bars["close"].iloc[0]
        close_now = atr_bars["close"].iloc[-1]
        trend_pct = (close_now - close_ago) / close_ago if close_ago != 0 else 0

        market_regime = detect_regime(adx, trend_pct)

        volume = float(t["Volume"])
        profit = float(t["Profit"])
        risk_amount = atr * volume * multiplier

        r_multiple = round(profit / risk_amount, 4) if risk_amount > 0 else 0.0

        enriched.append(
            {
                "symbol": symbol,
                "r": r_multiple,
                "regime": "IMPORT",
                "direction": t["direction"],
                "profit": profit,
                "volume": volume,
                "atr_entry": round(atr, 4),
                "adx_entry": round(adx, 1),
                "market_regime": market_regime,
                "entry_time": entry_ts.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

    return enriched


def inject_into_state(all_enriched, state_path, dry_run=False):
    """Inject enriched trades into the OL state file."""
    if not all_enriched:
        print("  ❌ Rien à injecter.")
        return False

    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    # Ensure online_history exists
    if "online_history" not in state:
        state["online_history"] = {}

    # Count trades per symbol before
    before_counts = {}
    for e in all_enriched:
        sym = e["symbol"]
        if sym not in before_counts:
            before_counts[sym] = len(state["online_history"].get(sym, []))

    # Add trades
    added_count = 0
    for e in all_enriched:
        sym = e["symbol"]
        if sym not in state["online_history"]:
            state["online_history"][sym] = []
        state["online_history"][sym].append(
            {
                "r": e["r"],
                "regime": e["regime"],
                "direction": e["direction"],
                "market_regime": e["market_regime"],
                "entry_time": e["entry_time"],
            }
        )
        added_count += 1

    if dry_run:
        print(f"\n  💡 DRY RUN: {added_count} trades seraient injectés")
        undoed = []
        for sym in sorted(before_counts.keys()):
            n = len(state["online_history"].get(sym, []))
            added = n - before_counts.get(sym, 0)
            if added:
                undoed.append(f"{sym}: +{added}")
        for s in undoed:
            print(f"    ↪ {s}")
        return False

    # Atomic write
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(state_path)

    print(f"\n✅ Injection réussie dans {state_path.name} !")
    for sym in sorted(before_counts.keys()):
        n = len(state["online_history"].get(sym, []))
        added = n - before_counts.get(sym, 0)
        if added:
            print(f"    {sym}: {before_counts.get(sym, 0)} → {n} (+{added})")

    return True


def process_file(path, all_enriched, symbol_set):
    """Process a single ReportHistory file."""
    print(f"\n📂 {path.name}...")
    trades = parse_excel(path)
    if trades is None or len(trades) == 0:
        return

    print(f"    {len(trades)} trades valides (Volume+Profit numériques)")
    for sym in sorted(trades["Symbole"].unique()):
        n = len(trades[trades["Symbole"] == sym])
        print(f"      {sym}: {n}")

    for sym in sorted(trades["Symbole"].unique()):
        hist = load_parquet(sym)
        if hist is None:
            print(f"    ⚠️  {sym}: pas de fichier H1 → ignoré")
            continue

        sym_trades = trades[trades["Symbole"] == sym].sort_values("entry_time")
        enriched = enricher(sym_trades, sym, hist)
        symbol_set.add(sym)

        rr = np.array([e["r"] for e in enriched])
        wins = int(np.sum(rr > 0))
        total_pnl = sum(e["profit"] for e in enriched)

        print(
            f"    {sym}: {len(enriched)} trades enrichis (WR={wins / len(enriched) * 100:.1f}% PnL=${total_pnl:.0f})"
            if enriched
            else f"    {sym}: 0 trades enrichis (pas assez de données H1)"
        )

        all_enriched.extend(enriched)


def print_summary(all_enriched):
    """Print a summary of all enriched trades."""
    if not all_enriched:
        return

    print(f"\n{'═' * 60}")
    print(f"  RÉSUMÉ — {len(all_enriched)} trades enrichis au total")
    print(f"{'═' * 60}")

    rr_all = np.array([e["r"] for e in all_enriched])
    print(
        f"  R-multiple: Q1={np.percentile(rr_all, 25):.3f}"
        f" median={np.median(rr_all):.3f}"
        f" Q3={np.percentile(rr_all, 75):.3f}"
    )
    print(f"  Wins: {int(np.sum(rr_all > 0))} / {len(rr_all)} (WR={np.mean(rr_all > 0) * 100:.1f}%)")
    print(f"  Total PnL: ${sum(e['profit'] for e in all_enriched):.2f}")

    by_sym = {}
    for e in all_enriched:
        by_sym.setdefault(e["symbol"], []).append(e)

    for sym in sorted(by_sym.keys()):
        trades = by_sym[sym]
        rr = np.array([t["r"] for t in trades])
        wins = int(np.sum(rr > 0))
        pnl = sum(t["profit"] for t in trades)
        n_buy = sum(1 for t in trades if t["direction"] == "BUY")
        n_sell = sum(1 for t in trades if t["direction"] == "SELL")
        print(f"\n  {sym}:")
        print(f"    {len(trades)} trades, WR={wins / len(trades) * 100:.1f}%, PnL=${pnl:.0f}")
        print(f"    {n_buy} BUY / {n_sell} SELL")
        print(f"    avg R={np.mean(rr):.3f}, med R={np.median(rr):.3f}")


def main():
    parser = argparse.ArgumentParser(description="Inject ReportHistory trades into OnlineLearner")
    parser.add_argument("--all", action="store_true", help="Process all files in data/reporthistory/")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans écriture")
    args = parser.parse_args()

    print("=" * 60)
    print("  FEED EXCEL TRADES → ONLINE LEARNER")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    if not STATE_PATH.exists():
        print(f"\n❌ State file introuvable: {STATE_PATH}")
        print("   Exécute d'abord le robot pour générer le fichier.")
        sys.exit(1)

    # Collect files to process
    files_to_process = []
    root_file = ROOT / "ReportHistory-1513621052.xlsx"
    if root_file.exists():
        files_to_process.append(root_file)

    if args.all:
        if REPORTHISTORY_DIR.exists():
            for f in sorted(REPORTHISTORY_DIR.glob("ReportHistory-*.xlsx")):
                if f not in files_to_process:
                    files_to_process.append(f)
        print(f"\n📋 {len(files_to_process)} fichiers à traiter")

    all_enriched = []
    symbol_set = set()

    for fpath in files_to_process:
        process_file(fpath, all_enriched, symbol_set)

    if not all_enriched:
        print("\n❌ Aucun trade enrichi — vérifie les fichiers parquet H1.")
        return

    print_summary(all_enriched)

    print(f"\n{'─' * 60}")
    inject_into_state(all_enriched, STATE_PATH, dry_run=args.dry_run)

    if not args.dry_run:
        # Show final state
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        print(f"\n📈 État final OnlineLearner:")
        for sym in sorted(symbol_set):
            trades = state.get("online_history", {}).get(sym, [])
            if trades:
                rr = [t["r"] for t in trades]
                wins = sum(1 for r in rr if r > 0)
                print(f"    {sym}: {len(trades)} trades, WR={wins / len(trades) * 100:.1f}%, avg_r={np.mean(rr):.3f}")
        print(f"\n    adapted_params:")
        for sym, params in state.get("adapted_params", {}).items():
            print(f"      {sym}: thresh={params['thresh']:.1f} risk_mult={params['risk_mult']:.3f}")

    print(f"\n✅ Terminé.")


if __name__ == "__main__":
    main()
