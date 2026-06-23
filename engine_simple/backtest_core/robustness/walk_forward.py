"""
WalkForwardAnalyzer — Walk-Forward Analysis avec Purging & Embargo.

Évalue la robustesse d'une stratégie en simulant son passage à travers
plusieurs fenêtres temporelles (train/test) pour détecter l'overfitting.

Purging : supprime les données de train adjacentes aux données de test
pour éviter la contamination par data leakage (look-ahead bias).
Embargo : gap obligatoire entre la fin du train et le début du test.

Algorithme :
  1. Découper les données en N fenêtres glibantes (ex: 5 folds, 60/40)
  2. Pour chaque fold :
     a. Purger : supprimer les purging_bars dernières barres du train
     b. Embargo : skip les embargo_bars premières barres du test
     c. Entraîner la stratégie sur les données train purgées
     d. Tester sur les données test avec embargo
  3. Comparer les métriques train vs test
  4. Calculer le Walk-Forward Efficiency (WFE)
  5. Détecter l'overfitting (chute de performance train→test)

Usage :
    wf = WalkForwardAnalyzer(n_splits=5, train_pct=0.6,
                              purging_bars=50, embargo_bars=20)
    results = wf.run(engine, strategy, data, symbol, timeframe)
    print(wf.summary(results))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

from engine_simple.backtest_core.engine import BacktestEngine, BacktestConfig

logger = logging.getLogger("backtest_core.robustness.walk_forward")


@dataclass
class WFFoldResult:
    """Résultat d'un fold de walk-forward."""

    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_metrics: dict = field(default_factory=dict)
    test_metrics: dict = field(default_factory=dict)
    train_trades: int = 0
    test_trades: int = 0
    train_win_rate: float = 0.0
    test_win_rate: float = 0.0
    train_profit: float = 0.0
    test_profit: float = 0.0
    train_pf: float = 0.0
    test_pf: float = 0.0
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    train_max_dd: float = 0.0
    test_max_dd: float = 0.0


@dataclass
class WFResult:
    """Résultat complet du walk-forward."""

    n_splits: int
    folds: list[WFFoldResult] = field(default_factory=list)
    avg_train_win_rate: float = 0.0
    avg_test_win_rate: float = 0.0
    avg_train_profit: float = 0.0
    avg_test_profit: float = 0.0
    avg_train_pf: float = 0.0
    avg_test_pf: float = 0.0
    avg_train_sharpe: float = 0.0
    avg_test_sharpe: float = 0.0
    avg_train_max_dd: float = 0.0
    avg_test_max_dd: float = 0.0
    wfe_win_rate: float = 0.0  # Walk-Forward Efficiency (WR)
    wfe_profit: float = 0.0  # WFE (PnL)
    wfe_sharpe: float = 0.0  # WFE (Sharpe)
    wfe_overall: float = 0.0  # WFE moyen pondéré
    is_robust: bool = False  # True si WFE > 0.70
    overfitting_detected: bool = False
    n_degenerate_folds: int = 0  # Folds où le test est pire que le train


class WalkForwardAnalyzer:
    """
    Analyse Walk-Forward avec purging & embargo.

    Attributes:
        n_splits: Nombre de fenêtres (défaut: 5)
        train_pct: Proportion des données pour l'entraînement (défaut: 0.60)
        purging_bars: Nombre de barres à purger à la fin du train (défaut: 50)
        embargo_bars: Nombre de barres d'embargo au début du test (défaut: 20)
        verbose: Afficher les logs détaillés
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_pct: float = 0.60,
        purging_bars: int = 50,
        embargo_bars: int = 20,
        verbose: bool = False,
    ):
        if n_splits < 2:
            raise ValueError("n_splits doit être >= 2")
        if not 0.3 <= train_pct <= 0.8:
            raise ValueError("train_pct doit être entre 0.3 et 0.8")
        if purging_bars < 0:
            raise ValueError("purging_bars doit être >= 0")
        if embargo_bars < 0:
            raise ValueError("embargo_bars doit être >= 0")

        self.n_splits = n_splits
        self.train_pct = train_pct
        self.purging_bars = purging_bars
        self.embargo_bars = embargo_bars
        self.verbose = verbose

    # ─── Run ──────────────────────────────────────────────────────────────

    def run(
        self,
        engine: BacktestEngine,
        strategy,
        data,
        symbol: str,
        timeframe: str = "H1",
    ) -> WFResult:
        """
        Exécute le walk-forward complet.

        Args:
            engine: BacktestEngine configuré
            strategy: Instance de stratégie
            data: pd.DataFrame complet
            symbol: Symbole à tester
            timeframe: Timeframe

        Returns:
            WFResult avec tous les folds et métriques agrégées
        """
        n = len(data)
        if n < 500:
            logger.warning(f"Walk-Forward: seulement {n} barres, résultats可能 instables")

        # Calculer les indices de séparation
        fold_borders = self._compute_fold_borders(n)

        results = WFResult(n_splits=self.n_splits)
        n_degenerate = 0

        for fold_idx in range(self.n_splits):
            train_end = fold_borders[fold_idx]
            test_start = fold_borders[fold_idx]
            test_end = fold_borders[fold_idx + 1] if fold_idx + 1 < len(fold_borders) else n

            # Train: 0 → train_end (avec purging)
            train_data = data.iloc[:train_end].copy()

            # Purging : retirer les dernières barres du train
            if self.purging_bars > 0 and len(train_data) > self.purging_bars * 2:
                train_data = train_data.iloc[: -self.purging_bars].copy()

            # Test : test_start + embargo → test_end
            test_start_effective = min(test_start + self.embargo_bars, test_end)
            if test_start_effective >= test_end:
                logger.warning(f"  Fold {fold_idx + 1}: embargo trop grand, skip")
                n_degenerate += 1
                continue

            test_data = data.iloc[test_start_effective:test_end].copy()

            if len(train_data) < 100 or len(test_data) < 50:
                logger.warning(
                    f"  Fold {fold_idx + 1}: données insuffisantes "
                    f"(train={len(train_data)}, test={len(test_data)}), skip"
                )
                n_degenerate += 1
                continue

            # Exécuter le backtest sur le train
            train_result = engine.run(
                symbol=symbol,
                strategy=strategy,
                data=train_data,
                timeframe=timeframe,
            )

            # Exécuter le backtest sur le test
            test_result = engine.run(
                symbol=symbol,
                strategy=strategy,
                data=test_data,
                timeframe=timeframe,
            )

            fold = WFFoldResult(
                fold=fold_idx + 1,
                train_start=data["timestamp"].iloc[0] if "timestamp" in data.columns else None,
                train_end=data["timestamp"].iloc[train_end - 1]
                if "timestamp" in data.columns and train_end > 0
                else None,
                test_start=data["timestamp"].iloc[test_start_effective]
                if "timestamp" in data.columns and test_start_effective < n
                else None,
                test_end=data["timestamp"].iloc[test_end - 1] if "timestamp" in data.columns and test_end > 0 else None,
                train_metrics=train_result.metrics,
                test_metrics=test_result.metrics,
                train_trades=train_result.total_trades,
                test_trades=test_result.total_trades,
                train_win_rate=train_result.win_rate,
                test_win_rate=test_result.win_rate,
                train_profit=train_result.net_profit,
                test_profit=test_result.net_profit,
                train_pf=train_result.metrics.get("profit_factor", 0),
                test_pf=test_result.metrics.get("profit_factor", 0),
                train_sharpe=train_result.metrics.get("sharpe_ratio", 0),
                test_sharpe=test_result.metrics.get("sharpe_ratio", 0),
                train_max_dd=train_result.metrics.get("max_dd_pct", 0),
                test_max_dd=test_result.metrics.get("max_dd_pct", 0),
            )

            results.folds.append(fold)

            if self.verbose:
                logger.info(
                    f"  Fold {fold_idx + 1}: train WR={fold.train_win_rate:.1f}% "
                    f"→ test WR={fold.test_win_rate:.1f}% | "
                    f"train PnL=${fold.train_profit:.0f} → test PnL=${fold.test_profit:.0f}"
                )

            # Détecter les folds dégénérés
            if test_result.total_trades < 5 or test_result.win_rate < 30:
                n_degenerate += 1

        results.n_degenerate_folds = n_degenerate

        # Calculer les moyennes
        self._compute_averages(results)
        self._compute_wfe(results)

        # Détection d'overfitting
        results.overfitting_detected = results.wfe_overall < 0.50 or results.n_degenerate_folds > self.n_splits // 2

        if self.verbose:
            logger.info(
                f"Walk-Forward terminé: WFE={results.wfe_overall:.3f}, "
                f"{'ROBUSTE' if results.is_robust else 'OVERFITTING'} "
                f"({results.n_degenerate_folds}/{self.n_splits} folds dégénérés)"
            )

        return results

    # ─── Calcul des frontières ───────────────────────────────────────────

    def _compute_fold_borders(self, n: int) -> list[int]:
        """
        Calcule les indices de séparation pour N folds.

        Stratégie : fenêtre train glissante, test fixe vers l'avant.
        Pour chaque fold, le train commence à fold_idx * step et
        le test est la portion suivante.
        """
        test_len = int(n * (1 - self.train_pct))
        train_len = n - test_len

        if self.n_splits == 1:
            return [train_len]

        step = max(train_len // self.n_splits, 50)

        borders = []
        for i in range(1, self.n_splits + 1):
            border = min(i * step, n - test_len)
            borders.append(border)

        # Ajuster le dernier fold pour couvrir la fin
        if borders[-1] < n - test_len:
            borders[-1] = n - test_len

        return borders

    # ─── Agrégation ──────────────────────────────────────────────────────

    def _compute_averages(self, results: WFResult) -> None:
        """Calcule les moyennes sur tous les folds."""
        folds = [f for f in results.folds if f.test_trades >= 5]
        if not folds:
            return

        results.avg_train_win_rate = float(np.mean([f.train_win_rate for f in folds]))
        results.avg_test_win_rate = float(np.mean([f.test_win_rate for f in folds]))
        results.avg_train_profit = float(np.mean([f.train_profit for f in folds]))
        results.avg_test_profit = float(np.mean([f.test_profit for f in folds]))
        results.avg_train_pf = float(np.mean([f.train_pf for f in folds]))
        results.avg_test_pf = float(np.mean([f.test_pf for f in folds]))
        results.avg_train_sharpe = float(np.mean([f.train_sharpe for f in folds]))
        results.avg_test_sharpe = float(np.mean([f.test_sharpe for f in folds]))
        results.avg_train_max_dd = float(np.mean([f.train_max_dd for f in folds]))
        results.avg_test_max_dd = float(np.mean([f.test_max_dd for f in folds]))

    def _compute_wfe(self, results: WFResult) -> None:
        """
        Calcule le Walk-Forward Efficiency (WFE).

        WFE = (moyenne test) / (moyenne train), plafonné à 1.0.
        Plus le WFE est proche de 1.0, plus la stratégie est robuste.
        WFE < 0.50 = overfitting probable.
        """
        folds = [f for f in results.folds if f.test_trades >= 5]
        if not folds:
            return

        # WFE Win Rate
        avg_train = np.mean([f.train_win_rate for f in folds])
        avg_test = np.mean([f.test_win_rate for f in folds])
        results.wfe_win_rate = min(avg_test / max(avg_train, 1), 1.0)

        # WFE Profit Factor
        avg_train_pf = np.mean([f.train_pf for f in folds])
        avg_test_pf = np.mean([f.test_pf for f in folds])
        results.wfe_profit = min(avg_test_pf / max(avg_train_pf, 0.01), 1.0)

        # WFE Sharpe
        avg_train_s = np.mean([f.train_sharpe for f in folds])
        avg_test_s = np.mean([f.test_sharpe for f in folds])
        results.wfe_sharpe = min(avg_test_s / max(avg_train_s, 0.001), 1.0) if avg_train_s > 0 else 0.5

        # WFE global pondéré
        results.wfe_overall = results.wfe_win_rate * 0.30 + results.wfe_profit * 0.35 + results.wfe_sharpe * 0.35

        results.is_robust = results.wfe_overall >= 0.70

    # ─── Résumé ──────────────────────────────────────────────────────────

    @staticmethod
    def summary(results: WFResult) -> str:
        """Génère un rapport textuel du walk-forward."""
        lines = []
        lines.append("═" * 70)
        lines.append("  WALK-FORWARD ANALYSIS  ")
        lines.append("═" * 70)
        lines.append(f"  Folds: {results.n_splits} ({results.n_degenerate_folds} dégénérés)")
        lines.append("")

        # En-tête du tableau
        lines.append(
            f"  {'Fold':<6} {'Train WR':>9} {'Test WR':>9} {'Train PnL':>11} "
            f"{'Test PnL':>11} {'Train PF':>9} {'Test PF':>9} {'Train SR':>9} {'Test SR':>9}"
        )
        lines.append("  " + "-" * 88)

        for fold in results.folds:
            lines.append(
                f"  {fold.fold:<6} {fold.train_win_rate:>8.1f}% "
                f"{fold.test_win_rate:>8.1f}% "
                f"${fold.train_profit:>+9.2f} ${fold.test_profit:>+9.2f} "
                f"{fold.train_pf:>8.2f} {fold.test_pf:>8.2f} "
                f"{fold.train_sharpe:>8.3f} {fold.test_sharpe:>8.3f}"
            )

        lines.append("")
        lines.append("  ─── Moyennes ───")
        lines.append(
            f"  Train moyen: WR={results.avg_train_win_rate:.1f}%, "
            f"PnL=${results.avg_train_profit:.2f}, "
            f"PF={results.avg_train_pf:.2f}, "
            f"Sharpe={results.avg_train_sharpe:.3f}"
        )
        lines.append(
            f"  Test  moyen: WR={results.avg_test_win_rate:.1f}%, "
            f"PnL=${results.avg_test_profit:.2f}, "
            f"PF={results.avg_test_pf:.2f}, "
            f"Sharpe={results.avg_test_sharpe:.3f}"
        )
        lines.append("")
        lines.append("  ─── Walk-Forward Efficiency ───")
        lines.append(f"  WFE Win Rate : {results.wfe_win_rate:.3f}")
        lines.append(f"  WFE Profit F.: {results.wfe_profit:.3f}")
        lines.append(f"  WFE Sharpe   : {results.wfe_sharpe:.3f}")
        lines.append(f"  WFE Overall  : {results.wfe_overall:.3f}")
        lines.append("")

        if results.is_robust:
            lines.append("  ✅ STRATÉGIE ROBUSTE (WFE >= 0.70)")
        elif results.wfe_overall >= 0.50:
            lines.append("  ⚠️  ROBUSTESSE MODÉRÉE (0.50 <= WFE < 0.70)")
        else:
            lines.append("  🔴 OVERFITTING DÉTECTÉ (WFE < 0.50)")

        lines.append("═" * 70)

        return "\n".join(lines)
