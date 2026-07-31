"""
Backtest H4 — Solution A (threshold 4.0×ATR, SL 1.5×ATR, TP 6.0×ATR, no trailing)
sur les paires demandées : Forex majors, XAUUSD, BTCUSD, USOIL.cash, US100.cash

Usage:
    python scripts/backtest_h4_solution_a.py
"""

import json, os, sys, time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ═══════════════════════════════════════════════════════════════════════════════
# PARAMÈTRES SOLUTION A (appliqués à TOUS les symboles testés)
# ═══════════════════════════════════════════════════════════════════════════════
THRESHOLD = 4.0  # ×ATR (unifié trending/ranging)
SL_ATR = 1.5  # ×ATR
TP_ATR = 6.0  # ×ATR (RR = 4.0)
MIN_RR = 2.0
RISK_PER_TRADE = 0.008  # 0.80%
INITIAL_BALANCE = 200_000.0
NO_TRAILING = True  # Solution A: pas de trailing
NO_PARTIAL_TP = True  # Solution A: pas de partial TP

# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOLES À TESTER
# ═══════════════════════════════════════════════════════════════════════════════
TARGET_SYMBOLS = [
    # Forex majors
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "USDCHF",
    # Commodités + Crypto + Indices demandés
    "XAUUSD",
    "BTCUSD",
    "USOIL.cash",
    "US100.cash",
]

TIMEFRAME = "H4"

# Coûts par symbole (spread_pips, pip_size, contract_value_per_point)
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

SLIPPAGE_PCT = 0.0002  # 0.02% slippage
COMMISSION_PER_LOT = 0.0  # commission fixe

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"
MIN_BARS = 100  # minimum de barres pour backtest

# ═══════════════════════════════════════════════════════════════════════════════
# INDICATEURS (copie minimaliste de engine_simple.indicators)
# ═══════════════════════════════════════════════════════════════════════════════


def atr(high, low, close, period=14):
    if len(high) < period + 1 or len(low) < period + 1 or len(close) < period + 1:
        return None
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr_vals = np.full(len(tr), np.nan)
    atr_vals[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period
    return atr_vals


def ema(arr, period):
    if len(arr) < period:
        return np.full_like(arr, np.nan)
    result = np.full_like(arr, np.nan)
    alpha = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def adx(high, low, close, period=14):
    if len(high) < period * 2 or len(low) < period * 2 or len(close) < period * 2:
        return 0.0, 0.0, 0.0
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    up = high - np.roll(high, 1)
    down = np.roll(low, 1) - low
    up[0] = 0
    down[0] = 0
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr_val = np.full(len(tr), np.nan)
    atr_val[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr_val[i] = (atr_val[i - 1] * (period - 1) + tr[i]) / period
    plus_di = np.full(len(plus_dm), np.nan)
    minus_di = np.full(len(minus_dm), np.nan)
    dx = np.full(len(plus_dm), np.nan)
    plus_di_smooth = np.full(len(plus_dm), np.nan)
    minus_di_smooth = np.full(len(minus_dm), np.nan)
    plus_di_smooth[period - 1] = np.mean(plus_dm[:period])
    minus_di_smooth[period - 1] = np.mean(minus_dm[:period])
    for i in range(period, len(plus_dm)):
        plus_di_smooth[i] = (plus_di_smooth[i - 1] * (period - 1) + plus_dm[i]) / period
        minus_di_smooth[i] = (minus_di_smooth[i - 1] * (period - 1) + minus_dm[i]) / period
        if atr_val[i] and atr_val[i] > 0:
            plus_di[i] = 100 * plus_di_smooth[i] / atr_val[i]
            minus_di[i] = 100 * minus_di_smooth[i] / atr_val[i]
            if plus_di[i] + minus_di[i] > 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / (plus_di[i] + minus_di[i])
    adx_val = np.full(len(dx), np.nan)
    valid = ~np.isnan(dx)
    if np.sum(valid) >= period:
        first_valid = np.where(valid)[0][0]
        idx_start = first_valid + period - 1
        if idx_start < len(adx_val):
            adx_val[idx_start] = np.nanmean(dx[valid][:period])
            for i in range(idx_start + 1, len(adx_val)):
                if not np.isnan(dx[i]):
                    adx_val[i] = (adx_val[i - 1] * (period - 1) + dx[i]) / period
    last_adx = float(adx_val[valid][-1]) if np.sum(valid) > 0 else 0.0
    last_pdi = float(plus_di[valid][-1]) if np.sum(valid) > 0 else 0.0
    last_mdi = float(minus_di[valid][-1]) if np.sum(valid) > 0 else 0.0
    return last_adx, last_pdi, last_mdi


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


class BacktestH4:
    def __init__(self, symbol: str, data: pd.DataFrame):
        self.symbol = symbol
        self.data = data
        self._costs = SYMBOL_COSTS.get(symbol, (2.0, 0.01, 1.0))

        # Résultats
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

        # Calcul ATR et ADX
        atr_vals = atr(high, low, close, 14)
        last_atr = None
        in_position = False
        entry_price = 0
        entry_idx = 0
        entry_side = 0  # 0=BUY, 1=SELL
        stop_loss = 0.0
        take_profit = 0.0
        position_direction = 0
        bars_since_entry = 0

        # Stats DD tracking
        dd_start_idx = 0

        for i in range(60, len(df)):  # Démarrer après assez de données
            if atr_vals is not None and not np.isnan(atr_vals[i]) and atr_vals[i] > 0:
                last_atr = atr_vals[i]
            else:
                continue

            current_atr = last_atr

            # Gérer la position ouverte
            if in_position:
                bars_since_entry += 1
                current_price = close[i]

                # Vérifier SL/TP
                if position_direction == 0:  # BUY
                    if current_price <= stop_loss:
                        # SL hit
                        entry_cost = self._cost_open(current_price, entry_price)
                        exit_cost = self._cost_close(entry_price, current_price)
                        pnl = -(entry_price - current_price) - exit_cost - entry_cost  # perte
                        self._close_trade(pnl, dates[entry_idx], dates[i], "SL")
                        in_position = False
                        continue
                    elif current_price >= take_profit:
                        # TP hit
                        entry_cost = self._cost_open(current_price, entry_price)
                        exit_cost = self._cost_close(entry_price, current_price)
                        pnl = (current_price - entry_price) - exit_cost - entry_cost
                        self._close_trade(pnl, dates[entry_idx], dates[i], "TP")
                        in_position = False
                        continue
                else:  # SELL
                    if current_price >= stop_loss:
                        entry_cost = self._cost_open(current_price, entry_price)
                        exit_cost = self._cost_close(entry_price, current_price)
                        pnl = -(current_price - entry_price) - exit_cost - entry_cost
                        self._close_trade(pnl, dates[entry_idx], dates[i], "SL")
                        in_position = False
                        continue
                    elif current_price <= take_profit:
                        entry_cost = self._cost_open(current_price, entry_price)
                        exit_cost = self._cost_close(entry_price, current_price)
                        pnl = (entry_price - current_price) - exit_cost - entry_cost
                        self._close_trade(pnl, dates[entry_idx], dates[i], "TP")
                        in_position = False
                        continue

            # Pas de position → chercher un signal
            if not in_position and i >= 20:
                # MOM20x3 signal
                mom = close[i] - close[i - 20]
                if np.isnan(mom) or np.isinf(mom):
                    continue

                mom_abs = abs(mom)
                threshold_value = THRESHOLD * current_atr

                if mom_abs < threshold_value:
                    continue  # pas de signal

                # Calculer ADX pour le filtre
                adx_val, pdi, mdi = adx(
                    high[max(0, i - 40) : i + 1], low[max(0, i - 40) : i + 1], close[max(0, i - 40) : i + 1], 14
                )

                # Vérifier RR min
                sl_dist = SL_ATR * current_atr
                tp_dist = TP_ATR * current_atr
                rr = tp_dist / sl_dist if sl_dist > 0 else 0
                if rr < MIN_RR:
                    continue

                # Ouvrir position
                entry_price = close[i]
                entry_idx = i
                entry_cost = self._cost_open(entry_price, entry_price)
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
                bars_since_entry = 0

        # Fermer la dernière position si encore ouverte
        if in_position:
            current_price = close[-1]
            if position_direction == 0:  # BUY
                pnl = current_price - entry_price
            else:
                pnl = entry_price - current_price
            exit_cost = self._cost_close(entry_price, current_price)
            pnl = pnl - exit_cost
            self._close_trade(pnl, dates[entry_idx], dates[-1], "EOF")

        return self

    def _cost_open(self, price, entry_price):
        spread_pips, pip_size, point_value = self._costs
        spread_cost = spread_pips * pip_size * point_value * RISK_PER_TRADE * INITIAL_BALANCE / 100
        slippage = SLIPPAGE_PCT * price * RISK_PER_TRADE
        return spread_cost + slippage

    def _cost_close(self, entry_price, exit_price):
        slippage = SLIPPAGE_PCT * exit_price * RISK_PER_TRADE
        return slippage

    def _close_trade(self, pnl, open_date, close_date, reason):
        risk_amount = RISK_PER_TRADE * self.balance
        pnl_real = pnl  # en dollars simulés
        self.balance += pnl_real
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        current_dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        self.max_dd = max(self.max_dd, current_dd)

        self.trades.append(
            {
                "open_time": str(open_date),
                "close_time": str(close_date),
                "pnl": round(pnl_real, 2),
                "reason": reason,
                "balance": round(self.balance, 2),
                "dd_pct": round(current_dd, 2),
            }
        )

    def results(self):
        if not self.trades:
            return {"symbol": self.symbol, "trades": 0, "error": "no trades"}

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.trades)

        return {
            "symbol": self.symbol,
            "timeframe": TIMEFRAME,
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
            "avg_rr": round(
                abs(sum(t["pnl"] for t in wins) / len(wins)) / abs(sum(t["pnl"] for t in losses) / len(losses)), 2
            )
            if wins and losses
            else 0,
        }


def load_data(symbol: str, tf: str = "H4") -> pd.DataFrame | None:
    path = DATA_DIR / f"{symbol}_{tf}.parquet"
    if not path.exists():
        print(f"  ⚠️  Données manquantes: {path}")
        return None
    try:
        df = pd.read_parquet(path)
        # Normaliser les colonnes
        rename = {}
        for col in df.columns:
            col_lower = col.lower()
            if col_lower in ("time", "date", "timestamp", "datetime"):
                rename[col] = "time"
            elif col_lower in ("open", "o"):
                rename[col] = "open"
            elif col_lower in ("high", "h"):
                rename[col] = "high"
            elif col_lower in ("low", "l"):
                rename[col] = "low"
            elif col_lower in ("close", "c"):
                rename[col] = "close"
            elif col_lower in ("volume", "vol", "v", "tickvolume"):
                rename[col] = "volume"
        if rename:
            df = df.rename(columns=rename)
        required = {"high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            print(f"  ⚠️  Colonnes manquantes pour {symbol}: {missing}")
            return None
        print(f"  ✅ {symbol}: {len(df)} barres, {df['close'].min():.2f}-{df['close'].max():.2f}")
        return df
    except Exception as e:
        print(f"  ❌ Erreur chargement {symbol}: {e}")
        return None


def ftmo_grade(r: dict) -> str:
    """Évalue si un résultat est FTMO-viable."""
    if r.get("trades", 0) < 10:
        return "❌ Pas assez de trades"
    if r.get("max_dd_pct", 100) > 10:
        return "❌ DD > 10%"
    if r.get("profit_factor", 0) < 1.0:
        return "❌ PF < 1.0"
    if r.get("profit_factor", 0) >= 1.3:
        return "✅✅ FTMO-safe"
    if r.get("profit_factor", 0) >= 1.1:
        return "✅ FTMO-borderline"
    return "⚠️  PF faible"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 70)
    print("  BACKTEST H4 — SOLUTION A (thresh=4.0, SL=1.5, TP=6.0, no trailing)")
    print("═" * 70)
    print(f"\n  Risk: {RISK_PER_TRADE * 100:.1f}%/trade")
    print(f"  Threshold: {THRESHOLD}×ATR | SL: {SL_ATR}×ATR | TP: {TP_ATR}×ATR | RR min: {MIN_RR}")
    print(f"  Trailing: {'❌ NON' if NO_TRAILING else '✅ OUI'}")
    print(f"  Partial TP: {'❌ NON' if NO_PARTIAL_TP else '✅ OUI'}")
    print(f"\n  Symboles ({len(TARGET_SYMBOLS)}): {', '.join(TARGET_SYMBOLS)}")
    print("═" * 70)

    results = []
    for sym in TARGET_SYMBOLS:
        print(f"\n📊 {sym}...")
        df = load_data(sym, TIMEFRAME)
        if df is None:
            results.append({"symbol": sym, "trades": 0, "error": "no data"})
            continue

        bt = BacktestH4(sym, df)
        bt.run()
        r = bt.results()
        results.append(r)

        if r.get("error"):
            print(f"  ⚠️  {r['error']}")
        else:
            print(
                f"  Trades: {r['trades']} | WR: {r['win_rate']}% | PF: {r['profit_factor']} | DD: {r['max_dd_pct']}% | PnL: ${r['total_pnl']}"
            )
            print(f"  Grade FTMO: {ftmo_grade(r)}")

    # Rapport final
    print("\n" + "═" * 70)
    print("  RAPPORT FINAL — BACKTEST H4 SOLUTION A")
    print("═" * 70)
    print(f"{'Symbole':<15} {'Trades':<8} {'WR':<7} {'PF':<7} {'DD%':<7} {'PnL':<12} {'Grade':<20}")
    print("-" * 70)

    viables = []
    for r in sorted(results, key=lambda x: x.get("trades", 0), reverse=True):
        if r.get("error") or r.get("trades", 0) == 0:
            print(f"{r['symbol']:<15} {'❌ ' + r.get('error', 'no trades'):<50}")
            continue
        grade = ftmo_grade(r)
        print(
            f"{r['symbol']:<15} {r['trades']:<8} {r['win_rate']:<7} {r['profit_factor']:<7} {r['max_dd_pct']:<7} ${r['total_pnl']:<9} {grade}"
        )
        if "✅" in grade:
            viables.append(r)

    print("-" * 70)
    if viables:
        print(f"\n🎯 Symboles FTMO-viables en H4 avec Solution A:")
        for r in viables:
            print(f"   ✅ {r['symbol']}: PF={r['profit_factor']}, DD={r['max_dd_pct']}%, {r['trades']} trades")
    else:
        print(f"\n⚠️  Aucun symbole FTMO-viable trouvé en H4 avec ces paramètres.")

    # Sauvegarder
    out_path = Path(__file__).resolve().parent.parent / "runtime" / "backtest_h4_solution_a.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n📁 Résultats sauvegardés: {out_path}")
    print("═" * 70)
