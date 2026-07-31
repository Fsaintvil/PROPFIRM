"""
Scan SL/TP pour les paires forex — Solution A sans trailing
Trouve la meilleure combinaison (SL×ATR, TP×ATR) pour chaque paire.

Usage:
    python scripts/scan_forex_sltp.py
"""

import json, os, sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

RISK_PER_TRADE = 0.008
INITIAL_BALANCE = 200_000.0
MIN_RR = 1.5
SLIPPAGE_PCT = 0.0002
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
TIMEFRAME = "H4"

# Meilleurs thresholds par symbole (issus du scan adaptatif)
BEST_THRESHOLDS = {
    "EURUSD": 3.5,
    "GBPUSD": 2.5,
    "USDJPY": 1.5,
    "USDCAD": 3.5,
    "AUDUSD": 2.5,
    "NZDUSD": 5.0,
    "USDCHF": 3.0,
}

# SL/TP combinations à tester
SL_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0]
TP_VALUES = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0]

SYMBOL_COSTS = {
    "EURUSD": (1.5, 0.0001, 10.0),
    "GBPUSD": (1.5, 0.0001, 10.0),
    "USDJPY": (1.5, 0.01, 1.0),
    "USDCAD": (1.5, 0.0001, 10.0),
    "AUDUSD": (1.5, 0.0001, 10.0),
    "NZDUSD": (1.5, 0.0001, 10.0),
    "USDCHF": (1.5, 0.0001, 10.0),
}

TARGET_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"]


def atr(high, low, close, period=14):
    if len(high) < period + 1:
        return None
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr_vals = np.full(len(tr), np.nan)
    atr_vals[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period
    return atr_vals


class BacktestSLTP:
    def __init__(self, symbol, data, threshold, sl_atr, tp_atr):
        self.symbol = symbol
        self.data = data
        self.threshold = threshold
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self._costs = SYMBOL_COSTS.get(symbol, (2.0, 0.01, 1.0))
        self.trades = []
        self.balance = INITIAL_BALANCE
        self.peak_balance = INITIAL_BALANCE
        self.max_dd = 0.0

    def run(self):
        df = self.data.copy()
        if len(df) < 100:
            return self
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        dates = df["time"].values if "time" in df.columns else np.arange(len(df))
        atr_vals = atr(high, low, close, 14)
        in_position = False
        entry_price = 0
        entry_idx = 0
        direction = 0
        sl, tp = 0.0, 0.0

        for i in range(60, len(df)):
            if atr_vals is None or np.isnan(atr_vals[i]) or atr_vals[i] <= 0:
                continue
            current_atr = atr_vals[i]

            if in_position:
                cp = close[i]
                if direction == 0:  # BUY
                    if cp <= sl:
                        pnl = -(entry_price - cp) - self._cost(entry_price, cp)
                        self._close(pnl, dates[entry_idx], dates[i], "SL")
                        in_position = False
                        continue
                    elif cp >= tp:
                        pnl = (cp - entry_price) - self._cost(entry_price, cp)
                        self._close(pnl, dates[entry_idx], dates[i], "TP")
                        in_position = False
                        continue
                else:  # SELL
                    if cp >= sl:
                        pnl = -(cp - entry_price) - self._cost(entry_price, cp)
                        self._close(pnl, dates[entry_idx], dates[i], "SL")
                        in_position = False
                        continue
                    elif cp <= tp:
                        pnl = (entry_price - cp) - self._cost(entry_price, cp)
                        self._close(pnl, dates[entry_idx], dates[i], "TP")
                        in_position = False
                        continue

            if not in_position and i >= 20:
                mom = close[i] - close[i - 20]
                if np.isnan(mom) or np.isinf(mom):
                    continue
                threshold_value = self.threshold * current_atr
                if abs(mom) < threshold_value:
                    continue
                rr = (self.tp_atr * current_atr) / (self.sl_atr * current_atr)
                if rr < MIN_RR:
                    continue
                entry_price = close[i]
                entry_idx = i
                self.balance -= self._cost(entry_price, entry_price)
                if mom > 0:
                    direction = 0
                    sl = entry_price - self.sl_atr * current_atr
                    tp = entry_price + self.tp_atr * current_atr
                else:
                    direction = 1
                    sl = entry_price + self.sl_atr * current_atr
                    tp = entry_price - self.tp_atr * current_atr
                in_position = True

        if in_position:
            cp = close[-1]
            pnl = (cp - entry_price) if direction == 0 else (entry_price - cp)
            pnl -= self._cost(entry_price, cp)
            self._close(pnl, dates[entry_idx], dates[-1], "EOF")
        return self

    def _cost(self, entry, exit_):
        sp, ps, pv = self._costs
        spread = sp * ps * pv * RISK_PER_TRADE * INITIAL_BALANCE / 100
        slip_entry = SLIPPAGE_PCT * entry * RISK_PER_TRADE
        slip_exit = SLIPPAGE_PCT * exit_ * RISK_PER_TRADE
        return spread + slip_entry + slip_exit

    def _close(self, pnl, open_dt, close_dt, reason):
        self.balance += pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        self.max_dd = max(self.max_dd, dd)
        self.trades.append({"pnl": round(pnl, 2), "reason": reason, "dd_pct": round(dd, 2)})

    def results(self):
        if not self.trades:
            return {"trades": 0, "error": "no trades"}
        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        return {
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades) * 100, 1),
            "total_pnl": round(sum(t["pnl"] for t in self.trades), 2),
            "profit_factor": round(abs(sum(t["pnl"] for t in wins) / max(sum(abs(t["pnl"]) for t in losses), 0.01)), 2)
            if losses
            else float("inf"),
            "max_dd_pct": round(self.max_dd, 2),
        }


def load_data(symbol, tf="H4"):
    path = DATA_DIR / f"{symbol}_{tf}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        rename = {}
        for col in df.columns:
            cl = col.lower()
            if cl in ("time", "date", "timestamp", "datetime"):
                rename[col] = "time"
            elif cl in ("close", "c"):
                rename[col] = "close"
            elif cl in ("high", "h"):
                rename[col] = "high"
            elif cl in ("low", "l"):
                rename[col] = "low"
        if rename:
            df = df.rename(columns=rename)
        if {"high", "low", "close"} - set(df.columns):
            return None
        return df
    except Exception:
        return None


def ftmo_grade(r):
    if r.get("trades", 0) < 10:
        return "❌ no trades"
    if r.get("max_dd_pct", 100) > 10:
        return "❌ DD>10%"
    pf = r.get("profit_factor", 0)
    if pf >= 1.3:
        return "✅✅ FTMO-safe"
    if pf >= 1.1:
        return "✅ FTMO-border"
    if pf >= 1.0:
        return "⚠️ PF=1.0"
    return f"❌ PF={pf}"


if __name__ == "__main__":
    print("═" * 90)
    print("  SCAN SL/TP — FOREX MAJORS H4 (Solution A, sans trailing)")
    print("═" * 90)
    print(f"  SL: {', '.join(f'{v}×ATR' for v in SL_VALUES)}")
    print(f"  TP: {', '.join(f'{v}×ATR' for v in TP_VALUES)}")
    print(f"  Thresholds: meilleur par symbole (issu du scan adaptatif)")
    print("═" * 90)

    all_results = {}
    viable_found = []

    for sym in TARGET_SYMBOLS:
        threshold = BEST_THRESHOLDS.get(sym, 2.5)
        print(f"\n{'─' * 90}")
        print(f"📊 {sym} — threshold={threshold}×ATR")
        df = load_data(sym)
        if df is None:
            print(f"  ❌ Données manquantes")
            continue

        sym_results = []
        for sl in SL_VALUES:
            for tp in TP_VALUES:
                rr = tp / sl
                if rr < MIN_RR:
                    continue
                bt = BacktestSLTP(sym, df, threshold, sl, tp)
                bt.run()
                r = bt.results()
                r["sl_atr"] = sl
                r["tp_atr"] = tp
                r["rr"] = round(rr, 2)
                r["grade"] = ftmo_grade(r)
                sym_results.append(r)

                # Afficher seulement si prometteur ou remarquable
                if r.get("profit_factor", 0) >= 0.8 or r.get("trades", 0) == 0:
                    status = r["grade"]
                    print(
                        f"  SL={sl:.1f} TP={tp:.1f} RR={rr:.1f} → {r['trades']:4d}t | WR={r['win_rate']:5.1f}% | PF={r['profit_factor']:.2f} | DD={r['max_dd_pct']:5.2f}% | ${r['total_pnl']:>8.0f} {status}"
                    )

        all_results[sym] = sym_results

        # Meilleure combinaison
        viable = [
            r
            for r in sym_results
            if r.get("profit_factor", 0) >= 1.0 and r.get("max_dd_pct", 100) < 10 and r.get("trades", 0) >= 10
        ]
        if viable:
            best = max(viable, key=lambda r: r["profit_factor"] * r["trades"])
            print(
                f"\n  👑 Meilleur {sym}: SL={best['sl_atr']}× TP={best['tp_atr']}× PF={best['profit_factor']} DD={best['max_dd_pct']}%"
            )
            viable_found.append(best)

    # Rapport final
    print("\n\n" + "█" * 90)
    print("  RAPPORT FINAL — FOREX VIABLES FTMO")
    print("█" * 90)

    if viable_found:
        viable_found.sort(key=lambda r: r["profit_factor"], reverse=True)
        print(
            f"\n  {'#':<3} {'Symbole':<12} {'Thresh':<8} {'SL':<6} {'TP':<6} {'RR':<6} {'Trades':<8} {'WR':<7} {'PF':<7} {'DD%':<7} {'PnL':<10}"
        )
        print(f"  {'—' * 75}")
        for i, r in enumerate(viable_found, 1):
            print(
                f"  {i:<3} {r['symbol']:<12} {BEST_THRESHOLDS.get(r['symbol'], 2.5):.1f}×{'':<4} {r['sl_atr']:<6} {r['tp_atr']:<6} {r['rr']:<6} {r['trades']:<8} {r['win_rate']:<7} {r['profit_factor']:<7} {r['max_dd_pct']:<7} ${r['total_pnl']:<8}"
            )
    else:
        print(f"\n  ❌ Aucune paire forex FTMO-viable trouvée.")
        print(f"     Meilleur PF par symbole:")
        for sym in TARGET_SYMBOLS:
            results = all_results.get(sym, [])
            if results:
                best = max(results, key=lambda r: r.get("profit_factor", 0))
                print(
                    f"     {sym:<10}: PF={best['profit_factor']} (SL={best['sl_atr']}× TP={best['tp_atr']}×) — {best.get('grade', 'N/A')}"
                )

    # Sauvegarder
    out_path = Path(__file__).resolve().parent.parent / "runtime" / "scan_forex_sltp.json"
    with open(out_path, "w") as f:
        json.dump({"results_by_symbol": all_results, "viable": viable_found}, f, indent=2, default=str)
    print(f"\n📁 Résultats: {out_path}")
    print("█" * 90)
