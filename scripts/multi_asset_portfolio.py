#!/usr/bin/env python3
"""
Système d'Optimisation de Portefeuille Multi-Actifs.

Ce système implémente:
- Théorie Moderne du Portefeuille (Markowitz)
- Allocation dynamique avec contraintes de corrélation
- Optimisation du ratio Sharpe et autres métriques de risque
- Diversification intelligente multi-paires de devises
- Rééquilibrage automatique basé sur les performances
"""

import pandas as pd
import numpy as np
import json
import os
import warnings
from datetime import datetime

# Optimisation
from scipy.optimize import minimize

warnings.filterwarnings("ignore")

# Matrices de covariance robustes avec fallback
try:
    from sklearn.covariance import LedoitWolf, OAS
    SKLEARN_AVAILABLE = True
    print("✅ scikit-learn disponible - covariance robuste activée")
except ImportError as e:
    SKLEARN_AVAILABLE = False
    print(f"⚠️  scikit-learn non disponible: {e}")
    print("🔄 Fallback vers covariance empirique classique")
except Exception as e:
    SKLEARN_AVAILABLE = False
    print(f"🔴 Erreur scikit-learn: {e}")
    print("🔄 Fallback vers covariance empirique classique")


class MultiAssetPortfolioOptimizer:
    """Optimiseur de portefeuille multi-actifs"""

    def __init__(self, risk_free_rate=0.02, rebalance_frequency="monthly"):
        """
        Args:
            risk_free_rate: Taux sans risque annualisé
            rebalance_frequency: Fréquence de rééquilibrage
                ('daily', 'weekly', 'monthly')
        """
        self.risk_free_rate = risk_free_rate
        self.rebalance_frequency = rebalance_frequency

        # État du portefeuille
        self.assets = []
        self.returns_data = None
        self.current_weights = None
        self.optimization_history = []

        # Contraintes par défaut
        self.min_weight = 0.0  # Poids minimum par actif
        self.max_weight = 0.5  # Poids maximum par actif
        self.max_correlation = 0.8  # Corrélation maximum autorisée

        print("📊 Portfolio Optimizer initialisé:")
        print(f"  💰 Taux sans risque: {risk_free_rate*100:.1f}%")
        print(f"  🔄 Rééquilibrage: {rebalance_frequency}")

    def add_asset_data(self, asset_name, price_data):
        """
        Ajouter les données d'un actif au portefeuille

        Args:
            asset_name: Nom de l'actif (ex: 'EURUSD', 'GBPUSD')
            price_data: DataFrame avec colonnes 'close' et 'datetime'
        """
        if not isinstance(price_data, pd.DataFrame):
            raise ValueError("price_data doit être un DataFrame")

        if "close" not in price_data.columns:
            raise ValueError("price_data doit avoir une colonne 'close'")

        # Calculer les rendements
        returns = price_data["close"].pct_change().dropna()

        self.assets.append(
            {
                "name": asset_name,
                "prices": price_data["close"],
                "returns": returns,
                "mean_return": returns.mean(),
                "volatility": returns.std(),
                "sharpe_ratio": (
                    (returns.mean() - self.risk_free_rate / 252)
                    / returns.std()
                    if returns.std() > 0
                    else 0
                ),
            }
        )

        annual_return = returns.mean() * 252 * 100
        annual_vol = returns.std() * np.sqrt(252) * 100
        sharpe = self.assets[-1]['sharpe_ratio']

        print(f"  ✅ {asset_name}: μ={annual_return:.1f}%, "
              f"σ={annual_vol:.1f}%, Sharpe={sharpe:.2f}")

    def create_synthetic_assets(self, base_asset_data, n_assets=5):
        """
        Créer des actifs synthétiques pour simulation multi-portefeuille

        Args:
            base_asset_data: DataFrame avec les données de base
            n_assets: Nombre d'actifs synthétiques à créer
        """
        print(f"🔬 Création de {n_assets} actifs synthétiques...")

        base_returns = base_asset_data["close"].pct_change().dropna()

        # Actif de base
        self.add_asset_data("BASE_ASSET", base_asset_data)

        # Générer des actifs synthétiques avec corrélations variées
        np.random.seed(42)  # Pour reproductibilité

        for i in range(n_assets - 1):
            # Paramètres aléatoires
            correlation = np.random.uniform(
                0.3, 0.9
            )  # Corrélation avec actif de base
            volatility_multiplier = np.random.uniform(0.8, 1.5)
            drift_adjustment = np.random.uniform(-0.0005, 0.0005)

            # Générer les rendements corrélés
            noise = np.random.normal(0, 1, len(base_returns))
            correlated_noise = (
                correlation * (base_returns / base_returns.std()).values
                + np.sqrt(1 - correlation**2) * noise
            )

            synthetic_returns = pd.Series(
                correlated_noise * base_returns.std() * volatility_multiplier
                + drift_adjustment,
                index=base_returns.index,
            )

            # Créer la série de prix
            synthetic_prices = pd.Series(index=base_returns.index, dtype=float)
            synthetic_prices.iloc[0] = 100  # Prix initial

            for j in range(1, len(synthetic_prices)):
                synthetic_prices.iloc[j] = synthetic_prices.iloc[j - 1] * (
                    1 + synthetic_returns.iloc[j - 1]
                )

            # Créer DataFrame
            synthetic_data = pd.DataFrame({"close": synthetic_prices})

            self.add_asset_data(f"SYNTH_{i+1}", synthetic_data)

    def prepare_returns_matrix(self):
        """Préparer la matrice des rendements alignés"""
        if not self.assets:
            raise ValueError("Aucun actif dans le portefeuille")

        # Aligner tous les rendements sur les mêmes dates
        returns_dict = {}
        for asset in self.assets:
            returns_dict[asset["name"]] = asset["returns"]

        self.returns_data = pd.DataFrame(returns_dict)
        self.returns_data = self.returns_data.dropna()

        print(
            f"  📊 Matrice rendements: {len(self.returns_data)} "
            f"périodes, {len(self.assets)} actifs"
        )

        return self.returns_data

    def calculate_covariance_matrix(self, method="empirical"):
        """
        Calculer la matrice de covariance

        Args:
            method: 'empirical', 'ledoit_wolf', 'oas'
        """
        if self.returns_data is None:
            self.prepare_returns_matrix()

        if method == "empirical" or not SKLEARN_AVAILABLE:
            cov_matrix = self.returns_data.cov()

        elif method == "ledoit_wolf":
            lw = LedoitWolf()
            cov_matrix = pd.DataFrame(
                lw.fit(self.returns_data).covariance_,
                index=self.returns_data.columns,
                columns=self.returns_data.columns,
            )

        elif method == "oas":
            oas = OAS()
            cov_matrix = pd.DataFrame(
                oas.fit(self.returns_data).covariance_,
                index=self.returns_data.columns,
                columns=self.returns_data.columns,
            )

        else:
            raise ValueError(f"Méthode {method} non supportée")

        # Vérifier que la matrice est définie positive
        eigenvals = np.linalg.eigvals(cov_matrix)
        if np.any(eigenvals <= 0):
            print("⚠️  Matrice non définie positive - Régularisation")
            # Régulariser en ajoutant une petite valeur sur la diagonale
            regularization = abs(min(eigenvals)) + 1e-8
            np.fill_diagonal(
                cov_matrix.values,
                cov_matrix.values.diagonal() + regularization,
            )

        return cov_matrix * 252  # Annualiser

    def calculate_expected_returns(self, method="historical"):
        """
        Calculer les rendements espérés

        Args:
            method: 'historical', 'capm', 'black_litterman'
        """
        if self.returns_data is None:
            self.prepare_returns_matrix()

        if method == "historical":
            expected_returns = self.returns_data.mean() * 252  # Annualiser

        elif method == "capm":
            # Simplification: utiliser la moyenne historique ajustée
            market_return = self.returns_data.mean(axis=1).mean() * 252
            betas = {}

            for asset in self.returns_data.columns:
                asset_returns = self.returns_data[asset]
                market_returns = self.returns_data.mean(axis=1)

                # Calculer beta
                covariance = np.cov(asset_returns, market_returns)[0, 1]
                market_variance = np.var(market_returns)
                beta = (
                    covariance / market_variance if market_variance > 0 else 1
                )
                betas[asset] = beta

            expected_returns = pd.Series(
                {
                    asset: self.risk_free_rate
                    + beta * (market_return - self.risk_free_rate)
                    for asset, beta in betas.items()
                }
            )

        else:
            # Fallback à historique
            expected_returns = self.returns_data.mean() * 252

        return expected_returns

    def optimize_portfolio(self, objective="sharpe", method="SLSQP"):
        """
        Optimiser le portefeuille selon différents objectifs

        Args:
            objective: 'sharpe', 'min_variance', 'max_return', 'risk_parity'
            method: Méthode d'optimisation scipy
        """
        if self.returns_data is None:
            self.prepare_returns_matrix()

        print(f"🎯 Optimisation portefeuille - Objectif: {objective}")

        # Données nécessaires
        expected_returns = self.calculate_expected_returns()
        cov_matrix = self.calculate_covariance_matrix()
        n_assets = len(expected_returns)

        # Poids initiaux (équipondérés)
        initial_weights = np.ones(n_assets) / n_assets

        # Contraintes
        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1}
        ]  # Somme = 1

        # Bornes pour chaque poids
        bounds = [(self.min_weight, self.max_weight) for _ in range(n_assets)]

        # Fonction objectif selon la stratégie
        if objective == "sharpe":

            def objective_function(weights):
                portfolio_return = np.dot(weights, expected_returns)
                portfolio_variance = np.dot(
                    weights, np.dot(cov_matrix, weights)
                )
                portfolio_std = np.sqrt(portfolio_variance)

                if portfolio_std == 0:
                    return -np.inf

                sharpe_ratio = (
                    portfolio_return - self.risk_free_rate
                ) / portfolio_std
                return -sharpe_ratio  # Minimiser le négatif = maximiser

        elif objective == "min_variance":

            def objective_function(weights):
                return np.dot(weights, np.dot(cov_matrix, weights))

        elif objective == "max_return":

            def objective_function(weights):
                return -np.dot(weights, expected_returns)

        elif objective == "risk_parity":

            def objective_function(weights):
                portfolio_variance = np.dot(
                    weights, np.dot(cov_matrix, weights)
                )

                # Contributions au risque
                marginal_contrib = np.dot(cov_matrix, weights)
                contrib = (
                    weights * marginal_contrib / portfolio_variance
                    if portfolio_variance > 0
                    else weights
                )

                # Minimiser la variance des contributions
                target_contrib = 1.0 / n_assets
                return np.sum((contrib - target_contrib) ** 2)

        else:
            raise ValueError(f"Objectif {objective} non supporté")

        # Contrainte de corrélation maximum
        if self.max_correlation < 1.0:

            def correlation_constraint(weights):
                # Calculer la corrélation moyenne pondérée
                corr_matrix = self.returns_data.corr()
                weighted_corr = 0
                total_weight_pairs = 0

                for i in range(n_assets):
                    for j in range(i + 1, n_assets):
                        pair_weight = weights[i] * weights[j]
                        weighted_corr += pair_weight * corr_matrix.iloc[i, j]
                        total_weight_pairs += pair_weight

                avg_corr = (
                    weighted_corr / total_weight_pairs
                    if total_weight_pairs > 0
                    else 0
                )
                return self.max_correlation - avg_corr

            constraints.append({"type": "ineq", "fun": correlation_constraint})

        # Optimisation
        try:
            result = minimize(
                objective_function,
                initial_weights,
                method=method,
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 1000},
            )

            if result.success:
                optimal_weights = result.x

                # Calculer les métriques du portefeuille optimal
                portfolio_return = np.dot(optimal_weights, expected_returns)
                portfolio_variance = np.dot(
                    optimal_weights, np.dot(cov_matrix, optimal_weights)
                )
                portfolio_std = np.sqrt(portfolio_variance)
                sharpe_ratio = (
                    (portfolio_return - self.risk_free_rate) / portfolio_std
                    if portfolio_std > 0
                    else 0
                )

                optimization_result = {
                    "success": True,
                    "objective": objective,
                    "weights": dict(
                        zip(self.returns_data.columns, optimal_weights)
                    ),
                    "expected_return": portfolio_return,
                    "volatility": portfolio_std,
                    "sharpe_ratio": sharpe_ratio,
                    "timestamp": datetime.now(),
                }

                self.current_weights = optimal_weights
                self.optimization_history.append(optimization_result)

                print("  ✅ Optimisation réussie:")
                print(f"    📈 Rendement espéré: {portfolio_return*100:.2f}%")
                print(f"    📊 Volatilité: {portfolio_std*100:.2f}%")
                print(f"    🎯 Ratio Sharpe: {sharpe_ratio:.3f}")

                # Afficher les poids
                print("    🎨 Allocation:")
                for asset, weight in optimization_result["weights"].items():
                    if weight > 0.01:  # Afficher seulement si > 1%
                        print(f"      {asset}: {weight*100:.1f}%")

                return optimization_result

            else:
                print(f"  ❌ Échec optimisation: {result.message}")
                return {"success": False, "message": result.message}

        except Exception as e:
            print(f"  ❌ Erreur optimisation: {e}")
            return {"success": False, "error": str(e)}

    def efficient_frontier(self, n_points=20):
        """
        Calculer la frontière efficiente

        Args:
            n_points: Nombre de points sur la frontière
        """
        print(f"📈 Calcul frontière efficiente - {n_points} points")

        expected_returns = self.calculate_expected_returns()
        cov_matrix = self.calculate_covariance_matrix()

        # Plage de rendements cibles
        min_return = expected_returns.min()
        max_return = expected_returns.max()
        target_returns = np.linspace(min_return, max_return, n_points)

        frontier_results = []

        for target_return in target_returns:
            # Optimisation avec contrainte de rendement
            n_assets = len(expected_returns)
            initial_weights = np.ones(n_assets) / n_assets

            constraints = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1},
                {
                    "type": "eq",
                    "fun": lambda w: np.dot(w, expected_returns) - target_return,
                },
            ]

            bounds = [(0, 1) for _ in range(n_assets)]

            def min_variance_objective(weights):
                return np.dot(weights, np.dot(cov_matrix, weights))

            try:
                result = minimize(
                    min_variance_objective,
                    initial_weights,
                    method="SLSQP",
                    bounds=bounds,
                    constraints=constraints,
                )

                if result.success:
                    weights = result.x
                    variance = np.dot(weights, np.dot(cov_matrix, weights))
                    volatility = np.sqrt(variance)

                    frontier_results.append(
                        {
                            "target_return": target_return,
                            "volatility": volatility,
                            "weights": weights,
                        }
                    )

            except Exception:
                continue

        print(f"  ✅ {len(frontier_results)} points calculés")
        return frontier_results

    def backtest_portfolio(self, start_date=None, end_date=None):
        """
        Backtester la performance du portefeuille optimisé

        Args:
            start_date: Date de début (str ou datetime)
            end_date: Date de fin (str ou datetime)
        """
        if self.current_weights is None:
            print("⚠️  Aucun portefeuille optimisé - Utilisation équipondérée")
            self.current_weights = np.ones(len(self.assets)) / len(self.assets)

        print("🔍 Backtest portefeuille...")

        if self.returns_data is None:
            self.prepare_returns_matrix()

        # Filtrer par dates si spécifiées
        backtest_data = self.returns_data.copy()
        if start_date:
            backtest_data = backtest_data[backtest_data.index >= start_date]
        if end_date:
            backtest_data = backtest_data[backtest_data.index <= end_date]

        # Calculer la performance du portefeuille
        portfolio_returns = pd.Series(
            np.dot(backtest_data, self.current_weights),
            index=backtest_data.index,
        )

        # Calculer la performance cumulative
        cumulative_returns = (1 + portfolio_returns).cumprod()

        # Métriques de performance
        total_return = cumulative_returns.iloc[-1] - 1
        annualized_return = (1 + total_return) ** (
            252 / len(portfolio_returns)
        ) - 1
        volatility = portfolio_returns.std() * np.sqrt(252)
        sharpe_ratio = (
            (annualized_return - self.risk_free_rate) / volatility
            if volatility > 0
            else 0
        )

        # Maximum Drawdown
        peak = cumulative_returns.expanding().max()
        drawdown = (cumulative_returns - peak) / peak
        max_drawdown = drawdown.min()

        # Calmar Ratio
        calmar_ratio = (
            annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0
        )

        # VaR 95%
        var_95 = np.percentile(portfolio_returns, 5)

        start_date = backtest_data.index[0].strftime('%Y-%m-%d')
        end_date = backtest_data.index[-1].strftime('%Y-%m-%d')

        backtest_results = {
            "period": f"{start_date} à {end_date}",
            "total_return": total_return,
            "annualized_return": annualized_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "calmar_ratio": calmar_ratio,
            "var_95": var_95,
            "portfolio_returns": portfolio_returns,
            "cumulative_returns": cumulative_returns,
        }

        print(f"  📊 Période: {backtest_results['period']}")
        print(f"  📈 Rendement total: {total_return*100:.2f}%")
        print(f"  📊 Rendement annualisé: {annualized_return*100:.2f}%")
        print(f"  📉 Volatilité: {volatility*100:.2f}%")
        print(f"  🎯 Sharpe: {sharpe_ratio:.3f}")
        print(f"  📉 Max Drawdown: {max_drawdown*100:.2f}%")
        print(f"  💰 Calmar: {calmar_ratio:.3f}")

        return backtest_results

    def save_portfolio_state(
        self, filepath="artifacts/portfolio_optimization"
    ):
        """Sauvegarder l'état du portefeuille"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Préparer les données pour la sérialisation
        portfolio_state = {
            "timestamp": datetime.now().isoformat(),
            "assets": [
                {
                    "name": asset["name"],
                    "mean_return": asset["mean_return"],
                    "volatility": asset["volatility"],
                    "sharpe_ratio": asset["sharpe_ratio"],
                }
                for asset in self.assets
            ],
            "current_weights": self.current_weights.tolist()
            if self.current_weights is not None
            else None,
            "optimization_history": [
                {
                    "objective": opt["objective"],
                    "weights": opt["weights"],
                    "expected_return": opt["expected_return"],
                    "volatility": opt["volatility"],
                    "sharpe_ratio": opt["sharpe_ratio"],
                    "timestamp": opt["timestamp"].isoformat(),
                }
                for opt in self.optimization_history
            ],
            "parameters": {
                "risk_free_rate": self.risk_free_rate,
                "rebalance_frequency": self.rebalance_frequency,
                "min_weight": self.min_weight,
                "max_weight": self.max_weight,
                "max_correlation": self.max_correlation,
            },
        }

        with open(f"{filepath}_state.json", "w") as f:
            json.dump(portfolio_state, f, indent=2, default=str)

        print(f"💾 État portefeuille sauvegardé: {filepath}_state.json")


def main():
    """Test du système d'optimisation de portefeuille"""
    print("📊 TEST SYSTÈME OPTIMISATION PORTEFEUILLE")
    print("=" * 45)

    try:
        # Charger les données de base
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        # Créer l'optimiseur
        optimizer = MultiAssetPortfolioOptimizer(
            risk_free_rate=0.02, rebalance_frequency="monthly"
        )

        # Créer des actifs synthétiques pour simulation
        base_data = pd.DataFrame(
            {"close": df["close"] if "close" in df.columns else df.iloc[:, 0]}
        )

        optimizer.create_synthetic_assets(base_data, n_assets=6)

        # Préparer les données
        optimizer.prepare_returns_matrix()

        print("\n🎯 OPTIMISATIONS MULTIPLES:")
        print("=" * 30)

        # Test différentes stratégies d'optimisation
        strategies = ["sharpe", "min_variance", "risk_parity"]

        results = {}
        for strategy in strategies:
            print(f"\n📈 Stratégie: {strategy.upper()}")
            result = optimizer.optimize_portfolio(objective=strategy)
            if result.get("success"):
                results[strategy] = result

        # Backtest du meilleur portefeuille (Sharpe)
        if "sharpe" in results:
            print("\n🔍 BACKTEST PORTEFEUILLE SHARPE:")
            print("=" * 35)
            optimizer.backtest_portfolio()

        # Calculer la frontière efficiente
        print("\n📈 FRONTIÈRE EFFICIENTE:")
        print("=" * 25)
        frontier = optimizer.efficient_frontier(n_points=10)

        if frontier:
            print(f"  ✅ {len(frontier)} points calculés")
            print(
                f"  📊 Rendement min: {frontier[0]['target_return']*100:.2f}%"
            )
            print(
                f"  📊 Rendement max: {frontier[-1]['target_return']*100:.2f}%"
            )

        # Sauvegarder
        optimizer.save_portfolio_state()

        print("\n🎊 RÉSUMÉ OPTIMISATIONS:")
        print("=" * 25)
        for strategy, result in results.items():
            print(f"  {strategy.upper()}:")
            print(f"    📈 Rendement: {result['expected_return']*100:.2f}%")
            print(f"    📊 Volatilité: {result['volatility']*100:.2f}%")
            print(f"    🎯 Sharpe: {result['sharpe_ratio']:.3f}")

        print("\n✅ Optimisation portefeuille terminée")

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
