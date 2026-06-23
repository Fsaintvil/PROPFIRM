"""
ReportGenerator — Génération de rapports de backtest complets.

Produit des rapports :
  1. Console (texte structuré avec tableaux ASCII)
  2. JSON structuré (export pour analyse ultérieure)
  3. CSV (détail des trades)

Supporte :
  - Rapport simple (1 symbole × 1 stratégie)
  - Rapport comparatif (N symboles × M stratégies)
  - Rapport Walk-Forward, Monte Carlo, Stress Tests
  - Rapport FTMO Challenge
  - Export des métriques rolling (par fenêtre de N trades)
  - Résumé exécutif (1 ligne)

Usage :
    rg = ReportGenerator()
    # Rapport simple
    print(rg.format_report(result, symbol="EURUSD", timeframe="H1"))
    # Rapport comparatif
    print(rg.format_comparison(all_results))
    # Export JSON
    rg.export_json(result, "runtime/backtest_report.json")
"""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("backtest_core.reporting")


@dataclass
class ReportConfig:
    """Configuration du rapport."""

    show_all_trades: bool = False
    show_monthly: bool = True
    show_sessions: bool = True
    show_direction: bool = True
    compare_benchmark: bool = True
    table_width: int = 80
    export_csv: bool = True
    export_json: bool = True


class ReportGenerator:
    """
    Génère des rapports structurés pour les résultats de backtest.

    Attributes:
        config: ReportConfig
    """

    def __init__(self, config: Optional[ReportConfig | dict] = None):
        if config is None:
            self.config = ReportConfig()
        elif isinstance(config, dict):
            self.config = ReportConfig(**config)
        else:
            self.config = config

    # ─── Format rapport simple ───────────────────────────────────────────

    def format_report(
        self,
        result,
        symbol: str = "",
        timeframe: str = "",
        strategy_name: str = "",
    ) -> str:
        """
        Génère un rapport formaté pour un résultat de backtest.

        Args:
            result: BacktestResult (ou objet avec .metrics, .trades, etc.)
            symbol: Symbole
            timeframe: Timeframe
            strategy_name: Nom de la stratégie

        Returns:
            str formaté pour affichage console
        """
        metrics = getattr(result, "metrics", result if isinstance(result, dict) else {})
        if not metrics:
            return "Métriques non disponibles"

        lines = []
        lines.append("=" * self.config.table_width)
        title = f"  BACKTEST REPORT — {symbol or ''} {timeframe or ''} {strategy_name or ''}".strip()
        lines.append(title)
        lines.append("=" * self.config.table_width)

        # Informations générales
        n = metrics.get("n", 0)
        lines.append(f"\n  Trades: {n} | Wins: {metrics.get('n_wins', 0)} | Losses: {metrics.get('n_losses', 0)}")

        lines.append("")
        lines.append("  ─── Performance ───")
        lines.append(f"  Net Profit   : ${metrics.get('net_profit', 0):>+10.2f}")
        lines.append(f"  Gross Profit : ${metrics.get('gross_profit', 0):>+10.2f}")
        lines.append(f"  Gross Loss   : ${metrics.get('gross_loss', 0):>+10.2f}")
        lines.append(f"  Return       : {metrics.get('return_pct', 0):>+10.2f}%")
        lines.append(f"  Win Rate     : {metrics.get('win_rate', 0):>9.1f}%")
        lines.append(f"  Profit Factor: {metrics.get('profit_factor', 0):>9.2f}")
        lines.append(f"  Expectancy   : ${metrics.get('expectancy', 0):>+9.2f}")
        lines.append(f"  Avg Trade    : ${metrics.get('avg_trade', 0):>+9.2f}")
        lines.append(f"  Avg RR       : {metrics.get('avg_rr', 0):>9.2f}")

        lines.append("")
        lines.append("  ─── Ratios de Risque ───")
        lines.append(f"  Sharpe Ratio : {metrics.get('sharpe_ratio', 0):>9.3f}")
        lines.append(f"  Sortino Ratio: {metrics.get('sortino_ratio', 0):>9.3f}")
        lines.append(f"  Calmar Ratio : {metrics.get('calmar_ratio', 0):>9.3f}")
        lines.append(f"  Recovery F.  : {metrics.get('recovery_factor', 0):>9.2f}")

        lines.append("")
        lines.append("  ─── Drawdown ───")
        lines.append(f"  Max DD %     : {metrics.get('max_dd_pct', 0):>9.2f}%")
        lines.append(f"  Max DD $     : ${metrics.get('max_dd_usd', 0):>+9.2f}")
        lines.append(f"  Avg DD %     : {metrics.get('avg_dd_pct', 0):>9.2f}%")
        lines.append(f"  Max DD Durée : {metrics.get('max_dd_duration_days', 0):>4} barres")

        lines.append("")
        lines.append("  ─── Significativité ───")
        p_value = metrics.get("p_value", 1.0)
        significant = metrics.get("significant", False)
        lines.append(f"  P-Value      : {p_value:.4f} ({'SIGNIFICATIF' if significant else 'NON SIGNIFICATIF'})")
        lines.append(f"  Z-Score      : {metrics.get('z_score', 0):>+9.3f}")

        lines.append("")
        lines.append("  ─── Séquence ───")
        lines.append(f"  Max Wins     : {metrics.get('max_consecutive_wins', 0)}")
        lines.append(f"  Max Losses   : {metrics.get('max_consecutive_losses', 0)}")

        # Direction
        if self.config.show_direction:
            lines.append("")
            lines.append("  ─── Direction ───")
            direction = metrics.get("direction", {})
            for d in ("long", "short"):
                if d in direction:
                    dd = direction[d]
                    lines.append(
                        f"  {d.upper():<7}: {dd.get('trades', 0):>4} trades, "
                        f"WR {dd.get('win_rate', 0):>5.1f}%, "
                        f"PnL ${dd.get('pnl', 0):>+8.2f}"
                    )

        # Sessions
        if self.config.show_sessions:
            lines.append("")
            lines.append("  ─── Sessions ───")
            sessions = metrics.get("sessions", {})
            for sess_name in ("asia", "london", "ny"):
                if sess_name in sessions:
                    s = sessions[sess_name]
                    lines.append(
                        f"  {sess_name.upper():<8}: {s.get('trades', 0):>4} trades, "
                        f"WR {s.get('win_rate', 0):>5.1f}%, "
                        f"PnL ${s.get('pnl', 0):>+8.2f}"
                    )

        # Monthly
        if self.config.show_monthly:
            monthly = metrics.get("monthly_returns", [])
            if monthly:
                lines.append("")
                lines.append("  ─── Performance Mensuelle ───")
                lines.append(f"  {'Mois':<10} {'Trades':>7} {'WR':>7} {'PnL':>12}")
                lines.append("  " + "-" * 40)
                for m in monthly[:24]:  # Max 24 mois
                    lines.append(f"  {m['month']:<10} {m['trades']:>7} {m['win_rate']:>6.1f}% ${m['pnl']:>+10.2f}")
                if len(monthly) > 24:
                    lines.append(f"  ... et {len(monthly) - 24} mois supplémentaires")

        # Annuel
        yearly = metrics.get("yearly", {})
        if yearly:
            lines.append("")
            lines.append("  ─── Performance Annuelle ───")
            lines.append(f"  {'Année':<8} {'Trades':>7} {'WR':>7} {'PnL':>12}")
            lines.append("  " + "-" * 38)
            for year in sorted(yearly.keys()):
                y = yearly[year]
                lines.append(f"  {year:<8} {y['trades']:>7} {y['win_rate']:>6.1f}% ${y['pnl']:>+10.2f}")

        lines.append("")
        lines.append("=" * self.config.table_width)

        return "\n".join(lines)

    # ─── Format comparaison ─────────────────────────────────────────────

    def format_comparison(
        self,
        results: dict[str, dict] | dict[str, object],
        title: str = "Comparaison Multi-Symboles",
    ) -> str:
        """
        Génère un tableau comparatif de plusieurs résultats.

        Args:
            results: Dict {nom: métriques_dict} ou {symbole: BacktestResult}
            title: Titre du tableau

        Returns:
            str formaté
        """
        # Extraire les métriques
        all_metrics = {}
        for name, res in results.items():
            if hasattr(res, "metrics"):
                all_metrics[name] = res.metrics
            elif isinstance(res, dict):
                all_metrics[name] = res
            else:
                continue

        if not all_metrics:
            return "Aucune métrique disponible"

        lines = []
        lines.append("=" * self.config.table_width)
        lines.append(f"  {title}")
        lines.append("=" * self.config.table_width)
        lines.append("")

        # Tableau principal
        header = f"  {'Symbole':<16} {'Trades':>7} {'WR':>7} {'PnL':>12} {'PF':>6} {'DD':>7} {'Sharpe':>8} {'Avg$':>9}"
        lines.append(header)
        lines.append("  " + "-" * self.config.table_width)

        totals = {"trades": 0, "wins": 0, "pnl": 0.0}

        for name in sorted(all_metrics.keys()):
            m = all_metrics[name]
            if "error" in m:
                continue

            n = m.get("n", 0)
            wr = m.get("win_rate", 0)
            pnl = m.get("net_profit", 0)
            pf = m.get("profit_factor", 0)
            dd = m.get("max_dd_pct", 0)
            sharpe = m.get("sharpe_ratio", 0)
            avg = m.get("avg_trade", 0)

            totals["trades"] += n
            totals["wins"] += m.get("n_wins", 0)
            totals["pnl"] += pnl

            lines.append(
                f"  {name:<16} {n:>7} {wr:>6.1f}% ${pnl:>+9.2f} {pf:>5.2f} {dd:>6.2f}% {sharpe:>7.3f} ${avg:>+7.2f}"
            )

        # Total
        lines.append("  " + "-" * self.config.table_width)
        total_wr = totals["wins"] / totals["trades"] * 100 if totals["trades"] > 0 else 0
        lines.append(f"  {'TOTAL':<16} {totals['trades']:>7} {total_wr:>6.1f}% ${totals['pnl']:>+9.2f}")

        lines.append("")
        lines.append("=" * self.config.table_width)

        return "\n".join(lines)

    # ─── Export JSON ─────────────────────────────────────────────────────

    def export_json(
        self,
        result,
        filepath: str | Path,
        include_trades: bool = True,
    ) -> str:
        """
        Exporte les métriques et trades en JSON.

        Args:
            result: BacktestResult
            filepath: Chemin du fichier JSON
            include_trades: Inclure le détail des trades

        Returns:
            Chemin du fichier créé
        """
        metrics = getattr(result, "metrics", result if isinstance(result, dict) else {})
        trades = getattr(result, "trades", [])

        data = {
            "symbol": getattr(result, "symbol", ""),
            "timeframe": getattr(result, "timeframe", ""),
            "strategy": getattr(result, "strategy_name", ""),
            "generated_at": datetime.utcnow().isoformat(),
            "total_trades": getattr(result, "total_trades", len(trades)),
            "metrics": metrics,
        }

        if include_trades and trades:
            trade_list = []
            for t in trades:
                trade_data = {
                    "symbol": getattr(t, "symbol", ""),
                    "action": getattr(t, "action", ""),
                    "entry": round(getattr(t, "entry", 0), 5),
                    "exit": round(getattr(t, "close_price", 0), 5),
                    "sl": round(getattr(t, "sl", 0), 5),
                    "tp": round(getattr(t, "tp", 0), 5),
                    "pnl_usd": round(getattr(t, "profit_usd", 0), 2),
                    "pnl_cost": round(getattr(t, "profit_usd_cost", 0), 2),
                    "lot": getattr(t, "lot", 0),
                    "regime": getattr(t, "regime", ""),
                    "open_time": str(getattr(t, "open_time", "")),
                    "close_time": str(getattr(t, "close_time", "")),
                    "closed": getattr(t, "closed", False),
                }
                trade_list.append(trade_data)
            data["trades"] = trade_list

        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"Rapport JSON exporté: {filepath}")
        return str(filepath)

    # ─── Export CSV ──────────────────────────────────────────────────────

    def export_csv(
        self,
        trades: list,
        filepath: str | Path,
    ) -> str:
        """
        Exporte les trades en CSV.

        Args:
            trades: Liste d'objets SimTrade
            filepath: Chemin du fichier CSV

        Returns:
            Chemin du fichier créé
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "symbol",
                    "action",
                    "entry",
                    "exit",
                    "sl",
                    "tp",
                    "pnl_usd",
                    "pnl_cost",
                    "lot",
                    "regime",
                    "open_time",
                    "close_time",
                    "bars_held",
                    "closed",
                ]
            )
            for t in trades:
                writer.writerow(
                    [
                        getattr(t, "symbol", ""),
                        getattr(t, "action", ""),
                        round(getattr(t, "entry", 0), 5),
                        round(getattr(t, "close_price", 0), 5),
                        round(getattr(t, "sl", 0), 5),
                        round(getattr(t, "tp", 0), 5),
                        round(getattr(t, "profit_usd", 0), 2),
                        round(getattr(t, "profit_usd_cost", 0), 2),
                        getattr(t, "lot", 0),
                        getattr(t, "regime", ""),
                        str(getattr(t, "open_time", "")),
                        str(getattr(t, "close_time", "")),
                        getattr(t, "bars_held", 0),
                        getattr(t, "closed", False),
                    ]
                )

        logger.info(f"Trades CSV exporté: {filepath} ({len(trades)} trades)")
        return str(filepath)

    # ─── Résumé exécutif ─────────────────────────────────────────────────

    @staticmethod
    def executive_summary(result) -> str:
        """Résumé en 1 ligne pour affichage rapide."""
        metrics = getattr(result, "metrics", result if isinstance(result, dict) else {})
        if not metrics or "error" in metrics:
            return "❌ Pas de données"

        symbol = getattr(result, "symbol", "")
        tf = getattr(result, "timeframe", "")
        n = metrics.get("n", 0)
        wr = metrics.get("win_rate", 0)
        pnl = metrics.get("net_profit", 0)
        pf = metrics.get("profit_factor", 0)
        dd = metrics.get("max_dd_pct", 0)
        sharpe = metrics.get("sharpe_ratio", 0)
        sig = "✅" if metrics.get("significant", False) else "❌"

        return (
            f"{symbol:<8} {tf:<4} {n:>5} trades | "
            f"WR {wr:>5.1f}% | PnL ${pnl:>+8.2f} | "
            f"PF {pf:>4.2f} | DD {dd:>5.2f}% | "
            f"Sharpe {sharpe:>5.3f} | {sig}"
        )
