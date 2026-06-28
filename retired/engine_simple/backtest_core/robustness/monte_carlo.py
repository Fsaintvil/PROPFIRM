"""
MonteCarloSimulator — Simulation Monte Carlo pour la robustesse des backtests.

Génère N scénarios (défaut: 1000) en reshufflant les outcomes des trades
pour estimer la distribution des performances possibles.

Méthodes supportées :
  1. Reshuffle (indépendance) : mélanger aléatoirement l'ordre des trades
     → préserve la distribution des PnL, détruit la dépendance temporelle
  2. Bootstrap (avec remise) : tirer N trades avec remise dans l'historique
     → préserve la distribution empirique, permet des scénarios extrêmes
  3. Probabiliste : générer des trades avec WR et RR moyens observés
     → plus lisse, utile quand peu de trades disponibles

Sorties :
  - Intervalles de confiance à 95% pour le PnL, WR, PF, DD
  - Probabilité de ruine (capital < 0)
  - Probabilité d'atteindre les objectifs FTMO
  - Distribution complète exportable en histogramme

Usage :
    mc = MonteCarloSimulator(n_simulations=1000, method="bootstrap")
    results = mc.run(trades, initial_balance=200_000)
    print(mc.summary(results))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("backtest_core.robustness.monte_carlo")


@dataclass
class MCResult:
    """Résultat complet d'une simulation Monte Carlo."""

    n_simulations: int
    method: str
    n_trades_input: int

    # Statistiques PnL
    mean_pnl: float = 0.0
    median_pnl: float = 0.0
    std_pnl: float = 0.0
    best_pnl: float = 0.0
    worst_pnl: float = 0.0
    ci_95_lower: float = 0.0
    ci_95_upper: float = 0.0

    # Probabilités
    prob_positive: float = 0.0  # P(PnL > 0)
    prob_ruin: float = 0.0  # P(capital < 0)
    prob_ftmo_pass: float = 0.0  # P(atteindre +5% dans 30 jours)
    prob_ftmo_fail_dd: float = 0.0  # P(dépasser -10% DD)

    # Statistiques Win Rate
    mean_win_rate: float = 0.0
    std_win_rate: float = 0.0
    ci_95_wr_lower: float = 0.0
    ci_95_wr_upper: float = 0.0

    # Statistiques Profit Factor
    mean_pf: float = 0.0
    ci_95_pf_lower: float = 0.0
    ci_95_pf_upper: float = 0.0

    # Drawdown
    mean_max_dd: float = 0.0
    std_max_dd: float = 0.0
    ci_95_dd_lower: float = 0.0
    ci_95_dd_upper: float = 0.0
    worst_dd: float = 0.0
    prob_dd_gt_10: float = 0.0  # P(DD > 10%)
    prob_dd_gt_5: float = 0.0  # P(DD > 5%)

    # Distribution complète
    pnl_distribution: list[float] = field(default_factory=list)
    wr_distribution: list[float] = field(default_factory=list)
    pf_distribution: list[float] = field(default_factory=list)
    dd_distribution: list[float] = field(default_factory=list)

    # Métriques descriptives
    skewness: float = 0.0
    kurtosis: float = 0.0
    var_95: float = 0.0  # Value at Risk 95%
    cvar_95: float = 0.0  # Conditional VaR 95%


class MonteCarloSimulator:
    """
    Simulateur Monte Carlo pour évaluer la robustesse d'une stratégie.

    Attributes:
        n_simulations: Nombre de simulations (défaut: 1000)
        method: Méthode de simulation ("bootstrap" | "reshuffle" | "probabilistic")
        seed: Graine aléatoire pour reproductibilité
        verbose: Logs détaillés
    """

    def __init__(
        self,
        n_simulations: int = 1000,
        method: str = "bootstrap",
        seed: Optional[int] = None,
        verbose: bool = False,
    ):
        if n_simulations < 100:
            raise ValueError("n_simulations doit être >= 100")
        if method not in ("bootstrap", "reshuffle", "probabilistic"):
            raise ValueError(f"Méthode inconnue: {method}")

        self.n_simulations = n_simulations
        self.method = method
        self.verbose = verbose

        if seed is not None:
            np.random.seed(seed)

    # ─── Run ──────────────────────────────────────────────────────────────

    def run(
        self,
        trades: list,
        initial_balance: float = 200_000.0,
        n_trades_target: Optional[int] = None,
    ) -> MCResult:
        """
        Exécute la simulation Monte Carlo.

        Args:
            trades: Liste d'objets SimTrade (doivent avoir profit_usd_cost)
            initial_balance: Capital initial
            n_trades_target: Nombre de trades par simulation
                             (None = même nb que l'input)

        Returns:
            MCResult avec toutes les statistiques
        """
        # Extraire les PnL
        pnls = [getattr(t, "profit_usd_cost", 0.0) for t in trades if hasattr(t, "closed") and t.closed]
        pnls = [p for p in pnls if p != 0]  # Ignorer les trades à l'équilibre

        if len(pnls) < 10:
            logger.error(f"Pas assez de trades pour MC: {len(pnls)}")
            return MCResult(
                n_simulations=self.n_simulations,
                method=self.method,
                n_trades_input=len(pnls),
            )

        n_trades = n_trades_target or len(pnls)

        # Répartir les trades pour le calcul des métriques
        # On va générer des séquences de trades et calculer les métriques
        # pour chaque simulation

        all_pnls = np.array(pnls, dtype=float)
        mean_trade = float(np.mean(all_pnls))
        std_trade = float(np.std(all_pnls))
        wr_input = float(np.mean(all_pnls > 0))
        win_pnls = all_pnls[all_pnls > 0]
        loss_pnls = all_pnls[all_pnls <= 0]
        mean_win = float(np.mean(win_pnls)) if len(win_pnls) > 0 else 0
        mean_loss = float(np.mean(loss_pnls)) if len(loss_pnls) > 0 else 0

        # Arrays pour stocker les résultats
        sim_pnls = np.zeros(self.n_simulations)
        sim_wrs = np.zeros(self.n_simulations)
        sim_pfs = np.zeros(self.n_simulations)
        sim_dds = np.zeros(self.n_simulations)

        for sim_idx in range(self.n_simulations):
            if self.method == "bootstrap":
                # Tirage avec remise
                sim_trades = np.random.choice(all_pnls, size=n_trades, replace=True)
            elif self.method == "reshuffle":
                # Mélanger aléatoirement
                sim_trades = np.random.permutation(all_pnls)[:n_trades]
            else:  # probabilistic
                # Génération probabiliste
                sim_trades = self._generate_probabilistic(n_trades, wr_input, mean_win, mean_loss)

            # Calculer les métriques pour cette simulation
            sim_pnl = float(np.sum(sim_trades))
            sim_wr = float(np.mean(sim_trades > 0)) * 100
            sim_pf = self._compute_pf(sim_trades)

            # Simuler la courbe d'equity
            equity = self._simulate_equity(sim_trades, initial_balance)
            sim_dd = self._compute_max_dd(equity)

            sim_pnls[sim_idx] = sim_pnl
            sim_wrs[sim_idx] = sim_wr
            sim_pfs[sim_idx] = sim_pf
            sim_dds[sim_idx] = sim_dd

        # ─── Calculer les statistiques ───────────────────────────────────
        pnl_sorted = np.sort(sim_pnls)
        wr_sorted = np.sort(sim_wrs)
        pf_sorted = np.sort(sim_pfs)
        dd_sorted = np.sort(sim_dds)

        ci_idx = int(self.n_simulations * 0.025)

        mean_pnl = float(np.mean(sim_pnls))
        median_pnl = float(np.median(sim_pnls))
        std_pnl = float(np.std(sim_pnls))

        result = MCResult(
            n_simulations=self.n_simulations,
            method=self.method,
            n_trades_input=len(pnls),
            # PnL
            mean_pnl=round(mean_pnl, 2),
            median_pnl=round(median_pnl, 2),
            std_pnl=round(std_pnl, 2),
            best_pnl=round(float(np.max(sim_pnls)), 2),
            worst_pnl=round(float(np.min(sim_pnls)), 2),
            ci_95_lower=round(float(pnl_sorted[ci_idx]), 2),
            ci_95_upper=round(float(pnl_sorted[-ci_idx - 1]), 2),
            # Probabilités
            prob_positive=round(float(np.mean(sim_pnls > 0)), 4),
            prob_ruin=round(float(np.mean(sim_pnls < -initial_balance * 0.9)), 4),
            prob_ftmo_pass=round(float(np.mean(sim_pnls > initial_balance * 0.05)), 4),
            prob_ftmo_fail_dd=round(float(np.mean(sim_dds > 10)), 4),
            # Win Rate
            mean_win_rate=round(float(np.mean(sim_wrs)), 1),
            std_win_rate=round(float(np.std(sim_wrs)), 1),
            ci_95_wr_lower=round(float(wr_sorted[ci_idx]), 1),
            ci_95_wr_upper=round(float(wr_sorted[-ci_idx - 1]), 1),
            # Profit Factor
            mean_pf=round(float(np.mean(sim_pfs)), 2),
            ci_95_pf_lower=round(float(pf_sorted[ci_idx]), 2),
            ci_95_pf_upper=round(float(pf_sorted[-ci_idx - 1]), 2),
            # Drawdown
            mean_max_dd=round(float(np.mean(sim_dds)), 2),
            std_max_dd=round(float(np.std(sim_dds)), 2),
            ci_95_dd_lower=round(float(dd_sorted[ci_idx]), 2),
            ci_95_dd_upper=round(float(dd_sorted[-ci_idx - 1]), 2),
            worst_dd=round(float(np.max(sim_dds)), 2),
            prob_dd_gt_10=round(float(np.mean(sim_dds > 10)), 4),
            prob_dd_gt_5=round(float(np.mean(sim_dds > 5)), 4),
            # Distributions
            pnl_distribution=[round(float(x), 2) for x in sim_pnls[:100]],  # Échantillon
            wr_distribution=[round(float(x), 1) for x in sim_wrs[:100]],
            pf_distribution=[round(float(x), 2) for x in sim_pfs[:100]],
            dd_distribution=[round(float(x), 2) for x in sim_dds[:100]],
            # Métriques de risque
            skewness=round(float(self._compute_skewness(sim_pnls)), 3),
            kurtosis=round(float(self._compute_kurtosis(sim_pnls)), 3),
            var_95=round(float(pnl_sorted[ci_idx]), 2),
            cvar_95=round(float(np.mean(pnl_sorted[: ci_idx + 1])), 2),
        )

        if self.verbose:
            logger.info(
                f"MC {self.n_simulations}x ({self.method}): "
                f"PnL moyen=${result.mean_pnl:.2f} ±${result.std_pnl:.2f}, "
                f"IC95=[${result.ci_95_lower:.2f}, ${result.ci_95_upper:.2f}]"
            )

        return result

    # ─── Méthodes internes ───────────────────────────────────────────────

    def _generate_probabilistic(self, n: int, wr: float, mean_win: float, mean_loss: float) -> np.ndarray:
        """Génère N trades selon une distribution probabiliste."""
        trades = np.zeros(n)
        n_wins = int(n * wr)
        n_losses = n - n_wins

        wins = np.random.exponential(mean_win, size=n_wins) if mean_win > 0 else np.zeros(n_wins)
        losses = -np.random.exponential(abs(mean_loss), size=n_losses) if mean_loss != 0 else np.zeros(n_losses)

        all_trades = np.concatenate([wins, losses])
        np.random.shuffle(all_trades)

        return all_trades[:n]

    @staticmethod
    def _compute_pf(pnls: np.ndarray) -> float:
        """Calcule le Profit Factor."""
        gross_profit = float(np.sum(pnls[pnls > 0]))
        gross_loss = float(abs(np.sum(pnls[pnls < 0])))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 2)

    @staticmethod
    def _simulate_equity(trades: np.ndarray, initial_balance: float) -> np.ndarray:
        """Simule la courbe d'equity à partir des PnL."""
        equity = np.full(len(trades) + 1, initial_balance, dtype=float)
        for i, pnl in enumerate(trades):
            equity[i + 1] = equity[i] + pnl
        return equity

    @staticmethod
    def _compute_max_dd(equity: np.ndarray) -> float:
        """Calcule le drawdown maximum à partir d'une courbe d'equity."""
        peak = equity[0]
        max_dd = 0.0
        for eq in equity:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _compute_skewness(data: np.ndarray) -> float:
        """Calcule le skewness (asymétrie)."""
        if len(data) < 3 or np.std(data) == 0:
            return 0.0
        return float(np.mean((data - np.mean(data)) ** 3) / np.std(data) ** 3)

    @staticmethod
    def _compute_kurtosis(data: np.ndarray) -> float:
        """Calcule la kurtosis (excès)."""
        if len(data) < 4 or np.std(data) == 0:
            return 0.0
        return float(np.mean((data - np.mean(data)) ** 4) / np.std(data) ** 4 - 3)

    # ─── Résumé ──────────────────────────────────────────────────────────

    @staticmethod
    def summary(results: MCResult) -> str:
        """Génère un rapport textuel de la simulation Monte Carlo."""
        lines = []
        lines.append("═" * 70)
        lines.append("  MONTE CARLO SIMULATION  ")
        lines.append("═" * 70)
        lines.append(f"  Simulations: {results.n_simulations:,}")
        lines.append(f"  Méthode: {results.method}")
        lines.append(f"  Trades d'entrée: {results.n_trades_input}")
        lines.append("")

        # PnL
        lines.append("  ─── Distribution du PnL ───")
        lines.append(f"  PnL moyen    : ${results.mean_pnl:>+10.2f}")
        lines.append(f"  PnL médian   : ${results.median_pnl:>+10.2f}")
        lines.append(f"  Écart-type   : ${results.std_pnl:>10.2f}")
        lines.append(f"  Best case    : ${results.best_pnl:>+10.2f}")
        lines.append(f"  Worst case   : ${results.worst_pnl:>10.2f}")
        lines.append(f"  IC 95%       : [${results.ci_95_lower:>+9.2f}, ${results.ci_95_upper:>+9.2f}]")
        lines.append(f"  VaR 95%      : ${results.var_95:>+10.2f}")
        lines.append(f"  CVaR 95%     : ${results.cvar_95:>+10.2f}")
        lines.append("")

        # Probabilités
        lines.append("  ─── Probabilités ───")
        lines.append(f"  P(PnL > 0)       : {results.prob_positive:.1%}")
        lines.append(f"  P(Ruine)         : {results.prob_ruin:.1%}")
        lines.append(f"  P(FTMO Pass)     : {results.prob_ftmo_pass:.1%}")
        lines.append(f"  P(FTMO Fail DD)  : {results.prob_ftmo_fail_dd:.1%}")
        lines.append("")

        # Win Rate
        lines.append("  ─── Distribution du Win Rate ───")
        lines.append(f"  WR moyen    : {results.mean_win_rate:.1f}%")
        lines.append(f"  WR std      : {results.std_win_rate:.1f}%")
        lines.append(f"  IC 95%      : [{results.ci_95_wr_lower:.1f}%, {results.ci_95_wr_upper:.1f}%]")
        lines.append("")

        # Profit Factor
        lines.append("  ─── Distribution du Profit Factor ───")
        lines.append(f"  PF moyen    : {results.mean_pf:.2f}")
        lines.append(f"  IC 95%      : [{results.ci_95_pf_lower:.2f}, {results.ci_95_pf_upper:.2f}]")
        lines.append("")

        # Drawdown
        lines.append("  ─── Distribution du Drawdown ───")
        lines.append(f"  DD moyen    : {results.mean_max_dd:.2f}%")
        lines.append(f"  DD std      : {results.std_max_dd:.2f}%")
        lines.append(f"  IC 95%      : [{results.ci_95_dd_lower:.2f}%, {results.ci_95_dd_upper:.2f}%]")
        lines.append(f"  Pire DD     : {results.worst_dd:.2f}%")
        lines.append(f"  P(DD > 10%) : {results.prob_dd_gt_10:.1%}")
        lines.append(f"  P(DD > 5%)  : {results.prob_dd_gt_5:.1%}")
        lines.append("")

        # Skewness / Kurtosis
        lines.append("  ─── Forme de la distribution ───")
        skew_desc = (
            "asymétrique positive (bon)"
            if results.skewness > 0.5
            else "asymétrique négative (risque)"
            if results.skewness < -0.5
            else "symétrique"
        )
        kurt_desc = (
            "queues épaisses (risque extrême)"
            if results.kurtosis > 1
            else "queues fines (risque réduit)"
            if results.kurtosis < -0.5
            else "normale"
        )
        lines.append(f"  Skewness    : {results.skewness:.3f} ({skew_desc})")
        lines.append(f"  Kurtosis    : {results.kurtosis:.3f} ({kurt_desc})")

        lines.append("═" * 70)

        return "\n".join(lines)
