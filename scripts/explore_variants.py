"""Exploration de variantes MOM20x3 pour trouver une config FTMO-viable."""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

from engine_simple import strategy as strat_module
from scripts.backtest_full import *

RISK_PER_TRADE = 0.008
INITIAL_BALANCE = 200000.0
MIN_TRADES = 10

_orig_get = strat_module.get_symbol_full_config


def make_config(th_tr, th_rg, sl_atr, tp_atr):
    def get_cfg(symbol):
        cfg = _orig_get(symbol)
        cfg["threshold_trending"] = th_tr
        cfg["threshold_ranging"] = th_rg
        cfg["sl_atr_trending"] = sl_atr
        cfg["tp_atr_trending"] = tp_atr
        cfg["sl_atr_ranging"] = sl_atr
        cfg["tp_atr_ranging"] = tp_atr
        return cfg

    return get_cfg


class SimTradeNoTrailNoPartial(SimTrade):
    """SimTrade sans trailing ni partial TP."""

    def update_trailing(self, atr_v):
        pass

    def check_partial(self, atr_v, current_price=None):
        pass


def backtest_symbol_cfg(symbol, timeframe, df, cfg_maker, use_trailing=True):
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    volume = df.get("volume", df.get("tick_volume", np.ones(len(close)))).values.astype(float)
    times = df["timestamp"].values
    n = len(close)
    times_dt = pd.to_datetime(times)
    sym_cfg = cfg_maker(symbol)
    atr_arr, adx_arr, pdi, ndi, ema20, rvol_arr, cmf_arr, obv_type_arr, obv_div_arr = precalc_indicators(
        high, low, close, volume
    )
    ol_emu = OnlineLearnerEmu()
    trades = []
    open_trades = []
    bars_since_last = 999

    SimClass = SimTrade if use_trailing else SimTradeNoTrailNoPartial

    for i in range(MIN_BARS, n):
        atr_v = atr_arr[i] if not np.isnan(atr_arr[i]) else 0
        still_open = []
        for t in open_trades:
            t.update_peak(high[i], low[i])
            if use_trailing:
                tatr = atr_v if atr_v > 0 else t.atr_val
                t.check_partial(tatr, current_price=close[i])
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
                still_open.append(t)
            else:
                ol_emu.record_result(t.symbol, t.profit_usd_cost > 0)
        open_trades = still_open
        bars_since_last += 1
        if atr_v <= 0:
            continue

        signal, raw_score = gen_signal_bar(
            i,
            close,
            high,
            low,
            volume,
            times_dt,
            atr_arr,
            adx_arr,
            pdi,
            ndi,
            ema20,
            rvol_arr,
            cmf_arr,
            obv_type_arr,
            obv_div_arr,
            symbol,
            sym_cfg,
            ol_emu,
        )
        if signal is None or bars_since_last < 3:
            continue
        sd = signal["sl_atr"] * atr_v
        td = signal["tp_atr"] * atr_v
        if signal["action"] == "BUY":
            sp, tp = close[i] - sd, close[i] + td
        else:
            sp, tp = close[i] + sd, close[i] - td
        min_rr = sym_cfg.get("min_rr", 1.5)
        if sd > 0 and td / sd < min_rr:
            continue
        if any(t.action == signal["action"] for t in open_trades):
            continue
        t = SimClass(symbol, signal["action"], close[i], sp, tp, atr_v, signal["regime"], i, times[i], INITIAL_BALANCE)
        trades.append(t)
        open_trades.append(t)
        bars_since_last = 0
    return trades


def backtest_multi_tf_cfg(symbol, cfg_maker, use_trailing=True):
    all_t = []
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
        trades = backtest_symbol_cfg(symbol, tf, df, cfg_maker, use_trailing)
        all_t.extend(trades)
    return all_t


def test_config(name, cfg_maker, symbols, tfs="ALL", use_trailing=True):
    """Test une config et affiche les resultats."""
    print()
    print(f"  ═══ {name} ═══")
    for sym in symbols:
        t0 = datetime.utcnow()
        if tfs == "ALL":
            all_t = backtest_multi_tf_cfg(sym, cfg_maker, use_trailing)
        else:
            all_t = []
            for tf in tfs:
                fp = Path(f"data/historical/{sym}_{tf}.parquet")
                if not fp.exists():
                    continue
                df = pd.read_parquet(fp)
                if len(df) < MIN_BARS:
                    continue
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df[df["timestamp"] >= "2012-01-01"].reset_index(drop=True)
                if len(df) < MIN_BARS:
                    continue
                all_t.extend(backtest_symbol_cfg(sym, tf, df, cfg_maker, use_trailing))
        closed = [t for t in all_t if t.closed]
        if len(closed) < MIN_TRADES:
            print(f"    {sym:12s}  {len(closed):>4d} trades  ❌  (min {MIN_TRADES})")
            continue
        m = compute_metrics(closed)
        n = m["n"]
        wr = m["win_rate"]
        pnl = m["total_pnl"]
        pf = m["profit_factor"]
        dd = m["max_drawdown_pct"]
        emoji = "✅" if m.get("significant") and wr > 50 and pnl > 0 else "⚠️"
        elapsed = (datetime.utcnow() - t0).total_seconds()
        print(
            f"    {emoji} {sym:12s}  {n:>5d} trades  WR={wr:>5.1f}%  PnL=${pnl:>+10.2f}  PF={pf:>5.2f}  DD={dd:>5.1f}%  {elapsed:.1f}s"
        )
    return


# ═══════════════════════════════════════════════════════════════════
#  MAIN TEST SUITE
# ═══════════════════════════════════════════════════════════════════

TOP_SYMBOLS = ["BTCUSD", "XAGUSD", "US30.cash", "USOIL.cash", "XAUUSD", "XAGUSD"]

print("=" * 100)
print("  EXPLORATION DE VARIANTS — trouver une config FTMO-viable")
print("  Risk: 0.80%/trade, Balance: $200,000")
print("=" * 100)

# Test 1: Default config (reference)
strat_module.get_symbol_full_config = _orig_get
test_config("REFERENCE: Default MOM20x3 (thresh=2.5/2.0, trailing prod)", _orig_get, TOP_SYMBOLS)

# Test 2: Higher thresholds + trailing
test_config("VAR1: Thresh=4.0/3.5, SL=1.5, TP=6.0, trailing prod", make_config(4.0, 3.5, 1.5, 6.0), TOP_SYMBOLS)

# Test 3: Higher thresholds, NO trailing, NO partial
test_config(
    "VAR2: Thresh=4.0/3.5, SL=1.5, TP=6.0, NO trailing, NO partial",
    make_config(4.0, 3.5, 1.5, 6.0),
    TOP_SYMBOLS,
    use_trailing=False,
)

# Test 4: Tight SL, wide TP, NO trailing
test_config(
    "VAR3: Thresh=3.0/2.5, SL=1.0, TP=5.0, NO trailing",
    make_config(3.0, 2.5, 1.0, 5.0),
    TOP_SYMBOLS,
    use_trailing=False,
)

# Test 5: Very tight SL, very wide TP (betting on strong trends)
test_config(
    "VAR4: Thresh=4.0/3.5, SL=0.8, TP=5.0, NO trailing",
    make_config(4.0, 3.5, 0.8, 5.0),
    ["BTCUSD", "XAGUSD", "XAUUSD"],
    use_trailing=False,
)

# Test 6: XAUUSD H4 only — multiple configs
print()
print("  ═══ XAUUSD H4 ONLY — config search ═══")
for name, maker in [
    ("VAR5a: th=3.5/3.0 sl=1.5 tp=6.0 trailing", make_config(3.5, 3.0, 1.5, 6.0)),
    ("VAR5b: th=3.5/3.0 sl=1.5 tp=6.0 NO-trail", make_config(3.5, 3.0, 1.5, 6.0)),
    ("VAR5c: th=3.0/2.5 sl=2.0 tp=6.0 trailing", make_config(3.0, 2.5, 2.0, 6.0)),
    ("VAR5d: th=3.0/2.5 sl=2.0 tp=6.0 NO-trail", make_config(3.0, 2.5, 2.0, 6.0)),
]:
    use_tr = "trailing" in name
    test_config(name, maker, ["XAUUSD"], tfs=["H4"], use_trailing=use_tr)

# Test 7: XAGUSD with different params
print()
print("  ═══ XAGUSD config search ═══")
for name, maker in [
    ("VAR6a: th=4.0/3.5 sl=1.2 tp=4.0", make_config(4.0, 3.5, 1.2, 4.0)),
    ("VAR6b: th=3.5/3.0 sl=1.5 tp=5.0", make_config(3.5, 3.0, 1.5, 5.0)),
    ("VAR6c: th=5.0/4.0 sl=1.5 tp=6.0", make_config(5.0, 4.0, 1.5, 6.0)),
]:
    test_config(name, maker, ["XAGUSD"])

print()
print("=" * 100)
print("  FIN DES TESTS")
print("=" * 100)
