"""
Analyse Quantitative 16 ans — 5 symboles actifs.
Calcule les métriques de rendement, volatilité, risque, et corrélation.

Lit les données Parquet de data/historical/.

Usage:
    python scripts/quant_analysis_16y.py
    python scripts/quant_analysis_16y.py --symbol XAUUSD
    python scripts/quant_analysis_16y.py --export
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ============================================================================
# CONFIGURATION
# ============================================================================
SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "US500.cash"]
TIMEFRAMES = ["H1", "H4", "D1"]
RISK_FREE_RATE = 0.05  # 5% annual (approximation)
TRADING_DAYS_PER_YEAR = 252


def compute_returns(close: np.ndarray) -> np.ndarray:
    """Calcule les rendements logarithmiques."""
    returns = np.diff(np.log(close))
    returns = returns[np.isfinite(returns)]
    return returns


def compute_sharpe_ratio(returns: np.ndarray, periods_per_year: int) -> float:
    """Calcule le Sharpe Ratio annualisé."""
    if len(returns) < 2:
        return 0.0
    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1)
    if std_ret == 0:
        return 0.0
    sharpe = (mean_ret * periods_per_year - RISK_FREE_RATE) / (std_ret * np.sqrt(periods_per_year))
    return round(sharpe, 3)


def compute_sortino_ratio(returns: np.ndarray, periods_per_year: int) -> float:
    """Calcule le Sortino Ratio (downside deviation only)."""
    if len(returns) < 2:
        return 0.0
    mean_ret = np.mean(returns)
    downside = returns[returns < 0]
    if len(downside) < 2:
        return 0.0
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return 0.0
    sortino = (mean_ret * periods_per_year - RISK_FREE_RATE) / (downside_std * np.sqrt(periods_per_year))
    return round(sortino, 3)


def compute_max_drawdown(close: np.ndarray) -> float:
    """Calcule le Max Drawdown en %."""
    peak = np.maximum.accumulate(close)
    dd = (peak - close) / peak
    return round(float(np.max(dd)) * 100, 2) if len(dd) > 0 else 0.0


def compute_calmar_ratio(returns: np.ndarray, max_dd: float, periods_per_year: int) -> float:
    """Calcule le Calmar Ratio (return / max DD)."""
    if max_dd == 0 or len(returns) < 2:
        return 0.0
    annual_return = np.mean(returns) * periods_per_year
    calmar = annual_return / (max_dd / 100)
    return round(calmar, 3)


def compute_var(returns: np.ndarray, confidence: float = 0.95) -> float:
    """Calcule le Value at Risk (parametric)."""
    if len(returns) < 2:
        return 0.0
    from scipy.stats import norm

    mu = np.mean(returns)
    sigma = np.std(returns, ddof=1)
    var = mu - sigma * norm.ppf(1 - confidence)
    return round(float(var) * 100, 4)


def compute_cvar(returns: np.ndarray, confidence: float = 0.95) -> float:
    """Calcule le Conditional VaR (Expected Shortfall)."""
    if len(returns) < 2:
        return 0.0
    threshold = np.percentile(returns, (1 - confidence) * 100)
    tail = returns[returns <= threshold]
    if len(tail) == 0:
        return 0.0
    return round(float(np.mean(tail)) * 100, 4)


def compute_volatility(returns: np.ndarray, periods_per_year: int) -> float:
    """Calcule la volatilité annualisée."""
    if len(returns) < 2:
        return 0.0
    return round(float(np.std(returns, ddof=1) * np.sqrt(periods_per_year)) * 100, 2)


def compute_skewness(returns: np.ndarray) -> float:
    """Calcule le skewness."""
    if len(returns) < 3:
        return 0.0
    from scipy.stats import skew

    return round(float(skew(returns)), 3)


def compute_kurtosis(returns: np.ndarray) -> float:
    """Calcule le kurtosis (excès)."""
    if len(returns) < 4:
        return 0.0
    from scipy.stats import kurtosis

    return round(float(kurtosis(returns)), 3)


def analyze_symbol_tf(symbol: str, tf: str, df: pd.DataFrame) -> dict:
    """Analyse complète d'un symbole + timeframe."""
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)

    if len(close) < 100:
        return None

    # Returns
    returns = compute_returns(close)

    # Periods per year
    tf_to_periods = {"M15": 96 * 252, "H1": 24 * 252, "H4": 6 * 252, "D1": 252}
    periods_per_year = tf_to_periods.get(tf, 24 * 252)

    # Basic metrics
    total_return = (close[-1] / close[0] - 1) * 100
    annual_return = np.mean(returns) * periods_per_year * 100

    # Volatility
    vol = compute_volatility(returns, periods_per_year)

    # Risk metrics
    max_dd = compute_max_drawdown(close)
    var_95 = compute_var(returns, 0.95)
    cvar_95 = compute_cvar(returns, 0.95)

    # Ratios
    sharpe = compute_sharpe_ratio(returns, periods_per_year)
    sortino = compute_sortino_ratio(returns, periods_per_year)
    calmar = compute_calmar_ratio(returns, max_dd, periods_per_year)

    # Distribution
    skew = compute_skewness(returns)
    kurt = compute_kurtosis(returns)

    # ATR
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr_val = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
    atr_pct = atr_val / close[-1] * 100 if close[-1] > 0 else 0

    # Win rate (simple: positive returns)
    win_rate = np.mean(returns > 0) * 100 if len(returns) > 0 else 0

    # Consecutive
    pos_runs = np.diff(np.where(np.concatenate(([returns[0] > 0], returns > 0, [returns[-1] > 0])))[0])[::2]
    neg_runs = np.diff(np.where(np.concatenate(([returns[0] <= 0], returns <= 0, [returns[-1] <= 0])))[0])[::2]
    max_consec_up = int(np.max(pos_runs)) if len(pos_runs) > 0 else 0
    max_consec_down = int(np.max(neg_runs)) if len(neg_runs) > 0 else 0

    return {
        "symbol": symbol,
        "timeframe": tf,
        "bars": len(close),
        "start_date": str(df["timestamp"].iloc[0])[:10] if "timestamp" in df.columns else "",
        "end_date": str(df["timestamp"].iloc[-1])[:10] if "timestamp" in df.columns else "",
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "volatility_pct": vol,
        "max_drawdown_pct": max_dd,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "var_95_pct": var_95,
        "cvar_95_pct": cvar_95,
        "skewness": skew,
        "kurtosis": kurt,
        "atr_value": round(atr_val, 6),
        "atr_pct": round(atr_pct, 4),
        "win_rate_pct": round(win_rate, 2),
        "max_consec_up": max_consec_up,
        "max_consec_down": max_consec_down,
    }


def compute_correlations(data: dict[str, dict[str, pd.DataFrame]]) -> dict:
    """Calcule les corrélations inter-symboles."""
    # Collect daily returns for each symbol
    daily_returns = {}
    for sym in SYMBOLS:
        if sym in data and "H1" in data[sym]:
            df = data[sym]["H1"]
            close = df["close"].values.astype(float)
            returns = compute_returns(close)
            daily_returns[sym] = returns

    # Compute correlation matrix
    symbols = list(daily_returns.keys())
    n = len(symbols)
    corr_matrix = np.eye(n)

    for i in range(n):
        for j in range(i + 1, n):
            r1 = daily_returns[symbols[i]]
            r2 = daily_returns[symbols[j]]
            min_len = min(len(r1), len(r2))
            if min_len > 10:
                corr = np.corrcoef(r1[:min_len], r2[:min_len])[0, 1]
                corr_matrix[i, j] = corr
                corr_matrix[j, i] = corr

    return {"symbols": symbols, "correlation_matrix": corr_matrix.tolist()}


# ============================================================================
# MAIN
# ============================================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyse Quantitative 16y")
    parser.add_argument("--symbol", type=str, default=None, help="Filtrer par symbole")
    parser.add_argument("--export", action="store_true", help="Exporter JSON")
    args = parser.parse_args()

    data_dir = Path("data/historical")
    out_dir = Path("runtime")
    out_dir.mkdir(exist_ok=True)

    # Load data
    print("=" * 90)
    print("  ANALYSE QUANTITATIVE 16 ANS — 5 SYMBOLES ACTIFS")
    print("=" * 90)

    symbols = [args.symbol] if args.symbol else SYMBOLS
    all_data = {}

    for sym in symbols:
        all_data[sym] = {}
        for tf in TIMEFRAMES:
            fpath = data_dir / f"{sym}_{tf}.parquet"
            if fpath.exists():
                df = pd.read_parquet(fpath)
                all_data[sym][tf] = df
                print(f"  Loaded {sym}_{tf}: {len(df)} bars")

    print()

    # Analyze each symbol × TF
    results = []
    for sym in symbols:
        for tf in TIMEFRAMES:
            if tf in all_data.get(sym, {}):
                df = all_data[sym][tf]
                r = analyze_symbol_tf(sym, tf, df)
                if r:
                    results.append(r)

    # Print summary table
    print(f"{'─' * 90}")
    print(
        f"  {'Symbol_TF':16s} {'Return':>8s} {'Ann.Ret':>8s} {'Vol':>7s} {'DD Max':>7s} "
        f"{'Sharpe':>7s} {'Sortino':>8s} {'VaR95':>7s} {'Win%':>6s}"
    )
    print(f"{'─' * 90}")

    for r in results:
        print(
            f"  {r['symbol']}_{r['timeframe']:3s} {r['total_return_pct']:>+7.1f}% "
            f"{r['annual_return_pct']:>+7.1f}% {r['volatility_pct']:>6.1f}% "
            f"{r['max_drawdown_pct']:>6.1f}% {r['sharpe_ratio']:>6.2f} "
            f"{r['sortino_ratio']:>7.2f} {r['var_95_pct']:>6.3f}% "
            f"{r['win_rate_pct']:>5.1f}%"
        )

    # Aggregate by symbol
    print(f"\n{'─' * 90}")
    print(f"  AGGREGATION PAR SYMOLE (meilleur TF)")
    print(f"{'─' * 90}")

    sym_best = {}
    for r in results:
        sym = r["symbol"]
        if sym not in sym_best or r["sharpe_ratio"] > sym_best[sym]["sharpe_ratio"]:
            sym_best[sym] = r

    for sym, r in sorted(sym_best.items()):
        print(
            f"  {sym:12s}: Return {r['total_return_pct']:+.1f}% | Vol {r['volatility_pct']:.1f}% | "
            f"DD {r['max_drawdown_pct']:.1f}% | Sharpe {r['sharpe_ratio']:.2f} | "
            f"Sortino {r['sortino_ratio']:.2f}"
        )

    # Correlations
    print(f"\n{'─' * 90}")
    print(f"  MATRICE DE CORRÉLATION (H1)")
    print(f"{'─' * 90}")

    corr = compute_correlations(all_data)
    symbols_corr = corr["symbols"]
    matrix = corr["correlation_matrix"]

    header = f"  {'':12s}" + "".join(f"{s:>10s}" for s in symbols_corr)
    print(header)
    for i, sym in enumerate(symbols_corr):
        row = f"  {sym:12s}" + "".join(f"{matrix[i][j]:>10.3f}" for j in range(len(symbols_corr)))
        print(row)

    # Risk warnings
    print(f"\n{'─' * 90}")
    print(f"  AVERTISSEMENTS DE RISQUE")
    print(f"{'─' * 90}")

    for r in results:
        warnings = []
        if r["max_drawdown_pct"] > 10:
            warnings.append(f"DD > 10% ({r['max_drawdown_pct']}%)")
        if r["sharpe_ratio"] < 0.5:
            warnings.append(f"Sharpe faible ({r['sharpe_ratio']})")
        if r["var_95_pct"] < -0.5:
            warnings.append(f"VaR élevé ({r['var_95_pct']}%)")
        if r["skewness"] < -1:
            warnings.append(f"Skewness négatif ({r['skewness']})")

        if warnings:
            print(f"  ⚠️  {r['symbol']}_{r['timeframe']}: {', '.join(warnings)}")

    print(f"\n{'═' * 90}")

    # Export
    if args.export:
        export_path = out_dir / "quant_analysis_16y.json"
        export_data = {
            "analysis_date": datetime.now().isoformat(),
            "results": results,
            "correlations": corr,
            "best_per_symbol": {sym: r for sym, r in sym_best.items()},
        }
        with open(export_path, "w") as f:
            json.dump(export_data, f, indent=2, default=str)
        print(f"  Exported to {export_path}")


if __name__ == "__main__":
    main()
