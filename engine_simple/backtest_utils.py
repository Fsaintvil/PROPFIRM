"""
Backtest Utils — Fonctions partagées entre les scripts de backtest.
Centralise le code dupliqué : SimTrade, get_pip_info, THRESHOLD, etc.

Usage:
    from engine_simple.backtest_utils import SimTrade, get_pip_info, compute_metrics
"""

from __future__ import annotations

import numpy as np
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# THRESHOLD CONSTANTS — SOURCE UNIQUE (Juillet 2026)
# Synchronisé avec engine_simple/strategy.py et config/default.yaml
# ═══════════════════════════════════════════════════════════════════════════════
THRESHOLD_TRENDING: float = 2.5
THRESHOLD_RANGING: float = 2.0
THRESHOLD_MAX: float = 3.0  # ⚠️ Doit correspondre à strategy.py (3.0, pas 2.5)
THRESHOLD_MIN: float = 1.5

SL_ATR_TRENDING: float = 2.0
TP_ATR_TRENDING: float = 5.0
SL_ATR_RANGING: float = 1.5
TP_ATR_RANGING: float = 4.0

# ═══════════════════════════════════════════════════════════════════════════════
# TRAILING LEVELS — SOURCE UNIQUE
# ═══════════════════════════════════════════════════════════════════════════════
TRAILING_LEVELS: dict[str, list[tuple[float, float]]] = {
    "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
    "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
    "LOW_VOL": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
}

# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL METADATA
# ═══════════════════════════════════════════════════════════════════════════════


def get_pip_info(symbol: str) -> tuple[float, float]:
    """Retourne (pip_size, pip_value) pour un symbole."""
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    if symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash"):
        return 0.01, 1.0
    if symbol in ("USOIL.cash", "UKOIL.cash"):
        return 0.01, 1.0
    if symbol in ("BTCUSD", "ETHUSD", "SOLUSD", "LNKUSD", "BNBUSD"):
        return 0.01, 1.0
    if symbol in ("NATGAS.cash", "GER40.cash", "UK100.cash"):
        return 0.01, 1.0
    return 0.0001, 10.0


def get_pip_value_per_lot(symbol: str) -> float:
    """Retourne la valeur en $ d'un pip pour 1 lot standard."""
    _, pv = get_pip_info(symbol)
    return pv


def get_contract_size(symbol: str) -> int:
    """Retourne la taille d'un contrat standard."""
    if symbol in ("XAUUSD", "XAGUSD"):
        return 100
    if symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash", "GER40.cash", "UK100.cash"):
        return 1
    if symbol in ("USOIL.cash", "UKOIL.cash", "NATGAS.cash"):
        return 100
    if symbol in ("BTCUSD", "ETHUSD", "SOLUSD", "LNKUSD", "BNBUSD"):
        return 1
    return 100_000


# ═══════════════════════════════════════════════════════════════════════════════
# PRECALCULATE INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════


def precalc_atr_and_adx(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Pré-calcule ATR et ADX pour toute la série.

    Returns:
        tuple: (atr_arr, adx_arr, pos_di, neg_di) — tableaux numpy
    """
    n = len(close)
    if n < period * 2:
        return np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)

    # True Range
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))

    # ATR (SMA of TR)
    atr_arr = np.full(n, np.nan)
    for i in range(period, n):
        atr_arr[i] = np.mean(tr[i - period : i])

    # ADX with +DI/-DI
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    tr_smoothed = np.full(n, np.nan)
    pos_smoothed = np.full(n, np.nan)
    neg_smoothed = np.full(n, np.nan)

    for i in range(period, n):
        tr_smoothed[i] = np.mean(tr[i - period : i])
        pos_smoothed[i] = np.mean(pos_dm[i - period : i])
        neg_smoothed[i] = np.mean(neg_dm[i - period : i])

    pos_di = 100 * pos_smoothed / np.maximum(tr_smoothed, 1e-10)
    neg_di = 100 * neg_smoothed / np.maximum(tr_smoothed, 1e-10)

    dx = np.abs(pos_di - neg_di) / np.maximum(pos_di + neg_di, 1e-10) * 100
    adx_arr = np.full(n, np.nan)
    for i in range(period * 2, n):
        adx_arr[i] = np.mean(dx[i - period : i])

    return atr_arr, adx_arr, pos_di, neg_di


# ═══════════════════════════════════════════════════════════════════════════════
# SIMTRADE CLASS
# ═══════════════════════════════════════════════════════════════════════════════


class SimTrade:
    """Classe de trading simulé pour backtest — version unifiée (Juillet 2026).

    Supporte :
    - Coûts (spread, slippage, commission)
    - Trailing stop
    - Partial TP
    """

    __slots__ = (
        "symbol",
        "timeframe",
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
        "profit_pct",
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
        "cost_pips",
        "commission_usd",
        "spread_cost_pips",
        "spread_from_data",
        "profit_usd_cost",
        "profit_pct_cost",
    )

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        action: str,
        entry: float,
        sl: float,
        tp: float,
        atr_val: float,
        regime: str,
        bar_idx: int,
        bar_time: Any,
        balance: float,
        spread_pts: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
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
        self.result: str | None = None
        self.profit_usd = 0.0
        self.profit_pct = 0.0
        self.profit_usd_cost = 0.0
        self.profit_pct_cost = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time: Any = None
        self.close_price = entry
        self.lot = 0.01
        self.cost_pips = 0.0
        self.commission_usd = 0.0
        self.spread_cost_pips = 0.0
        self.spread_from_data: float | None = spread_pts if (spread_pts is not None and spread_pts > 0) else None
        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._contract_size = get_contract_size(symbol)

    def get_pip_info(self) -> tuple[float, float]:
        return self._pip_size, self._pip_value


def compute_metrics(closed_trades: list[Any], use_cost: bool = False) -> dict[str, Any]:
    """Calcule les métriques de performance pour une liste de trades fermés.

    Args:
        closed_trades: Liste de SimTrade ou dicts avec 'profit_usd'/'profit_usd_cost'
        use_cost: Si True, utilise profit_usd_cost (avec coûts)

    Returns:
        dict avec win_rate, profit_factor, total_pnl, max_drawdown, etc.
    """
    if not closed_trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
        }

    # Extraire les PnL selon la source
    pnls = []
    for t in closed_trades:
        if hasattr(t, "profit_usd_cost") and use_cost:
            pnls.append(t.profit_usd_cost)
        elif hasattr(t, "profit_usd"):
            pnls.append(t.profit_usd)
        elif hasattr(t, "profit_pct"):
            pnls.append(t.profit_pct)
        elif isinstance(t, dict):
            pnls.append(t.get("profit_usd_cost" if use_cost else "profit_usd", 0))
        else:
            pnls.append(0)

    pnls = np.array(pnls)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    total_pnl = float(pnls.sum())
    win_rate = float(len(wins) / len(pnls)) if len(pnls) > 0 else 0
    profit_factor = float(abs(wins.sum() / max(abs(losses.sum()), 1e-10))) if len(losses) > 0 else float("inf")

    # Drawdown (simple peak-to-trough)
    cumsum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumsum)
    dd = cumsum - peak
    max_dd_pct = float(abs(min(dd)) / max(abs(peak[-1]), 1)) if len(dd) > 0 else 0

    return {
        "total_trades": len(pnls),
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "total_pnl": round(total_pnl, 2),
        "max_drawdown_pct": round(max_dd_pct * 100, 1),
        "avg_win": round(float(wins.mean()), 2) if len(wins) > 0 else 0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) > 0 else 0,
        "total_wins": len(wins),
        "total_losses": len(losses),
    }


if __name__ == "__main__":
    # Test rapide
    pip, pv = get_pip_info("EURUSD")
    print(f"EURUSD: pip={pip}, pip_value={pv}")
    pip, pv = get_pip_info("XAUUSD")
    print(f"XAUUSD: pip={pip}, pip_value={pv}")
    pip, pv = get_pip_info("US100.cash")
    print(f"US100.cash: pip={pip}, pip_value={pv}")
    print("✅ backtest_utils.py loaded successfully")
