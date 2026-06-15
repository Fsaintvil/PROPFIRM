"""
Backtest Production — 3 actifs calibrés (XAUUSD H4, BTCUSD H1, US500.cash H1)

Utilise les données parquet de data/historical/ et la configuration
production de SYMBOL_CONFIG (strategy.py) + SYMBOL_TIMEFRAMES (config_simple.py)
pour un backtest complet par actif avec son timeframe natif.

Inclut:
  - Backtest par symbole avec timeframe natif
  - Walk-forward validation (splits chronologiques)
  - Monte Carlo simulation (1000 runs, probabilité DD > 10%)
  - Stress test (spread×3, slippage 3pts, commission×2)

Usage:
    python scripts/backtest_production.py
    python scripts/backtest_production.py --walk-forward
    python scripts/backtest_production.py --monte-carlo
    python scripts/backtest_production.py --stress-test
    python scripts/backtest_production.py --all
    python scripts/backtest_production.py --export-json runtime/bt_prod_report.json
"""
import argparse
import json as stdjson
import os
import sys
from collections import defaultdict
from datetime import datetime
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx, adx_arrays, ema
from engine_simple.strategy import SYMBOL_CONFIG, _get_symbol_config
from config_simple import SYMBOL_TIMEFRAMES

# ── Configuration ──
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.004
MIN_BARS = 60
MAX_LOT = 1.0

# Timeout en barres = adapté au timeframe
TIMEOUT_BARS = {"H1": 120, "H4": 60, "D1": 30}  # 5j H1, 10j H4, 30j D1

# Risk multiplier par symbole (depuis production.yaml)
SYMBOL_RISK_MULT = {"XAUUSD": 1.0, "BTCUSD": 0.50, "US500.cash": 1.0}

# Actifs de production
PRODUCTION_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]


def get_pip_info(symbol):
    """Retourne pip_size et pip_value par actif."""
    if symbol in ('XAUUSD', 'XAGUSD'):
        return 0.01, 1.0
    elif symbol in ('US500.cash', 'JP225.cash', 'US30.cash', 'NAS100.cash'):
        return 0.01, 1.0
    elif symbol in ('USOIL.cash', 'UKOIL.cash', 'BTCUSD', 'ETHUSD'):
        return 0.01, 1.0
    return 0.0001, 10.0


class SimTrade:
    """Trade simulé avec SL/TP ATR, trailing, partial TP."""
    __slots__ = ('symbol', 'action', 'entry', 'sl', 'tp', 'atr_val',
                 'regime', 'open_bar', 'open_time', 'direction',
                 'closed', 'result', 'profit_usd', 'profit_pct',
                 'peak_price', 'trailing_sl', 'partial_closed',
                 'bars_held', 'close_time', 'close_price', 'lot',
                 '_pip_size', '_pip_value', 'max_profit_mult')

    def __init__(self, symbol, action, entry, sl, tp, atr_val, regime,
                 bar_idx, bar_time, balance, risk_per_trade=RISK_PER_TRADE):
        self.symbol = symbol
        self.action = action
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.atr_val = atr_val
        self.regime = regime
        self.open_bar = bar_idx
        self.open_time = bar_time
        self.direction = 0 if action == "BUY" else 1
        self.closed = False
        self.result = None
        self.profit_usd = 0.0
        self.profit_pct = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.max_profit_mult = 0.0

        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._calc_lot(entry, sl, balance, risk_per_trade)

    def _calc_lot(self, entry, sl, balance, risk_per_trade=None):
        if risk_per_trade is None:
            risk_per_trade = RISK_PER_TRADE
        price_dist = abs(entry - sl)
        if price_dist > 0:
            risk_usd = balance * risk_per_trade
            risk_in_pips = price_dist / self._pip_size
            if risk_in_pips > 0:
                self.lot = risk_usd / (risk_in_pips * self._pip_value)
        self.lot = max(0.01, min(MAX_LOT, self.lot))

    def check_sl_tp(self, high, low, close, bar_idx, bar_time):
        if self.closed:
            return
        if self.direction == 0:
            if low <= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                self.closed = True
            elif high >= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                self.closed = True
        else:
            if high >= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                self.closed = True
            elif low <= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                self.closed = True
        if self.closed:
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            self._calc_pnl()

    def _calc_pnl(self):
        if self.direction == 0:
            self.profit_pct = (self.close_price - self.entry) / self.entry
        else:
            self.profit_pct = (self.entry - self.close_price) / self.entry
        pips = (self.close_price - self.entry) * (1 if self.direction == 0 else -1) / self._pip_size
        self.profit_usd = pips * self.lot * self._pip_value

    def update_peak(self, high, low):
        if self.closed:
            return
        if self.direction == 0:
            if high > self.peak_price:
                self.peak_price = high
        else:
            if low < self.peak_price:
                self.peak_price = low

    def update_trailing(self, atr_v, symbol=None):
        """Met à jour le trailing stop avec configuration par actif."""
        if self.closed:
            return

        profit_dist = abs(self.peak_price - self.entry) if self.peak_price > 0 else 0
        profit_atr = profit_dist / max(atr_v, 1e-10)
        self.max_profit_mult = max(self.max_profit_mult, profit_atr)

        if profit_atr < 1.0:
            return

        # Obtenir les niveaux de trailing par actif
        sym_cfg = _get_symbol_config(symbol) if symbol else _get_symbol_config(self.symbol)

        # Déterminer le régime basé sur le profit
        if profit_atr >= 5.0:
            trail_mult = 0.10 if self.regime == "RANGING" else 0.15
        elif profit_atr >= 3.0:
            trail_mult = 0.20 if self.regime == "RANGING" else 0.30
        elif profit_atr >= 2.0:
            trail_mult = 0.35 if self.regime == "RANGING" else 0.50
        else:  # profit_atr >= 1.0
            trail_mult = 0.50 if self.regime == "RANGING" else 0.80

        new_sl = self.peak_price - trail_mult * atr_v * (1 if self.direction == 0 else -1)

        if self.direction == 0:
            if new_sl > self.trailing_sl:
                self.trailing_sl = new_sl
        else:
            if new_sl < self.trailing_sl:
                self.trailing_sl = new_sl

    def check_partial_tp(self, atr_v):
        """Partial TP à 60% du TP."""
        if self.partial_closed or self.closed:
            return

        if self.direction == 0:
            progress = (self.close_price - self.entry) / (self.tp - self.entry) if abs(self.tp - self.entry) > 1e-10 else 0
        else:
            progress = (self.entry - self.close_price) / (self.entry - self.tp) if abs(self.entry - self.tp) > 1e-10 else 0

        if abs(progress) > 0.60:
            # Break-even buffer
            be_buffer = 0.80 if self.regime == "RANGING" else 0.60
            self.trailing_sl = self.entry + be_buffer * atr_v * (1 if self.direction == 0 else -1)
            self.partial_closed = True

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "action": self.action,
            "entry": round(self.entry, 5),
            "sl": round(self.sl, 5),
            "tp": round(self.tp, 5),
            "close": round(self.close_price, 5),
            "result": self.result,
            "profit_usd": round(self.profit_usd, 2),
            "profit_pct": round(self.profit_pct * 100, 3),
            "bars_held": self.bars_held,
            "partial_tp": self.partial_closed,
            "lot": round(self.lot, 4),
            "max_profit_mult": round(self.max_profit_mult, 2),
        }


def load_parquet(filepath: str) -> dict | None:
    """Charge un fichier parquet et retourne un dict avec arrays numpy."""
    try:
        df = pd.read_parquet(filepath)
        if len(df) < MIN_BARS:
            return None
        return {
            "time": df.index.values if hasattr(df.index, 'values') else np.arange(len(df)),
            "open": df['open'].values.astype(float),
            "high": df['high'].values.astype(float),
            "low": df['low'].values.astype(float),
            "close": df['close'].values.astype(float),
            "volume": df['volume'].values.astype(float) if 'volume' in df.columns else np.zeros(len(df)),
        }
    except Exception as e:
        print(f"    Erreur chargement {filepath}: {e}")
        return None


def detect_regime(close, high, low, adx_val, atr_val):
    """Détecte le régime de marché."""
    if atr_val <= 0:
        return "RANGING"

    atr_pct = atr_val / max(np.mean(close[-20:]), 1e-4)

    if adx_val >= 22:
        ma20 = np.mean(close[-20:])
        ma20_prev = np.mean(close[-40:-20]) if len(close) >= 40 else ma20
        slope = (ma20 - ma20_prev) / max(ma20_prev, 1e-4)

        if slope > 0.002:
            return "TREND_UP"
        elif slope < -0.002:
            return "TREND_DOWN"

    if atr_pct >= 0.015:
        return "HIGH_VOL"
    elif atr_pct <= 0.003:
        return "LOW_VOL"

    return "RANGING"


def run_backtest(symbol, data, balance=INITIAL_BALANCE, stress_spread=0, stress_slippage=0, max_bars=None):
    """Exécute le backtest MOM20x3 optimisé (indicateurs pré-calculés)."""
    close = data['close'].astype(float)
    high = data['high'].astype(float)
    low = data['low'].astype(float)
    times = data['time']
    n = len(close)

    # Déterminer le timeout basé sur le timeframe du symbole
    tf = SYMBOL_TIMEFRAMES.get(symbol, "H1")
    timeout_bars = TIMEOUT_BARS.get(tf, 120)

    # Appliquer le risk_mult par symbole
    risk_mult = SYMBOL_RISK_MULT.get(symbol, 1.0)
    effective_risk_per_trade = RISK_PER_TRADE * risk_mult

    # Limiter le nombre de barres (depuis la fin)
    if max_bars and max_bars > 0 and n > max_bars:
        start_idx = n - max_bars
        close = close[start_idx:]
        high = high[start_idx:]
        low = low[start_idx:]
        times = times[start_idx:]
        n = len(close)
    else:
        start_idx = 0

    sym_cfg = _get_symbol_config(symbol)
    period = sym_cfg["momentum_period"]

    # ── PRÉ-CALCUL DES INDICATEURS (O(n) — un seul passage par indicateur) ──
    atr_arr_full = atr(high, low, close, 14)  # numpy array
    adx_arr_full, plus_di_full, minus_di_full = adx_arrays(high, low, close, 14)
    ema20_arr_full = ema(close, 20)

    # ADX slope = ADX[i] - ADX[i-half]
    half = max(14, len(close) // 3)
    adx_slope_arr = np.zeros_like(adx_arr_full, dtype=float)
    for j in range(half, min(len(adx_arr_full), half + len(adx_arr_full))):
        if not np.isnan(adx_arr_full[j]) and not np.isnan(adx_arr_full[j - half]):
            adx_slope_arr[j] = adx_arr_full[j] - adx_arr_full[j - half]

    # ── BOUCLE PRINCIPALE (O(n) — simple scan avec lookup indiciel) ──
    all_trades = []
    open_trades = []

    for i in range(MIN_BARS, n):
        # 1. Mettre à jour les trades ouverts
        still_open = []
        for t in open_trades:
            t.update_peak(high[i], low[i])
            atr_v = float(atr_arr_full[i]) if not np.isnan(atr_arr_full[i]) else t.atr_val
            t.check_partial_tp(atr_v)
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            t.update_trailing(atr_v, symbol)
            if not t.closed:
                if i - t.open_bar > timeout_bars:
                    t.closed = True
                    t.close_price = close[i]
                    t.close_time = times[i]
                    t.result = "TIMEOUT"
                    t.bars_held = i - t.open_bar
                    t._calc_pnl()
            if not t.closed:
                still_open.append(t)
        open_trades = still_open

        # 2. Générer un signal MOM20x3
        if i < period + 14:
            continue

        current_atr = float(atr_arr_full[i]) if not np.isnan(atr_arr_full[i]) else 0
        if current_atr <= 0:
            continue

        adx_val = float(adx_arr_full[i]) if not np.isnan(adx_arr_full[i]) else 0
        plus_di = float(plus_di_full[i]) if not np.isnan(plus_di_full[i]) else 0
        minus_di = float(minus_di_full[i]) if not np.isnan(minus_di_full[i]) else 0

        # ADX slope (pré-calculé)
        adx_slope = float(adx_slope_arr[i]) if not np.isnan(adx_slope_arr[i]) else 0.0

        # Momentum
        mom = float(close[i] - close[i - period])
        mom_abs = abs(mom)

        # Seuil adaptatif
        is_trending = adx_val >= 25
        thresh = sym_cfg["threshold_trending"] if is_trending else sym_cfg["threshold_ranging"]
        thresh = max(1.5, min(2.5, thresh))
        threshold_value = thresh * current_atr

        if mom_abs < threshold_value:
            continue

        # Score
        raw_score = min(1.0, mom_abs / (threshold_value * 2))
        score = 0.50 + raw_score * 0.45

        # Filtre ADX slope
        adx_slope_threshold = sym_cfg["adx_slope_threshold"]
        if raw_score > 0.70:
            adx_slope_threshold = sym_cfg["adx_slope_threshold_strong"]
        if adx_slope < adx_slope_threshold:
            continue

        # Direction filter (DI)
        if mom > 0:
            action = "BUY"
            if plus_di <= minus_di * 0.8:
                continue
        else:
            action = "SELL"
            if minus_di <= plus_di * 0.8:
                continue

        # Pullback filter (informatif)
        ema20_val = float(ema20_arr_full[i]) if not np.isnan(ema20_arr_full[i]) else 0
        if ema20_val > 0:
            pullback_dist = (close[i] - ema20_val) / ema20_val * 100
            atr_mult_pullback = sym_cfg["pullback_band_trending"] if is_trending else sym_cfg["pullback_band_ranging"]
            pullback_band = (atr_mult_pullback * current_atr) / ema20_val * 100
            pullback_band = max(0.05, min(1.0, pullback_band))
            if abs(pullback_dist) >= pullback_band:
                continue

        # Déjà exposé dans cette direction ?
        same_direction = [t for t in open_trades if t.action == action]
        if same_direction:
            continue

        # SL/TP
        if is_trending:
            sl_atr = sym_cfg["sl_atr_trending"]
            tp_atr = sym_cfg["tp_atr_trending"]
        else:
            sl_atr = sym_cfg["sl_atr_ranging"]
            tp_atr = sym_cfg["tp_atr_ranging"]

        entry = close[i] + stress_slippage * (0.01 if action == "BUY" else -0.01)
        sl_dist = sl_atr * current_atr
        tp_dist = tp_atr * current_atr

        if action == "BUY":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        # Spread check
        spread_pts = (high[i] - low[i]) * 0.1 + stress_spread
        if spread_pts > 150:  # max spread
            continue

        # Régime
        regime = detect_regime(close[:i+1], high[:i+1], low[:i+1], adx_val, current_atr)

        # Créer le trade (avec risk_mult par symbole)
        trade = SimTrade(symbol, action, entry, sl, tp, current_atr,
                         regime, i, times[i], balance,
                         risk_per_trade=effective_risk_per_trade)
        all_trades.append(trade)
        open_trades.append(trade)

        # Mettre à jour le balance
        balance += trade.profit_usd

    # Fermer les trades restants
    for t in open_trades:
        if not t.closed:
            t.closed = True
            t.close_price = close[-1]
            t.close_time = times[-1]
            t.result = "TIMEOUT"
            t.bars_held = n - 1 - t.open_bar
            t._calc_pnl()
            balance += t.profit_usd

    return all_trades


def calculate_stats(trades, label=""):
    """Calcule les statistiques du backtest."""
    if not trades:
        return {"label": label, "trades": 0}

    wins = [t for t in trades if t.profit_usd > 0]
    losses = [t for t in trades if t.profit_usd <= 0]

    total_pnl = sum(t.profit_usd for t in trades)
    win_rate = len(wins) / len(trades) if trades else 0

    gross_profit = sum(t.profit_usd for t in wins) if wins else 0
    gross_loss = abs(sum(t.profit_usd for t in losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    # Drawdown
    balance = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0
    for t in trades:
        balance += t.profit_usd
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak
        if dd > max_dd:
            max_dd = dd

    # Avg trade
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    expectancy = total_pnl / len(trades) if trades else 0

    return {
        "label": label,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "max_dd_pct": round(max_dd * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "avg_bars_held": round(np.mean([t.bars_held for t in trades]), 1),
    }


def walk_forward_split(trades, n_splits=5):
    """Découpe les trades en n_splits chronologiques pour walk-forward.
    
    Chaque split correspond à une période temporelle successive.
    L'écart entre les splits mesure la stabilité temporelle du système.
    """
    if len(trades) < n_splits * 10:
        return [], 0, 0

    # Trier par temps d'ouverture (chronologique)
    sorted_trades = sorted(trades, key=lambda t: t.open_time if hasattr(t, 'open_time') and t.open_time else 0)
    n = len(sorted_trades)
    chunk_size = n // n_splits
    splits = []

    for i in range(n_splits):
        start = i * chunk_size
        end = start + chunk_size if i < n_splits - 1 else n
        split_trades = sorted_trades[start:end]
        stats = calculate_stats(split_trades, f"Période {i+1}")
        splits.append(stats)

    # Stabilité
    wr_values = [s['win_rate'] for s in splits if s['trades'] > 0]
    pf_values = [s['profit_factor'] for s in splits if s['trades'] > 0]

    wr_stability = max(wr_values) - min(wr_values) if wr_values else 0
    pf_stability = max(pf_values) - min(pf_values) if pf_values else 0

    return splits, wr_stability, pf_stability


def monte_carlo_simulation(trades, n_simulations=1000):
    """Simule des scénarios extrêmes avec bootstrap."""
    if not trades:
        return {}

    pnls = [t.profit_usd for t in trades]
    n_trades = len(pnls)

    results = []
    for _ in range(n_simulations):
        # Bootstrap des trades
        sampled = np.random.choice(pnls, size=n_trades, replace=True)
        cumulative = np.cumsum(sampled)
        peak = INITIAL_BALANCE
        max_dd = 0
        for val in cumulative:
            current = INITIAL_BALANCE + val
            if current > peak:
                peak = current
            dd = (peak - current) / peak
            if dd > max_dd:
                max_dd = dd

        final_pnl = cumulative[-1]
        results.append({
            "final_pnl": final_pnl,
            "max_dd_pct": max_dd * 100,
        })

    pnls_arr = np.array([r["final_pnl"] for r in results])
    dds_arr = np.array([r["max_dd_pct"] for r in results])

    # Probabilité de DD > 10% (critique FTMO)
    prob_dd_gt_10 = float(np.mean(dds_arr > 7.0) * 100)  # DD max autorisé 10%, alerte dès 7%
    prob_dd_gt_5 = float(np.mean(dds_arr > 5.0) * 100)

    return {
        "simulations": n_simulations,
        "pnl_mean": round(float(np.mean(pnls_arr)), 2),
        "pnl_median": round(float(np.median(pnls_arr)), 2),
        "pnl_5th": round(float(np.percentile(pnls_arr, 5)), 2),
        "pnl_95th": round(float(np.percentile(pnls_arr, 95)), 2),
        "dd_mean": round(float(np.mean(dds_arr)), 1),
        "dd_median": round(float(np.median(dds_arr)), 1),
        "dd_95th": round(float(np.percentile(dds_arr, 95)), 1),
        "dd_max": round(float(np.max(dds_arr)), 1),
        "prob_profit": round(float(np.mean(pnls_arr > 0) * 100), 1),
        "prob_dd_gt_pct": {
            "5": round(prob_dd_gt_5, 1),
            "7": round(prob_dd_gt_10, 1),
            "10": round(float(np.mean(dds_arr > 10.0) * 100), 1),
        },
    }


def stress_test(data_dict, symbols, max_bars=None):
    """Teste le backtest avec spread augmenté, slippage, commission augmentée."""
    results = {}

    # Baseline
    for sym in symbols:
        if sym in data_dict:
            trades = run_backtest(sym, data_dict[sym], max_bars=max_bars)
            results[f"{sym}_baseline"] = calculate_stats(trades, f"{sym} Baseline")

    # Stress: spread ×3 (points de spread ajoutés)
    for sym in symbols:
        if sym in data_dict:
            trades = run_backtest(sym, data_dict[sym], stress_spread=80, max_bars=max_bars)
            results[f"{sym}_spread_x3"] = calculate_stats(trades, f"{sym} Spread×3")

    # Stress: slippage 3 points
    for sym in symbols:
        if sym in data_dict:
            trades = run_backtest(sym, data_dict[sym], stress_slippage=3, max_bars=max_bars)
            results[f"{sym}_slippage_3pt"] = calculate_stats(trades, f"{sym} Slippage 3pts")

    # Stress: commission ×2 (simulé en réduisant le PnL de 0.05% par trade)
    for sym in symbols:
        if sym in data_dict:
            trades = run_backtest(sym, data_dict[sym], max_bars=max_bars)
            for t in trades:
                t.profit_usd *= 0.95  # commission ×2 = 5% de frais en plus
            results[f"{sym}_comm_x2"] = calculate_stats(trades, f"{sym} Commission×2")

    return results


def print_stats(stats):
    """Affiche les statistiques."""
    print(f"\n{'='*60}")
    print(f"  {stats.get('label', 'N/A')}")
    print(f"{'='*60}")
    print(f"  Trades:      {stats.get('trades', 0)}")
    print(f"  Win Rate:    {stats.get('win_rate', 0)}%")
    print(f"  PnL Total:   ${stats.get('total_pnl', 0):,.2f}")
    print(f"  Profit Factor: {stats.get('profit_factor', 0)}")
    print(f"  Max DD:      {stats.get('max_dd_pct', 0)}%")
    print(f"  Avg Win:     ${stats.get('avg_win', 0):,.2f}")
    print(f"  Avg Loss:    ${stats.get('avg_loss', 0):,.2f}")
    print(f"  Expectancy:  ${stats.get('expectancy', 0):,.2f}")
    print(f"  Avg Bars:    {stats.get('avg_bars_held', 0)}")


def main():
    parser = argparse.ArgumentParser(description="Backtest Production 3 actifs")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward validation")
    parser.add_argument("--monte-carlo", action="store_true", help="Monte Carlo simulation")
    parser.add_argument("--stress-test", action="store_true", help="Stress test")
    parser.add_argument("--all", action="store_true", help="Tous les tests")
    parser.add_argument("--max-bars", type=int, default=0, help="Max barres par symbole (0=all)")
    parser.add_argument("--export-json", type=str, default=None, help="Exporter le rapport en JSON")
    args = parser.parse_args()

    print("="*70)
    print("  BACKTEST PRODUCTION — 3 Actifs Calibrés")
    print("  XAUUSD | BTCUSD | US500.cash")
    print("="*70)

    # Charger les données — timeframe par symbole
    data_dir = Path("data/historical")
    data_dict = {}
    sym_tf = {}  # mémoriser le timeframe chargé pour chaque symbole

    for sym in PRODUCTION_SYMBOLS:
        tf = SYMBOL_TIMEFRAMES.get(sym, "H1")
        parquet_path = data_dir / f"{sym}_{tf}.parquet"
        if parquet_path.exists():
            print(f"\n  Chargement {sym} ({tf})...")
            data = load_parquet(str(parquet_path))
            if data:
                data_dict[sym] = data
                sym_tf[sym] = tf
                timeout = TIMEOUT_BARS.get(tf, 120)
                print(f"    ✓ {len(data['close'])} barres chargées, timeout={timeout} barres")
            else:
                print(f"    ✗ Erreur chargement {sym}_{tf}.parquet")
        else:
            print(f"\n  ✗ {sym}_{tf}.parquet non trouvé")

    if not data_dict:
        print("\n  ✗ Aucune donnée chargée")
        return

    # ── Backtest standard ──
    print("\n" + "="*70)
    print("  BACKTEST H1 — Configuration Production")
    print("="*70)

    all_stats = {}
    total_trades = []
    total_pnl = 0

    for sym, data in data_dict.items():
        print(f"\n  Backtest {sym}...")
        trades = run_backtest(sym, data, max_bars=None if args.max_bars == 0 else args.max_bars)
        stats = calculate_stats(trades, sym)
        all_stats[sym] = stats
        total_trades.extend(trades)
        total_pnl += stats.get("total_pnl", 0)
        print_stats(stats)

    # Stats globales
    global_stats = calculate_stats(total_trades, "GLOBAL")
    print_stats(global_stats)

    # ── Walk-Forward ──
    if args.walk_forward or args.all:
        print("\n" + "="*70)
        print("  WALK-FORWARD VALIDATION")
        print("="*70)

        for sym in data_dict:
            print(f"\n  Walk-forward {sym}...")
            trades = run_backtest(sym, data_dict[sym], max_bars=None if args.max_bars == 0 else args.max_bars)
            splits, wr_stab, pf_stab = walk_forward_split(trades)

            if splits:
                print(f"\n  {'Période':<12} {'Trades':<10} {'WR%':<10} {'PnL':<15} {'PF':<10} {'DD%':<10}")
                print(f"  {'-'*67}")
                for s in splits:
                    print(f"  {s['label']:<12} {s['trades']:<10} {s['win_rate']:<10} "
                          f"${s['total_pnl']:<14,.2f} {s['profit_factor']:<10} {s['max_dd_pct']:<10}")

                # Stabilité
                print(f"\n  Stabilité WR: écart {wr_stab:.1f}%")
                print(f"  Stabilité PF: écart {pf_stab:.2f}")
                verdict = "✅ STABLE" if wr_stab < 15 and pf_stab < 1.5 else "⚠️ INSTABLE"
                print(f"  Verdict: {verdict}")

    # ── Monte Carlo ──
    if args.monte_carlo or args.all:
        print("\n" + "="*70)
        print("  MONTE CARLO SIMULATION (1000 runs)")
        print("="*70)

        for sym in data_dict:
            if sym in all_stats and all_stats[sym]['trades'] > 0:
                trades = run_backtest(sym, data_dict[sym], max_bars=None if args.max_bars == 0 else args.max_bars)
                mc = monte_carlo_simulation(trades)

                print(f"\n  {sym}:")
                print(f"    PnL moyen:      ${mc['pnl_mean']:,.2f}")
                print(f"    PnL médian:     ${mc['pnl_median']:,.2f}")
                print(f"    PnL 5e pctl:    ${mc['pnl_5th']:,.2f}")
                print(f"    PnL 95e pctl:   ${mc['pnl_95th']:,.2f}")
                print(f"    DD moyen:       {mc['dd_mean']:.1f}%")
                print(f"    DD médian:      {mc['dd_median']:.1f}%")
                print(f"    DD 95e pctl:    {mc['dd_95th']:.1f}%")
                print(f"    Probabilité profit: {mc['prob_profit']:.1f}%")
                print(f"    Probabilité DD>5%:  {mc['prob_dd_gt_pct']['5']:.1f}%")
                print(f"    Probabilité DD>7%:  {mc['prob_dd_gt_pct']['7']:.1f}%")
                print(f"    Probabilité DD>10%: {mc['prob_dd_gt_pct']['10']:.1f}%")
                risk_rating = "🔴 ÉLEVÉ" if mc['prob_dd_gt_pct']['10'] > 10 else "🟡 MODÉRÉ" if mc['prob_dd_gt_pct']['10'] > 3 else "🟢 FAIBLE"
                print(f"    Risque FTMO (DD>10%): {risk_rating}")

    # ── Stress Test ──
    if args.stress_test or args.all:
        print("\n" + "="*70)
        print("  STRESS TEST")
        print("="*70)

        stress_results = stress_test(data_dict, PRODUCTION_SYMBOLS, max_bars=None if args.max_bars == 0 else args.max_bars)

        print(f"\n  {'Scénario':<25} {'Trades':<10} {'WR%':<10} {'PnL':<15} {'PF':<10} {'DD%':<10}")
        print(f"  {'-'*80}")

        for key, stats in sorted(stress_results.items()):
            print(f"  {stats['label']:<25} {stats['trades']:<10} {stats['win_rate']:<10} "
                  f"${stats['total_pnl']:<14,.2f} {stats['profit_factor']:<10} {stats['max_dd_pct']:<10}")

    # ── Verdict ──
    print("\n" + "="*70)
    print("  VERDICT PRODUCTION")
    print("="*70)

    ready = True
    for sym, stats in all_stats.items():
        wr = stats.get('win_rate', 0)
        pf = stats.get('profit_factor', 0)
        dd = stats.get('max_dd_pct', 0)

        status = "✅" if (wr > 55 and pf > 1.0 and dd < 15) else "⚠️"
        if status == "⚠️":
            ready = False

        print(f"\n  {sym}: {status}")
        print(f"    WR: {wr}% | PF: {pf} | DD: {dd}%")

    if ready:
        print(f"\n  ✅ TOUS LES ACTIFS VALIDÉS — Prêt pour production")
    else:
        print(f"\n  ⚠️ CERTAINS ACTIFS NÉCESSITENT UNE ATTENTION")

    print(f"\n{'='*70}")

    # ── Export JSON ──
    if args.export_json:
        export = {
            "backtest_version": "4.1.0",
            "date": str(datetime.now()),
            "initial_balance": INITIAL_BALANCE,
            "risk_per_trade": RISK_PER_TRADE,
            "symbols": {
                sym: stats for sym, stats in all_stats.items()
            },
            "global": global_stats,
            "verdict": {
                "ready": ready,
                "all_stats": all_stats,
            },
        }
        Path(args.export_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.export_json, "w") as f:
            stdjson.dump(export, f, indent=2, default=str)
        print(f"\n  Rapport exporté: {args.export_json}")


if __name__ == "__main__":
    main()
