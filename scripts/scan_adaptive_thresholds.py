"""
Scan adaptatif des thresholds par symbole — Solution A sans trailing
Cherche le meilleur threshold (1.5× à 5.0×ATR) pour chaque symbole.

Usage:
    python scripts/scan_adaptive_thresholds.py
"""

import json, os, sys, time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ═══════════════════════════════════════════════════════════════════════════════
# PARAMÈTRES FIXES (Solution A — sauf threshold qui varie)
# ═══════════════════════════════════════════════════════════════════════════════
SL_ATR = 1.5
TP_ATR = 6.0
MIN_RR = 2.0
RISK_PER_TRADE = 0.008
INITIAL_BALANCE = 200_000.0
NO_TRAILING = True
NO_PARTIAL_TP = True

# Thresholds à tester
THRESHOLDS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]

# Symboles à tester
TARGET_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "USDCHF",
    "XAUUSD",
    "BTCUSD",
    "USOIL.cash",
    "US100.cash",
]

TIMEFRAME = "H4"

SYMBOL_COSTS = {
    "EURUSD": (1.5, 0.0001, 10.0),
    "GBPUSD": (1.5, 0.0001, 10.0),
    "USDJPY": (1.5, 0.01, 1.0),
    "USDCAD": (1.5, 0.0001, 10.0),
    "USDCHF": (1.5, 0.0001, 10.0),
    "AUDUSD": (1.5, 0.0001, 10.0),
    "NZDUSD": (1.5, 0.0001, 10.0),
    "XAUUSD": (5.0, 0.01, 1.0),
    "BTCUSD": (15.0, 0.01, 1.0),
    "USOIL.cash": (5.0, 0.01, 1.0),
    "US100.cash": (2.0, 0.01, 1.0),
}

SLIPPAGE_PCT = 0.0002
COMMISSION_PER_LOT = 0.0
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
MIN_BARS = 100


# ═══════════════════════════════════════════════════════════════════════════════
# INDICATEURS
# ═══════════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE (paramétrisé par threshold)
# ═══════════════════════════════════════════════════════════════════════════════


class BacktestScan:
    def __init__(self, symbol: str, data: pd.DataFrame, threshold: float):
        self.symbol = symbol
        self.data = data
        self.threshold = threshold
        self._costs = SYMBOL_COSTS.get(symbol, (2.0, 0.01, 1.0))
        self.trades = []
        self.balance = INITIAL_BALANCE
        self.peak_balance = INITIAL_BALANCE
        self.max_dd = 0.0

    def run(self):
        df = self.data.copy()
        if len(df) < MIN_BARS:
            return self

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        dates = df["time"].values if "time" in df.columns else np.arange(len(df))

        atr_vals = atr(high, low, close, 14)
        in_position = False
        entry_price = 0
        entry_idx = 0
        position_direction = 0
        stop_loss = 0.0
        take_profit = 0.0

        for i in range(60, len(df)):
            if atr_vals is None or np.isnan(atr_vals[i]) or atr_vals[i] <= 0:
                continue
            current_atr = atr_vals[i]

            if in_position:
                current_price = close[i]
                if position_direction == 0:  # BUY
                    if current_price <= stop_loss:
                        pnl = -(entry_price - current_price) - self._cost_total(entry_price, current_price)
                        self._close_trade(pnl, dates[entry_idx], dates[i], "SL")
                        in_position = False
                        continue
                    elif current_price >= take_profit:
                        pnl = (current_price - entry_price) - self._cost_total(entry_price, current_price)
                        self._close_trade(pnl, dates[entry_idx], dates[i], "TP")
                        in_position = False
                        continue
                else:  # SELL
                    if current_price >= stop_loss:
                        pnl = -(current_price - entry_price) - self._cost_total(entry_price, current_price)
                        self._close_trade(pnl, dates[entry_idx], dates[i], "SL")
                        in_position = False
                        continue
                    elif current_price <= take_profit:
                        pnl = (entry_price - current_price) - self._cost_total(entry_price, current_price)
                        self._close_trade(pnl, dates[entry_idx], dates[i], "TP")
                        in_position = False
                        continue

            if not in_position and i >= 20:
                mom = close[i] - close[i - 20]
                if np.isnan(mom) or np.isinf(mom):
                    continue
                mom_abs = abs(mom)
                threshold_value = self.threshold * current_atr

                if mom_abs < threshold_value:
                    continue

                sl_dist = SL_ATR * current_atr
                tp_dist = TP_ATR * current_atr
                rr = tp_dist / sl_dist if sl_dist > 0 else 0
                if rr < MIN_RR:
                    continue

                entry_price = close[i]
                entry_idx = i
                entry_cost = self._cost_entry(entry_price)
                self.balance -= entry_cost

                if mom > 0:  # BUY
                    position_direction = 0
                    stop_loss = entry_price - sl_dist
                    take_profit = entry_price + tp_dist
                else:  # SELL
                    position_direction = 1
                    stop_loss = entry_price + sl_dist
                    take_profit = entry_price - tp_dist

                in_position = True

        # Fermer si encore ouvert
        if in_position:
            current_price = close[-1]
            if position_direction == 0:
                pnl = current_price - entry_price
            else:
                pnl = entry_price - current_price
            pnl = pnl - self._cost_exit(entry_price, current_price)
            self._close_trade(pnl, dates[entry_idx], dates[-1], "EOF")

        return self

    def _cost_entry(self, price):
        spread_pips, pip_size, point_value = self._costs
        spread_cost = spread_pips * pip_size * point_value * RISK_PER_TRADE * INITIAL_BALANCE / 100
        slippage = SLIPPAGE_PCT * price * RISK_PER_TRADE
        return spread_cost + slippage

    def _cost_exit(self, entry_price, exit_price):
        return SLIPPAGE_PCT * exit_price * RISK_PER_TRADE

    def _cost_total(self, entry_price, exit_price):
        return self._cost_entry(entry_price) + self._cost_exit(entry_price, exit_price)

    def _close_trade(self, pnl, open_date, close_date, reason):
        self.balance += pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        self.max_dd = max(self.max_dd, current_dd)
        self.trades.append(
            {
                "open_time": str(open_date),
                "close_time": str(close_date),
                "pnl": round(pnl, 2),
                "reason": reason,
                "balance": round(self.balance, 2),
                "dd_pct": round(current_dd, 2),
            }
        )

    def results(self):
        if not self.trades:
            return {"symbol": self.symbol, "threshold": self.threshold, "trades": 0, "error": "no trades"}
        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.trades)
        return {
            "symbol": self.symbol,
            "timeframe": TIMEFRAME,
            "threshold": self.threshold,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades) * 100, 1) if self.trades else 0,
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(abs(sum(t["pnl"] for t in wins) / max(sum(abs(t["pnl"]) for t in losses), 0.01)), 2)
            if losses
            else float("inf"),
            "max_dd_pct": round(self.max_dd, 2),
            "final_balance": round(self.balance, 2),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        }


def load_data(symbol: str, tf: str = "H4") -> pd.DataFrame | None:
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
            elif cl in ("open", "o"):
                rename[col] = "open"
            elif cl in ("high", "h"):
                rename[col] = "high"
            elif cl in ("low", "l"):
                rename[col] = "low"
            elif cl in ("close", "c"):
                rename[col] = "close"
            elif cl in ("volume", "vol", "v", "tickvolume"):
                rename[col] = "volume"
        if rename:
            df = df.rename(columns=rename)
        required = {"high", "low", "close"}
        if required - set(df.columns):
            return None
        return df
    except Exception:
        return None


def ftmo_grade(r: dict) -> str:
    if r.get("trades", 0) < 10:
        return "❌ no trades"
    if r.get("max_dd_pct", 100) > 10:
        return "❌ DD>10%"
    if r.get("profit_factor", 0) < 1.0:
        return f"❌ PF={r['profit_factor']}"
    pf = r.get("profit_factor", 0)
    if pf >= 1.3:
        return "✅✅ FTMO-safe"
    if pf >= 1.1:
        return "✅ FTMO-border"
    return "⚠️ PF faible"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 80)
    print("  SCAN ADAPTATIF DES THRESHOLDS PAR SYMBOLE — H4 Solution A")
    print("═" * 80)
    print(f"\n  Paramètres: SL={SL_ATR}×ATR, TP={TP_ATR}×ATR, RR≥{MIN_RR}")
    print(f"  Risk: {RISK_PER_TRADE * 100:.1f}%/trade, Balance initiale: ${INITIAL_BALANCE:,.0f}")
    print(f"  Thresholds testés: {', '.join(f'{t}×' for t in THRESHOLDS)}")
    print(f"  Symboles ({len(TARGET_SYMBOLS)}): {', '.join(TARGET_SYMBOLS)}")
    print("═" * 80)

    all_results = {}
    best_results = {}

    for sym in TARGET_SYMBOLS:
        print(f"\n{'─' * 70}")
        print(f"📊 {sym} — scan thresholds...")
        df = load_data(sym, TIMEFRAME)
        if df is None:
            print(f"  ❌ Données manquantes")
            all_results[sym] = [{"symbol": sym, "threshold": t, "trades": 0, "error": "no data"} for t in THRESHOLDS]
            continue

        sym_results = []
        for thresh in THRESHOLDS:
            bt = BacktestScan(sym, df, thresh)
            bt.run()
            r = bt.results()
            r["grade"] = ftmo_grade(r)
            sym_results.append(r)

            status = f"✅ {r['grade']}" if "✅" in r.get("grade", "") else f"  {r['grade']}"
            print(
                f"  {thresh:.1f}×ATR → {r['trades']:4d} trades | WR={r['win_rate']:5.1f}% | PF={r['profit_factor']:.2f} | DD={r['max_dd_pct']:5.2f}% | PnL=${r['total_pnl']:>8.0f} {status}"
            )

        all_results[sym] = sym_results

        # Meilleur threshold pour ce symbole (critère: PF max, avec DD < 10%)
        viable = [
            r
            for r in sym_results
            if r.get("trades", 0) >= 10 and r.get("max_dd_pct", 100) < 10 and r.get("profit_factor", 0) >= 1.0
        ]
        if viable:
            best = max(viable, key=lambda r: r["profit_factor"] * r["trades"])
            best_results[sym] = best
            print(
                f"\n  👑 Meilleur pour {sym}: threshold={best['threshold']}×ATR → PF={best['profit_factor']}, DD={best['max_dd_pct']}%, WR={best['win_rate']}%, {best['trades']} trades"
            )

    # ═════════════════════════════════════════════════════════════════════════
    # RAPPORT FINAL
    # ═════════════════════════════════════════════════════════════════════════
    print("\n\n" + "█" * 80)
    print("  RAPPORT FINAL — MEILLEURS THRESHOLDS PAR SYMBOLE")
    print("█" * 80)
    print(
        f"\n  {'Symbole':<15} {'Threshold':<10} {'Trades':<8} {'WR':<7} {'PF':<7} {'DD%':<7} {'PnL':<12} {'Grade':<20}"
    )
    print("  " + "-" * 80)

    viables = []
    for sym in TARGET_SYMBOLS:
        if sym in best_results:
            r = best_results[sym]
            print(
                f"  {r['symbol']:<15} {r['threshold']:.1f}×ATR{'':<5} {r['trades']:<8} {r['win_rate']:<7} {r['profit_factor']:<7} {r['max_dd_pct']:<7} ${r['total_pnl']:<9} {r['grade']}"
            )
            viables.append(r)
        else:
            # Montrer le meilleur même si non viable
            sym_res = all_results.get(sym, [])
            if sym_res:
                best_of_bad = max(sym_res, key=lambda r: r.get("profit_factor", 0) if r.get("trades", 0) > 0 else -1)
                print(
                    f"  {sym:<15} {'—':<10} {best_of_bad.get('trades', 0):<8} {best_of_bad.get('win_rate', 0):<7} {best_of_bad.get('profit_factor', 0):<7} {best_of_bad.get('max_dd_pct', 0):<7} ${best_of_bad.get('total_pnl', 0):<9} ❌ aucun viable"
                )

    print("  " + "-" * 80)

    # Classement des viables par PF
    viables.sort(key=lambda r: r["profit_factor"], reverse=True)

    if viables:
        print(f"\n{'=' * 60}")
        print(f"  🏆 TOP SYMBOLES FTMO-VIABLES (classés par PF)")
        print(f"{'=' * 60}")
        print(
            f"\n  {'#':<3} {'Symbole':<15} {'Threshold':<10} {'PF':<7} {'DD%':<7} {'WR':<7} {'Trades':<8} {'PnL':<12}"
        )
        print(f"  {'—' * 60}")
        for i, r in enumerate(viables, 1):
            mark = "🏆" if i == 1 else "✅"
            print(
                f"  {i:<3} {r['symbol']:<15} {r['threshold']:.1f}×ATR{'':<5} {r['profit_factor']:<7} {r['max_dd_pct']:<7} {r['win_rate']:<7} {r['trades']:<8} ${r['total_pnl']:<9} {mark}"
            )
    else:
        print(f"\n  ❌ Aucun symbole FTMO-viable trouvé avec ces paramètres.")

    # Sauvegarder
    out_path = Path(__file__).resolve().parent.parent / "runtime" / "scan_adaptive_thresholds.json"
    output = {
        "params": {
            "sl_atr": SL_ATR,
            "tp_atr": TP_ATR,
            "min_rr": MIN_RR,
            "risk": RISK_PER_TRADE,
            "no_trailing": NO_TRAILING,
        },
        "thresholds_tested": THRESHOLDS,
        "results_by_symbol": {sym: all_results[sym] for sym in TARGET_SYMBOLS},
        "best_per_symbol": best_results,
        "viable_symbols": [r for r in viables],
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n📁 Résultats complets: {out_path}")
    print("█" * 80)
