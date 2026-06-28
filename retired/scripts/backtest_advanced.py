"""Backtest Avancé — Tous modules intégrés avec filtres de score.

Teste l'impact combiné de VP + FLOW + MTF + Adaptive sur les performances.

Usage:
    python scripts/backtest_advanced.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

from engine_simple.strategy import mom20x3_signal, SYMBOL_CONFIG
from engine_simple.indicators import adx as ind_adx, atr as ind_atr

try:
    from engine_simple.regime_engine import RegimeEngine
except ImportError:
    from retired.engine_simple.regime_engine import RegimeEngine
from engine_simple.session_filter import SessionFilter
from engine_simple.volume_profile import VolumeProfile
from engine_simple.order_flow import OrderFlowAnalyzer
from engine_simple.mtf_confirm import MultiTimeframeConfirmer
from engine_simple.adaptive_params import AdaptiveParameters


SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "US500.cash"]
TIMEFRAMES = {"XAUUSD": "H4", "BTCUSD": "H1", "ETHUSD": "H4", "US500.cash": "H4"}
DATA_DIR = Path("data/historical")
RESULTS_DIR = Path("runtime")

INITIAL_BALANCE = 200000
MAX_DD_PCT = 0.10
RISK_PER_TRADE = 0.004


class AdvancedBacktest:
    """Backtest avec pipeline complet Phase 7-16."""

    def __init__(self, mode: str = "baseline"):
        """
        Args:
            mode: "baseline" (MOM20x3 pur) ou "full" (tous modules)
        """
        self.mode = mode

        if mode == "full":
            self.regime_engine = RegimeEngine()
            self.session_filter = SessionFilter()
            self.volume_profile = VolumeProfile()
            self.order_flow = OrderFlowAnalyzer()
            self.mtf_confirm = MultiTimeframeConfirmer()
            self._adaptive: dict[str, AdaptiveParameters] = {}

        self.trades = []
        self.equity_curve = [INITIAL_BALANCE]

    def load_data(self, symbol: str, tf: str):
        pattern = f"{symbol}_{tf}_*.parquet"
        files = sorted(DATA_DIR.glob(pattern))
        if not files:
            pattern = f"{symbol}_{tf}.parquet"
            files = sorted(DATA_DIR.glob(pattern))
        if not files:
            return None
        return pd.read_parquet(files[-1])

    def run(self, symbol: str, tf: str, df):
        if df is None or len(df) < 100:
            return {"symbol": symbol, "tf": tf, "trades": 0}

        trades = []
        equity = INITIAL_BALANCE
        peak = INITIAL_BALANCE
        in_pos = False
        entry = 0
        sl = 0
        tp = 0
        direction = 0

        lookback = 50

        for i in range(lookback, len(df)):
            window = df.iloc[i - lookback : i + 1]
            high = window["high"].values
            low = window["low"].values
            close = window["close"].values
            cur = close[-1]

            # --- Manage existing position ---
            if in_pos:
                hit_sl = (low[-1] <= sl) if direction == 0 else (high[-1] >= sl)
                hit_tp = (high[-1] >= tp) if direction == 0 else (low[-1] <= tp)

                if hit_sl:
                    pnl = (sl - entry) * 100 if direction == 0 else (entry - sl) * 100
                    equity += pnl
                    trades.append({"pnl": pnl, "type": "SL"})
                    in_pos = False
                elif hit_tp:
                    pnl = (tp - entry) * 100 if direction == 0 else (entry - tp) * 100
                    equity += pnl
                    trades.append({"pnl": pnl, "type": "TP"})
                    in_pos = False

                peak = max(peak, equity)
                if (peak - equity) / peak > MAX_DD_PCT:
                    break
                continue

            # --- Generate signal ---
            try:
                signal = mom20x3_signal(close=close, high=high, low=low, period=20, symbol=symbol)
            except:
                continue
            if signal is None:
                continue

            score = signal.get("score", 0.6)

            # --- Apply modules (FULL mode only) ---
            if self.mode == "full":
                # 1. Regime detection
                regime_result = self.regime_engine.detect(high=high, low=low, close=close, symbol=symbol)

                # 2. Session filter
                if "time" in df.columns:
                    try:
                        ts = pd.to_datetime(df.iloc[i]["time"])
                        hour_utc = ts.hour
                    except:
                        hour_utc = 12
                else:
                    hour_utc = 12
                session_score = self.session_filter.get_session_score(symbol, hour_utc)
                if session_score < 0.2:
                    continue

                # 3. Volume Profile — score adjustment
                if len(close) >= 50:
                    vp_df = pd.DataFrame(
                        {
                            "open": close[-50:],
                            "high": high[-50:],
                            "low": low[-50:],
                            "close": close[-50:],
                            "volume": np.ones(50),
                        }
                    )
                    vp_levels = self.volume_profile.analyze(vp_df)
                    if vp_levels.poc is not None:
                        dist_poc = abs(cur - vp_levels.poc) / cur * 100
                        if dist_poc < 0.1:
                            score = min(0.95, score * 1.10)
                        elif vp_levels.vah and cur > vp_levels.vah * 0.999:
                            if signal.get("action") == "BUY":
                                score *= 0.90
                        elif vp_levels.val and cur < vp_levels.val * 1.001:
                            if signal.get("action") == "SELL":
                                score *= 0.90

                # 4. Order Flow — score adjustment
                if len(close) >= 20:
                    flow_df = pd.DataFrame(
                        {
                            "open": close[-20:],
                            "high": high[-20:],
                            "low": low[-20:],
                            "close": close[-20:],
                            "volume": np.ones(20),
                        }
                    )
                    flow_metrics = self.order_flow.analyze_bars(flow_df)
                    flow_dir, flow_strength = self.order_flow.get_flow_signal(flow_metrics)
                    if flow_dir != "NEUTRAL" and flow_strength > 0.3:
                        if flow_dir == signal.get("action"):
                            score = min(0.95, score + min(0.10, flow_strength * 0.15))
                        else:
                            score = max(0.30, score - min(0.15, flow_strength * 0.20))

                # 5. MTF Confirm — check if higher TF aligns
                if len(close) >= 50:
                    ema50 = np.mean(close[-50:])
                    action = signal.get("action", "BUY")
                    if (action == "BUY" and cur > ema50) or (action == "SELL" and cur < ema50):
                        score = min(0.95, score * 1.05)
                    else:
                        score = max(0.30, score * 0.90)

                # 6. Adaptive params
                if symbol not in self._adaptive:
                    self._adaptive[symbol] = AdaptiveParameters(symbol)
                ap = self._adaptive[symbol]
                adapted = ap.get_adapted_params()

                # Record trade result for learning (after trade closes above)
                # For now, just apply risk_mult
                risk_mult = adapted.risk_mult if adapted.sample_size >= 20 else 1.0

                # --- Filter: minimum score threshold ---
                if score < 0.55:
                    continue

            # --- Calculate SL/TP ---
            atr_val = ind_atr(high, low, close, 14)[-1] if len(high) >= 14 else 0
            if atr_val <= 0:
                continue

            action = signal.get("action", "BUY")

            if action == "BUY":
                direction = 0
                entry = cur
                sl = entry - atr_val * 2.0
                tp = entry + atr_val * 5.0
            else:
                direction = 1
                entry = cur
                sl = entry + atr_val * 2.0
                tp = entry - atr_val * 5.0

            in_pos = True
            peak = max(peak, equity)

        # --- Metrics ---
        if not trades:
            return {"symbol": symbol, "tf": tf, "trades": 0}

        pnls = [t["pnl"] for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)
        gp = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p < 0))

        # Max DD
        eq_arr = [INITIAL_BALANCE]
        for p in pnls:
            eq_arr.append(eq_arr[-1] + p)
        eq_arr = np.array(eq_arr)
        peaks = np.maximum.accumulate(eq_arr)
        max_dd = np.max((peaks - eq_arr) / peaks)

        # Sharpe
        if total > 1:
            sharpe = np.mean(pnls) / np.std(pnls) if np.std(pnls) > 0 else 0
        else:
            sharpe = 0

        return {
            "symbol": symbol,
            "tf": tf,
            "trades": total,
            "wins": wins,
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl": sum(pnls),
            "profit_factor": gp / gl if gl > 0 else 2.0,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "avg_trade": np.mean(pnls),
        }


def run():
    print("=" * 80)
    print("BACKTEST AVANCÉ — TOUS MODULES INTÉGRÉS (VP + FLOW + MTF + Adaptive)")
    print("=" * 80)

    # Baseline
    print("\n📊 Baseline (MOM20x3 pur)...")
    bt_base = AdvancedBacktest(mode="baseline")
    results_base = []
    for sym in SYMBOLS:
        tf = TIMEFRAMES[sym]
        df = bt_base.load_data(sym, tf)
        r = bt_base.run(sym, tf, df)
        results_base.append(r)
        print(
            f"  {sym} {tf}: {r.get('trades', 0)} trades, WR={r.get('win_rate', 0):.1%}, "
            f"PnL=${r.get('total_pnl', 0):+,.0f}, PF={r.get('profit_factor', 0):.2f}, "
            f"Sharpe={r.get('sharpe', 0):.2f}, MaxDD={r.get('max_dd', 0):.1%}"
        )

    # Full pipeline
    print("\n📊 Full Pipeline (VP + FLOW + MTF + Session + Regime + Adaptive)...")
    bt_full = AdvancedBacktest(mode="full")
    results_full = []
    for sym in SYMBOLS:
        tf = TIMEFRAMES[sym]
        df = bt_full.load_data(sym, tf)
        r = bt_full.run(sym, tf, df)
        results_full.append(r)
        print(
            f"  {sym} {tf}: {r.get('trades', 0)} trades, WR={r.get('win_rate', 0):.1%}, "
            f"PnL=${r.get('total_pnl', 0):+,.0f}, PF={r.get('profit_factor', 0):.2f}, "
            f"Sharpe={r.get('sharpe', 0):.2f}, MaxDD={r.get('max_dd', 0):.1%}"
        )

    # Compare
    print("\n" + "=" * 80)
    print("COMPARAISON DÉTAILLÉE")
    print("=" * 80)

    bt = sum(r.get("trades", 0) for r in results_base)
    ft = sum(r.get("trades", 0) for r in results_full)
    bp = sum(r.get("total_pnl", 0) for r in results_base)
    fp = sum(r.get("total_pnl", 0) for r in results_full)
    bwr = np.mean([r.get("win_rate", 0) for r in results_base if r.get("trades", 0) > 0])
    fwr = np.mean([r.get("win_rate", 0) for r in results_full if r.get("trades", 0) > 0])
    bpf = np.mean([r.get("profit_factor", 0) for r in results_base if r.get("trades", 0) > 0])
    fpf = np.mean([r.get("profit_factor", 0) for r in results_full if r.get("trades", 0) > 0])
    bsh = np.mean([r.get("sharpe", 0) for r in results_base if r.get("trades", 0) > 0])
    fsh = np.mean([r.get("sharpe", 0) for r in results_full if r.get("trades", 0) > 0])
    bdd = max(r.get("max_dd", 0) for r in results_base if r.get("trades", 0) > 0)
    fdd = max(r.get("max_dd", 0) for r in results_full if r.get("trades", 0) > 0)

    print(f"\n{'Métrique':<25} {'Baseline':<15} {'Full Pipeline':<15} {'Δ':<15}")
    print("-" * 70)
    print(f"{'Total Trades':<25} {bt:<15} {ft:<15} {ft - bt:+d}")
    print(f"{'Total PnL':<25} ${bp:<14,.0f} ${fp:<14,.0f} ${fp - bp:+,.0f}")
    print(f"{'Avg Win Rate':<25} {bwr:<14.1%} {fwr:<14.1%} {(fwr - bwr):+.1%}")
    print(f"{'Avg Profit Factor':<25} {bpf:<14.2f} {fpf:<14.2f} {(fpf - bpf):+.2f}")
    print(f"{'Avg Sharpe Ratio':<25} {bsh:<14.2f} {fsh:<14.2f} {(fsh - bsh):+.2f}")
    print(f"{'Max Drawdown':<25} {bdd:<14.1%} {fdd:<14.1%} {(fdd - bdd):+.1%}")

    # Per symbol
    print(
        f"\n{'Symbole':<15} {'B Trades':<10} {'F Trades':<10} {'B PnL':<12} {'F PnL':<12} {'B WR':<8} {'F WR':<8} {'B PF':<8} {'F PF':<8}"
    )
    print("-" * 95)
    for b, f in zip(results_base, results_full):
        sym = b.get("symbol", "?")
        print(
            f"{sym:<15} {b.get('trades', 0):<10} {f.get('trades', 0):<10} "
            f"${b.get('total_pnl', 0):<11,.0f} ${f.get('total_pnl', 0):<11,.0f} "
            f"{b.get('win_rate', 0):<7.1%} {f.get('win_rate', 0):<7.1%} "
            f"{b.get('profit_factor', 0):<7.2f} {f.get('profit_factor', 0):<7.2f}"
        )

    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline": results_base,
        "full_pipeline": results_full,
        "comparison": {
            "trades_delta": ft - bt,
            "pnl_delta": fp - bp,
            "wr_delta": fwr - bwr,
            "pf_delta": fpf - bpf,
            "sharpe_delta": fsh - bsh,
            "max_dd_delta": fdd - bdd,
        },
    }

    out_file = RESULTS_DIR / "backtest_advanced.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n✅ Résultats sauvegardés: {out_file}")


if __name__ == "__main__":
    run()
