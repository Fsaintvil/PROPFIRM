"""
Walk-Forward Validation — DONCHIAN sur USDJPY
5 folds : 2012-2014 / 2015-2017 / 2018-2020 / 2021-2023 / 2024-2026
Mesure la stabilité OOS du PF=1.00 observé
"""

import json, os, sys, math
from datetime import datetime
from pathlib import Path
from math import erf, sqrt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Paramètres ──
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044
MIN_BARS = 100
TIMEOUT_BARS = 80
MAX_LOT = 1.0
MIN_TRADES = 15

SPREAD = 1.5
SLIPPAGE = 1.0
COMMISSION = 7.0

# Folds temporels
FOLDS = [
    ("2012-2014", "2012-01-01", "2015-01-01"),
    ("2015-2017", "2015-01-01", "2018-01-01"),
    ("2018-2020", "2018-01-01", "2021-01-01"),
    ("2021-2023", "2021-01-01", "2024-01-01"),
    ("2024-2026", "2024-01-01", "2027-01-01"),
]

DANGER_HOURS = [0, 1, 2, 3, 4, 5, 22, 23]
TRAILING_LEVELS = {
    "TREND_UP": [(1.0, 0.60), (2.0, 0.40), (3.0, 0.20), (5.0, 0.10)],
    "TREND_DOWN": [(1.0, 0.60), (2.0, 0.40), (3.0, 0.20), (5.0, 0.10)],
    "RANGING": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.10)],
}


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
    ema50 = np.full(n, np.nan)
    a50 = 2 / 51
    ema50[0] = close[0]
    for i in range(1, n):
        ema50[i] = close[i] * a50 + ema50[i - 1] * (1 - a50)
    dc_high = np.full(n, np.nan)
    dc_low = np.full(n, np.nan)
    for i in range(20, n):
        dc_high[i] = np.max(high[i - 20 : i])
        dc_low[i] = np.min(low[i - 20 : i])
    return {"atr": atr_arr, "adx": adx_arr, "dc_high": dc_high, "dc_low": dc_low, "ema50": ema50}


def strat_donchian(close, high, low, times, ind):
    n = len(close)
    dt_hours = np.array([t.hour for t in times], dtype=int)
    dt_weekday = np.array([t.weekday() for t in times], dtype=int)
    signals = np.full(n, None, dtype=object)
    for i in range(MIN_BARS, n):
        if dt_weekday[i] >= 5:
            continue
        if dt_hours[i] in DANGER_HOURS:
            continue
        dc_h = ind["dc_high"][i]
        dc_l = ind["dc_low"][i]
        adx_v = ind["adx"][i]
        atr_v = ind["atr"][i]
        ema50_v = ind["ema50"][i]
        price = close[i]
        if np.isnan(dc_h) or np.isnan(dc_l) or np.isnan(atr_v) or atr_v <= 0:
            continue
        if np.isnan(adx_v) or np.isnan(ema50_v):
            continue
        action = None
        score = 0.0
        if price > dc_h and adx_v > 20 and price > ema50_v:
            action = "BUY"
            score = 0.50 + min(0.45, ((price - dc_h) / atr_v) * 0.15)
        elif price < dc_l and adx_v > 20 and price < ema50_v:
            action = "SELL"
            score = 0.50 + min(0.45, ((dc_l - price) / atr_v) * 0.15)
        if action is None or score < 0.60:
            continue
        regime = "TREND_UP" if action == "BUY" else "TREND_DOWN"
        signals[i] = {
            "action": action,
            "score": min(0.95, score),
            "atr": atr_v,
            "adx": adx_v,
            "regime": regime,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
        }
    return signals


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
        "commission_usd",
        "cost_pips",
        "strategy",
        "_pip_size",
        "_pip_value",
        "_contract_size",
        "_spread_pips",
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
        self.strategy = "DONCHIAN"
        self._pip_size = 0.01
        self._pip_value = 1.0
        self._contract_size = 100_000
        self._spread_pips = 1.5
        self._calc_lot(entry, sl, balance)

    def _pip_value_corrected(self, price=None):
        pip_per_lot = self._contract_size * self._pip_size
        rate = abs(price if price else self.entry)
        return pip_per_lot / max(rate, 1e-10)

    def _notional_usd(self):
        return self.lot * self._contract_size

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
        self.cost_pips = self._spread_pips + SLIPPAGE
        notional = self._notional_usd()
        self.commission_usd = (notional / 100_000) * COMMISSION * 2
        pips_cost = pips - self.cost_pips
        self.profit_usd_cost = pips_cost * usdpp - self.commission_usd

    def update_peak(self, high, low):
        if self.closed:
            return
        if self.direction == 0 and high > self.peak_price:
            self.peak_price = high
        elif self.direction == 1 and low < self.peak_price:
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


def backtest_period(df, tf):
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    times = df["timestamp"].values
    times_dt = pd.to_datetime(times)
    ind = precalc_indicators(high, low, close)
    sigs = strat_donchian(close, high, low, times_dt, ind)
    n = len(close)
    trades = []
    open_t = []
    bars_since = 999
    timeout = TIMEOUT_BARS if tf in ("H1",) else 40

    for i in range(MIN_BARS, n):
        atr_v = ind["atr"][i] if not np.isnan(ind["atr"][i]) else 0
        still = []
        for t in open_t:
            t.update_peak(high[i], low[i])
            tatr = atr_v if atr_v > 0 else t.atr_val
            t.check_partial(tatr)
            t.update_trailing(tatr)
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            if not t.closed and i - t.open_bar > timeout:
                t.closed = True
                t.close_price = close[i]
                t.close_time = times[i]
                t.result = "TIMEOUT"
                t.bars_held = i - t.open_bar
                t._calc_pnl()
            if not t.closed:
                still.append(t)
        open_t = still
        bars_since += 1
        if atr_v <= 0:
            continue
        sig = sigs[i]
        if sig is not None and bars_since >= 2:
            sd = sig["sl_atr"] * atr_v
            td = sig["tp_atr"] * atr_v
            if sig["action"] == "BUY":
                sp = close[i] - sd
                tp = close[i] + td
            else:
                sp = close[i] + sd
                tp = close[i] - td
            if sd > 0 and td / sd >= 1.5:
                if not any(t.action == sig["action"] for t in open_t):
                    t = SimTrade(
                        "USDJPY", sig["action"], close[i], sp, tp, atr_v, sig["regime"], i, times[i], INITIAL_BALANCE
                    )
                    trades.append(t)
                    open_t.append(t)
                    bars_since = 0
    return trades


def compute_metrics(closed):
    if not closed:
        return {"n": 0}
    wins = [t for t in closed if t.profit_usd_cost > 0]
    losses = [t for t in closed if t.profit_usd_cost <= 0]
    n, nw = len(closed), len(wins)
    wr = nw / n * 100 if n else 0
    tp = sum(t.profit_usd_cost for t in closed)
    gp = sum(max(0, t.profit_usd_cost) for t in closed)
    gl = abs(sum(min(0, t.profit_usd_cost) for t in closed))
    pf = gp / gl if gl > 0 else (0 if gp <= 0 else float("inf"))
    peak = INITIAL_BALANCE
    dd_max = 0.0
    bal = INITIAL_BALANCE
    for t in sorted(closed, key=lambda x: x.close_time or x.open_time):
        bal += t.profit_usd_cost
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        dd_max = max(dd_max, dd)
    avg_win = gp / nw if nw else 0
    avg_loss = -gl / (n - nw) if n > nw else 0
    avg_cost = sum(abs(t.profit_usd - t.profit_usd_cost) for t in closed) / n if n else 0
    p_val = 1.0
    if n >= 5:
        z = (wr / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
        p_val = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    return {
        "n": n,
        "wins": nw,
        "losses": n - nw,
        "win_rate": round(wr, 1),
        "total_pnl": round(tp, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(dd_max, 1),
        "p_value": round(p_val, 4),
        "significant": p_val < 0.05 and wr > 50,
        "avg_pnl": round(tp / n, 2) if n else 0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_cost": round(avg_cost, 2),
    }


def main():
    print("=" * 90)
    print("  WALK-FORWARD VALIDATION — DONCHIAN USDJPY")
    print("  5 folds temporels indépendants")
    print(f"  Coûts: spread {SPREAD}p + slippage {SLIPPAGE}p + commission ${COMMISSION}/100K")
    print("=" * 90)

    data_dir = Path("data/historical")
    all_results = {}
    combined_closed = []

    for label, start, end in FOLDS:
        t0 = datetime.utcnow()
        fold_trades = []
        for tf in ("H1",):
            fp = data_dir / f"USDJPY_{tf}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            mask = (df["timestamp"] >= start) & (df["timestamp"] < end)
            df = df[mask].reset_index(drop=True)
            if len(df) < MIN_BARS:
                continue
            trades = backtest_period(df, tf)
            fold_trades.extend(trades)

        closed = [t for t in fold_trades if t.closed]
        combined_closed.extend(closed)
        m = compute_metrics(closed)
        elapsed = (datetime.utcnow() - t0).total_seconds()
        all_results[label] = m

        if m["n"] >= MIN_TRADES:
            status = "✅" if m["significant"] and m["total_pnl"] > 0 else "⚠️" if m["profit_factor"] >= 0.95 else "❌"
        else:
            status = "⏭️"

        print(f"\n  ─── Fold {label} ({start} → {end}) [{elapsed:.1f}s] ───")
        if m["n"] >= MIN_TRADES:
            print(
                f"    {status} Trades={m['n']:>4d}  WR={m['win_rate']:>5.1f}%  "
                f"PnL=${m['total_pnl']:>+9.2f}  PF={m['profit_factor']:>5.2f}  "
                f"DD={m['max_drawdown_pct']:>4.1f}%  p={m['p_value']:.4f}"
            )
        else:
            print(f"    {status} Trades={m['n']:>4d} (< {MIN_TRADES})")

    # Résumé consolidé
    print(f"\n{'=' * 90}")
    print(f"  RÉSUMÉ WALK-FORWARD DONCHIAN USDJPY")
    print(f"{'=' * 90}")

    header = f"  {'Fold':<14s} {'Trades':>7s} {'WR':>6s} {'PnL':>10s} {'PF':>6s} {'DD':>6s} {'p-val':>7s}"
    print(header)
    print(f"  {'-' * 60}")

    total_pnl = 0
    total_n = 0
    positive_folds = 0
    for label, m in all_results.items():
        pnl = m.get("total_pnl", 0)
        total_pnl += pnl
        total_n += m.get("n", 0)
        if pnl > 0:
            positive_folds += 1
        if m["n"] >= MIN_TRADES:
            sig = "✅" if m.get("significant") and pnl > 0 else "⚠️" if m.get("profit_factor", 0) >= 0.95 else "❌"
            print(
                f"  {label:<14s} {m['n']:>7d} {m['win_rate']:>5.1f}% "
                f"${pnl:>+8.0f}{sig} {m['profit_factor']:>5.2f} {m['max_drawdown_pct']:>5.1f}% {m['p_value']:>6.4f}"
            )
        else:
            print(f"  {label:<14s} {m['n']:>7d} {'N/A':>6s} ${pnl:>+8.0f} {'N/A':>6s} {'N/A':>6s} {'N/A':>7s}")

    # Consolidated
    cm = compute_metrics(combined_closed)
    print(f"\n  {'─' * 60}")
    print(
        f"  TOTAL CONSOLIDÉ: {cm['n']:>4d} trades | WR={cm['win_rate']}% | "
        f"PnL=${cm['total_pnl']:>+.2f} | PF={cm['profit_factor']} | DD={cm['max_drawdown_pct']}% | "
        f"p={cm['p_value']:.4f}"
    )

    # Verdict
    print(f"\n  {'═' * 60}")
    print(f"  VERDICT")
    print(f"  {'═' * 60}")
    if total_n >= 100:
        if cm["profit_factor"] >= 1.0 and cm["significant"]:
            print(f"  ✅ DONCHIAN USDJPY est ROBUSTE — PF={cm['profit_factor']} sur {cm['n']} trades OOS")
            print(f"     Recommandation: STRATÉGIE VIABLE pour usage futur")
        elif cm["profit_factor"] >= 0.95:
            print(f"  ⚠️ DONCHIAN USDJPY est quasi-robuste — PF={cm['profit_factor']} sur {cm['n']} trades")
            print(f"     Recommandation: GARDER EN RÉSERVE, pas utilisable seul")
        else:
            print(f"  ❌ DONCHIAN USDJPY n'est PAS ROBUSTE — PF={cm['profit_factor']}")
            print(f"     Recommandation: ABANDONNER le Forex")
    else:
        print(f"  ⚠️ Pas assez de données pour un verdict ({total_n} trades)")

    print(f"  Folds positifs: {positive_folds}/{len(FOLDS)}")
    print(f"  PnL total OOS: ${total_pnl:+.2f}")

    # Sauvegarde
    out = Path("runtime")
    out.mkdir(parents=True, exist_ok=True)
    verdict = (
        "robuste"
        if cm.get("profit_factor", 0) >= 1.0 and cm.get("significant")
        else "quasi-robuste"
        if cm.get("profit_factor", 0) >= 0.95
        else "non-robuste"
    )
    report = {
        "type": "walkforward_donchian_usdjpy",
        "timestamp": datetime.utcnow().isoformat(),
        "folds": all_results,
        "consolidated": cm,
        "positive_folds": positive_folds,
        "total_folds": len(FOLDS),
        "verdict": verdict,
        "recommendation": "VIABLE" if verdict == "robuste" else "RESERVE" if verdict == "quasi-robuste" else "ABANDON",
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

    with open(out / "walkforward_donchian_usdjpy.json", "w") as f:
        json.dump(cj(report), f, indent=2)

    print(f"\n  Rapport: runtime/walkforward_donchian_usdjpy.json")


if __name__ == "__main__":
    main()
