#!/usr/bin/env python3
"""
Gestion dynamique du risque pour optimiser la taille des positions.

Implémente :
- Kelly Criterion pour le sizing optimal
- Volatility-adjusted position sizing
- Drawdown-based position reduction
- Correlation-aware portfolio exposure
"""

import pandas as pd
import numpy as np
from datetime import datetime
import json
import os


class DynamicRiskManager:
    """Gestionnaire de risque dynamique"""

    def __init__(
        self,
        initial_capital=10000,
        max_risk_per_trade=0.02,
        max_portfolio_risk=0.06,
        drawdown_threshold=0.10,
    ):
        """
        Args:
            initial_capital: Capital initial
            max_risk_per_trade: Risque max par trade (2%)
            max_portfolio_risk: Risque max du portefeuille (6%)
            drawdown_threshold: Seuil de drawdown pour réduction (10%)
        """
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.max_risk_per_trade = max_risk_per_trade
        self.max_portfolio_risk = max_portfolio_risk
        self.drawdown_threshold = drawdown_threshold

        # Historique pour calculs
        self.trade_history = []
        self.equity_curve = [initial_capital]
        self.timestamps = [datetime.now()]

        # Métriques de performance
        self.peak_equity = initial_capital
        self.current_drawdown = 0.0
        self.volatility_window = 20

    def kelly_criterion_sizing(self, win_rate, avg_win, avg_loss):
        """
        Calcul de la taille optimale selon Kelly Criterion

        Args:
            win_rate: Taux de réussite historique
            avg_win: Gain moyen des trades gagnants
            avg_loss: Perte moyenne des trades perdants

        Returns:
            Fraction optimale du capital à risquer
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.01  # Fraction conservative par défaut

        # Kelly fraction = (bp - q) / b
        # où b = avg_win/avg_loss, p = win_rate, q = 1-win_rate
        b = abs(avg_win / avg_loss)
        p = win_rate
        q = 1 - win_rate

        kelly_fraction = (b * p - q) / b

        # Appliquer des limites de sécurité
        kelly_fraction = max(
            0.001, min(kelly_fraction, 0.25)
        )  # Entre 0.1% et 25%

        # Fraction Kelly conservative (50% du Kelly complet)
        return kelly_fraction * 0.5

    def volatility_adjusted_sizing(
        self, symbol_volatility, market_volatility=0.15
    ):
        """
        Ajustement de la taille basé sur la volatilité

        Args:
            symbol_volatility: Volatilité du symbole
            market_volatility: Volatilité de référence du marché

        Returns:
            Multiplicateur de taille (0.5 à 2.0)
        """
        if symbol_volatility <= 0:
            return 1.0

        # Ratio de volatilité
        vol_ratio = symbol_volatility / market_volatility

        # Ajustement inverse : plus de volatilité = moins de taille
        vol_multiplier = 1.0 / np.sqrt(vol_ratio)

        # Limiter entre 0.5x et 2x
        return np.clip(vol_multiplier, 0.5, 2.0)

    def drawdown_adjustment(self):
        """
        Ajustement basé sur le drawdown actuel

        Returns:
            Multiplicateur de réduction (0.1 à 1.0)
        """
        if self.current_drawdown >= self.drawdown_threshold:
            # Réduction progressive selon le drawdown
            excess_dd = self.current_drawdown - self.drawdown_threshold
            reduction = 1.0 - (
                excess_dd * 2
            )  # Réduction de 2x le drawdown excédentaire
            return max(0.1, reduction)  # Minimum 10% de la taille normale

        return 1.0  # Pas de réduction

    def correlation_adjustment(self, new_position, existing_positions):
        """
        Ajustement basé sur les corrélations du portefeuille

        Args:
            new_position: Dict avec 'symbol', 'direction'
            existing_positions: Liste des positions existantes

        Returns:
            Multiplicateur de corrélation (0.3 à 1.0)
        """
        if not existing_positions:
            return 1.0

        # Calculer l'exposition directionnelle existante
        directional_exposure = sum(
            pos.get("size", 0) * pos.get("direction", 0)
            for pos in existing_positions
        )

        new_direction = new_position.get("direction", 0)

        # Si nouvelle position dans même direction que l'exposition nette
        if np.sign(directional_exposure) == np.sign(new_direction):
            # Calculer le facteur de concentration
            total_exposure = abs(directional_exposure)
            max_directional_exposure = (
                self.max_portfolio_risk * 3
            )  # 18% max unidirectionnel

            if total_exposure > max_directional_exposure:
                concentration_penalty = max_directional_exposure / (
                    total_exposure + 1e-8
                )
                return max(0.3, concentration_penalty)

        return 1.0

    def calculate_optimal_position_size(
        self,
        signal_strength,
        symbol_volatility,
        stop_loss_distance,
        existing_positions=None,
    ):
        """
        Calcul de la taille optimale de position

        Args:
            signal_strength: Force du signal (0-1)
            symbol_volatility: Volatilité annualisée du symbole
            stop_loss_distance: Distance du stop loss (en %)
            existing_positions: Positions existantes

        Returns:
            Dict avec taille recommandée et justifications
        """
        if existing_positions is None:
            existing_positions = []

        # 1. Calcul Kelly si historique disponible
        kelly_size = 0.02  # Défaut
        if len(self.trade_history) >= 10:
            wins = [t for t in self.trade_history if t["pnl"] > 0]
            losses = [t for t in self.trade_history if t["pnl"] <= 0]

            if wins and losses:
                win_rate = len(wins) / len(self.trade_history)
                avg_win = np.mean([t["pnl"] for t in wins])
                avg_loss = np.mean([abs(t["pnl"]) for t in losses])
                kelly_size = self.kelly_criterion_sizing(
                    win_rate, avg_win, avg_loss
                )

        # 2. Ajustement volatilité
        vol_multiplier = self.volatility_adjusted_sizing(symbol_volatility)

        # 3. Ajustement drawdown
        dd_multiplier = self.drawdown_adjustment()

        # 4. Ajustement corrélation
        corr_multiplier = self.correlation_adjustment(
            {"direction": 1 if signal_strength > 0 else -1}, existing_positions
        )

        # 5. Ajustement force du signal
        signal_multiplier = abs(signal_strength)  # 0-1

        # 6. Taille basée sur stop loss
        if stop_loss_distance > 0:
            # Risquer max_risk_per_trade du capital
            risk_amount = self.current_capital * self.max_risk_per_trade
            sl_based_size = risk_amount / (
                stop_loss_distance * self.current_capital
            )
        else:
            sl_based_size = 0.01  # Défaut conservatif

        # Combinaison de tous les facteurs
        base_size = min(kelly_size, sl_based_size)
        final_size = (
            base_size
            * vol_multiplier
            * dd_multiplier
            * corr_multiplier
            * signal_multiplier
        )

        # Limites de sécurité finales
        final_size = max(0.001, min(final_size, self.max_risk_per_trade))

        return {
            "recommended_size": final_size,
            "base_kelly_size": kelly_size,
            "sl_based_size": sl_based_size,
            "volatility_multiplier": vol_multiplier,
            "drawdown_multiplier": dd_multiplier,
            "correlation_multiplier": corr_multiplier,
            "signal_multiplier": signal_multiplier,
            "current_drawdown": self.current_drawdown,
            "risk_capacity": self.current_capital * self.max_risk_per_trade,
        }

    def update_trade_result(
        self, symbol, entry_price, exit_price, size, direction, timestamp=None
    ):
        """
        Mettre à jour l'historique avec un nouveau trade

        Args:
            symbol: Symbole tradé
            entry_price: Prix d'entrée
            exit_price: Prix de sortie
            size: Taille de la position
            direction: 1 pour long, -1 pour short
            timestamp: Timestamp du trade
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Calculer le P&L
        price_change = (exit_price - entry_price) / entry_price
        pnl = price_change * direction * size * self.current_capital

        # Ajouter à l'historique
        trade = {
            "timestamp": timestamp,
            "symbol": symbol,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size": size,
            "direction": direction,
            "pnl": pnl,
            "pnl_pct": pnl / self.current_capital,
        }

        self.trade_history.append(trade)

        # Mettre à jour le capital
        self.current_capital += pnl
        self.equity_curve.append(self.current_capital)
        self.timestamps.append(timestamp)

        # Mettre à jour les métriques de drawdown
        self.peak_equity = max(self.peak_equity, self.current_capital)
        self.current_drawdown = (
            self.peak_equity - self.current_capital
        ) / self.peak_equity

        return trade

    def get_portfolio_metrics(self):
        """Calculer les métriques du portefeuille"""
        if len(self.trade_history) < 2:
            return {
                "total_return": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "trades_count": len(self.trade_history),
            }

        # Retours
        equity_series = pd.Series(
            self.equity_curve[1:], index=self.timestamps[1:]
        )
        returns = equity_series.pct_change().dropna()

        total_return = (
            self.current_capital - self.initial_capital
        ) / self.initial_capital

        # Sharpe ratio
        if len(returns) > 1 and returns.std() > 0:
            sharpe = returns.mean() / returns.std() * np.sqrt(252)  # Annualisé
        else:
            sharpe = 0

        # Max drawdown
        peak = equity_series.cummax()
        drawdown = (equity_series - peak) / peak
        max_dd = drawdown.min()

        # Win rate
        winning_trades = [t for t in self.trade_history if t["pnl"] > 0]
        win_rate = len(winning_trades) / len(self.trade_history)

        # Profit factor
        gross_profit = sum(t["pnl"] for t in winning_trades)
        losing_trades = [t for t in self.trade_history if t["pnl"] <= 0]
        gross_loss = abs(sum(t["pnl"] for t in losing_trades))

        profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )

        return {
            "total_return": total_return,
            "annualized_return": (
                (1 + total_return) ** (252 / len(returns)) - 1
            )
            if len(returns) > 0
            else 0,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "current_drawdown": self.current_drawdown,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "trades_count": len(self.trade_history),
            "current_capital": self.current_capital,
            "peak_capital": self.peak_equity,
        }

    def save_state(self, filepath):
        """Sauvegarder l'état du gestionnaire de risque"""
        state = {
            "initial_capital": self.initial_capital,
            "current_capital": self.current_capital,
            "trade_history": self.trade_history,
            "equity_curve": self.equity_curve,
            "timestamps": [t.isoformat() for t in self.timestamps],
            "peak_equity": self.peak_equity,
            "current_drawdown": self.current_drawdown,
            "metrics": self.get_portfolio_metrics(),
        }

        with open(filepath, "w") as f:
            json.dump(state, f, indent=2, default=str)

    @classmethod
    def load_state(cls, filepath):
        """Charger l'état d'un gestionnaire de risque"""
        with open(filepath, "r") as f:
            state = json.load(f)

        # Créer une nouvelle instance
        manager = cls(initial_capital=state["initial_capital"])
        manager.current_capital = state["current_capital"]
        manager.trade_history = state["trade_history"]
        manager.equity_curve = state["equity_curve"]
        manager.timestamps = [
            datetime.fromisoformat(t) for t in state["timestamps"]
        ]
        manager.peak_equity = state["peak_equity"]
        manager.current_drawdown = state["current_drawdown"]

        return manager


def simulate_trading_with_risk_management():
    """Simulation de trading avec gestion de risque dynamique"""
    print("🎲 Simulation de trading avec gestion de risque dynamique")

    # Créer le gestionnaire de risque
    risk_manager = DynamicRiskManager(
        initial_capital=10000,
        max_risk_per_trade=0.02,
        max_portfolio_risk=0.06,
        drawdown_threshold=0.10,
    )

    # Simuler 100 trades
    np.random.seed(42)
    symbols = ["EURUSD", "XAUUSD", "BTCUSD"]

    results = []

    for i in range(100):
        # Générer signal aléatoire
        symbol = np.random.choice(symbols)
        signal_strength = np.random.uniform(0.3, 1.0)
        symbol_volatility = np.random.uniform(
            0.10, 0.30
        )  # 10-30% vol annuelle
        stop_loss_distance = np.random.uniform(0.01, 0.03)  # 1-3% SL

        # Calculer taille optimale
        sizing_result = risk_manager.calculate_optimal_position_size(
            signal_strength=signal_strength,
            symbol_volatility=symbol_volatility,
            stop_loss_distance=stop_loss_distance,
        )

        # Simuler l'exécution du trade
        entry_price = 100.0  # Prix de référence
        direction = 1 if signal_strength > 0 else -1
        size = sizing_result["recommended_size"]

        # Simuler le résultat (win rate ~55%)
        if np.random.random() < 0.55:
            # Trade gagnant
            exit_price = entry_price * (
                1 + np.random.uniform(0.005, 0.03) * direction
            )
        else:
            # Trade perdant
            exit_price = entry_price * (
                1 - np.random.uniform(0.005, 0.02) * direction
            )

        # Enregistrer le trade
        trade = risk_manager.update_trade_result(
            symbol=symbol,
            entry_price=entry_price,
            exit_price=exit_price,
            size=size,
            direction=direction,
        )

        # Enregistrer les détails
        result = {
            "trade_num": i + 1,
            "symbol": symbol,
            "signal_strength": signal_strength,
            "position_size": size,
            "pnl": trade["pnl"],
            "capital": risk_manager.current_capital,
            "drawdown": risk_manager.current_drawdown,
            "sizing_details": sizing_result,
        }
        results.append(result)

        # Affichage périodique
        if (i + 1) % 20 == 0:
            metrics = risk_manager.get_portfolio_metrics()
            print(
                f"Trade {i+1}: Capital={risk_manager.current_capital:.0f}, "
                f"Return={metrics['total_return']:.1%}, "
                f"DD={metrics['current_drawdown']:.1%}"
            )

    # Résultats finaux
    final_metrics = risk_manager.get_portfolio_metrics()

    print("\n📊 RÉSULTATS FINAUX:")
    print(f"Capital initial: ${risk_manager.initial_capital:,.0f}")
    print(f"Capital final: ${risk_manager.current_capital:,.0f}")
    print(f"Retour total: {final_metrics['total_return']:.2%}")
    print(f"Sharpe ratio: {final_metrics['sharpe_ratio']:.2f}")
    print(f"Max drawdown: {final_metrics['max_drawdown']:.2%}")
    print(f"Win rate: {final_metrics['win_rate']:.1%}")
    print(f"Profit factor: {final_metrics['profit_factor']:.2f}")
    print(f"Nombre de trades: {final_metrics['trades_count']}")

    # Sauvegarder
    os.makedirs("artifacts/risk_management", exist_ok=True)

    risk_manager.save_state("artifacts/risk_management/simulation_state.json")

    results_df = pd.DataFrame(results)
    results_df.to_csv(
        "artifacts/risk_management/simulation_results.csv", index=False
    )

    print("\n💾 Résultats sauvegardés dans artifacts/risk_management/")

    return risk_manager, results_df


def main():
    """Test du gestionnaire de risque dynamique"""
    print("🚀 Test du gestionnaire de risque dynamique")
    print("=" * 50)

    # Lancer la simulation
    risk_manager, results_df = simulate_trading_with_risk_management()

    # Analyse des ajustements de taille
    avg_size = results_df["position_size"].mean()
    size_std = results_df["position_size"].std()

    print("\n📈 ANALYSE DES TAILLES DE POSITION:")
    print(f"Taille moyenne: {avg_size:.3f} ({avg_size*100:.1f}%)")
    print(f"Écart-type: {size_std:.3f}")
    print(f"Taille min: {results_df['position_size'].min():.3f}")
    print(f"Taille max: {results_df['position_size'].max():.3f}")

    # Impact du drawdown sur le sizing
    high_dd_trades = results_df[results_df["drawdown"] > 0.05]
    if len(high_dd_trades) > 0:
        avg_size_high_dd = high_dd_trades["position_size"].mean()
        print(
            f"\nImpact drawdown: Taille réduite à {avg_size_high_dd:.3f} "
            f"(-{(avg_size - avg_size_high_dd)/avg_size*100:.1f}%)"
        )

    print("\n✅ Gestion de risque dynamique testée avec succès")


if __name__ == "__main__":
    main()
