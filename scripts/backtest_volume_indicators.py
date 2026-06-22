"""
Backtest — Impact des indicateurs volume (RVOL, CMF, OBV Divergence) sur le MOM20x3.

Compare deux modes :
  - BASELINE : MOM20x3 pur (sans filtres volume)
  - VOLUME   : MOM20x3 + RVOL/CMF/OBV Divergence (Phases 7b et 8 du SignalPipeline)

Optimisé avec pré-calcul vectorisé.

Usage:
    python scripts/backtest_volume_indicators.py
    python scripts/backtest_volume_indicators.py --symbols XAUUSD,BTCUSD
    python scripts/backtest_volume_indicators.py --all
    python scripts/backtest_volume_indicators.py --export
"""

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx_arrays, ema, chaikin_money_flow, relative_volume, obv_divergence
from engine_simple.strategy import _get_symbol_config

# ── Config ──
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.004
MIN_BARS = 100
MAX_BARS = 5000  # ~7 mois H1, ~3 semaines M15
TIMEOUT_BARS = 120
MIN_SIGNAL_SCORE = 0.55
TIMEFRAME = "M15"  # "H1" ou "M15" — test sur M15 pour plus de sensibilité volume

RVOL_LOW = 0.5
RVOL_HIGH = 2.0
# Pénalités volume (22 Juin 2026)
# Note: seuils CMF et OBV sont PER-SYMBOL (lus depuis default.yaml via sym_cfg)
# BTCUSD: cmf_threshold=0.20, obv_div_penalty_high=0.85, obv_div_penalty_low=0.92
# Forex/Indices: cmf_threshold=0.10, obv_div_penalty_high=0.70, obv_div_penalty_low=0.85
RVOL_PENALTY_LOW = 0.75  # was 0.85
RVOL_BOOST_HIGH = 1.10  # unchanged
CMF_CONFLICT_PENALTY = 0.85  # was 0.92 (conflit directionnel)
CMF_BOOST = 1.08  # unchanged (bonus quand CMF confirme direction)

PROD_SYMBOLS = ["XAUUSD", "BTCUSD", "US500.cash"]


def get_pip_info(symbol):
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    elif symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash"):
        return 0.01, 1.0
    elif symbol in ("BTCUSD", "ETHUSD"):
        return 0.01, 1.0
    elif symbol in ("USOIL.cash", "UKOIL.cash"):
        return 0.01, 1.0
    return 0.0001, 10.0


def precompute_indicators(close, high, low, volume, sym_cfg, period):
    """Pré-calcule tous les indicateurs pour tout l'historique (vectorisé).

    Returns:
        dict avec arrays numpy pré-calculés
    """
    n = len(close)

    # ATR (permet d'avoir atr[-1] pour chaque position)
    atr_arr = np.full(n, np.nan)
    raw_atr = atr(high, low, close, 14)
    if raw_atr is not None and len(raw_atr) > 0:
        atr_arr[: len(raw_atr)] = raw_atr

    # ADX + DI (utilise adx_arrays pour avoir les arrays complets)
    adx_arr = np.full(n, np.nan)
    plus_di_arr = np.full(n, np.nan)
    minus_di_arr = np.full(n, np.nan)
    try:
        raw_adx, raw_pdi, raw_mdi = adx_arrays(high, low, close, 14)
        if raw_adx is not None and hasattr(raw_adx, "__len__"):
            length = min(len(raw_adx), n)
            adx_arr[:length] = raw_adx[:length]
            plus_di_arr[:length] = raw_pdi[:length]
            minus_di_arr[:length] = raw_mdi[:length]
    except Exception as e:
        pass

    # EMA20
    ema20_arr = ema(close, 20)

    # Momentum (période configurable)
    mom_arr = np.full(n, np.nan)
    if n > period:
        mom_arr[period:] = close[period:] - close[:-period]

    return {
        "atr": atr_arr,
        "adx": adx_arr,
        "plus_di": plus_di_arr,
        "minus_di": minus_di_arr,
        "ema20": ema20_arr,
        "mom": mom_arr,
    }


def apply_volume_filters_at(i, action, score, close, high, low, volume, lookback=60, sym_cfg=None):
    """Applique RVOL + CMF + OBV Divergence à la position i.

    Les seuils CMF et pénalités OBV sont configurables par symbole (sym_cfg).
    """
    start = max(0, i - lookback)
    c_slice = close[start : i + 1]
    h_slice = high[start : i + 1]
    l_slice = low[start : i + 1]
    v_slice = volume[start : i + 1]

    if len(c_slice) < 20:
        return score

    # Lecture des seuils par symbole depuis la config (default.yaml / production.yaml)
    if sym_cfg is None:
        sym_cfg = {}
    cmf_threshold = sym_cfg.get("cmf_threshold", 0.10)
    obv_high = sym_cfg.get("obv_div_penalty_high", 0.70)
    obv_low = sym_cfg.get("obv_div_penalty_low", 0.85)

    orig_score = score
    adj_log = []  # log des ajustements pour debug

    # RVOL (pénalité renforcée 0.75)
    rvol = relative_volume(v_slice, period=50)
    if rvol < RVOL_LOW:
        score *= RVOL_PENALTY_LOW
        _vol_debug["rvol_low"] += 1
        adj_log.append(f"RVOL_LOW({rvol:.2f})×{RVOL_PENALTY_LOW}")
    elif rvol > RVOL_HIGH:
        old = score
        score = min(0.95, score * RVOL_BOOST_HIGH)
        _vol_debug["rvol_high"] += 1
        adj_log.append(f"RVOL_HIGH({rvol:.2f})×{RVOL_BOOST_HIGH}")

    # CMF (seuil par symbole : BTCUSD=0.20, forex/indices=0.10)
    cmf = chaikin_money_flow(c_slice, h_slice, l_slice, v_slice, period=20)
    if cmf > cmf_threshold:
        _vol_debug["cmf_bull"] += 1
        if action == "BUY":
            score = min(0.95, score * CMF_BOOST)
            adj_log.append(f"CMF_BULL({cmf:.3f})×{CMF_BOOST}")
        else:
            score = max(0.3, score * CMF_CONFLICT_PENALTY)
            adj_log.append(f"CMF_BULL({cmf:.3f})×{CMF_CONFLICT_PENALTY}")
    elif cmf < -cmf_threshold:
        _vol_debug["cmf_bear"] += 1
        if action == "SELL":
            score = min(0.95, score * CMF_BOOST)
            adj_log.append(f"CMF_BEAR({cmf:.3f})×{CMF_BOOST}")
        else:
            score = max(0.3, score * CMF_CONFLICT_PENALTY)
            adj_log.append(f"CMF_BEAR({cmf:.3f})×{CMF_CONFLICT_PENALTY}")

    # OBV Divergence (pénalités par symbole : BTCUSD=0.85/0.92, forex=0.70/0.85)
    div_type, div_strength = obv_divergence(c_slice, v_slice, period=20)
    if div_type != "none":
        _vol_debug["obv_div"] += 1
        penalty = obv_high if div_strength > 0.5 else obv_low
        score = max(0.3, score * penalty)
        adj_log.append(f"OBV_{div_type}({div_strength:.2f})×{penalty}")

    if score < orig_score:
        _vol_debug["score_drop"] += 1
    elif score > orig_score:
        _vol_debug["score_boost"] += 1
    _vol_debug["total"] += 1

    return score


# Compteurs globaux pour diagnostic
_vol_debug = {
    "rvol_low": 0,
    "rvol_high": 0,
    "cmf_bull": 0,
    "cmf_bear": 0,
    "obv_div": 0,
    "score_drop": 0,
    "score_boost": 0,
    "rejected": 0,
    "total": 0,
}


def run_backtest(symbol, close, high, low, volume, sym_cfg, period, use_volume_filters):
    """Backtest MOM20x3 avec/sans volume, utilisant des indicateurs pré-calculés."""
    n = len(close)
    pip_size, pip_value = get_pip_info(symbol)

    # Pré-calcul vectorisé
    ind = precompute_indicators(close, high, low, volume, sym_cfg, period)

    trades = []
    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    max_dd = 0.0
    atr_arr = ind["atr"]

    start_idx = max(MIN_BARS, n - MAX_BARS)
    for i in range(start_idx, n):
        if i < period + 14:
            continue

        current_atr = float(atr_arr[i])
        if np.isnan(current_atr) or current_atr <= 0:
            continue

        # Momentum
        mom = ind["mom"][i]
        if np.isnan(mom):
            continue
        mom_abs = abs(mom)

        # ADX / style de marché
        adx_val = ind["adx"][i]
        is_trending = not np.isnan(adx_val) and adx_val >= 25

        # Seuil
        thresh = sym_cfg["threshold_trending"] if is_trending else sym_cfg["threshold_ranging"]
        thresh = max(1.5, min(2.5, thresh))
        threshold_value = thresh * current_atr

        if mom_abs < threshold_value:
            continue

        # Direction + DI filter
        plus_di = ind["plus_di"][i]
        minus_di = ind["minus_di"][i]
        if mom > 0:
            action = "BUY"
            if not np.isnan(plus_di) and not np.isnan(minus_di) and plus_di <= minus_di * 0.8:
                continue
        else:
            action = "SELL"
            if not np.isnan(plus_di) and not np.isnan(minus_di) and minus_di <= plus_di * 0.8:
                continue

        # Pullback (EMA20)
        ema20_val = ind["ema20"][i]
        if not np.isnan(ema20_val) and ema20_val > 0:
            pullback_dist = (close[i] - ema20_val) / ema20_val * 100
            atr_mult = sym_cfg["pullback_band_trending"] if is_trending else sym_cfg["pullback_band_ranging"]
            pullback_band = (atr_mult * current_atr) / ema20_val * 100
            pullback_band = max(0.05, min(1.0, pullback_band))
            if abs(pullback_dist) >= pullback_band:
                continue

        # Score
        score = 0.60 + min(mom_abs / (threshold_value * 3), 0.35)

        # Volume filters (passe sym_cfg pour seuils par symbole)
        if use_volume_filters:
            score = apply_volume_filters_at(i, action, score, close, high, low, volume, sym_cfg=sym_cfg)
            if score < MIN_SIGNAL_SCORE:
                _vol_debug["rejected"] += 1
                continue

        # SL/TP
        if is_trending:
            sl_dist = sym_cfg["sl_atr_trending"] * current_atr
            tp_dist = sym_cfg["tp_atr_trending"] * current_atr
        else:
            sl_dist = sym_cfg["sl_atr_ranging"] * current_atr
            tp_dist = sym_cfg["tp_atr_ranging"] * current_atr

        entry = close[i]
        if action == "BUY":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        # Simulation SL/TP
        result = None
        exit_price = entry
        end = min(i + TIMEOUT_BARS, n)
        for j in range(i + 1, end):
            if action == "BUY":
                if low[j] <= sl:
                    result = "SL"
                    exit_price = sl
                    break
                elif high[j] >= tp:
                    result = "TP"
                    exit_price = tp
                    break
            else:
                if high[j] >= sl:
                    result = "SL"
                    exit_price = sl
                    break
                elif low[j] <= tp:
                    result = "TP"
                    exit_price = tp
                    break

        if result is None:
            result = "TIMEOUT"
            exit_price = close[end - 1]

        # PnL
        if action == "BUY":
            pips = (exit_price - entry) / pip_size
        else:
            pips = (entry - exit_price) / pip_size

        risk_usd = balance * RISK_PER_TRADE
        risk_in_pips = sl_dist / pip_size
        lot = risk_usd / (risk_in_pips * pip_value) if risk_in_pips > 0 else 0.01
        lot = max(0.01, min(1.0, lot))

        profit_usd = pips * lot * pip_value
        balance += profit_usd

        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance
        if dd > max_dd:
            max_dd = dd

        trades.append(
            {
                "bar": i,
                "action": action,
                "entry": entry,
                "exit": exit_price,
                "result": result,
                "profit_usd": profit_usd,
                "lot": lot,
                "score": round(score, 3),
            }
        )

    return trades, balance, max_dd


def compute_stats(trades, final_balance, max_dd):
    if not trades:
        return {"trades": 0}
    wins = [t for t in trades if t["profit_usd"] > 0]
    losses = [t for t in trades if t["profit_usd"] <= 0]
    total_pnl = sum(t["profit_usd"] for t in trades)
    win_rate = len(wins) / len(trades) * 100
    gross_profit = sum(t["profit_usd"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["profit_usd"] for t in losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    sl_count = sum(1 for t in trades if t["result"] == "SL")
    tp_count = sum(1 for t in trades if t["result"] == "TP")
    expectancy = total_pnl / len(trades) if trades else 0
    max_consec = 0
    cur = 0
    for t in trades:
        if t["profit_usd"] <= 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0
    # Skyline : meilleure série de trades gagnants consécutifs
    max_consec_wins = 0
    cur_w = 0
    for t in trades:
        if t["profit_usd"] > 0:
            cur_w += 1
            max_consec_wins = max(max_consec_wins, cur_w)
        else:
            cur_w = 0
    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "pnl": total_pnl,
        "final_balance": final_balance,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "sl": sl_count,
        "tp": tp_count,
        "max_consec_losses": max_consec,
        "max_consec_wins": max_consec_wins,
    }


def print_comparison(sym, base, vol):
    if base["trades"] == 0:
        print(f"  {sym}: Aucun trade (baseline)")
        return
    if vol["trades"] == 0:
        print(f"  {sym}: Aucun trade (volume)")
        return

    print(f"\n  {'=' * 70}")
    print(f"  {sym}")
    print(f"  {'=' * 70}")
    print(f"  {'Métrique':<25} {'BASELINE':>12} {'VOLUME':>12} {'Δ':>12}")
    print(f"  {'─' * 25} {'─' * 12} {'─' * 12} {'─' * 12}")

    for label, key in [
        ("Trades", "trades"),
        ("Win Rate", "win_rate"),
        ("PnL Total", "pnl"),
        ("Profit Factor", "profit_factor"),
        ("Max DD", "max_dd_pct"),
        ("Expectancy", "expectancy"),
        ("Avg Win", "avg_win"),
        ("Avg Loss", "avg_loss"),
        ("Max Cons. Wins", "max_consec_wins"),
        ("Max Cons. Losses", "max_consec_losses"),
    ]:
        bv = base[key]
        vv = vol[key]
        diff = vv - bv

        if key in ("trades", "max_consec_wins", "max_consec_losses"):
            b_str = f"{int(bv):d}"
            v_str = f"{int(vv):d}"
            d_str = f"{int(diff):+d}"
        elif key in ("win_rate", "max_dd_pct"):
            b_str = f"{bv:.1f}%"
            v_str = f"{vv:.1f}%"
            d_str = f"{diff:+.1f}%"
        elif key in ("pnl", "expectancy", "avg_win", "avg_loss"):
            b_str = f"${bv:,.2f}"
            v_str = f"${vv:,.2f}"
            d_str = f"${diff:+,.2f}"
        else:
            b_str = f"{bv:.3f}"
            v_str = f"{vv:.3f}"
            d_str = f"{diff:+.3f}"

        is_good = False
        if key in ("win_rate", "pnl", "profit_factor", "expectancy", "avg_win", "max_consec_wins"):
            is_good = diff > 0
        elif key in ("max_dd_pct", "avg_loss", "max_consec_losses"):
            is_good = diff < 0

        marker = (
            " ✅"
            if is_good and abs(diff) > 0.001
            else (" ⚠️" if not is_good and abs(diff) > 0.001 and key not in ("trades",) else "")
        )
        print(f"  {label:<25} {b_str:>12} {v_str:>12} {d_str:>12}{marker}")

    rejection = (1 - vol["trades"] / base["trades"]) * 100
    print(f"  {'Réjection par volume':<25} {'':>12} {rejection:>11.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Backtest volume indicators impact")
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--export", action="store_true")
    parser.add_argument(
        "--tf", type=str, default=TIMEFRAME, choices=["M15", "H1", "H4"], help="Timeframe (défaut: M15)"
    )
    args = parser.parse_args()
    tf = args.tf

    if args.all:
        symbols = sorted(set(f.stem.replace(f"_{tf}", "") for f in Path("data/historical").glob(f"*_{tf}.parquet")))
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = PROD_SYMBOLS

    # Définir le nombre de barres selon le timeframe
    bar_limits = {"M15": 15000, "H1": 10000, "H4": 5000}
    effective_max = min(bar_limits.get(tf, 5000), 20000)

    print("=" * 80)
    print("  BACKTEST — IMPACT DES INDICATEURS VOLUME")
    print("  RVOL + CMF + OBV Divergence (Phases 7b et 8 du SignalPipeline)")
    print("=" * 80)
    print(f"  Timeframe: {tf} | Symboles: {', '.join(symbols)}")
    print(f"  Capital: ${INITIAL_BALANCE:,.0f} | Risque: {RISK_PER_TRADE * 100:.1f}%")
    print(f"  Pénalités: RVOL×{RVOL_PENALTY_LOW} CMF_BOOST×{CMF_BOOST} CMF_CONFLIT×{CMF_CONFLICT_PENALTY}")
    print(f"  Seuils CMF/OBV par symbole (config: cmf_threshold, obv_div_penalty_high/low)")
    print()

    data_dir = Path("data/historical")
    all_base, all_vol = [], []

    for sym in symbols:
        parquet_path = data_dir / f"{sym}_{tf}.parquet"
        if not parquet_path.exists():
            print(f"  {sym}_{tf}: Données non trouvées → skip")
            continue

        print(f"  ── {sym} ──")
        # Reset debug counters per symbol
        for k in _vol_debug:
            _vol_debug[k] = 0

        print(f"  Chargement...")
        df = pd.read_parquet(parquet_path)
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)

        end_idx = len(close)
        n_bars = min(effective_max, end_idx)
        start_idx = max(0, end_idx - n_bars)
        close = close[start_idx:end_idx]
        high = high[start_idx:end_idx]
        low = low[start_idx:end_idx]
        volume = volume[start_idx:end_idx]
        print(f"  {len(close)} barres ({tf})")

        sym_cfg = _get_symbol_config(sym)
        period = sym_cfg["momentum_period"]

        # BASELINE
        t0 = time.time()
        base_t, base_b, base_dd = run_backtest(sym, close, high, low, volume, sym_cfg, period, False)
        t_base = time.time() - t0
        bs = compute_stats(base_t, base_b, base_dd)
        print(f"  BASELINE: {bs['trades']} trades ({t_base:.1f}s) WR={bs['win_rate']:.1f}% PnL=${bs['pnl']:,.0f}")

        # VOLUME
        t0 = time.time()
        vol_t, vol_b, vol_dd = run_backtest(sym, close, high, low, volume, sym_cfg, period, True)
        t_vol = time.time() - t0
        vs = compute_stats(vol_t, vol_b, vol_dd)
        print(f"  VOLUME:   {vs['trades']} trades ({t_vol:.1f}s) WR={vs['win_rate']:.1f}% PnL=${vs['pnl']:,.0f}")

        # Debug volume filters
        if _vol_debug["total"] > 0:
            print(
                f"  VOL DEBUG: called={_vol_debug['total']}, "
                f"rvol_low={_vol_debug['rvol_low']}, rvol_high={_vol_debug['rvol_high']}, "
                f"cmf_bull={_vol_debug['cmf_bull']}, cmf_bear={_vol_debug['cmf_bear']}, "
                f"obv_div={_vol_debug['obv_div']}, "
                f"score_drop={_vol_debug['score_drop']}, score_boost={_vol_debug['score_boost']}, "
                f"rejected={_vol_debug['rejected']}"
            )
            # Analyse des scores ajustés
            scores_adj = [t.get("score", 0.6) for t in vol_t]
            near_rej = sum(1 for s in scores_adj if s < 0.60)
            print(
                f"  VOL SEUIL: trades < 0.60: {near_rej}/{len(scores_adj)} ({near_rej / max(len(scores_adj), 1) * 100:.1f}%)"
            )

        print_comparison(sym, bs, vs)
        all_base.extend(base_t)
        all_vol.extend(vol_t)

        if args.export:
            pd.DataFrame(base_t).to_csv(f"runtime/bt_vol_base_{sym}.csv", index=False)
            pd.DataFrame(vol_t).to_csv(f"runtime/bt_vol_vol_{sym}.csv", index=False)

    # Global
    print(f"\n  {'=' * 70}")
    print(f"  RÉSUMÉ GLOBAL")
    print(f"  {'=' * 70}")
    if all_base and all_vol:
        bg = compute_stats(all_base, 0, 0)
        vg = compute_stats(all_vol, 0, 0)
        pnl_diff = vg["pnl"] - bg["pnl"]
        wr_diff = vg["win_rate"] - bg["win_rate"]
        pf_diff = vg["profit_factor"] - bg["profit_factor"]

        print(f"  {'Métrique':<25} {'BASELINE':>12} {'VOLUME':>12} {'Δ':>12}")
        print(f"  {'─' * 25} {'─' * 12} {'─' * 12} {'─' * 12}")
        print(f"  {'Trades':<25} {bg['trades']:>12d} {vg['trades']:>12d} {(vg['trades'] - bg['trades']):>+12d}")
        print(f"  {'Win Rate':<25} {bg['win_rate']:>11.1f}% {vg['win_rate']:>11.1f}% {wr_diff:>+11.1f}%")
        print(f"  {'PnL Total':<25} ${bg['pnl']:>10,.0f} ${vg['pnl']:>10,.0f} ${pnl_diff:>+10,.0f}")
        print(f"  {'Profit Factor':<25} {bg['profit_factor']:>11.3f} {vg['profit_factor']:>11.3f} {pf_diff:>+11.3f}")

        improvement = (vg["pnl"] - bg["pnl"]) / max(abs(bg["pnl"]), 1) * 100
        print(f"\n  {'═' * 70}")
        if vg["win_rate"] > bg["win_rate"] and vg["pnl"] > bg["pnl"]:
            verdict = "✅ Les indicateurs VOLUME AMÉLIORENT les performances"
        elif vg["win_rate"] > bg["win_rate"] or vg["pnl"] > bg["pnl"]:
            verdict = "⚠️ Amélioration partielle — un des deux métriques s'améliore"
        elif vg["pnl"] < bg["pnl"] * 0.95:
            verdict = "❌ Les indicateurs volume DÉGRADENT les performances"
        else:
            verdict = "➖ Impact neutre"
        print(f"  {verdict}")
        print(f"  Δ PnL: {improvement:+.1f}%")
        print(f"  {'═' * 70}")

    if args.export:
        pd.DataFrame(all_base).to_csv("runtime/bt_vol_base_global.csv", index=False)
        pd.DataFrame(all_vol).to_csv("runtime/bt_vol_vol_global.csv", index=False)
        print(f"\n  Trades exportés dans runtime/bt_vol_*.csv")

    print()


if __name__ == "__main__":
    main()
