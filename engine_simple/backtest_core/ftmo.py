"""
FTMOChallengeSimulator — Simulation et validation des règles FTMO.

Simule un challenge FTMO (ou toute prop firm similaire) en testant
la stratégie contre les règles suivantes :

Règles FTMO 1-Step (200K) :
  - Profit Target : +5% ($10,000) en 30 jours de trading
  - Max Daily Loss : 2% ($4,000) basé sur le capital de fin de journée
  - Max Loss (Drawdown) : 10% ($20,000) basé sur le capital initial
  - Consistency Rule : aucun jour ne doit dépasser 30% du profit total
  - Min Trading Days : 10 jours minimum
  - Leverage : 1:30 (Forex), 1:20 (Indices), 1:5 (Crypto)

Sorties :
  - Verdict : PASS / FAIL
  - Raison de l'échec si FAIL
  - Jours de trading, profit progress, DD max, daily loss max
  - Probabilité de succès sur N simulations
  - Analyse de la marge de sécurité

Usage :
    ftmo = FTMOChallengeSimulator(account_size=200_000, profit_target_pct=0.05)
    verdict = ftmo.evaluate(trades, equity_curve, dates)
    print(ftmo.summary(verdict))
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger("backtest_core.ftmo")


@dataclass
class FTMOConfig:
    """Configuration d'un challenge FTMO."""

    account_size: float = 200_000.0
    profit_target_pct: float = 0.05  # 5%
    max_daily_loss_pct: float = 0.02  # 2%
    max_dd_pct: float = 0.10  # 10%
    consistency_pct: float = 0.30  # 30%
    min_trading_days: int = 10
    max_duration_days: int = 30
    leverage_forex: int = 30
    leverage_indices: int = 20
    leverage_crypto: int = 5


@dataclass
class FTMOVerdict:
    """Verdict complet d'un challenge FTMO."""

    config: FTMOConfig = field(default_factory=FTMOConfig)
    passed: bool = False
    fail_reason: str = ""

    # Métriques
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_dd_pct: float = 0.0
    max_daily_loss_pct: float = 0.0
    trading_days: int = 0
    best_day_pct: float = 0.0
    best_day_pnl: float = 0.0

    # Détail journalier
    daily_pnl: dict[str, float] = field(default_factory=dict)
    daily_loss_pct: dict[str, float] = field(default_factory=dict)

    # Analyse de risque
    best_day_consistency_pct: float = 0.0
    consistency_ok: bool = True
    days_to_target: int = 0
    margin_of_safety: float = 0.0  # Marge avant d'atteindre -10%

    # Stats de trading
    total_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Métadonnées portfolio
    metadata: dict = field(default_factory=dict)


class FTMOChallengeSimulator:
    """
    Simule un challenge FTMO et évalue la conformité.

    Attributes:
        config: FTMOConfig avec les paramètres du challenge
        verbose: Logs détaillés
    """

    def __init__(
        self,
        config: Optional[FTMOConfig | dict] = None,
        verbose: bool = False,
    ):
        if config is None:
            self.config = FTMOConfig()
        elif isinstance(config, dict):
            self.config = FTMOConfig(**config)
        else:
            self.config = config

        self.verbose = verbose

    # ─── Évaluation ──────────────────────────────────────────────────────

    def evaluate(
        self,
        trades: list,
        equity_curve: Optional[list[float]] = None,
        dates: Optional[list[datetime] | list] = None,
        balance: float = 200_000.0,
    ) -> FTMOVerdict:
        """
        Évalue un backtest contre les règles FTMO.

        Args:
            trades: Liste d'objets SimTrade avec profit_usd_cost et close_time
            equity_curve: Courbe d'equity (optionnelle)
            dates: Dates correspondant à equity_curve
            balance: Capital initial

        Returns:
            FTMOVerdict avec PASS/FAIL et détails
        """
        config = self.config
        verdict = FTMOVerdict(config=config)

        # Extraire les trades fermés
        closed = [t for t in trades if hasattr(t, "closed") and t.closed]
        if not closed:
            verdict.fail_reason = "Aucun trade fermé"
            return verdict

        verdict.total_trades = len(closed)
        verdict.n_wins = sum(1 for t in closed if getattr(t, "profit_usd_cost", 0) > 0)
        verdict.n_losses = verdict.total_trades - verdict.n_wins
        verdict.win_rate = (verdict.n_wins / verdict.total_trades * 100) if verdict.total_trades > 0 else 0

        # Calculer le PnL total
        total_pnl = sum(getattr(t, "profit_usd_cost", 0) for t in closed)
        verdict.total_pnl = round(total_pnl, 2)
        verdict.total_pnl_pct = round(total_pnl / config.account_size * 100, 2)

        gross_profit = sum(p for t in closed if (p := getattr(t, "profit_usd_cost", 0)) > 0)
        gross_loss = abs(sum(p for t in closed if (p := getattr(t, "profit_usd_cost", 0)) <= 0))
        verdict.profit_factor = round(gross_profit / max(gross_loss, 1), 2)

        # --- 1. Analyse journalière ---
        daily_pnl = defaultdict(float)
        daily_counts = defaultdict(int)

        for t in closed:
            close_time = getattr(t, "close_time", None)
            if close_time and hasattr(close_time, "date"):
                day = close_time.date().isoformat()
            else:
                day = "unknown"
            pnl = getattr(t, "profit_usd_cost", 0)
            daily_pnl[day] += pnl
            daily_counts[day] += 1

        verdict.trading_days = len(daily_pnl)
        verdict.daily_pnl = dict(daily_pnl)

        # --- 2. Règle : Daily Loss ---
        # Le daily loss est calculé sur le capital de début de journée
        # Capital de début de journée = initial + sum(PnL jours précédents)
        sorted_days = sorted(daily_pnl.keys())
        day_capital = config.account_size
        max_daily_loss_pct = 0.0
        worst_day = ""

        for day in sorted_days:
            day_pnl = daily_pnl[day]
            day_loss_pct = abs(day_pnl) / day_capital * 100 if day_pnl < 0 else 0

            verdict.daily_loss_pct[day] = round(day_loss_pct, 2)

            if day_loss_pct > max_daily_loss_pct:
                max_daily_loss_pct = day_loss_pct
                worst_day = day

            # Mettre à jour le capital pour le jour suivant
            day_capital += day_pnl

        verdict.max_daily_loss_pct = round(max_daily_loss_pct, 2)

        if max_daily_loss_pct > config.max_daily_loss_pct * 100:
            verdict.fail_reason = (
                f"Daily loss dépassé: {max_daily_loss_pct:.2f}% "
                f"(max {config.max_daily_loss_pct * 100:.0f}%) le {worst_day}"
            )

        # --- 3. Règle : Max Drawdown ---
        # Basé sur le capital initial (FTMO 1-Step)
        if equity_curve is not None and len(equity_curve) > 0:
            peak = equity_curve[0]
            max_dd = 0.0
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / config.account_size * 100  # Basé sur capital initial
                if dd > max_dd:
                    max_dd = dd
        else:
            # Calculer depuis les trades
            max_dd = 0.0
            running_balance = config.account_size
            peak_balance = running_balance
            for t in closed:
                pnl = getattr(t, "profit_usd_cost", 0)
                running_balance += pnl
                if running_balance > peak_balance:
                    peak_balance = running_balance
                dd = (peak_balance - running_balance) / config.account_size * 100
                if dd > max_dd:
                    max_dd = dd

        verdict.max_dd_pct = round(max_dd, 2)

        if max_dd > config.max_dd_pct * 100:
            if not verdict.fail_reason:
                verdict.fail_reason = f"Max DD dépassé: {max_dd:.2f}% (max {config.max_dd_pct * 100:.0f}%)"

        # Marge de sécurité
        verdict.margin_of_safety = round((config.max_dd_pct * 100 - max_dd) / config.max_dd_pct * 100, 1)

        # --- 4. Règle : Consistency ---
        if total_pnl > 0 and sorted_days:
            best_day = max(sorted_days, key=lambda d: daily_pnl[d])
            best_day_pnl = daily_pnl[best_day]
            verdict.best_day_pnl = round(best_day_pnl, 2)
            best_day_pct = best_day_pnl / total_pnl * 100
            verdict.best_day_pct = round(best_day_pnl, 2)
            verdict.best_day_consistency_pct = round(best_day_pct, 1)

            if best_day_pct > config.consistency_pct * 100:
                verdict.consistency_ok = False
                if not verdict.fail_reason:
                    verdict.fail_reason = (
                        f"Consistency rule violée: meilleur jour "
                        f"{best_day_pct:.1f}% du total "
                        f"(max {config.consistency_pct * 100:.0f}%)"
                    )
        else:
            verdict.consistency_ok = True
            verdict.best_day_pct = 0.0

        # --- 5. Règle : Min Trading Days ---
        if verdict.trading_days < config.min_trading_days and total_pnl > 0:
            if not verdict.fail_reason:
                verdict.fail_reason = (
                    f"Jours de trading insuffisants: {verdict.trading_days} (min {config.min_trading_days})"
                )

        # --- 6. Vérifier le profit target ---
        if total_pnl >= config.account_size * config.profit_target_pct:
            # C'est bon, mais il faut que les autres règles passent
            verdict.days_to_target = len(sorted_days) if sorted_days else 0
        else:
            if not verdict.fail_reason:
                verdict.fail_reason = (
                    f"Profit target non atteint: ${total_pnl:+.2f} "
                    f"({verdict.total_pnl_pct:.2f}%) "
                    f"sur ${config.account_size * config.profit_target_pct:.0f} requis"
                )

        # --- 7. Verdict final ---
        if not verdict.fail_reason and total_pnl >= config.account_size * config.profit_target_pct:
            verdict.passed = True
            verdict.fail_reason = "CHALLENGE PASSÉ ✅"

        if self.verbose:
            logger.info(
                f"FTMO: {'PASS ✅' if verdict.passed else 'FAIL ❌'} | "
                f"PnL ${verdict.total_pnl:+.2f} ({verdict.total_pnl_pct:.2f}%), "
                f"DD {verdict.max_dd_pct:.2f}%, "
                f"Daily Loss {verdict.max_daily_loss_pct:.2f}%, "
                f"{verdict.trading_days} jours"
            )

        return verdict

    # ─── Simulation de probabilité ───────────────────────────────────────

    def monte_carlo_pass_probability(
        self,
        trades: list,
        n_simulations: int = 1000,
        account_size: float = 200_000.0,
    ) -> dict:
        """
        Simule la probabilité de passer le challenge FTMO
        par Monte Carlo sur les trades.

        Args:
            trades: Liste de trades SimTrade
            n_simulations: Nombre de simulations
            account_size: Taille du compte

        Returns:
            Dict avec probabilité de PASS et statistiques
        """
        pnls = [getattr(t, "profit_usd_cost", 0.0) for t in trades if hasattr(t, "closed") and t.closed]
        pnls = [p for p in pnls if p != 0]

        if len(pnls) < 20:
            return {
                "pass_probability": 0.0,
                "n_simulations": 0,
                "error": "Trop peu de trades pour la simulation",
            }

        passes = 0
        max_dds = []
        total_pnls = []

        for _ in range(n_simulations):
            # Simuler une séquence de trades avec bootstrap
            sim_pnls = np.random.choice(pnls, size=len(pnls), replace=True)

            # Simuler le challenge
            balance = account_size
            peak = account_size
            daily_pnl = defaultdict(float)
            max_dd = 0.0
            max_daily_loss = 0.0
            days = 0

            for pnl in sim_pnls:
                balance += pnl
                if balance > peak:
                    peak = balance

                dd = (peak - balance) / account_size * 100
                if dd > max_dd:
                    max_dd = dd

                # Daily loss simulé (on suppose 1 trade = 1 jour pour simplifier)
                day_key = days // 5  # ~5 trades/semaine
                daily_pnl[day_key] += pnl
                daily_loss = abs(daily_pnl[day_key]) / account_size * 100 if daily_pnl[day_key] < 0 else 0
                if daily_loss > max_daily_loss:
                    max_daily_loss = daily_loss

                days += 1

            total_pnl = sum(sim_pnls)
            total_pnls.append(total_pnl)
            max_dds.append(max_dd)

            # Vérifier les règles
            if total_pnl < account_size * self.config.profit_target_pct:
                continue
            if max_dd > self.config.max_dd_pct * 100:
                continue
            if max_daily_loss > self.config.max_daily_loss_pct * 100:
                continue

            passes += 1

        pass_prob = passes / n_simulations

        return {
            "pass_probability": round(pass_prob, 4),
            "n_simulations": n_simulations,
            "mean_pnl": round(float(np.mean(total_pnls)), 2),
            "mean_max_dd": round(float(np.mean(max_dds)), 2),
            "p95_max_dd": round(float(np.percentile(max_dds, 95)), 2),
        }

    # ─── Résumé ──────────────────────────────────────────────────────────

    @staticmethod
    def summary(verdict: FTMOVerdict) -> str:
        """Génère un rapport textuel du challenge FTMO."""
        lines = []
        lines.append("═" * 70)
        lines.append("  CHALLENGE FTMO — VERDICT  ")
        lines.append("═" * 70)

        lines.append(f"\n  Compte: ${verdict.config.account_size:,}")
        lines.append(
            f"  Profit Target: +${verdict.config.account_size * verdict.config.profit_target_pct:,.0f} "
            f"({verdict.config.profit_target_pct * 100:.0f}%)"
        )
        lines.append(f"  Max Daily Loss: {verdict.config.max_daily_loss_pct * 100:.0f}%")
        lines.append(f"  Max DD: {verdict.config.max_dd_pct * 100:.0f}%")
        lines.append(f"  Consistency: max {verdict.config.consistency_pct * 100:.0f}%/jour")
        lines.append("")

        # Résultat
        if verdict.passed:
            lines.append("  ✅  CHALLENGE PASSÉ")
        else:
            lines.append(f"  ❌  ÉCHOUÉ — {verdict.fail_reason}")
        lines.append("")

        # Métriques clés
        lines.append("  ─── Résultats ───")
        lines.append(f"  PnL total      : ${verdict.total_pnl:>+10.2f} ({verdict.total_pnl_pct:+.2f}%)")
        lines.append(f"  Max DD         : {verdict.max_dd_pct:.2f}%")
        lines.append(f"  Pire Daily Loss: {verdict.max_daily_loss_pct:.2f}%")
        lines.append(f"  Jours tradés   : {verdict.trading_days}")
        lines.append(
            f"  Meilleur jour  : ${verdict.best_day_pnl:+.2f} ({verdict.best_day_consistency_pct:.1f}% du total)"
        )
        lines.append(
            f"  Trades         : {verdict.total_trades} (WR {verdict.win_rate:.1f}%, PF {verdict.profit_factor:.2f})"
        )
        lines.append("")

        # Règles
        lines.append("  ─── Règles ───")
        lines.append(
            f"  Profit Target: ${verdict.total_pnl:>+10.2f} / "
            f"${verdict.config.account_size * verdict.config.profit_target_pct:>+10,.0f} "
            f"({'✅' if verdict.total_pnl >= verdict.config.account_size * verdict.config.profit_target_pct else '❌'})"
        )
        lines.append(
            f"  Max DD       : {verdict.max_dd_pct:>8.2f}% / "
            f"{verdict.config.max_dd_pct * 100:.0f}% "
            f"({'✅' if verdict.max_dd_pct <= verdict.config.max_dd_pct * 100 else '❌'})"
        )
        lines.append(
            f"  Daily Loss   : {verdict.max_daily_loss_pct:>8.2f}% / "
            f"{verdict.config.max_daily_loss_pct * 100:.0f}% "
            f"({'✅' if verdict.max_daily_loss_pct <= verdict.config.max_daily_loss_pct * 100 else '❌'})"
        )
        lines.append(
            f"  Consistency  : {verdict.best_day_consistency_pct:>8.1f}% / "
            f"{verdict.config.consistency_pct * 100:.0f}% "
            f"({'✅' if verdict.consistency_ok else '❌'})"
        )
        lines.append(
            f"  Min Jours    : {verdict.trading_days:>8} / "
            f"{verdict.config.min_trading_days} "
            f"({'✅' if verdict.trading_days >= verdict.config.min_trading_days else '❌'})"
        )
        lines.append("")

        # Marge de sécurité
        lines.append("  ─── Marge de Sécurité ───")
        lines.append(f"  Marge DD: {verdict.margin_of_safety:.1f}%")
        if verdict.margin_of_safety > 50:
            lines.append("  ✅ Confortable (> 50%)")
        elif verdict.margin_of_safety > 20:
            lines.append("  ⚠️  Modérée (20-50%)")
        else:
            lines.append("  🔴 Faible (< 20%)")

        lines.append("═" * 70)

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# FTMOPortfolioSimulator
# ═══════════════════════════════════════════════════════════════════════════


class FTMOPortfolioSimulator:
    """
    Simule un challenge FTMO sur un portefeuille multi-symboles combiné.

    Prend les résultats de backtest de N symboles, combine tous les trades
    chronologiquement, et évalue les règles FTMO (daily loss, max DD,
    consistency, profit target) sur le PnL combiné.

    Usage :
        sim = FTMOPortfolioSimulator(account_size=200_000)
        verdict = sim.evaluate_portfolio(results_dict)  # {symbole: BacktestResult}
        print(FTMOChallengeSimulator.summary(verdict))
    """

    def __init__(
        self,
        config: Optional[FTMOConfig | dict] = None,
        verbose: bool = False,
    ):
        if config is None:
            self.config = FTMOConfig()
        elif isinstance(config, dict):
            self.config = FTMOConfig(**config)
        else:
            self.config = config

        self.verbose = verbose
        self._ftmo = FTMOChallengeSimulator(config=self.config, verbose=verbose)

    def evaluate_portfolio(
        self,
        symbol_results: dict,
        balance: float = 200_000.0,
    ) -> FTMOVerdict:
        """
        Évalue les règles FTMO sur un portefeuille de symboles.

        Args:
            symbol_results: Dict {symbole: BacktestResult}
            balance: Capital initial

        Returns:
            FTMOVerdict avec PASS/FAIL sur le portefeuille combiné
        """
        # ─── 1. Combiner tous les trades chronologiquement ───────────────
        all_trades = []

        for symbol, result in symbol_results.items():
            if not hasattr(result, "trades"):
                logger.warning(f"FTMO Portfolio: {symbol} n'a pas de trades, ignoré")
                continue

            closed = [t for t in result.trades if hasattr(t, "closed") and t.closed]
            all_trades.extend(closed)
            logger.info(
                f"  Portfolio: {symbol} → {len(closed)} trades, "
                f"PnL ${sum(getattr(t, 'profit_usd_cost', 0) for t in closed):+.2f}"
            )

        if not all_trades:
            verdict = FTMOVerdict(config=self.config)
            verdict.fail_reason = "Aucun trade dans le portefeuille"
            return verdict

        # Trier chronologiquement par close_time
        all_trades.sort(key=lambda t: getattr(t, "close_time", None) or "")

        logger.info(f"  Portfolio combiné: {len(all_trades)} trades, {len(symbol_results)} symboles")

        # ─── 2. Évaluer via FTMOChallengeSimulator ───────────────────────
        # Note : on ne passe PAS d'equity_curve pour éviter les bugs
        # d'alignement temporel entre courbes de symboles différents.
        # Le FTMOChallengeSimulator calcule le DD depuis la liste
        # chronologique des trades, ce qui est correct car les trades
        # sont déjà triés par close_time.
        verdict = self._ftmo.evaluate(
            trades=all_trades,
            equity_curve=None,  # Calcule DD depuis les trades (correct)
            dates=None,
            balance=balance,
        )

        # Ajouter des métriques portfolio
        verdict.metadata = {
            "n_symbols": len(symbol_results),
            "symbols": list(symbol_results.keys()),
            "trades_per_symbol": {
                sym: len([t for t in result.trades if hasattr(t, "closed") and t.closed])
                for sym, result in symbol_results.items()
            },
            "pnl_per_symbol": {
                sym: round(
                    sum(getattr(t, "profit_usd_cost", 0) for t in result.trades if hasattr(t, "closed") and t.closed),
                    2,
                )
                for sym, result in symbol_results.items()
            },
        }

        if self.verbose:
            logger.info(
                f"FTMO Portfolio: {'PASS ✅' if verdict.passed else 'FAIL ❌'} | "
                f"{len(all_trades)} trades, {len(symbol_results)} symboles, "
                f"PnL ${verdict.total_pnl:+.2f}, DD {verdict.max_dd_pct:.2f}%"
            )

        return verdict

    def _build_combined_equity(
        self,
        all_trades: list,
        equity_curves: dict[str, tuple[list[float], list]],
        initial_balance: float,
    ) -> tuple[list[float], list]:
        """
        Construit la courbe d'equity combinée à partir :
        1. Si des courbes d'equity sont disponibles → les somme barre par barre
        2. Sinon → calcule depuis les trades (running PnL)

        Returns:
            (equity_curve, dates)
        """
        if equity_curves:
            # Trouver la plus longue série de dates
            ref_dates = None
            ref_len = 0
            for sym, (eq, dates) in equity_curves.items():
                if dates and len(dates) > ref_len:
                    ref_dates = dates
                    ref_len = len(dates)

            if ref_dates and ref_len > 0:
                # Sommer les equity curves (besoin d'alignement temporel)
                # Approche simplifiée : on prend la somme des PnL flottants
                combined_eq = [initial_balance] * ref_len
                for sym, (eq_curve, dates) in equity_curves.items():
                    for i in range(min(ref_len, len(eq_curve))):
                        if i < len(eq_curve):
                            # Le PnL de ce symbole = equity - initial_balance
                            symbol_pnl = eq_curve[i] - initial_balance
                            combined_eq[i] += symbol_pnl
                # Re-centrer : l'equity combinée = initial_balance + somme des PnL
                for i in range(ref_len):
                    combined_eq[i] = initial_balance + (combined_eq[i] - initial_balance * (1 + len(equity_curves)))
                return combined_eq, ref_dates

        # Fallback : calculer depuis les trades
        running = initial_balance
        eq_curve = [running]
        dates_list = []
        for t in all_trades:
            pnl = getattr(t, "profit_usd_cost", 0)
            running += pnl
            eq_curve.append(running)
            ct = getattr(t, "close_time", None)
            dates_list.append(ct)

        return eq_curve, dates_list

    def monte_carlo_pass_probability(
        self,
        symbol_results: dict,
        n_simulations: int = 1000,
    ) -> dict:
        """
        Simule Monte Carlo sur le portefeuille combiné.

        Prend tous les PnL de tous les symboles, les mélange par bootstrap,
        et évalue la probabilité de passer le challenge FTMO.
        """
        all_pnls = []
        for symbol, result in symbol_results.items():
            if not hasattr(result, "trades"):
                continue
            pnls = [getattr(t, "profit_usd_cost", 0.0) for t in result.trades if hasattr(t, "closed") and t.closed]
            all_pnls.extend(p for p in pnls if p != 0)

        if len(all_pnls) < 20:
            return {
                "pass_probability": 0.0,
                "n_simulations": 0,
                "error": "Trop peu de trades pour la simulation",
            }

        return self._ftmo.monte_carlo_pass_probability(
            trades=[type("t", (), {"closed": True, "profit_usd_cost": p})() for p in all_pnls],
            n_simulations=n_simulations,
            account_size=self.config.account_size,
        )

    @staticmethod
    def portfolio_summary(
        verdict: FTMOVerdict,
        symbol_results: Optional[dict] = None,
    ) -> str:
        """Génère un rapport textuel du challenge FTMO portfolio."""
        lines = []
        lines.append("═" * 70)
        lines.append("  CHALLENGE FTMO — PORTEFEUILLE MULTI-SYMBOLES  ")
        lines.append("═" * 70)

        # En-tête
        n_sym = verdict.metadata.get("n_symbols", 0) if hasattr(verdict, "metadata") else 0
        symbols_list = verdict.metadata.get("symbols", []) if hasattr(verdict, "metadata") else []
        lines.append(f"\n  Compte: ${verdict.config.account_size:,}")
        lines.append(f"  Symboles: {', '.join(symbols_list)} ({n_sym} actifs)")
        lines.append(f"  Profit Target: +${verdict.config.account_size * verdict.config.profit_target_pct:,.0f}")
        lines.append("")

        # Résultat
        if verdict.passed:
            lines.append("  ✅  CHALLENGE PASSÉ")
        else:
            lines.append(f"  ❌  ÉCHOUÉ — {verdict.fail_reason}")
        lines.append("")

        # Métriques clés
        lines.append("  ─── Résultats Portefeuille ───")
        lines.append(f"  PnL total      : ${verdict.total_pnl:>+10.2f} ({verdict.total_pnl_pct:+.2f}%)")
        lines.append(f"  Max DD         : {verdict.max_dd_pct:.2f}%")
        lines.append(f"  Pire Daily Loss: {verdict.max_daily_loss_pct:.2f}%")
        lines.append(f"  Jours tradés   : {verdict.trading_days}")
        lines.append(
            f"  Trades total    : {verdict.total_trades} (WR {verdict.win_rate:.1f}%, PF {verdict.profit_factor:.2f})"
        )
        lines.append("")

        # PnL par symbole
        if hasattr(verdict, "metadata") and verdict.metadata.get("pnl_per_symbol"):
            lines.append("  ─── PnL par Symbole ───")
            for sym, pnl in sorted(
                verdict.metadata["pnl_per_symbol"].items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                n_trades = verdict.metadata["trades_per_symbol"].get(sym, 0)
                lines.append(f"  {sym:<10}: ${pnl:>+8.2f} ({n_trades} trades)")

        lines.append("")

        # Règles
        lines.append("  ─── Règles FTMO ───")
        target = verdict.config.account_size * verdict.config.profit_target_pct
        lines.append(
            f"  Profit Target: ${verdict.total_pnl:>+10.2f} / ${target:>+10,.0f} {'✅' if verdict.total_pnl >= target else '❌'}"
        )
        lines.append(
            f"  Max DD       : {verdict.max_dd_pct:>8.2f}% / {verdict.config.max_dd_pct * 100:.0f}% {'✅' if verdict.max_dd_pct <= verdict.config.max_dd_pct * 100 else '❌'}"
        )
        lines.append(
            f"  Daily Loss   : {verdict.max_daily_loss_pct:>8.2f}% / {verdict.config.max_daily_loss_pct * 100:.0f}% {'✅' if verdict.max_daily_loss_pct <= verdict.config.max_daily_loss_pct * 100 else '❌'}"
        )
        lines.append(
            f"  Consistency  : {verdict.best_day_consistency_pct:>8.1f}% / {verdict.config.consistency_pct * 100:.0f}% {'✅' if verdict.consistency_ok else '❌'}"
        )
        lines.append(
            f"  Min Jours    : {verdict.trading_days:>8} / {verdict.config.min_trading_days} {'✅' if verdict.trading_days >= verdict.config.min_trading_days else '❌'}"
        )
        lines.append("")

        # Marge de sécurité
        lines.append("  ─── Marge de Sécurité ───")
        lines.append(f"  Marge DD: {verdict.margin_of_safety:.1f}%")
        if verdict.margin_of_safety > 50:
            lines.append("  ✅ Confortable (> 50%)")
        elif verdict.margin_of_safety > 20:
            lines.append("  ⚠️  Modérée (20-50%)")
        else:
            lines.append("  🔴 Faible (< 20%)")

        lines.append("═" * 70)
        return "\n".join(lines)
