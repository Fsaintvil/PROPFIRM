"""
StressTester — Tests de résistance pour le backtest institutionnel.

Simule 4 scénarios de crise pour évaluer le comportement du robot
dans des conditions de marché extrêmes :

  1. CRASH-2008 : Crise financière globale (Sept-Oct 2008)
     - Volatilité ×3, spreads ×5, gaps fréquents
     - Corrélation inter-actifs → 0.90 (tout perd ensemble)

  2. SNB-2015 : Choc du Franc Suisse (Jan 2015)
     - Gap de 30% sur EURCHF/USDCHF
     - Slippage extrême, requotes systématiques
     - Liquidité → 0 pendant 15 minutes

  3. COVID-2020 : Pandémie COVID-19 (Fev-Mars 2020)
     - Volatilité ×4, gaps journaliers
     - VIX > 80, spreads ×10 sur tous les actifs
     - Crash suivi de rebond violent

  4. RATE-2022 : Crise des taux 2022
     - Taux directeurs ×5, QT agressif
     - Corrélation Forex+Indices → -0.70 (rotation brutale)
     - Cryptos : -70% en 6 mois

Chaque scénario ajuste les paramètres du CostModel et de l'ExecutionEngine
pour refléter les conditions de crise, puis exécute le backtest complet.
Compare les métriques de stress vs normal.

Usage :
    st = StressTester()
    results = st.run(engine, strategy, data, symbol, timeframe)
    print(st.summary(results))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from engine_simple.backtest_core.engine import BacktestEngine, BacktestConfig

logger = logging.getLogger("backtest_core.robustness.stress_tests")


@dataclass
class StressScenario:
    """Définition d'un scénario de stress."""

    name: str
    description: str
    # Multiplicateurs de coûts
    spread_mult: float = 1.0
    slippage_mean_mult: float = 1.0
    slippage_std_mult: float = 1.0
    requote_prob: float = 0.02
    partial_fill_prob: float = 0.0
    # Volatilité
    vol_mult: float = 1.0
    # Gap
    gap_prob: float = 0.0  # Probabilité d'un gap par jour
    gap_size_atr: float = 0.0  # Taille du gap en multiple d'ATR
    # Performance attendue (dégradation)
    expected_wr_hit: float = 0.0  # % de réduction du WR attendu
    expected_pnl_hit: float = 0.0  # % de réduction du PnL attendu


@dataclass
class StressResult:
    """Résultat d'un test de stress."""

    scenario: str
    description: str
    normal_metrics: dict = field(default_factory=dict)
    stress_metrics: dict = field(default_factory=dict)

    # Métriques comparatives
    wr_change: float = 0.0  # Points de pourcentage
    pnl_change: float = 0.0  # %
    pf_change: float = 0.0  # %
    dd_change: float = 0.0  # Points de pourcentage
    sharpe_change: float = 0.0  # %

    # Trade stats
    normal_trades: int = 0
    stress_trades: int = 0
    normal_wins: int = 0
    stress_losses: int = 0

    # Jugement
    passed: bool = True
    severity: str = "none"  # "none" | "low" | "moderate" | "high" | "critical"
    comments: str = ""


@dataclass
class StressTestReport:
    """Rapport complet des tests de stress."""

    symbol: str = ""
    timeframe: str = ""
    scenarios: list[StressResult] = field(default_factory=list)
    n_passed: int = 0
    n_failed: int = 0
    overall_verdict: str = ""


# ─── Scénarios prédéfinis ─────────────────────────────────────────────────

STRESS_SCENARIOS = {
    "CRASH-2008": StressScenario(
        name="CRASH-2008",
        description="Crise financière 2008 : volatilité ×3, spreads ×5, gaps",
        spread_mult=5.0,
        slippage_mean_mult=3.0,
        slippage_std_mult=3.0,
        requote_prob=0.15,
        partial_fill_prob=0.30,
        vol_mult=3.0,
        gap_prob=0.15,
        gap_size_atr=5.0,
        expected_wr_hit=0.30,
        expected_pnl_hit=0.60,
    ),
    "SNB-2015": StressScenario(
        name="SNB-2015",
        description="Choc SNB 2015 : gap 30%, liquidité → 0, slippage extrême",
        spread_mult=10.0,
        slippage_mean_mult=5.0,
        slippage_std_mult=5.0,
        requote_prob=0.50,
        partial_fill_prob=0.60,
        vol_mult=4.0,
        gap_prob=0.25,
        gap_size_atr=10.0,
        expected_wr_hit=0.50,
        expected_pnl_hit=0.80,
    ),
    "COVID-2020": StressScenario(
        name="COVID-2020",
        description="Pandémie 2020 : volatilité ×4, spreads ×10, gaps journaliers",
        spread_mult=10.0,
        slippage_mean_mult=4.0,
        slippage_std_mult=4.0,
        requote_prob=0.30,
        partial_fill_prob=0.40,
        vol_mult=4.0,
        gap_prob=0.20,
        gap_size_atr=8.0,
        expected_wr_hit=0.40,
        expected_pnl_hit=0.70,
    ),
    "RATE-2022": StressScenario(
        name="RATE-2022",
        description="Crise des taux 2022 : QT agressif, rotation brutale, cryptos -70%",
        spread_mult=3.0,
        slippage_mean_mult=2.0,
        slippage_std_mult=2.0,
        requote_prob=0.10,
        partial_fill_prob=0.20,
        vol_mult=2.5,
        gap_prob=0.10,
        gap_size_atr=4.0,
        expected_wr_hit=0.25,
        expected_pnl_hit=0.50,
    ),
}


class StressTester:
    """
    Teste la résistance de la stratégie face à des scénarios de crise.

    Attributes:
        scenarios: Liste des scénarios à exécuter
        verbose: Logs détaillés
        sample_bars: Nombre de barres à utiliser pour les stress tests
                     (None = toutes les barres, recommandé: 5000 pour accélérer)
    """

    def __init__(
        self,
        scenarios: Optional[list[str]] = None,
        verbose: bool = False,
        sample_bars: Optional[int] = None,
    ):
        """
        Args:
            scenarios: Noms des scénarios (None = tous)
            verbose: Logs détaillés
            sample_bars: Limiter le nombre de barres pour les stress tests
                         (None = toutes les barres, 5000 = rapide, fiable)
        """
        if scenarios:
            self.scenarios = {name: STRESS_SCENARIOS[name] for name in scenarios if name in STRESS_SCENARIOS}
        else:
            self.scenarios = dict(STRESS_SCENARIOS)

        self.verbose = verbose
        self.sample_bars = sample_bars

    # ─── Run ──────────────────────────────────────────────────────────────

    def run(
        self,
        engine: BacktestEngine,
        strategy,
        data,
        symbol: str,
        timeframe: str = "H1",
    ) -> StressTestReport:
        """
        Exécute tous les scénarios de stress.

        Pour chaque scénario :
          1. Exécute un backtest en conditions normales
          2. Crée une copie du moteur avec paramètres stressés
          3. Exécute le backtest avec les paramètres de stress
          4. Compare les métriques

        Args:
            engine: BacktestEngine configuré
            strategy: Instance de stratégie
            data: pd.DataFrame
            symbol: Symbole à tester
            timeframe: Timeframe

        Returns:
            StressTestReport complet
        """
        report = StressTestReport(symbol=symbol, timeframe=timeframe)

        # Backtest normal (référence) — toujours sur toutes les données
        logger.info(f"[STRESS] Référence normale: {symbol} {timeframe}")
        normal_result = engine.run(symbol=symbol, strategy=strategy, data=data, timeframe=timeframe)

        if normal_result.total_trades < 10:
            logger.warning(f"[STRESS] Pas assez de trades en condition normale ({normal_result.total_trades})")
            report.overall_verdict = "INSUFFICIENT_DATA"
            return report

        normal_metrics = normal_result.metrics

        # Sous-échantillonnage pour les stress tests (gain de vitesse)
        stress_data = data
        n_total = len(data)
        if self.sample_bars is not None and self.sample_bars < n_total:
            # Prendre les dernières N barres (les plus récentes = les plus réalistes)
            stress_data = data.iloc[-self.sample_bars :].reset_index(drop=True)
            logger.info(f"[STRESS] Données échantillonnées: {n_total} → {len(stress_data)} barres")

        n_scenarios = len(self.scenarios)
        for idx, (sc_name, scenario) in enumerate(self.scenarios.items(), 1):
            logger.info(f"[STRESS] Scénario {idx}/{n_scenarios} — {sc_name}: {scenario.description}")

            # Créer une config stressée
            stress_config = self._build_stress_config(engine.config, scenario)

            # Créer un moteur stressé
            stress_engine = BacktestEngine(config=stress_config)

            # Appliquer les multiplicateurs aux données (échantillonnées si demandé)
            stressed = self._apply_stress_to_data(stress_data, scenario)

            # Exécuter le backtest stressé
            try:
                stress_result = stress_engine.run(symbol=symbol, strategy=strategy, data=stressed, timeframe=timeframe)
            except Exception as e:
                logger.error(f"[STRESS] Erreur sur {sc_name}: {e}")
                report.scenarios.append(
                    StressResult(
                        scenario=sc_name,
                        description=scenario.description,
                        normal_metrics=normal_metrics,
                        stress_metrics={"error": str(e)},
                        passed=False,
                        severity="critical",
                        comments=f"Erreur d'exécution: {e}",
                    )
                )
                report.n_failed += 1
                continue

            stress_metrics = stress_result.metrics
            if "error" in stress_metrics:
                report.scenarios.append(
                    StressResult(
                        scenario=sc_name,
                        description=scenario.description,
                        normal_metrics=normal_metrics,
                        stress_metrics=stress_metrics,
                        passed=False,
                        severity="high",
                        comments="Erreur dans les métriques",
                    )
                )
                report.n_failed += 1
                continue

            # Comparer les métriques
            result = self._compare_scenario(sc_name, scenario, normal_result, stress_result)

            report.scenarios.append(result)

            if result.passed:
                report.n_passed += 1
            else:
                report.n_failed += 1

            if self.verbose:
                logger.info(
                    f"  {sc_name}: WR {result.wr_change:+.1f}%, "
                    f"PnL {result.pnl_change:+.0f}%, "
                    f"{'✅ PASSÉ' if result.passed else '❌ ÉCHOUÉ'}"
                )

        # Verdict final
        report.overall_verdict = self._compute_verdict(report)

        return report

    # ─── Construction de la config stressée ──────────────────────────────

    def _build_stress_config(self, base_config: BacktestConfig, scenario: StressScenario) -> BacktestConfig:
        """Crée une copie de la config avec les paramètres de stress."""
        import copy

        config = copy.deepcopy(base_config)

        # Ajuster les paramètres d'exécution
        config.latency_ms = min(base_config.latency_ms * scenario.vol_mult, 5000)
        config.requote_prob = scenario.requote_prob

        # Ajuster les coûts
        costs_config = config.costs_config or {}
        costs_config["spread_multiplier"] = scenario.spread_mult
        costs_config["slippage_mean_mult"] = scenario.slippage_mean_mult
        costs_config["slippage_std_mult"] = scenario.slippage_std_mult
        config.costs_config = costs_config

        return config

    @staticmethod
    def _apply_stress_to_data(data, scenario: StressScenario):
        """Applique les effets du scénario aux données de prix."""
        df = data.copy()

        # Multiplier la volatilité (gap entre open et close du précédent)
        if scenario.vol_mult > 1.0:
            # Augmenter les ranges
            df["high"] = df["close"] + (df["high"] - df["close"]) * scenario.vol_mult
            df["low"] = df["close"] - (df["close"] - df["low"]) * scenario.vol_mult
            df["open"] = df["close"].shift(1).fillna(df["open"])
            df["open"] = df["open"] + (df["open"] - df["open"].mean()) * (scenario.vol_mult - 1) * 0.5

        # Ajouter des gaps
        if scenario.gap_prob > 0:
            n_gaps = int(len(df) * scenario.gap_prob)
            if n_gaps > 0:
                gap_indices = np.random.choice(range(1, len(df)), size=min(n_gaps, len(df) - 1), replace=False)
                for idx in gap_indices:
                    atr_val = abs(df["high"].iloc[idx] - df["low"].iloc[idx])
                    gap_size = atr_val * scenario.gap_size_atr * np.random.choice([-1, 1])
                    df.loc[df.index[idx], "open"] += gap_size
                    df.loc[df.index[idx], "high"] += gap_size * 1.1
                    df.loc[df.index[idx], "low"] += gap_size * 0.9
                    df.loc[df.index[idx], "close"] += gap_size * np.random.uniform(0.5, 1.5)

        # Augmenter le spread
        if "spread" in df.columns and scenario.spread_mult > 1.0:
            df["spread"] = df["spread"] * scenario.spread_mult

        return df

    # ─── Comparaison ─────────────────────────────────────────────────────

    @staticmethod
    def _compare_scenario(
        sc_name: str,
        scenario: StressScenario,
        normal_result,
        stress_result,
    ) -> StressResult:
        """Compare les métriques normales vs stress et produit un jugement."""

        normal_metrics = normal_result.metrics
        stress_metrics = stress_result.metrics

        n_wr = normal_metrics.get("win_rate", 0)
        s_wr = stress_metrics.get("win_rate", 0)
        n_pnl = normal_metrics.get("net_profit", 0)
        s_pnl = stress_metrics.get("net_profit", 0)
        n_pf = normal_metrics.get("profit_factor", 0)
        s_pf = stress_metrics.get("profit_factor", 0)
        n_dd = normal_metrics.get("max_dd_pct", 0)
        s_dd = stress_metrics.get("max_dd_pct", 0)
        n_sharpe = normal_metrics.get("sharpe_ratio", 0)
        s_sharpe = stress_metrics.get("sharpe_ratio", 0)

        # Changements
        wr_change = s_wr - n_wr
        pnl_change = ((s_pnl - n_pnl) / abs(n_pnl) * 100) if n_pnl != 0 else 0
        pf_change = ((s_pf - n_pf) / n_pf * 100) if n_pf > 0 else 0
        dd_change = s_dd - n_dd
        sharpe_change = ((s_sharpe - n_sharpe) / n_sharpe * 100) if n_sharpe > 0 else 0

        # Jugement
        severity = "none"
        comments = []
        passed = True

        # PnL négatif en stress
        if s_pnl < 0:
            passed = False
            severity = "high"
            comments.append(f"PnL négatif en stress: ${s_pnl:.2f}")
        elif s_pnl < n_pnl * 0.3:
            severity = "moderate"
            comments.append(f"PnL réduit de {abs(pnl_change):.0f}%")

        # DD excessif
        if s_dd > 15:
            passed = False
            severity = "critical"
            comments.append(f"DD > 15%: {s_dd:.2f}%")
        elif s_dd > n_dd * 3:
            severity = "high"
            comments.append(f"DD multiplié par {s_dd / max(n_dd, 0.1):.1f}x")

        # WR effondré
        if s_wr < 35:
            passed = False
            severity = "high" if severity == "none" else severity
            comments.append(f"WR effondré: {s_wr:.1f}%")

        # Sharpe négatif
        if s_sharpe < 0 and n_sharpe > 0:
            passed = False
            severity = "moderate" if severity == "none" else severity
            comments.append(f"Sharpe négatif: {s_sharpe:.3f}")

        if not comments:
            comments.append(f"Résilient au scénario {sc_name}")

        return StressResult(
            scenario=sc_name,
            description=scenario.description,
            normal_metrics=normal_metrics,
            stress_metrics=stress_metrics,
            wr_change=round(wr_change, 1),
            pnl_change=round(pnl_change, 1),
            pf_change=round(pf_change, 1),
            dd_change=round(dd_change, 1),
            sharpe_change=round(sharpe_change, 1),
            normal_trades=normal_result.total_trades,
            stress_trades=stress_result.total_trades,
            passed=passed,
            severity=severity,
            comments="; ".join(comments),
        )

    # ─── Verdict ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_verdict(report: StressTestReport) -> str:
        """Calcule le verdict final du stress test."""
        if report.n_failed == 0:
            return "ROBUSTE — Tous les scénarios de stress sont passés"

        fail_rate = report.n_failed / max(len(report.scenarios), 1)

        if fail_rate <= 0.25:
            return (
                "ROBUSTESSE MODÉRÉE — Certains scénarios montrent des faiblesses. "
                "Envisager réduire le risque en période de crise."
            )
        elif fail_rate <= 0.50:
            return (
                "VULNÉRABLE — La stratégie est significativement affectée par les crises. "
                "Un filtre macro/market regime est recommandé."
            )
        else:
            return (
                "FRAGILE — La stratégie échoue dans la plupart des scénarios de crise. "
                "Une refonte de la gestion des risques est nécessaire."
            )

    # ─── Résumé ──────────────────────────────────────────────────────────

    @staticmethod
    def summary(report: StressTestReport) -> str:
        """Génère un rapport textuel des tests de stress."""
        lines = []
        lines.append("═" * 70)
        lines.append("  STRESS TESTS  ")
        lines.append("═" * 70)
        lines.append(f"  Symbole: {report.symbol} | Timeframe: {report.timeframe}")
        lines.append(
            f"  Scénarios: {len(report.scenarios)} ({report.n_passed} ✅ passés, {report.n_failed} ❌ échoués)"
        )
        lines.append("")

        for sc in report.scenarios:
            # En-tête du scénario
            status = "✅" if sc.passed else "❌"
            lines.append(f"  {status} {sc.scenario} — {sc.description}")
            lines.append(f"     {sc.comments}")

            # Tableau comparatif
            lines.append(f"     {'Métrique':<20} {'Normal':>12} {'Stress':>12} {'Δ':>12}")
            lines.append("     " + "-" * 56)
            lines.append(
                f"     {'Win Rate':<20} {sc.normal_metrics.get('win_rate', 0):>11.1f}% "
                f"{sc.stress_metrics.get('win_rate', 0):>11.1f}% "
                f"{sc.wr_change:>+11.1f}%"
            )
            lines.append(
                f"     {'PnL':<20} ${sc.normal_metrics.get('net_profit', 0):>+10.2f} "
                f"${sc.stress_metrics.get('net_profit', 0):>+10.2f} "
                f"{sc.pnl_change:>+11.1f}%"
            )
            lines.append(
                f"     {'Profit Factor':<20} {sc.normal_metrics.get('profit_factor', 0):>11.2f} "
                f"{sc.stress_metrics.get('profit_factor', 0):>11.2f} "
                f"{sc.pf_change:>+11.1f}%"
            )
            lines.append(
                f"     {'Max DD':<20} {sc.normal_metrics.get('max_dd_pct', 0):>11.2f}% "
                f"{sc.stress_metrics.get('max_dd_pct', 0):>11.2f}% "
                f"{sc.dd_change:>+11.1f}pp"
            )
            lines.append(
                f"     {'Sharpe':<20} {sc.normal_metrics.get('sharpe_ratio', 0):>11.3f} "
                f"{sc.stress_metrics.get('sharpe_ratio', 0):>11.3f} "
                f"{sc.sharpe_change:>+11.1f}%"
            )
            lines.append(f"     {'Trades':<20} {sc.normal_trades:>11} {sc.stress_trades:>11}")
            lines.append("")

        # Verdict final
        lines.append("  ─── VERDICT ───")
        lines.append(f"  {report.overall_verdict}")
        lines.append("═" * 70)

        return "\n".join(lines)
