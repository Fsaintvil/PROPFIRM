"""
MetricsCalculator — Calcul de toutes les métriques de performance.

Produit :
  - Performances : Net/Gross Profit, Win Rate, Profit Factor, Expectancy
  - Ratios ajustés au risque : Sharpe, Sortino, Calmar, Recovery Factor
  - Drawdown : max DD %, max DD $, durée DD
  - Analyse temporelle : trades/mois, trades/jour, monthly returns
  - Analyse directionnelle : long vs short
  - Analyse par session : Asia, London, NY

Usage :
    mc = MetricsCalculator()
    metrics = mc.compute(trades, equity_curve, dates)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from math import erf, sqrt
from typing import Optional

import numpy as np

logger = logging.getLogger("backtest_core.metrics")

# ─── Session definitions (UTC) ───────────────────────────────────────────

SESSIONS = {
    "asia": (0, 8),
    "london": (7, 16),
    "ny": (13, 21),
}

# ─── Risk-free rate ──────────────────────────────────────────────────────

RISK_FREE_RATE = 0.02  # 2% annuel (obligations US)


# ═══════════════════════════════════════════════════════════════════════════
# MetricsCalculator
# ═══════════════════════════════════════════════════════════════════════════


class MetricsCalculator:
    """Calcule l'ensemble des métriques de performance pour un backtest."""

    @staticmethod
    def compute(
        trades: list,
        initial_balance: float = 200_000.0,
        equity_curve: Optional[list[float]] = None,
        dates: Optional[list[datetime]] = None,
    ) -> dict:
        """
        Calcule toutes les métriques à partir d'une liste de trades.

        Args:
            trades: Liste d'objets SimTrade (ou tout objet avec to_dict())
            initial_balance: Capital initial
            equity_curve: Courbe d'equity (optionnelle, calculée si absente)
            dates: Dates correspondant à equity_curve

        Returns:
            dict complet avec toutes les métriques.
        """
        if not trades:
            return {"error": "no trades", "n": 0}

        # S'assurer que les trades sont fermés
        closed = [t for t in trades if hasattr(t, "closed") and t.closed]
        if not closed:
            return {"error": "no closed trades", "n": len(trades)}

        # Extraire les PnL (net = après coûts)
        net_pnls = [getattr(t, "profit_usd_cost", 0.0) for t in closed]
        gross_pnls = [getattr(t, "profit_usd", 0.0) for t in closed]
        actions = [getattr(t, "action", "BUY") for t in closed]

        # ─── Métriques de base ───────────────────────────────────────────
        n = len(closed)
        n_wins = sum(1 for p in net_pnls if p > 0)
        n_losses = n - n_wins
        win_rate = n_wins / n * 100 if n > 0 else 0.0

        gross_profit = sum(p for p in net_pnls if p > 0)
        gross_loss = abs(sum(p for p in net_pnls if p <= 0))
        net_profit = sum(net_pnls)

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        avg_trade = net_profit / n if n > 0 else 0.0
        avg_win = gross_profit / n_wins if n_wins > 0 else 0.0
        avg_loss = gross_loss / n_losses if n_losses > 0 else 0.0

        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * abs(avg_loss))
        avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        # ─── Significativité statistique ─────────────────────────────────
        if n >= 5:
            z = (win_rate / 100 - 0.5) / sqrt(0.5 * 0.5 / n)
            p_value = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
        else:
            z = 0.0
            p_value = 1.0

        significant = p_value < 0.05

        # ─── Drawdown ────────────────────────────────────────────────────
        if equity_curve is not None and len(equity_curve) > 0:
            dd_info = MetricsCalculator._compute_drawdown(equity_curve, dates)
        else:
            dd_info = MetricsCalculator._compute_drawdown_from_trades(closed, initial_balance)

        # ─── Ratios ajustés au risque ────────────────────────────────────
        # Calculer les rendements quotidiens pour Sharpe/Sortino
        daily_returns = MetricsCalculator._get_daily_returns(net_pnls, closed)
        daily_returns_arr = np.array(daily_returns) if daily_returns else np.array([0.0])

        sharpe = MetricsCalculator._compute_sharpe(daily_returns_arr)
        sortino = MetricsCalculator._compute_sortino(daily_returns_arr)
        calmar = net_profit / dd_info["max_dd_usd"] if dd_info["max_dd_usd"] > 0 else 0.0
        recovery = net_profit / dd_info["max_dd_usd"] if dd_info["max_dd_usd"] > 0 else 0.0

        # ─── Séquence de pertes ──────────────────────────────────────────
        max_consecutive_losses = 0
        max_consecutive_wins = 0
        current_losses = 0
        current_wins = 0
        for p in net_pnls:
            if p > 0:
                current_wins += 1
                current_losses = 0
                max_consecutive_wins = max(max_consecutive_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_consecutive_losses = max(max_consecutive_losses, current_losses)

        # ─── Analyse directionnelle ──────────────────────────────────────
        long_pnls = [p for p, a in zip(net_pnls, actions) if a == "BUY"]
        short_pnls = [p for p, a in zip(net_pnls, actions) if a == "SELL"]
        long_wins = sum(1 for p in long_pnls if p > 0) if long_pnls else 0
        short_wins = sum(1 for p in short_pnls if p > 0) if short_pnls else 0

        dir_analysis = {
            "long": {
                "trades": len(long_pnls),
                "wins": long_wins,
                "win_rate": round(long_wins / len(long_pnls) * 100, 1) if long_pnls else 0.0,
                "pnl": round(sum(long_pnls), 2),
            },
            "short": {
                "trades": len(short_pnls),
                "wins": short_wins,
                "win_rate": round(short_wins / len(short_pnls) * 100, 1) if short_pnls else 0.0,
                "pnl": round(sum(short_pnls), 2),
            },
        }

        # ─── Analyse par session ─────────────────────────────────────────
        session_analysis = MetricsCalculator._analyze_sessions(closed)

        # ─── Analyse temporelle ──────────────────────────────────────────
        monthly_returns, yearly_analysis = MetricsCalculator._analyze_time(closed, initial_balance)

        # ─── Assemblage ──────────────────────────────────────────────────
        return {
            # Informations générales
            "n": n,
            "n_wins": n_wins,
            "n_losses": n_losses,
            "initial_balance": initial_balance,
            "final_balance": round(initial_balance + net_profit, 2),
            # Performance
            "net_profit": round(net_profit, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "avg_trade": round(avg_trade, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_rr": round(avg_rr, 2),
            "return_pct": round(net_profit / initial_balance * 100, 2) if initial_balance > 0 else 0.0,
            # Ratios ajustés au risque
            "sharpe_ratio": round(sharpe, 3),
            "sortino_ratio": round(sortino, 3),
            "calmar_ratio": round(calmar, 3),
            "recovery_factor": round(recovery, 2),
            # Significativité
            "z_score": round(z, 3),
            "p_value": round(p_value, 4),
            "significant": significant,
            # Drawdown
            "max_dd_pct": dd_info["max_dd_pct"],
            "max_dd_usd": dd_info["max_dd_usd"],
            "avg_dd_pct": dd_info["avg_dd_pct"],
            "max_dd_duration_days": dd_info["max_dd_duration_days"],
            # Séquence
            "max_consecutive_wins": max_consecutive_wins,
            "max_consecutive_losses": max_consecutive_losses,
            # Analyse directionnelle
            "direction": dir_analysis,
            # Analyse sessions
            "sessions": session_analysis,
            # Analyse temporelle
            "monthly_returns": monthly_returns,
            "yearly": yearly_analysis,
            "trades_per_month": round(n / max(len(monthly_returns), 1), 1),
            "trades_per_day": round(
                n / max(len(set(t.close_time.date() for t in closed if hasattr(t, "close_time") and t.close_time)), 1),
                1,
            ),
        }

    # ─── Drawdown ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_drawdown(equity_curve: list[float], dates: Optional[list[datetime]] = None) -> dict:
        """Calcule les métriques de drawdown à partir de la courbe d'equity."""
        if not equity_curve:
            return {"max_dd_pct": 0, "max_dd_usd": 0, "avg_dd_pct": 0, "max_dd_duration_days": 0}

        peak = equity_curve[0]
        max_dd_pct = 0.0
        max_dd_usd = 0.0
        dd_start_idx = 0
        max_dd_duration = 0
        current_dd_duration = 0
        total_dd_pct = 0.0
        dd_count = 0

        for i, eq in enumerate(equity_curve):
            if eq > peak:
                peak = eq
                current_dd_duration = 0
            else:
                dd_pct = (peak - eq) / peak * 100
                dd_usd = peak - eq
                total_dd_pct += dd_pct
                dd_count += 1
                current_dd_duration += 1

                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
                    max_dd_usd = dd_usd
                    dd_start_idx = i

                if current_dd_duration > max_dd_duration:
                    max_dd_duration = current_dd_duration

        # Durée du DD max en jours
        max_dd_duration_days = max_dd_duration
        if dates and dd_start_idx < len(dates) - 1:
            # Estimation plus précise si dates disponibles
            pass

        return {
            "max_dd_pct": round(max_dd_pct, 2),
            "max_dd_usd": round(max_dd_usd, 2),
            "avg_dd_pct": round(total_dd_pct / max(dd_count, 1), 2),
            "max_dd_duration_days": max_dd_duration_days,
        }

    @staticmethod
    def _compute_drawdown_from_trades(trades: list, initial_balance: float) -> dict:
        """Calcule le drawdown à partir des trades (sans courbe d'equity)."""
        peak = initial_balance
        balance = initial_balance
        max_dd_pct = 0.0
        max_dd_usd = 0.0
        total_dd_pct = 0.0
        dd_count = 0
        dd_duration = 0
        max_dd_duration = 0

        for t in trades:
            pnl = getattr(t, "profit_usd_cost", 0.0)
            balance += pnl
            if balance > peak:
                peak = balance
                dd_duration = 0
            else:
                dd_pct = (peak - balance) / peak * 100
                dd_usd = peak - balance
                total_dd_pct += dd_pct
                dd_count += 1
                dd_duration += 1
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
                    max_dd_usd = dd_usd
                if dd_duration > max_dd_duration:
                    max_dd_duration = dd_duration

        return {
            "max_dd_pct": round(max_dd_pct, 2),
            "max_dd_usd": round(max_dd_usd, 2),
            "avg_dd_pct": round(total_dd_pct / max(dd_count, 1), 2),
            "max_dd_duration_days": max_dd_duration,
        }

    # ─── Sharpe / Sortino ─────────────────────────────────────────────────

    @staticmethod
    def _compute_sharpe(daily_returns: np.ndarray) -> float:
        """Sharpe Ratio annualisé."""
        if len(daily_returns) < 2 or np.std(daily_returns) == 0:
            return 0.0
        excess = np.mean(daily_returns) - RISK_FREE_RATE / 252
        return excess / np.std(daily_returns) * np.sqrt(252)

    @staticmethod
    def _compute_sortino(daily_returns: np.ndarray) -> float:
        """Sortino Ratio annualisé (downside deviation only)."""
        if len(daily_returns) < 2:
            return 0.0
        downside = daily_returns[daily_returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0.0
        excess = np.mean(daily_returns) - RISK_FREE_RATE / 252
        downside_dev = np.std(downside)
        return excess / downside_dev * np.sqrt(252)

    @staticmethod
    def _get_daily_returns(pnls: list[float], trades: list) -> list[float]:
        """Calcule les rendements quotidiens à partir des trades."""
        daily_pnl = defaultdict(float)
        for t, pnl in zip(trades, pnls):
            dt = getattr(t, "close_time", None)
            if dt:
                if hasattr(dt, "date"):
                    day = dt.date()
                elif isinstance(dt, datetime):
                    day = dt.date()
                else:
                    continue
                daily_pnl[day] += pnl

        # Capital initial pour calculer le rendement
        if not daily_pnl:
            return []

        # Estimer le capital au début de chaque jour
        sorted_days = sorted(daily_pnl.keys())
        capital = 200_000.0
        daily_returns = []
        for day in sorted_days:
            ret = daily_pnl[day] / capital if capital > 0 else 0.0
            daily_returns.append(ret)
            capital += daily_pnl[day]

        return daily_returns

    # ─── Analyse sessions ────────────────────────────────────────────────

    @staticmethod
    def _analyze_sessions(trades: list) -> dict:
        """Analyse les performances par session de trading."""
        sessions = {
            "asia": {"trades": 0, "wins": 0, "pnl": 0.0},
            "london": {"trades": 0, "wins": 0, "pnl": 0.0},
            "ny": {"trades": 0, "wins": 0, "pnl": 0.0},
        }

        for t in trades:
            close_time = getattr(t, "close_time", None)
            if not close_time:
                continue
            hour = close_time.hour if hasattr(close_time, "hour") else 0
            pnl = getattr(t, "profit_usd_cost", 0.0)
            is_win = pnl > 0

            for sess_name, (start, end) in SESSIONS.items():
                if start <= hour < end:
                    sessions[sess_name]["trades"] += 1
                    if is_win:
                        sessions[sess_name]["wins"] += 1
                    sessions[sess_name]["pnl"] += pnl

        # Calculer WR et PnL moyen par session
        result = {}
        for sess_name, data in sessions.items():
            n_trades = data["trades"]
            result[sess_name] = {
                "trades": n_trades,
                "win_rate": round(data["wins"] / n_trades * 100, 1) if n_trades > 0 else 0.0,
                "pnl": round(data["pnl"], 2),
                "avg_trade": round(data["pnl"] / n_trades, 2) if n_trades > 0 else 0.0,
            }

        return result

    # ─── Analyse temporelle ──────────────────────────────────────────────

    @staticmethod
    def _analyze_time(trades: list, initial_balance: float) -> tuple[list, dict]:
        """Analyse les performances par mois et par année."""
        monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        yearly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})

        for t in trades:
            close_time = getattr(t, "close_time", None)
            if not close_time:
                continue
            pnl = getattr(t, "profit_usd_cost", 0.0)

            year = close_time.year
            month = close_time.month
            key = f"{year}-{month:02d}"

            monthly[key]["trades"] += 1
            monthly[key]["wins"] += 1 if pnl > 0 else 0
            monthly[key]["pnl"] += pnl

            yearly[year]["trades"] += 1
            yearly[year]["wins"] += 1 if pnl > 0 else 0
            yearly[year]["pnl"] += pnl

        # Format monthly returns
        monthly_returns = []
        for key in sorted(monthly.keys()):
            data = monthly[key]
            monthly_returns.append(
                {
                    "month": key,
                    "trades": data["trades"],
                    "win_rate": round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0.0,
                    "pnl": round(data["pnl"], 2),
                }
            )

        # Format yearly analysis
        yearly_analysis = {}
        for year in sorted(yearly.keys()):
            data = yearly[year]
            yearly_analysis[str(year)] = {
                "trades": data["trades"],
                "win_rate": round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0.0,
                "pnl": round(data["pnl"], 2),
            }

        return monthly_returns, yearly_analysis

    # ─── Comparaison rapide ──────────────────────────────────────────────

    @staticmethod
    def compare(results: dict[str, dict]) -> str:
        """Génère un tableau comparatif ASCII de plusieurs résultats."""
        lines = []
        lines.append(
            f"{'Symbole':<14} {'Trades':>7} {'WR':>7} {'PnL':>12} {'PF':>6} {'DD':>7} {'Sharpe':>8} {'Sortino':>8}"
        )
        lines.append("-" * 75)

        for sym, m in sorted(results.items()):
            if "error" in m:
                continue
            lines.append(
                f"{sym:<14} {m['n']:>7} {m['win_rate']:>6.1f}% "
                f"${m['net_profit']:>+9.2f} {m['profit_factor']:>5.2f} "
                f"{m['max_dd_pct']:>6.2f}% "
                f"{m.get('sharpe_ratio', 0):>7.3f} "
                f"{m.get('sortino_ratio', 0):>7.3f}"
            )

        return "\n".join(lines)
