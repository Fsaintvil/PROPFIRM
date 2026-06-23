"""
ChartGenerator — Visualisation des résultats de backtest.

Produit :
  - PNG charts : equity curve, drawdown, monthly returns, trades
  - PDF report : rapport complet avec tous les charts
  - HTML dashboard : interactive avec Plotly

Charts disponibles :
  1. Equity Curve + Balance
  2. Drawdown Underwater
  3. Monthly Returns (barres)
  4. Yearly Returns (barres)
  5. Trade PnL Distribution (histogramme)
  6. Win/Loss by Session
  7. Win Rate Rolling (N trades)
  8. Sharpe Rolling (N trades)
  9. Consecutive Wins/Losses
  10. Scatter Plot (Trade # vs PnL)

Usage :
    cg = ChartGenerator(output_dir="backtest/results")
    cg.equity_curve(result, "EURUSD_H1_equity.png")
    cg.drawdown(result, "EURUSD_H1_dd.png")
    cg.full_report(result, "EURUSD_H1")  # Génère tout + PDF
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("backtest_core.visualization")

# Tentative d'import matplotlib (optionnel)
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    from matplotlib.figure import Figure

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logger.warning("matplotlib non installé — les charts PNG/PDF ne seront pas générés")

# Tentative d'import Plotly (optionnel)
try:
    import plotly.graph_objects as go
    import plotly.offline as py_offline

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False
    logger.warning("plotly non installé — le dashboard HTML ne sera pas généré")

# Tentative d'import FPDF (optionnel)
try:
    from fpdf import FPDF

    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False
    logger.warning("fpdf non installé — les rapports PDF ne seront pas générés")


class ChartGenerator:
    """
    Générateur de graphiques et rapports visuels.

    Attributes:
        output_dir: Dossier de sortie pour les fichiers
        style: Style matplotlib ("dark_background", "default", etc.)
        figsize: Taille des figures (width, height) en pouces
        dpi: Résolution des PNG
    """

    def __init__(
        self,
        output_dir: str | Path = "backtest/results",
        style: str = "default",
        figsize: tuple[int, int] = (12, 6),
        dpi: int = 100,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.style = style
        self.figsize = figsize
        self.dpi = dpi

    # ═══════════════════════════════════════════════════════════════════════
    # Charts individuels
    # ═══════════════════════════════════════════════════════════════════════

    def equity_curve(
        self,
        result,
        filename: str = "equity_curve.png",
        show_trades: bool = True,
    ) -> Optional[str]:
        """
        Génère le graphique Equity Curve + Balance + Trades.

        Args:
            result: BacktestResult
            filename: Nom du fichier PNG
            show_trades: Afficher les trades individuels

        Returns:
            Chemin du fichier ou None si matplotlib absent
        """
        if not HAS_MATPLOTLIB:
            return None

        equity = getattr(result, "equity_curve", [])
        balance = getattr(result, "balance_curve", [])
        dates = getattr(result, "dates", [])
        trades = getattr(result, "trades", [])

        if not equity:
            logger.warning("Pas de courbe d'equity à tracer")
            return None

        fig, ax = plt.subplots(1, 1, figsize=self.figsize)

        # Convertir les dates en indices si nécessaire
        x = range(len(equity))

        # Equity curve
        ax.plot(x, equity, label="Equity", color="#00ff88", linewidth=1.5)
        if balance:
            ax.plot(x, balance, label="Balance", color="#4488ff", linewidth=1, alpha=0.7)

        # Trades individuels
        if show_trades and trades:
            for t in trades:
                bar_idx = getattr(t, "bar_idx", 0)
                if bar_idx < len(equity):
                    pnl = getattr(t, "profit_usd_cost", 0)
                    color = "#00ff88" if pnl > 0 else "#ff4444"
                    ax.scatter(bar_idx, equity[min(bar_idx, len(equity) - 1)], color=color, s=15, alpha=0.5, zorder=5)

        # Ligne de base
        ax.axhline(y=equity[0], color="gray", linestyle="--", alpha=0.5, label="Capital initial")

        ax.set_title(f"Equity Curve — {getattr(result, 'symbol', '')} {getattr(result, 'timeframe', '')}")
        ax.set_xlabel("Barre")
        ax.set_ylabel("Equity ($)")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)

        # Ajouter les métriques en texte
        metrics = getattr(result, "metrics", {})
        text = (
            f"PnL: ${metrics.get('net_profit', 0):+.2f} | "
            f"WR: {metrics.get('win_rate', 0):.1f}% | "
            f"DD: {metrics.get('max_dd_pct', 0):.2f}% | "
            f"Sharpe: {metrics.get('sharpe_ratio', 0):.3f}"
        )
        ax.text(
            0.02,
            0.95,
            text,
            transform=ax.transAxes,
            fontsize=9,
            verticalalignment="top",
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8},
        )

        filepath = self.output_dir / filename
        plt.tight_layout()
        plt.savefig(filepath, dpi=self.dpi, bbox_inches="tight")
        plt.close(fig)

        logger.info(f"Equity curve sauvegardée: {filepath}")
        return str(filepath)

    def drawdown(
        self,
        result,
        filename: str = "drawdown.png",
    ) -> Optional[str]:
        """
        Génère le graphique Underwater Drawdown.

        Args:
            result: BacktestResult
            filename: Nom du fichier PNG

        Returns:
            Chemin du fichier ou None
        """
        if not HAS_MATPLOTLIB:
            return None

        dd_curve = getattr(result, "dd_curve", [])
        if not dd_curve:
            # Calculer depuis equity
            equity = getattr(result, "equity_curve", [])
            if not equity:
                return None
            peak = equity[0]
            dd_curve = []
            for eq in equity:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                dd_curve.append(dd)

        fig, ax = plt.subplots(1, 1, figsize=self.figsize)

        x = range(len(dd_curve))
        ax.fill_between(x, dd_curve, 0, color="#ff4444", alpha=0.5, label="Drawdown")
        ax.plot(x, dd_curve, color="#ff2222", linewidth=1)

        ax.set_title(f"Drawdown — {getattr(result, 'symbol', '')} {getattr(result, 'timeframe', '')}")
        ax.set_xlabel("Barre")
        ax.set_ylabel("Drawdown (%)")
        ax.legend(loc="lower left")
        ax.grid(True, alpha=0.3)
        ax.invert_yaxis()

        max_dd = max(dd_curve) if dd_curve else 0
        ax.axhline(y=max_dd, color="red", linestyle="--", alpha=0.7, label=f"Max DD: {max_dd:.2f}%")
        ax.legend()

        filepath = self.output_dir / filename
        plt.tight_layout()
        plt.savefig(filepath, dpi=self.dpi, bbox_inches="tight")
        plt.close(fig)

        logger.info(f"Drawdown sauvegardé: {filepath}")
        return str(filepath)

    def monthly_returns(
        self,
        result,
        filename: str = "monthly_returns.png",
    ) -> Optional[str]:
        """
        Génère le graphique des rendements mensuels.

        Args:
            result: BacktestResult
            filename: Nom du fichier PNG

        Returns:
            Chemin du fichier ou None
        """
        if not HAS_MATPLOTLIB:
            return None

        metrics = getattr(result, "metrics", {})
        monthly = metrics.get("monthly_returns", [])
        if not monthly:
            return None

        fig, ax = plt.subplots(1, 1, figsize=(max(12, len(monthly) * 0.3), 6))

        months = [m["month"] for m in monthly]
        pnls = [m["pnl"] for m in monthly]
        colors = ["#00cc66" if p > 0 else "#ff4444" for p in pnls]

        ax.bar(range(len(pnls)), pnls, color=colors, alpha=0.8)
        ax.axhline(y=0, color="black", linewidth=0.5)

        ax.set_title(f"Rendements Mensuels — {getattr(result, 'symbol', '')} {getattr(result, 'timeframe', '')}")
        ax.set_xlabel("Mois")
        ax.set_ylabel("PnL ($)")
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels(months, rotation=45, ha="right", fontsize=8)
        ax.grid(True, alpha=0.3, axis="y")

        # Ajouter les valeurs
        for i, pnl in enumerate(pnls):
            ax.text(
                i,
                pnl + (max(pnls) * 0.02 if pnl > 0 else min(pnls) * 0.02),
                f"${pnl:.0f}",
                ha="center",
                va="bottom" if pnl > 0 else "top",
                fontsize=7,
                rotation=45,
            )

        filepath = self.output_dir / filename
        plt.tight_layout()
        plt.savefig(filepath, dpi=self.dpi, bbox_inches="tight")
        plt.close(fig)

        logger.info(f"Monthly returns sauvegardé: {filepath}")
        return str(filepath)

    def trade_pnl_distribution(
        self,
        result,
        filename: str = "pnl_distribution.png",
    ) -> Optional[str]:
        """
        Génère l'histogramme de distribution des PnL.

        Args:
            result: BacktestResult
            filename: Nom du fichier PNG

        Returns:
            Chemin du fichier ou None
        """
        if not HAS_MATPLOTLIB:
            return None

        trades = getattr(result, "trades", [])
        closed = [t for t in trades if getattr(t, "closed", False)]
        if not closed:
            return None

        pnls = [getattr(t, "profit_usd_cost", 0) for t in closed]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(self.figsize[0], self.figsize[1] * 0.8))

        # Histogramme
        ax1.hist(pnls, bins=min(50, len(pnls) // 5 + 1), color="#4488ff", alpha=0.7, edgecolor="white")
        ax1.axvline(x=0, color="red", linestyle="--", linewidth=1)
        ax1.axvline(x=np.mean(pnls), color="green", linestyle="--", linewidth=1, label=f"Moy: ${np.mean(pnls):.2f}")
        ax1.set_title("Distribution des PnL par Trade")
        ax1.set_xlabel("PnL ($)")
        ax1.set_ylabel("Fréquence")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Boxplot
        ax2.boxplot(pnls, vert=True, patch_artist=True, boxprops={"facecolor": "#4488ff", "alpha": 0.7})
        ax2.axhline(y=0, color="red", linestyle="--", linewidth=1)
        ax2.set_title("Boxplot des PnL")
        ax2.set_ylabel("PnL ($)")
        ax2.set_xticklabels(["Tous les trades"])
        ax2.grid(True, alpha=0.3, axis="y")

        filepath = self.output_dir / filename
        plt.tight_layout()
        plt.savefig(filepath, dpi=self.dpi, bbox_inches="tight")
        plt.close(fig)

        logger.info(f"PnL distribution sauvegardée: {filepath}")
        return str(filepath)

    def rolling_metrics(
        self,
        result,
        window: int = 50,
        filename: str = "rolling_metrics.png",
    ) -> Optional[str]:
        """
        Génère les métriques roulantes (WR, PnL cumulé, DD).

        Args:
            result: BacktestResult
            window: Fenêtre pour le rolling
            filename: Nom du fichier PNG

        Returns:
            Chemin du fichier ou None
        """
        if not HAS_MATPLOTLIB:
            return None

        trades = getattr(result, "trades", [])
        closed = [t for t in trades if getattr(t, "closed", False)]
        if len(closed) < window:
            return None

        pnls = [getattr(t, "profit_usd_cost", 0) for t in closed]

        # Calculer rolling WR
        rolling_wr = []
        for i in range(window, len(pnls) + 1):
            chunk = pnls[i - window : i]
            wr = sum(1 for p in chunk if p > 0) / window * 100
            rolling_wr.append(wr)

        # Rolling Sharpe
        rolling_sharpe = []
        for i in range(window, len(pnls) + 1):
            chunk = pnls[i - window : i]
            if np.std(chunk) > 0:
                sharpe = np.mean(chunk) / np.std(chunk) * np.sqrt(252) if len(chunk) > 1 else 0
            else:
                sharpe = 0
            rolling_sharpe.append(sharpe)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(self.figsize[0], self.figsize[1]))

        x = range(len(rolling_wr))

        # Rolling WR
        ax1.plot(x, rolling_wr, color="#00cc66", linewidth=1.5)
        ax1.axhline(y=50, color="red", linestyle="--", alpha=0.5, label="50%")
        ax1.axhline(
            y=np.mean(rolling_wr), color="orange", linestyle="--", alpha=0.7, label=f"Moy: {np.mean(rolling_wr):.1f}%"
        )
        ax1.set_title(f"Rolling Win Rate ({window} trades)")
        ax1.set_ylabel("Win Rate (%)")
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim(0, 100)

        # Rolling Sharpe
        ax2.plot(x, rolling_sharpe, color="#4488ff", linewidth=1.5)
        ax2.axhline(y=0, color="red", linestyle="--", alpha=0.5)
        ax2.axhline(
            y=np.mean(rolling_sharpe),
            color="orange",
            linestyle="--",
            alpha=0.7,
            label=f"Moy: {np.mean(rolling_sharpe):.3f}",
        )
        ax2.set_title(f"Rolling Sharpe ({window} trades)")
        ax2.set_xlabel("Trade #")
        ax2.set_ylabel("Sharpe")
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        filepath = self.output_dir / filename
        plt.tight_layout()
        plt.savefig(filepath, dpi=self.dpi, bbox_inches="tight")
        plt.close(fig)

        logger.info(f"Rolling metrics sauvegardées: {filepath}")
        return str(filepath)

    # ═══════════════════════════════════════════════════════════════════════
    # Rapport complet
    # ═══════════════════════════════════════════════════════════════════════

    def full_report(
        self,
        result,
        name: str = "backtest",
    ) -> dict[str, str]:
        """
        Génère tous les graphiques pour un résultat.

        Args:
            result: BacktestResult
            name: Nom de base pour les fichiers

        Returns:
            Dict {type: filepath}
        """
        generated = {}

        if not HAS_MATPLOTLIB:
            logger.warning("matplotlib non installé, skip visualisation")
            return generated

        # Générer tous les charts
        eq = self.equity_curve(result, f"{name}_equity.png")
        if eq:
            generated["equity"] = eq

        dd = self.drawdown(result, f"{name}_drawdown.png")
        if dd:
            generated["drawdown"] = dd

        mr = self.monthly_returns(result, f"{name}_monthly.png")
        if mr:
            generated["monthly"] = mr

        pnl = self.trade_pnl_distribution(result, f"{name}_pnl_dist.png")
        if pnl:
            generated["pnl_distribution"] = pnl

        roll = self.rolling_metrics(result, filename=f"{name}_rolling.png")
        if roll:
            generated["rolling"] = roll

        # Générer le PDF si disponible
        pdf_path = self._generate_pdf(result, name, generated)
        if pdf_path:
            generated["pdf"] = pdf_path

        # Générer le HTML dashboard si disponible
        html_path = self._generate_html(result, name, generated)
        if html_path:
            generated["html"] = html_path

        logger.info(f"Rapport complet généré: {len(generated)} fichiers dans {self.output_dir}")
        return generated

    # ─── PDF ─────────────────────────────────────────────────────────────

    def _generate_pdf(
        self,
        result,
        name: str,
        charts: dict[str, str],
    ) -> Optional[str]:
        """Génère un rapport PDF avec tous les charts et métriques."""
        if not HAS_FPDF:
            return None

        try:
            pdf = FPDF()
            pdf.add_page()

            # Titre
            pdf.set_font("Arial", "B", 16)
            safe_name = name.replace("—", "-").replace("–", "-")
            pdf.cell(0, 10, f"Backtest Report - {safe_name}", new_x="LMARGIN", new_y="NEXT", align="C")
            pdf.ln(5)

            # Métriques
            metrics = getattr(result, "metrics", {})
            pdf.set_font("Arial", "", 10)
            metrics_text = (
                f"Symbole: {getattr(result, 'symbol', '')} | "
                f"Timeframe: {getattr(result, 'timeframe', '')} | "
                f"Trades: {metrics.get('n', 0)} | "
                f"WR: {metrics.get('win_rate', 0):.1f}% | "
                f"PnL: ${metrics.get('net_profit', 0):+.2f} | "
                f"PF: {metrics.get('profit_factor', 0):.2f} | "
                f"DD: {metrics.get('max_dd_pct', 0):.2f}% | "
                f"Sharpe: {metrics.get('sharpe_ratio', 0):.3f}"
            )
            pdf.multi_cell(0, 5, metrics_text)
            pdf.ln(5)

            # Ajouter les charts
            chart_types = ["equity", "drawdown", "monthly", "pnl_distribution", "rolling"]
            for chart_type in chart_types:
                if chart_type in charts:
                    chart_path = charts[chart_type]
                    if pdf.get_y() > 200:
                        pdf.add_page()
                    pdf.image(chart_path, x=10, w=180)
                    pdf.ln(5)

            # Sauvegarder
            filepath = self.output_dir / f"{name}_report.pdf"
            pdf.output(str(filepath))
            logger.info(f"PDF généré: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Erreur génération PDF: {e}")
            return None

    # ─── HTML ────────────────────────────────────────────────────────────

    def _generate_html(
        self,
        result,
        name: str,
        charts: dict[str, str],
    ) -> Optional[str]:
        """Génère un dashboard HTML interactif."""
        if not HAS_PLOTLY:
            return None

        try:
            trades = getattr(result, "trades", [])
            closed = [t for t in trades if getattr(t, "closed", False)]
            metrics = getattr(result, "metrics", {})

            if not closed:
                return None

            # Extraire les données pour Plotly
            pnls = [getattr(t, "profit_usd_cost", 0) for t in closed]
            actions = [getattr(t, "action", "BUY") for t in closed]
            trade_indices = list(range(len(closed)))

            # Créer les figures Plotly
            fig_equity = go.Figure()
            equity = getattr(result, "equity_curve", [])
            if equity:
                fig_equity.add_trace(go.Scatter(y=equity, mode="lines", name="Equity", line={"color": "#00ff88"}))
            fig_equity.update_layout(title="Equity Curve", height=400)

            fig_pnl = go.Figure()
            colors = ["#00cc66" if p > 0 else "#ff4444" for p in pnls]
            fig_pnl.add_trace(go.Bar(x=trade_indices, y=pnls, name="PnL", marker_color=colors))
            fig_pnl.update_layout(title="Trade PnL", height=400)

            # Générer le HTML
            html_parts = [
                "<html><head>",
                "<title>Backtest Dashboard</title>",
                '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>',
                "</head><body>",
                f"<h1>Backtest Report — {name}</h1>",
                f"<p>Symbole: {getattr(result, 'symbol', '')} | "
                f"Timeframe: {getattr(result, 'timeframe', '')} | "
                f"Trades: {metrics.get('n', 0)} | "
                f"WR: {metrics.get('win_rate', 0):.1f}% | "
                f"PnL: ${metrics.get('net_profit', 0):+.2f}</p>",
                py_offline.plot(fig_equity, include_plotlyjs=False, output_type="div"),
                py_offline.plot(fig_pnl, include_plotlyjs=False, output_type="div"),
            ]

            # Ajouter les charts matplotlib
            for chart_type in ["equity", "drawdown", "monthly"]:
                if chart_type in charts:
                    chart_rel = Path(charts[chart_type]).name
                    html_parts.append(f'<img src="{chart_rel}" style="width:100%;max-width:900px;">')

            html_parts.append("</body></html>")

            html_content = "\n".join(html_parts)

            filepath = self.output_dir / f"{name}_dashboard.html"
            with open(filepath, "w") as f:
                f.write(html_content)

            logger.info(f"HTML dashboard généré: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Erreur génération HTML: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════════
    # Utilitaires
    # ═══════════════════════════════════════════════════════════════════════

    def generate_all(
        self,
        results: dict[str, object],
    ) -> dict[str, dict[str, str]]:
        """
        Génère les rapports pour tous les résultats.

        Args:
            results: Dict {name: BacktestResult}

        Returns:
            Dict {name: {type: filepath}}
        """
        all_reports = {}
        for name, result in results.items():
            all_reports[name] = self.full_report(result, name)
        return all_reports
