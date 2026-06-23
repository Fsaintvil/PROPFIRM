"""Backtest Phase 7-16 — Impact des nouveaux modules sur les performances.

Compare MOM20x3 pur (baseline) vs MOM20x3 + tous les modules (Phase 7-16).

Usage:
    python scripts/backtest_phase7_16.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

from engine_simple.strategy import mom20x3_signal, SYMBOL_CONFIG
from engine_simple.indicators import adx as ind_adx, atr as ind_atr, ema as ind_ema

try:
    from engine_simple.regime_engine import RegimeEngine
except ImportError:
    from retired.engine_simple.regime_engine import RegimeEngine
from engine_simple.session_filter import SessionFilter
from engine_simple.portfolio_controller import PortfolioController
from engine_simple.strategy_selector import StrategySelector
from engine_simple.news_filter import NewsFilter
from engine_simple.volume_profile import VolumeProfile
from engine_simple.order_flow import OrderFlowAnalyzer
from engine_simple.mtf_confirm import MultiTimeframeConfirmer

try:
    from engine_simple.risk_parity import RiskParitySizer
except ImportError:
    from retired.engine_simple.risk_parity import RiskParitySizer
from engine_simple.adaptive_params import AdaptiveParameters

try:
    from engine_simple.walk_forward_opt import WalkForwardOptimizer
except ImportError:
    from retired.engine_simple.walk_forward_opt import WalkForwardOptimizer


# ============================================================================
# CONFIGURATION
# ============================================================================
SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "US500.cash"]
TIMEFRAMES = {"XAUUSD": "H4", "BTCUSD": "H1", "ETHUSD": "H4", "US500.cash": "H4"}
DATA_DIR = Path("data/historical")
RESULTS_DIR = Path("runtime")

# FTMO constraints
INITIAL_BALANCE = 200000
MAX_DD_PCT = 0.10
MAX_DAILY_LOSS_PCT = 0.02
RISK_PER_TRADE = 0.004
COOLDOWN_MINUTES = 15


class BacktestPhase7_16:
    """Backtest avec/sans les modules Phase 7-16."""

    def __init__(self, use_modules: bool = False):
        self.use_modules = use_modules

        # Initialize modules if enabled
        if use_modules:
            self.regime_engine = RegimeEngine()
            self.session_filter = SessionFilter()
            self.portfolio_controller = PortfolioController()
            self.strategy_selector = StrategySelector()
            self.news_filter = NewsFilter()
            self.volume_profile = VolumeProfile()
            self.order_flow = OrderFlowAnalyzer()
            self.mtf_confirm = MultiTimeframeConfirmer()
            self.risk_parity = RiskParitySizer()
            # Adaptive params per symbol
            self._adaptive: dict[str, AdaptiveParameters] = {}
            self._wfo: dict[str, WalkForwardOptimizer] = {}

        # Results
        self.trades = []
        self.equity_curve = []
        self.balance = INITIAL_BALANCE
        self.peak_equity = INITIAL_BALANCE
        self.positions = []
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.cooldown_until = None

    def load_data(self, symbol: str, tf: str) -> pd.DataFrame:
        """Charge les données historiques."""
        pattern = f"{symbol}_{tf}_*.parquet"
        files = sorted(DATA_DIR.glob(pattern))

        if not files:
            # Try alternative pattern
            pattern = f"{symbol}_{tf}.parquet"
            files = sorted(DATA_DIR.glob(pattern))

        if not files:
            return None

        # Load most recent file
        df = pd.read_parquet(files[-1])

        # Ensure required columns
        required = ["open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                if col == "volume":
                    df["volume"] = 1.0
                else:
                    return None

        return df

    def run_backtest(self, symbol: str, tf: str, df: pd.DataFrame) -> dict:
        """Lance le backtest sur un symbole."""
        if df is None or len(df) < 100:
            return {"symbol": symbol, "tf": tf, "trades": 0, "error": "insufficient data"}

        trades = []
        equity = INITIAL_BALANCE
        peak_equity = INITIAL_BALANCE

        # State
        in_position = False
        entry_price = 0
        entry_idx = 0
        sl_price = 0
        tp_price = 0
        direction = 0  # 0=BUY, 1=SELL

        lookback = 50

        for i in range(lookback, len(df)):
            # Get window
            window = df.iloc[i - lookback : i + 1]
            high = window["high"].values
            low = window["low"].values
            close = window["close"].values
            volume = window["volume"].values

            current_price = close[-1]

            # Check existing position
            if in_position:
                # Check SL/TP
                if direction == 0:  # BUY
                    if low[-1] <= sl_price:
                        # SL hit
                        pnl = (sl_price - entry_price) * 100  # Simplified
                        equity += pnl
                        trades.append({"entry": entry_price, "exit": sl_price, "pnl": pnl, "type": "SL"})
                        in_position = False
                        self.consecutive_losses += 1
                    elif high[-1] >= tp_price:
                        # TP hit
                        pnl = (tp_price - entry_price) * 100
                        equity += pnl
                        trades.append({"entry": entry_price, "exit": tp_price, "pnl": pnl, "type": "TP"})
                        in_position = False
                        self.consecutive_losses = 0
                else:  # SELL
                    if high[-1] >= sl_price:
                        pnl = (entry_price - sl_price) * 100
                        equity += pnl
                        trades.append({"entry": entry_price, "exit": sl_price, "pnl": pnl, "type": "SL"})
                        in_position = False
                        self.consecutive_losses += 1
                    elif low[-1] <= tp_price:
                        pnl = (entry_price - tp_price) * 100
                        equity += pnl
                        trades.append({"entry": entry_price, "exit": tp_price, "pnl": pnl, "type": "TP"})
                        in_position = False
                        self.consecutive_losses = 0

                # Update peak
                peak_equity = max(peak_equity, equity)

                # Check DD
                dd = (peak_equity - equity) / peak_equity
                if dd > MAX_DD_PCT:
                    break

                continue

            # Skip if in cooldown
            if self.cooldown_until and i < self.cooldown_until:
                continue

            # Generate signal
            try:
                signal = mom20x3_signal(
                    close=close,
                    high=high,
                    low=low,
                    period=20,
                    symbol=symbol,
                )
            except Exception as e:
                continue

            if signal is None:
                continue

            # Apply modules if enabled
            if self.use_modules:
                # Regime
                regime_result = self.regime_engine.detect(
                    high=high,
                    low=low,
                    close=close,
                    symbol=symbol,
                )
                regime = regime_result.regime

                # Session filter — use historical hour if available
                if "time" in df.columns:
                    try:
                        ts = pd.to_datetime(df.iloc[i]["time"])
                        hour_utc = ts.hour
                    except Exception:
                        hour_utc = datetime.now(timezone.utc).hour
                else:
                    hour_utc = datetime.now(timezone.utc).hour
                session_score = self.session_filter.get_session_score(symbol, hour_utc)
                if session_score < 0.2:
                    continue

                # Volume Profile — score adjustment
                score = signal.get("score", 0.6)
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
                        current_price = close[-1]
                        dist_poc = abs(current_price - vp_levels.poc) / current_price * 100
                        if dist_poc < 0.1:
                            score = min(0.95, score * 1.1)  # Near POC → boost
                        elif vp_levels.vah and current_price > vp_levels.vah * 0.999:
                            if signal.get("action") == "BUY":
                                score *= 0.9  # Resistance above
                        elif vp_levels.val and current_price < vp_levels.val * 1.001:
                            if signal.get("action") == "SELL":
                                score *= 0.9  # Support below

                # Order Flow — score adjustment
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
                        if flow_dir == signal.get("action", "BUY"):
                            score = min(0.95, score + min(0.1, flow_strength * 0.15))
                        else:
                            score = max(0.3, score - min(0.15, flow_strength * 0.2))

                # MTF Confirm — score adjustment
                # Use higher TF trend alignment (simplified: check if close > EMA50)
                if len(close) >= 50:
                    ema50 = np.mean(close[-50:])
                    if signal.get("action") == "BUY" and close[-1] > ema50:
                        score = min(0.95, score * 1.05)  # Higher TF confirms
                    elif signal.get("action") == "SELL" and close[-1] < ema50:
                        score = min(0.95, score * 1.05)
                    else:
                        score = max(0.3, score * 0.90)  # Higher TF contradicts

                signal["score"] = score

                # Adaptive params
                if symbol not in self._adaptive:
                    self._adaptive[symbol] = AdaptiveParameters(symbol)
                ap = self._adaptive[symbol]
                adapted = ap.get_adapted_params()
                if adapted.sample_size >= 20:
                    signal["risk_mult"] = signal.get("risk_mult", 1.0) * adapted.risk_mult

            # Calculate SL/TP
            atr_val = ind_atr(high, low, close, 14)[-1] if len(high) >= 14 else 0
            if atr_val <= 0:
                continue

            action = signal.get("action", "BUY")

            if action == "BUY":
                direction = 0
                entry_price = current_price
                sl_price = entry_price - atr_val * 2.0
                tp_price = entry_price + atr_val * 5.0
            else:
                direction = 1
                entry_price = current_price
                sl_price = entry_price + atr_val * 2.0
                tp_price = entry_price - atr_val * 5.0

            # Risk management
            risk_amount = equity * RISK_PER_TRADE
            sl_distance = abs(entry_price - sl_price)
            if sl_distance <= 0:
                continue

            in_position = True
            entry_idx = i
            peak_equity = max(peak_equity, equity)

        # Calculate metrics
        if not trades:
            return {"symbol": symbol, "tf": tf, "trades": 0}

        pnls = [t["pnl"] for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)

        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 2.0

        # Sharpe
        if len(pnls) > 1:
            avg = np.mean(pnls)
            std = np.std(pnls)
            sharpe = avg / std if std > 0 else 0
        else:
            sharpe = 0

        # Max DD
        equity_arr = [INITIAL_BALANCE]
        for p in pnls:
            equity_arr.append(equity_arr[-1] + p)
        equity_arr = np.array(equity_arr)
        peaks = np.maximum.accumulate(equity_arr)
        dds = (peaks - equity_arr) / peaks
        max_dd = np.max(dds)

        return {
            "symbol": symbol,
            "tf": tf,
            "trades": total,
            "wins": wins,
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl": sum(pnls),
            "profit_factor": pf,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "avg_trade": np.mean(pnls),
            "avg_win": np.mean([p for p in pnls if p > 0]) if wins > 0 else 0,
            "avg_loss": np.mean([p for p in pnls if p < 0]) if total - wins > 0 else 0,
        }


def run_comparison():
    """Lance la comparaison baseline vs modules."""
    print("=" * 70)
    print("BACKTEST PHASE 7-16 — IMPACT DES NOUVEAUX MODULES")
    print("=" * 70)

    # Baseline (MOM20x3 pur)
    print("\n📊 Baseline (MOM20x3 pur)...")
    bt_baseline = BacktestPhase7_16(use_modules=False)
    results_baseline = []

    for symbol in SYMBOLS:
        tf = TIMEFRAMES[symbol]
        df = bt_baseline.load_data(symbol, tf)
        if df is not None:
            result = bt_baseline.run_backtest(symbol, tf, df)
            results_baseline.append(result)
            print(
                f"  {symbol} {tf}: {result.get('trades', 0)} trades, "
                f"WR={result.get('win_rate', 0):.1%}, "
                f"PnL=${result.get('total_pnl', 0):+,.0f}"
            )

    # With modules
    print("\n📊 With Phase 7-16 modules...")
    bt_modules = BacktestPhase7_16(use_modules=True)
    results_modules = []

    for symbol in SYMBOLS:
        tf = TIMEFRAMES[symbol]
        df = bt_modules.load_data(symbol, tf)
        if df is not None:
            result = bt_modules.run_backtest(symbol, tf, df)
            results_modules.append(result)
            print(
                f"  {symbol} {tf}: {result.get('trades', 0)} trades, "
                f"WR={result.get('win_rate', 0):.1%}, "
                f"PnL=${result.get('total_pnl', 0):+,.0f}"
            )

    # Compare
    print("\n" + "=" * 70)
    print("COMPARAISON")
    print("=" * 70)

    # Aggregate
    baseline_total = sum(r.get("trades", 0) for r in results_baseline)
    baseline_pnl = sum(r.get("total_pnl", 0) for r in results_baseline)
    baseline_wr = (
        np.mean([r.get("win_rate", 0) for r in results_baseline if r.get("trades", 0) > 0]) if results_baseline else 0
    )

    modules_total = sum(r.get("trades", 0) for r in results_modules)
    modules_pnl = sum(r.get("total_pnl", 0) for r in results_modules)
    modules_wr = (
        np.mean([r.get("win_rate", 0) for r in results_modules if r.get("trades", 0) > 0]) if results_modules else 0
    )

    print(f"\n{'Métrique':<25} {'Baseline':<15} {'Modules':<15} {'Δ':<15}")
    print("-" * 70)
    print(f"{'Total Trades':<25} {baseline_total:<15} {modules_total:<15} {modules_total - baseline_total:+d}")
    print(f"{'Total PnL':<25} ${baseline_pnl:<14,.0f} ${modules_pnl:<14,.0f} ${modules_pnl - baseline_pnl:+,.0f}")
    print(f"{'Avg Win Rate':<25} {baseline_wr:<14.1%} {modules_wr:<14.1%} {(modules_wr - baseline_wr):+.1%}")

    # Per symbol
    print(f"\n{'Symbole':<15} {'Baseline Trades':<18} {'Modules Trades':<18} {'Baseline PnL':<15} {'Modules PnL':<15}")
    print("-" * 80)

    for b, m in zip(results_baseline, results_modules):
        sym = b.get("symbol", "?")
        b_trades = b.get("trades", 0)
        m_trades = m.get("trades", 0)
        b_pnl = b.get("total_pnl", 0)
        m_pnl = m.get("total_pnl", 0)
        print(f"{sym:<15} {b_trades:<18} {m_trades:<18} ${b_pnl:<14,.0f} ${m_pnl:<14,.0f}")

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline": results_baseline,
        "modules": results_modules,
        "comparison": {
            "baseline_total_trades": baseline_total,
            "modules_total_trades": modules_total,
            "baseline_total_pnl": baseline_pnl,
            "modules_total_pnl": modules_pnl,
            "baseline_avg_wr": baseline_wr,
            "modules_avg_wr": modules_wr,
        },
    }

    output_file = RESULTS_DIR / "backtest_phase7_16.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Résultats sauvegardés: {output_file}")

    return output


if __name__ == "__main__":
    run_comparison()
