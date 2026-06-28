"""
Backtest MOM20x3 — Univers complet (15 symboles × H1/H4/D1, 2012-2026).
Avec filtres prod + coûts réels — pour choisir les meilleurs symboles.

Usage:
    python scripts/backtest_universe.py
    python scripts/backtest_universe.py --tf H1
    python scripts/backtest_universe.py --tf H4 --min-trades 200
    python scripts/backtest_universe.py --all-tf   # les 3 TF
"""

import json
import os
import sys
from datetime import datetime
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ─── Paramètres ──────────────────────────────────────
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044
MIN_BARS = 80
TIMEOUT_BARS = {"H1": 120, "H4": 60, "D1": 30}
MAX_LOT = 1.0
MIN_TRADES = 100  # seuil minimum pour considérer un symbole

# Danger hours (config actuelle)
DANGER_HOURS = [0, 1, 6, 7, 9, 12, 15, 18]

# Seuils MOM20x3
THRESHOLD_TRENDING = 2.5
THRESHOLD_RANGING = 2.0
THRESHOLD_MAX = 2.5
THRESHOLD_MIN = 1.5
SL_ATR_TRENDING = 2.0
TP_ATR_TRENDING = 5.0
SL_ATR_RANGING = 1.5
TP_ATR_RANGING = 4.0

# Périodes momentum (symboles connus)
SYMBOL_MOMENTUM_PERIODS = {
    "USDCAD": 24,
    "USDCHF": 14,
    "EURUSD": 18,
    "GBPUSD": 20,
    "AUDUSD": 24,
    "NZDUSD": 22,
    "XAUUSD": 30,
    "EURJPY": 20,
    "GBPJPY": 20,
    "USDJPY": 20,
    "BTCUSD": 20,
    "ETHUSD": 20,
    "JP225.cash": 20,
    "US500.cash": 20,
    "USOIL.cash": 20,
    # Crypto
    "SOLUSD": 20,
    "LNKUSD": 20,
    "BNBUSD": 20,
}

# Coûts par type de symbole
SYMBOL_COSTS = {
    # Forex majeurs (1.5 pips spread typique MT5 ECN)
    "EURUSD": (1.5, 0.0001, 10.0, 100_000),
    "GBPUSD": (1.5, 0.0001, 10.0, 100_000),
    "USDJPY": (1.5, 0.01, 1.0, 100_000),
    "USDCAD": (1.5, 0.0001, 10.0, 100_000),
    "USDCHF": (1.5, 0.0001, 10.0, 100_000),
    "AUDUSD": (1.5, 0.0001, 10.0, 100_000),
    "NZDUSD": (1.5, 0.0001, 10.0, 100_000),
    # Forex cross (2-3 pips)
    "EURJPY": (2.0, 0.01, 1.0, 100_000),
    "GBPJPY": (3.0, 0.01, 1.0, 100_000),
    # Métaux
    "XAUUSD": (5.0, 0.01, 1.0, 100),
    # Indices
    "US500.cash": (2.0, 0.01, 1.0, 1),
    "JP225.cash": (2.0, 0.01, 1.0, 1),
    # Commodités
    "USOIL.cash": (5.0, 0.01, 1.0, 100),
    # Crypto
    "BTCUSD": (10.0, 0.01, 1.0, 1),
    "ETHUSD": (10.0, 0.01, 1.0, 1),
    # Altcoins (coûts similaires BTC — spread 10pts, pip 0.01, pip_value $1)
    "SOLUSD": (10.0, 0.01, 1.0, 1),
    "LNKUSD": (10.0, 0.01, 1.0, 1),
    "BNBUSD": (10.0, 0.01, 1.0, 1),
    # Forex mineurs
    "NZDUSD": (1.5, 0.0001, 10.0, 100_000),
    # Indices additionnels
    "US30.cash": (2.0, 0.01, 1.0, 1),
    "US100.cash": (2.0, 0.01, 1.0, 1),
    # Métaux
    "XAGUSD": (10.0, 0.001, 5.0, 5000),
    # Commodités
    "NATGAS.cash": (5.0, 0.001, 10.0, 10000),
}
DEFAULT_SPREAD = 2.0
DEFAULT_PIP = 0.0001
DEFAULT_PIP_VALUE = 10.0
DEFAULT_CONTRACT = 100_000
SLIPPAGE_PIPS = 1.0
COMMISSION_PER_100K = 7.0

TRAILING_LEVELS = {
    "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
    "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
    "LOW_VOL": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
}


def get_specs(symbol):
    """Retourne (spread_pips, pip_size, pip_value, contract_size) pour un symbole."""
    if symbol in SYMBOL_COSTS:
        return SYMBOL_COSTS[symbol]
    return (DEFAULT_SPREAD, DEFAULT_PIP, DEFAULT_PIP_VALUE, DEFAULT_CONTRACT)


# ═══════════════════════════════════════════════════════
#  INDICATEURS VECTORISÉS
# ═══════════════════════════════════════════════════════


def precalc_indicators(high, low, close, period=14):
    n = len(close)
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr_arr = np.full(n, np.nan)
    for i in range(period, n):
        atr_arr[i] = np.mean(tr[i - period : i])
    up = np.diff(high)
    down = -np.diff(low)
    pos_dm = np.where((up > down) & (up > 0), up, 0)
    neg_dm = np.where((down > up) & (down > 0), down, 0)
    tr_sm = np.full(n, np.nan)
    pos_sm = np.full(n, np.nan)
    neg_sm = np.full(n, np.nan)
    for i in range(period, n):
        tr_sm[i] = np.mean(tr[i - period : i])
        pos_sm[i] = np.mean(pos_dm[i - period : i])
        neg_sm[i] = np.mean(neg_dm[i - period : i])
    pos_di = np.where(tr_sm > 0, 100 * pos_sm / tr_sm, 0)
    neg_di = np.where(tr_sm > 0, 100 * neg_sm / tr_sm, 0)
    di_sum = pos_di + neg_di
    dx = np.where(di_sum > 0, 100 * np.abs(pos_di - neg_di) / di_sum, 0)
    adx_arr = np.full(n, np.nan)
    for i in range(period * 2, n):
        adx_arr[i] = np.mean(dx[i - period : i])
    # EMA20
    ema20 = np.full(n, np.nan)
    alpha = 2 / 21
    if n > 0:
        ema20[0] = close[0]
        for i in range(1, n):
            ema20[i] = close[i] * alpha + ema20[i - 1] * (1 - alpha)
    return atr_arr, adx_arr, pos_di, neg_di, ema20


def batch_prod_signals(close, high, low, times, atr_arr, adx_arr, pos_di, neg_di, ema20, symbol):
    """Signaux PROD avec tous les filtres — batch vectorisé O(n)."""
    n = len(close)
    mp = SYMBOL_MOMENTUM_PERIODS.get(symbol, 20)
    half = max(14, mp // 2)
    # ADX slope pré-calculé
    adx_slope_arr = np.zeros(n)
    for i in range(half + 28, n):
        if not np.isnan(adx_arr[i]) and not np.isnan(adx_arr[i - half]):
            adx_slope_arr[i] = adx_arr[i] - adx_arr[i - half]

    # Pré-conversion des timestamps (évite pd.Timestamp dans la boucle)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)

    signals = np.full(n, None, dtype=object)

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i]
        adx_v = adx_arr[i]
        if np.isnan(atr_v) or atr_v <= 0 or np.isnan(adx_v) or adx_v <= 0:
            continue

        # Danger hours + weekend (pré-converti)
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue

        if i < mp + 1:
            continue
        mom = float(close[i] - close[i - mp])
        mom_abs = abs(mom)
        is_trending = adx_v >= 25
        thresh = THRESHOLD_TRENDING if is_trending else THRESHOLD_RANGING
        thresh = max(THRESHOLD_MIN, min(THRESHOLD_MAX, thresh))
        tv = thresh * atr_v
        if mom_abs < tv:
            continue

        raw_score = min(1.0, mom_abs / (tv * 2)) if mom_abs > 0 else 0.0

        # ADX slope
        slope_ok = True
        st = -3.5 if raw_score > 0.70 else -2.0
        if adx_slope_arr[i] < st:
            slope_ok = False

        # Direction filter
        pdi = pos_di[i]
        ndi = neg_di[i]
        if np.isnan(pdi) or np.isnan(ndi):
            continue
        dir_ok = True
        di_sugg = None
        action = None
        score = 0.0

        if mom > 0:
            if pdi <= ndi * 0.8:
                dir_ok = False
                di_sugg = "SELL"
        else:
            if ndi <= pdi * 0.8:
                dir_ok = False
                di_sugg = "BUY"

        if mom > 0 and mom_abs >= tv:
            action = "BUY"
            score = 0.50 + raw_score * 0.45
        elif mom < 0 and mom_abs >= tv:
            action = "SELL"
            score = 0.50 + raw_score * 0.45

        if action is None:
            continue

        # DI Override
        if not dir_ok and di_sugg is not None and i >= 7:
            sm = float(close[i] - close[i - 5])
            sma = abs(sm)
            ot = tv * 2.0 if adx_v < 22 else tv * 0.5
            if di_sugg == "SELL" and sm < -ot:
                action = "SELL"
                score = 0.50 + min(1.0, sma / (tv * 2)) * 0.45
                dir_ok = True
            elif di_sugg == "BUY" and sm > ot:
                action = "BUY"
                score = 0.50 + min(1.0, sma / (tv * 2)) * 0.45
                dir_ok = True

        if not slope_ok or not dir_ok:
            continue

        # Pullback
        ev = ema20[i]
        if np.isnan(ev) or ev <= 0:
            continue
        pb_dist = (close[i] - ev) / ev * 100
        pb_mult = 0.5 if is_trending else 0.3
        pb_band = max(0.05, min(1.0, (pb_mult * atr_v) / ev * 100))
        pb_active = abs(pb_dist) < pb_band

        regime = ("TREND_UP" if action == "BUY" else "TREND_DOWN") if is_trending else "RANGING"
        signals[i] = {
            "action": action,
            "score": min(0.99, score),
            "atr": atr_v,
            "adx": adx_v,
            "regime": regime,
            "sl_atr": SL_ATR_TRENDING if is_trending else SL_ATR_RANGING,
            "tp_atr": TP_ATR_TRENDING if is_trending else TP_ATR_RANGING,
            "threshold_value": tv,
        }
    return signals


def batch_simple_signals(close, times, atr_arr, adx_arr):
    """MOM20x3 simple — pas de filtres."""
    n = len(close)
    signals = np.full(n, None, dtype=object)
    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i]
        adx_v = adx_arr[i]
        if np.isnan(atr_v) or atr_v <= 0 or np.isnan(adx_v) or adx_v <= 0:
            continue
        if i < 21:
            continue
        # Pas de filtres horaires pour le simple
        mom = float(close[i] - close[i - 20])
        ma = abs(mom)
        it = adx_v >= 25
        th = THRESHOLD_TRENDING if it else THRESHOLD_RANGING
        th = max(THRESHOLD_MIN, min(THRESHOLD_MAX, th))
        tv = th * atr_v
        if ma < tv:
            continue
        act = "BUY" if mom > 0 else "SELL"
        reg = ("TREND_UP" if act == "BUY" else "TREND_DOWN") if it else "RANGING"
        signals[i] = {
            "action": act,
            "regime": reg,
            "sl_atr": SL_ATR_TRENDING if it else SL_ATR_RANGING,
            "tp_atr": TP_ATR_TRENDING if it else TP_ATR_RANGING,
            "atr": atr_v,
            "adx": adx_v,
            "threshold_value": tv,
        }
    return signals


# ═══════════════════════════════════════════════════════
#  SimTrade
# ═══════════════════════════════════════════════════════


class SimTrade:
    __slots__ = (
        "symbol",
        "action",
        "entry",
        "sl",
        "tp",
        "atr_val",
        "regime",
        "open_bar",
        "open_time",
        "direction",
        "closed",
        "result",
        "profit_usd",
        "profit_usd_cost",
        "peak_price",
        "trailing_sl",
        "partial_closed",
        "bars_held",
        "close_time",
        "close_price",
        "lot",
        "_pip_size",
        "_pip_value",
        "_contract_size",
        "_spread_pips",
        "commission_usd",
        "cost_pips",
    )

    def __init__(self, symbol, action, entry, sl, tp, atr_val, regime, bar_idx, bar_time, balance):
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
        self.profit_usd_cost = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.commission_usd = 0.0
        self.cost_pips = 0.0
        sp, ps, pv, cs = get_specs(symbol)
        self._spread_pips = sp
        self._pip_size = ps
        self._pip_value = pv
        self._contract_size = cs
        self._calc_lot(entry, sl, balance)

    def _pip_value_corrected(self, price=None):
        """Corrige pip_value pour les paires dont la devise de cote n'est pas l'USD.

        Le pip_value statique suppose la devise de cote = USD (ex: EURUSD → $10/pip/lot).
        Pour les paires JPY (USDJPY, EURJPY, GBPJPY), la devise de cote est le JPY.
        Il faut convertir: pip_value_USD = contract_size × pip_size / taux_JPYversUSD
        """
        pv = self._pip_value
        if self.symbol in ("USDJPY", "EURJPY", "GBPJPY"):
            pip_per_lot_quote = self._contract_size * self._pip_size  # ex: 100000*0.01=¥1000
            if self.symbol == "USDJPY":
                # entry = USDJPY rate. Convertir JPY→USD: diviser par le taux.
                rate = abs(price if price is not None else self.entry)
                pv = pip_per_lot_quote / max(rate, 1e-10)
            else:
                # EURJPY/GBPJPY: approximation USDJPY≈150
                pv = pip_per_lot_quote / 150.0
        return pv

    def _notional_usd(self):
        """Calcule le notionnel en USD pour le calcul de commission.

        La formule standard (lot × contract_size × entry) donne le notionnel
        dans la devise de cote. Pour les paires non-USD, il faut convertir.
        """
        raw = self.lot * self._contract_size * abs(self.entry)
        if self.symbol in ("USDJPY", "EURJPY", "GBPJPY"):
            if self.symbol == "USDJPY":
                # USDJPY: base = USD, notionnel déjà en USD
                return self.lot * self._contract_size
            else:
                # EURJPY/GBPJPY: notionnel en JPY → diviser par USDJPY≈150
                return raw / 150.0
        return raw

    def _calc_lot(self, entry, sl, balance):
        dist = abs(entry - sl)
        if dist > 0:
            risk = balance * RISK_PER_TRADE
            pips = dist / self._pip_size
            if pips > 0:
                pv = self._pip_value_corrected(entry)
                self.lot = risk / (pips * pv)
        self.lot = max(0.01, min(MAX_LOT, self.lot))

    def check_sl_tp(self, high, low, close, bar_idx, bar_time):
        if self.closed:
            return
        hit = False
        if self.direction == 0:
            if low <= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                hit = True
            elif high >= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                hit = True
        else:
            if high >= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = "SL"
                hit = True
            elif low <= self.tp:
                self.close_price = self.tp
                self.result = "TP"
                hit = True
        if hit:
            self.closed = True
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            self._calc_pnl()

    def _calc_pnl(self):
        pv = self._pip_value_corrected()
        usdpp = self.lot * pv
        if self.direction == 0:
            pips = (self.close_price - self.entry) / self._pip_size
        else:
            pips = (self.entry - self.close_price) / self._pip_size
        self.profit_usd = pips * usdpp
        # Coûts
        self.cost_pips = self._spread_pips + SLIPPAGE_PIPS
        notional = self._notional_usd()
        self.commission_usd = (notional / 100_000) * COMMISSION_PER_100K * 2
        pips_cost = pips - self.cost_pips
        self.profit_usd_cost = pips_cost * usdpp - self.commission_usd

    def update_peak(self, high, low):
        if self.closed:
            return
        if self.direction == 0:
            if high > self.peak_price:
                self.peak_price = high
        else:
            if low < self.peak_price:
                self.peak_price = low

    def update_trailing(self, atr_v):
        if self.closed or atr_v <= 0:
            return
        if self.direction == 0:
            profit_atr = (self.peak_price - self.entry) / atr_v
        else:
            profit_atr = (self.entry - self.peak_price) / atr_v
        if profit_atr <= 1.0:
            return
        lvls = TRAILING_LEVELS.get(self.regime, TRAILING_LEVELS["RANGING"])
        dm = lvls[-1][1]
        for th, d in reversed(lvls):
            if profit_atr > th:
                dm = d
                break
        dist = dm * atr_v
        if self.direction == 0:
            ns = self.peak_price - dist
            if ns > self.trailing_sl:
                self.trailing_sl = ns
        else:
            ns = self.peak_price + dist
            if ns < self.trailing_sl:
                self.trailing_sl = ns

    def check_partial(self, atr_v):
        if self.closed or self.partial_closed or atr_v <= 0:
            return
        if self.direction == 0:
            prog = (self.peak_price - self.entry) / max(self.tp - self.entry, 1e-10)
        else:
            prog = (self.entry - self.peak_price) / max(self.entry - self.tp, 1e-10)
        if prog < 0.60:
            return
        self.partial_closed = True
        be = 0.80 * atr_v
        if self.direction == 0:
            ns = self.entry + be
            if ns > self.trailing_sl:
                self.trailing_sl = ns
        else:
            ns = self.entry - be
            if ns < self.trailing_sl:
                self.trailing_sl = ns

    def to_dict(self):
        return dict(
            symbol=self.symbol,
            action=self.action,
            regime=self.regime,
            entry=round(self.entry, 5),
            sl=round(self.sl, 5),
            tp=round(self.tp, 5),
            close_price=round(self.close_price, 5),
            result=self.result,
            profit_usd=round(self.profit_usd, 2),
            profit_usd_cost=round(self.profit_usd_cost, 2),
            cost_pips=round(self.cost_pips, 1),
            commission_usd=round(self.commission_usd, 2),
            lot=round(self.lot, 4),
            bars_held=self.bars_held,
            open_time=str(self.open_time)[:19],
            close_time=str(self.close_time)[:19] if self.close_time else "",
        )


# ═══════════════════════════════════════════════════════
#  Backtest
# ═══════════════════════════════════════════════════════


def backtest_symbol(symbol, timeframe, df):
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    n = len(close)

    atr_arr, adx_arr, pdi, ndi, ema20 = precalc_indicators(high, low, close)

    # Pré-convertir les timestamps pour les batchs
    times_dt = pd.to_datetime(times)
    sigs_simple = batch_simple_signals(close, times_dt, atr_arr, adx_arr)
    sigs_prod = batch_prod_signals(close, high, low, times_dt, atr_arr, adx_arr, pdi, ndi, ema20, symbol)

    trades_simple, trades_prod, trades_prod_cost = [], [], []
    open_s, open_p, open_pc = [], [], []
    bars_since = 999

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0

        for lst in (open_s, open_p, open_pc):
            still = []
            for t in lst:
                t.update_peak(high[i], low[i])
                tatr = atr_v if atr_v > 0 else t.atr_val
                t.check_partial(tatr)
                t.update_trailing(tatr)
                t.check_sl_tp(high[i], low[i], close[i], i, times[i])
                if not t.closed and i - t.open_bar > TIMEOUT_BARS.get(timeframe, 120):
                    t.closed = True
                    t.close_price = close[i]
                    t.close_time = times[i]
                    t.result = "TIMEOUT"
                    t.bars_held = i - t.open_bar
                    t._calc_pnl()
                if not t.closed:
                    still.append(t)
            lst[:] = still

        bars_since += 1
        if atr_v <= 0:
            continue

        # Simple
        ss = sigs_simple[i]
        if ss is not None and bars_since >= 3:
            sd = ss["sl_atr"] * atr_v
            td = ss["tp_atr"] * atr_v
            if ss["action"] == "BUY":
                sp = close[i] - sd
                tp = close[i] + td
            else:
                sp = close[i] + sd
                tp = close[i] - td
            if td / sd >= 2.0 if sd > 0 else False:
                if not any(t.action == ss["action"] for t in open_s):
                    t = SimTrade(
                        symbol, ss["action"], close[i], sp, tp, atr_v, ss["regime"], i, times[i], INITIAL_BALANCE
                    )
                    trades_simple.append(t)
                    open_s.append(t)
                    bars_since = 0

        # Prod
        sp_sig = sigs_prod[i]
        if sp_sig is not None and bars_since >= 3:
            sd = sp_sig["sl_atr"] * atr_v
            td = sp_sig["tp_atr"] * atr_v
            if sp_sig["action"] == "BUY":
                sp = close[i] - sd
                tp = close[i] + td
            else:
                sp = close[i] + sd
                tp = close[i] - td
            if sd > 0 and td / sd >= 2.0 and not any(t.action == sp_sig["action"] for t in open_p):
                t = SimTrade(
                    symbol, sp_sig["action"], close[i], sp, tp, atr_v, sp_sig["regime"], i, times[i], INITIAL_BALANCE
                )
                trades_prod.append(t)
                open_p.append(t)
                tc = SimTrade(
                    symbol, sp_sig["action"], close[i], sp, tp, atr_v, sp_sig["regime"], i, times[i], INITIAL_BALANCE
                )
                trades_prod_cost.append(tc)
                open_pc.append(tc)
                bars_since = 0

    return trades_simple, trades_prod, trades_prod_cost


# ═══════════════════════════════════════════════════════
#  Métriques
# ═══════════════════════════════════════════════════════


def compute_metrics(closed, use_cost=False):
    if not closed:
        return {"n": 0}
    key = "profit_usd_cost" if use_cost else "profit_usd"
    wins = [t for t in closed if getattr(t, key) > 0]
    losses = [t for t in closed if getattr(t, key) <= 0]
    n = len(closed)
    nw = len(wins)
    wr = nw / n * 100 if n > 0 else 0
    tp = sum(getattr(t, key) for t in closed)
    gp = sum(max(0, getattr(t, key)) for t in closed)
    gl = abs(sum(min(0, getattr(t, key)) for t in closed))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
    # DD
    peak = INITIAL_BALANCE
    dd_max = 0.0
    bal = INITIAL_BALANCE
    for t in sorted(closed, key=lambda x: x.close_time or x.open_time):
        bal += getattr(t, key)
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)
    # P-value
    p = 1.0
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    return {
        "n": n,
        "wins": nw,
        "losses": n - nw,
        "win_rate": round(wr, 1),
        "total_pnl": round(tp, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "p_value": round(p, 4),
        "significant": p < 0.05 and wr > 50,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "avg_pnl": round(tp / n, 2) if n else 0,
        "avg_win": round(gp / nw, 2) if nw else 0,
        "avg_loss": round(-gl / (n - nw), 2) if n > nw else 0,
    }


def avg_costs(closed):
    if not closed:
        return {}
    c = [abs(t.profit_usd - t.profit_usd_cost) for t in closed]
    return {
        "avg_cost_pips": round(float(np.mean([t.cost_pips for t in closed])), 2),
        "avg_commission_usd": round(float(np.mean([t.commission_usd for t in closed])), 2),
        "avg_total_cost_usd": round(float(np.mean(c)), 2),
        "total_costs_usd": round(sum(c), 2),
    }


# ═══════════════════════════════════════════════════════
#  Fondu enchaîné multi-TF
# ═══════════════════════════════════════════════════════


def backtest_multi_tf(symbol):
    """Backtest un symbole sur H1+H4+D1 et agrège."""
    all_s, all_p, all_pc = [], [], []
    for tf in ("H1", "H4", "D1"):
        fp = Path(f"data/historical/{symbol}_{tf}.parquet")
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)
        if len(df) < MIN_BARS:
            continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
        if len(df) < MIN_BARS:
            continue
        s, p, pc = backtest_symbol(symbol, tf, df)
        all_s.extend(s)
        all_p.extend(p)
        all_pc.extend(pc)
    return all_s, all_p, all_pc


# ═══════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tf", choices=["H1", "H4", "D1", "ALL"], default="ALL", help="Timeframe: H1, H4, D1, ou ALL (cumulé)"
    )
    parser.add_argument(
        "--min-trades", type=int, default=100, help="Nombre minimum de trades pour qualifier un symbole"
    )
    parser.add_argument("--top", type=int, default=15, help="Afficher les N meilleurs symboles")
    args = parser.parse_args()

    data_dir = Path("data/historical")
    if not data_dir.exists():
        print("❌ data/historical/ introuvable")
        sys.exit(1)

    # Tous les symboles disponibles
    all_files = sorted(data_dir.glob("*.parquet"))
    symbols_set = set()
    for f in all_files:
        stem = f.stem  # ex: "EURUSD_H1"
        parts = stem.split("_")
        sym = "_".join(parts[:-1])
        symbols_set.add(sym)
    all_symbols = sorted(symbols_set)

    print("=" * 120)
    print(f"  BACKTEST MOM20x3 — Univers de {len(all_symbols)} symboles (2012-2026)")
    print(f"  TF: {args.tf}  |  Min trades: {args.min_trades}")
    print(f"  Filtres: ADX slope + DI + DI Override + Pullback + Danger hours + Weekend")
    print(f"  Coûts: spread + slippage ({SLIPPAGE_PIPS} pip) + commission (${COMMISSION_PER_100K}/100K)")
    print(f"  Risk: {RISK_PER_TRADE * 100:.2f}%/trade, lots max {MAX_LOT}")
    print("=" * 120)

    start_all = datetime.utcnow()
    results = {}

    for sym in all_symbols:
        t0 = datetime.utcnow()

        if args.tf == "ALL":
            cls, clp, clpc = backtest_multi_tf(sym)
        else:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            if len(df) < MIN_BARS:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
            if len(df) < MIN_BARS:
                continue
            cls, clp, clpc = backtest_symbol(sym, args.tf, df)

        closed_s = [t for t in cls if t.closed]
        closed_p = [t for t in clp if t.closed]
        closed_pc = [t for t in clpc if t.closed]
        elapsed = (datetime.utcnow() - t0).total_seconds()

        if len(closed_pc) < args.min_trades:
            continue

        m_s = compute_metrics(closed_s, use_cost=False)
        m_p = compute_metrics(closed_p, use_cost=False)
        m_pc = compute_metrics(closed_pc, use_cost=True)
        costs = avg_costs(closed_pc)

        # PnL par trade (annualisé approximatif)
        bars_total = 0
        if args.tf == "ALL":
            for tf in ("H1", "H4", "D1"):
                fp = data_dir / f"{sym}_{tf}.parquet"
                if fp.exists():
                    df_sz = pd.read_parquet(fp)
                    bars_total += len(df_sz)
        else:
            fp = data_dir / f"{sym}_{args.tf}.parquet"
            if fp.exists():
                df_sz = pd.read_parquet(fp)
                bars_total = len(df_sz)

        results[sym] = {
            "simple": m_s,
            "prod": m_p,
            "prod_cost": m_pc,
            "costs": costs,
            "survives": m_pc.get("significant", False) and m_pc["win_rate"] > 50 and m_pc["total_pnl"] > 0,
            "total_bars": bars_total,
            "elapsed_s": round(elapsed, 1),
        }

        # Émoji
        emoji = "✅" if results[sym]["survives"] else "⚠️" if m_pc["win_rate"] > 50 else "❌"
        print(
            f"  {emoji} {sym:12s}  Prod+Cost: {m_pc['n']:>5d} trades  "
            f"WR={m_pc['win_rate']:>5.1f}%  PnL=${m_pc['total_pnl']:>+9.2f}  "
            f"PF={m_pc['profit_factor']:>5.2f}  DD={m_pc['max_drawdown_pct']:>5.1f}%  "
            f"Cost=${costs.get('avg_total_cost_usd', 0):>+5.2f}/tr  "
            f"{elapsed:.1f}s"
        )

    total_elapsed = (datetime.utcnow() - start_all).total_seconds()

    # ─── CLASSEMENT ────────────────────────────────
    if not results:
        print("\n❌ Aucun résultat. Vérifie les données dans data/historical/")
        return

    # Trier par PnL (prod+cost)
    ranked = sorted(results.items(), key=lambda x: x[1]["prod_cost"]["total_pnl"], reverse=True)

    print(f"\n{'=' * 120}")
    print(f"  🏆 CLASSEMENT — MOM20x3 PROD+COÛTS ({args.tf}) — 2012-2026")
    print(f"  Trie par PnL net (après tous filtres + coûts)")
    print(f"{'=' * 120}")
    print(
        f"  {'#':>2s} {'Symbole':12s} {'TF':>4s} {'Trades':>6s} {'WR':>5s}  {'PnL':>10s}  {'PF':>5s}  {'DD':>5s}  "
        f"{'AvgWin':>7s}  {'AvgLoss':>7s}  {'Cost/tr':>7s}  {'Signif':>7s}"
    )
    print(f"  {'-' * 105}")

    survivors = []
    for rank, (sym, r) in enumerate(ranked[: args.top], 1):
        pc = r["prod_cost"]
        c = r["costs"]
        survive = "✅" if r["survives"] else "⚠️" if pc["win_rate"] > 50 else "❌"
        sig = "✅" if pc.get("significant") else "❌"
        print(
            f"  {rank:>2d} {sym:12s} {args.tf:>4s} {pc['n']:>6d} {pc['win_rate']:>4.1f}%{survive} "
            f"${pc['total_pnl']:>+9.2f} {pc['profit_factor']:>5.2f} {pc['max_drawdown_pct']:>5.1f}% "
            f"${pc.get('avg_win', 0):>+6.2f} ${pc.get('avg_loss', 0):>+6.2f} "
            f"${c.get('avg_total_cost_usd', 0):>+6.2f} {sig}"
        )
        if r["survives"]:
            survivors.append(sym)

    # Totaux
    total_n = sum(r["prod_cost"]["n"] for _, r in ranked)
    total_pnl = sum(r["prod_cost"]["total_pnl"] for _, r in ranked)
    total_wins = sum(r["prod_cost"]["wins"] for _, r in ranked)
    total_gp = sum(r["prod_cost"]["gross_profit"] for _, r in ranked)
    total_gl = sum(r["prod_cost"]["gross_loss"] for _, r in ranked)
    total_wr = total_wins / total_n * 100 if total_n else 0
    total_pf = total_gp / total_gl if total_gl > 0 else 0
    print(f"  {'-' * 105}")
    print(f"  {'TOTAL':>15s} {total_n:>6d} {total_wr:>4.1f}%   ${total_pnl:>+9.2f} {total_pf:>5.2f}")
    print()

    # ─── TOP SURVIVORS ────────────────────────────
    print(f"  🏆 SYMBOLES QUI SURVIVENT (WR>50%, PnL>0, p<0.05)")
    if survivors:
        for i, sym in enumerate(survivors, 1):
            r = results[sym]
            pc = r["prod_cost"]
            c = r["costs"]
            avg_pnl_per_trade = pc["total_pnl"] / pc["n"]
            print(
                f"  {i:>2d}. {sym:12s}  {pc['n']:>5d} trades  WR={pc['win_rate']:>5.1f}%  "
                f"PnL=${pc['total_pnl']:>+9.2f}  PF={pc['profit_factor']:>4.2f}  "
                f"DD={pc['max_drawdown_pct']:>4.1f}%  "
                f"${avg_pnl_per_trade:>+.2f}/trade  Cost=${c.get('avg_total_cost_usd', 0):>+.2f}/tr"
            )
    else:
        print("  ❌ Aucun symbole ne survit aux coûts réels avec ces paramètres.")

    # ─── RECOMMENDATION ───────────────────────────
    print(f"\n  {'═' * 60}")
    print(f"  RECOMMANDATION POUR LE CHALLENGE FTMO 200K$")
    print(f"  {'═' * 60}")
    pnl_surv = sum(results[s]["prod_cost"]["total_pnl"] for s in survivors) if survivors else 0
    if survivors:
        print(f"  Symboles recommandés: {', '.join(survivors)}")
        print(f"  PnL net cumulé: ${pnl_surv:+.2f}")
        print(f"  ✅ Un portefeuille de {len(survivors)} symboles a un edge réel après coûts.")
    else:
        print(f"  ❌ Aucun symbole ne survit. La stratégie MOM20x3 n'a pas d'edge net")
        print(f"     après coûts sur H1 avec les paramètres actuels.")
        print(f"  → Solutions:")
        print(f"    1. Réduire les coûts (spread plus serré, broker différent)")
        print(f"    2. Augmenter la taille des lots pour que le gain moyen écrase les coûts")
        print(f"    3. Changer de timeframe (H4 ou D1 donne peut-être plus d'edge)")
        print(f"    4. Changer de stratégie (MOM20x3 n'est pas rentable net sur Forex)")

    # Sauvegarde
    out = Path("runtime")
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "timeframe": args.tf,
            "initial_balance": INITIAL_BALANCE,
            "risk_per_trade": RISK_PER_TRADE,
            "slippage_pips": SLIPPAGE_PIPS,
            "commission_per_100k": COMMISSION_PER_100K,
            "danger_hours": DANGER_HOURS,
            "min_trades": args.min_trades,
        },
        "per_symbol": results,
        "ranking": [
            {
                "rank": i + 1,
                "symbol": s,
                "pnl": results[s]["prod_cost"]["total_pnl"],
                "wr": results[s]["prod_cost"]["win_rate"],
                "pf": results[s]["prod_cost"]["profit_factor"],
                "survives": results[s]["survives"],
            }
            for i, (s, _) in enumerate(ranked)
        ],
        "survivors": survivors,
        "totals": {"n": total_n, "wr": round(total_wr, 1), "pnl": round(total_pnl, 2), "pf": round(total_pf, 2)},
    }

    def cj(o):
        if isinstance(o, dict):
            return {k: cj(v) for k, v in o.items()}
        if isinstance(o, list):
            return [cj(v) for v in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.bool_):
            return bool(o)
        return o

    with open(out / "backtest_universe_report.json", "w") as f:
        json.dump(cj(report), f, indent=2)
    print(f"\n  Rapport: runtime/backtest_universe_report.json")
    print(f"  Terminé en {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
