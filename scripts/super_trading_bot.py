#!/usr/bin/env python3
"""
Script intégré utilisant toutes les améliorations du robot de trading:

1. Features avancées (50 features vs 5)
2. Ensemble de modèles (LightGBM + XGBoost + CatBoost)
3. Seuil optimal (0.68)
4. Gestion dynamique du risque
5. Backtesting avancé
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import pickle

# Imports des modules créés
from scripts.ensemble_models import (
    create_ensemble_models,
        create_adaptive_ensemble,
            )
from scripts.dynamic_risk_management import DynamicRiskManager
from scripts.simple_enhanced_features import create_enhanced_features

import warnings

warnings.filterwarnings("ignore")


class SuperTradingBot:
    """Bot de trading intégré avec toutes les améliorations"""

    def __init__(
        self,
            initial_capital=10000,
                optimal_threshold=0.68,
                use_enhanced_features=True,
                use_ensemble=True,
                use_dynamic_risk=True,
                ):
        """
        Args:
            initial_capital: Capital de départ
            optimal_threshold: Seuil optimal trouvé (0.68)
            use_enhanced_features: Utiliser les 50 features avancées
            use_ensemble: Utiliser l'ensemble de modèles
            use_dynamic_risk: Utiliser la gestion dynamique du risque
        """
        self.initial_capital = initial_capital
        self.optimal_threshold = optimal_threshold
        self.use_enhanced_features = use_enhanced_features
        self.use_ensemble = use_ensemble
        self.use_dynamic_risk = use_dynamic_risk

        # Composants
        self.ensemble_model = None
        self.risk_manager = None
        self.feature_columns = None

        # Métriques
        self.training_metrics = {}
        self.backtest_results = {}

        print("🤖 SuperTradingBot initialisé")
        print(f"  💰 Capital: ${initial_capital:,}")
        print(f"  🎯 Seuil optimal: {optimal_threshold}")
        enhanced_status = "✅" if use_enhanced_features else "❌"
        print(f"  📊 Features avancées: {enhanced_status}")
        print(f"  🤝 Ensemble: {'✅' if use_ensemble else '❌'}")
        print(f"  ⚖️  Gestion risque: {'✅' if use_dynamic_risk else '❌'}")

    def prepare_features(self, df):
        """Préparer les features selon la configuration"""
        if self.use_enhanced_features:
            print("📊 Utilisation des features avancées (50 features)...")
            enhanced_df = create_enhanced_features(df)

            # Sélectionner les meilleures features pour éviter overfitting
            feature_importance = self._calculate_feature_importance(
                enhanced_df
            )
            top_features = feature_importance.head(20).index.tolist()  # Top 20

            return enhanced_df[top_features]
        else:
            print("📊 Utilisation des features basiques (5 features)...")
            basic_features = [
                "close",
                    "volume",
                        "sma_1T",
                        "ema_15T",
                        "rsi_60T",
                        ]
            return df[basic_features]

    def _calculate_feature_importance(self, df):
        """Calculer l'importance des features avec un modèle simple"""
        try:
            from lightgbm import LGBMClassifier

            # Préparer les labels
            returns = df["close"].pct_change(5).shift(-5)
            y = np.where(returns > 0.002, 1, 0)

            # Features numériques seulement
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            X = df[numeric_cols]

            # Nettoyer
            valid_mask = ~(np.isnan(y) | np.isnan(X).any(axis=1))
            X_clean = X[valid_mask]
            y_clean = y[valid_mask]

            # Modèle rapide pour importance
            lgb = LGBMClassifier(num_leaves=10, n_estimators=50, verbose=-1)
            lgb.fit(X_clean, y_clean)

            # Importance des features
            importance = pd.Series(
                lgb.feature_importances_, index=X_clean.columns
            )
            return importance.sort_values(ascending=False)

        except Exception as e:
            print(f"Erreur calcul importance: {e}")
            # Fallback: retourner les features de base
            basic_features = [
                "close",
                    "volume",
                        "sma_1T",
                        "ema_15T",
                        "rsi_60T",
                        ]
            available_features = [f for f in basic_features if f in df.columns]
            return pd.Series(1.0, index=available_features)

    def create_labels(self, df, horizon=5, threshold=0.002):
        """Créer les labels pour l'entraînement"""
        returns = df["close"].pct_change(horizon).shift(-horizon)
        labels = np.where(returns > threshold, 1, 0)
        return labels

    def train_model(self, df):
        """Entraîner le modèle selon la configuration"""
        print("🏋️  Entraînement du modèle...")

        # Préparer features et labels
        X = self.prepare_features(df)
        y = self.create_labels(df)

        # Nettoyer les données
        valid_mask = ~(np.isnan(y) | np.isnan(X).any(axis=1))
        X_clean = X[valid_mask]
        y_clean = y[valid_mask]

        if len(X_clean) < 50:
            raise ValueError("Pas assez de données pour l'entraînement")

        print(f"  📈 Données d'entraînement: {len(X_clean)} échantillons")
        print(f"  📊 Features utilisées: {len(X_clean.columns)}")

        # Sauvegarder les colonnes de features
        self.feature_columns = X_clean.columns.tolist()

        if self.use_ensemble:
            print("  🤝 Entraînement de l'ensemble de modèles...")

            # Créer les modèles
            models = create_ensemble_models()

            # Créer l'ensemble pondéré
            self.ensemble_model, performances = create_adaptive_ensemble(
                X_clean, y_clean, models, method="weighted"
            )

            # Entraîner l'ensemble
            self.ensemble_model.fit(X_clean, y_clean)

            # Évaluer sur les données d'entraînement
            train_pred = self.ensemble_model.predict(X_clean)
            train_accuracy = np.mean(train_pred == y_clean)

            self.training_metrics = {
                "train_accuracy": train_accuracy,
                    "ensemble_performances": performances,
                        "feature_count": len(X_clean.columns),
                        "training_samples": len(X_clean),
                        }

            print(f"  ✅ Ensemble entraîné - Accuracy: {train_accuracy:.4f}")

        else:
            print("  🤖 Entraînement modèle simple LightGBM...")

            from lightgbm import LGBMClassifier

            self.ensemble_model = LGBMClassifier(
                num_leaves=15, learning_rate=0.1, random_state=42, verbose=-1
            )

            self.ensemble_model.fit(X_clean, y_clean)

            train_pred = self.ensemble_model.predict(X_clean)
            train_accuracy = np.mean(train_pred == y_clean)

            self.training_metrics = {
                "train_accuracy": train_accuracy,
                    "model_type": "single_lightgbm",
                        "feature_count": len(X_clean.columns),
                        "training_samples": len(X_clean),
                        }

            print(f"  ✅ LightGBM entraîné - Accuracy: {train_accuracy:.4f}")

        # Initialiser le gestionnaire de risque
        if self.use_dynamic_risk:
            self.risk_manager = DynamicRiskManager(
                initial_capital=self.initial_capital,
                    max_risk_per_trade=0.02,
                        max_portfolio_risk=0.06,
                        drawdown_threshold=0.10,
                        )
            print("  ⚖️  Gestionnaire de risque initialisé")

    def predict(self, features_row):
        """Faire une prédiction sur une ligne de features"""
        if self.ensemble_model is None:
            raise ValueError("Modèle non entraîné")

        # Assurer que les features correspondent
        if isinstance(features_row, pd.Series):
            features_row = features_row[self.feature_columns].values.reshape(
                1, -1
            )
        elif isinstance(features_row, pd.DataFrame):
            features_row = features_row[self.feature_columns].values

        # Prédiction
        if hasattr(self.ensemble_model, "predict_proba"):
            proba = self.ensemble_model.predict_proba(features_row)[0, 1]
        else:
            proba = 0.6  # Fallback

        # Appliquer le seuil optimal
        prediction = 1 if proba >= self.optimal_threshold else 0

        return {
            "prediction": prediction,
                "probability": proba,
                    "signal_strength": proba,
                    "threshold_used": self.optimal_threshold,
                    }

    def calculate_position_size(
        self, signal_result, current_price, stop_loss_price=None
    ):
        """Calculer la taille de position optimale"""
        if not self.use_dynamic_risk or self.risk_manager is None:
            # Taille fixe conservatrice
            return 0.01

        # Calculer la distance du stop loss
        if stop_loss_price is not None:
            sl_distance = abs(current_price - stop_loss_price) / current_price
        else:
            sl_distance = 0.02  # 2% par défaut

        # Simuler une volatilité (à remplacer par calcul réel)
        symbol_volatility = 0.20  # 20% annuel par défaut

        # Calculer la taille optimale
        sizing_result = self.risk_manager.calculate_optimal_position_size(
            signal_strength=signal_result["signal_strength"],
                symbol_volatility=symbol_volatility,
                    stop_loss_distance=sl_distance,
                    )

        return sizing_result["recommended_size"]

    def backtest_enhanced(self, df):
        """Backtesting avec toutes les améliorations"""
        print("🔄 Backtesting avec améliorations...")

        if self.ensemble_model is None:
            raise ValueError("Modèle non entraîné")

        # Préparer les données
        X = self.prepare_features(df)

        # Simuler le trading
        trades = []
        capital = self.initial_capital
        equity_curve = [capital]
        timestamps = []

        # Commencer après un minimum de données pour les features
        start_idx = 50

        for i in range(start_idx, len(X) - 10):  # Garder 10 points pour exit
            try:
                # Current timestamp
                current_time = df.index[i]
                timestamps.append(current_time)

                # Features à ce moment
                current_features = X.iloc[i]

                # Skip si des NaN
                if current_features.isnull().any():
                    equity_curve.append(capital)
                    continue

                # Prédiction
                pred_result = self.predict(current_features)

                if pred_result["prediction"] == 1:  # Signal d'achat
                    # Prix d'entrée
                    entry_price = df["close"].iloc[i]

                    # Calculer taille de position
                    stop_loss_price = entry_price * 0.98  # 2% SL
                    position_size = self.calculate_position_size(
                        pred_result, entry_price, stop_loss_price
                    )

                    # Simuler la sortie après 5 périodes (horizon)
                    exit_idx = min(i + 5, len(df) - 1)
                    exit_price = df["close"].iloc[exit_idx]

                    # Calculer P&L
                    price_change = (exit_price - entry_price) / entry_price
                    pnl = price_change * position_size * capital

                    # Mettre à jour le capital
                    capital += pnl

                    # Enregistrer le trade
                    trade = {
                        "entry_time": current_time,
                            "exit_time": df.index[exit_idx],
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "position_size": position_size,
                                "pnl": pnl,
                                "pnl_pct": pnl / capital,
                                "probability": pred_result["probability"],
                                "signal_strength": pred_result["signal_strength"],
                                }
                    trades.append(trade)

                    # Mettre à jour le gestionnaire de risque
                    if self.risk_manager is not None:
                        self.risk_manager.update_trade_result(
                            symbol="TEST",
                                entry_price=entry_price,
                                    exit_price=exit_price,
                                    size=position_size,
                                    direction=1,
                                    timestamp=current_time,
                                    )

                equity_curve.append(capital)

            except Exception:
                # En cas d'erreur, garder le capital actuel
                equity_curve.append(capital)
                continue

        # Calculer les métriques finales
        if trades:
            trades_df = pd.DataFrame(trades)

            # Métriques de base
            total_return = (
                capital - self.initial_capital
            ) / self.initial_capital
            n_trades = len(trades)
            winning_trades = trades_df[trades_df["pnl"] > 0]
            win_rate = len(winning_trades) / n_trades if n_trades > 0 else 0

            # Sharpe ratio
            if len(trades_df) > 1:
                returns = trades_df["pnl_pct"]
                sharpe = returns.mean() / (returns.std() + 1e-8) * np.sqrt(252)
            else:
                sharpe = 0

            # Max drawdown
            equity_series = pd.Series(equity_curve)
            peak = equity_series.cummax()
            drawdown = (equity_series - peak) / peak
            max_drawdown = drawdown.min()

            self.backtest_results = {
                "total_return": total_return,
                    "final_capital": capital,
                        "n_trades": n_trades,
                        "win_rate": win_rate,
                        "sharpe_ratio": sharpe,
                        "max_drawdown": max_drawdown,
                        "avg_trade_pnl": trades_df["pnl"].mean()
                if n_trades > 0
                else 0,
                    "best_trade": trades_df["pnl"].max() if n_trades > 0 else 0,
                        "worst_trade": trades_df["pnl"].min() if n_trades > 0 else 0,
                        "trades_details": trades,
                        }

            print(f"  📈 Retour total: {total_return:.2%}")
            print(f"  🎯 Trades: {n_trades} (Win rate: {win_rate:.1%})")
            print(f"  📊 Sharpe: {sharpe:.2f}")
            print(f"  📉 Max DD: {max_drawdown:.2%}")

        else:
            print("  ⚠️  Aucun trade généré")
            self.backtest_results = {"error": "no_trades"}

    def save_model(self, filepath):
        """Sauvegarder le modèle complet"""
        model_data = {
            "ensemble_model": self.ensemble_model,
                "risk_manager": self.risk_manager,
                    "feature_columns": self.feature_columns,
                    "optimal_threshold": self.optimal_threshold,
                    "training_metrics": self.training_metrics,
                    "backtest_results": self.backtest_results,
                    "config": {
                "use_enhanced_features": self.use_enhanced_features,
                    "use_ensemble": self.use_ensemble,
                        "use_dynamic_risk": self.use_dynamic_risk,
                        },
                        }

        with open(filepath, "wb") as f:
            pickle.dump(model_data, f)

        print(f"💾 Modèle sauvegardé: {filepath}")

    def generate_report(self):
        """Générer un rapport complet"""
        report = {
            "timestamp": datetime.now().isoformat(),
                "model_config": {
                "enhanced_features": self.use_enhanced_features,
                    "ensemble_models": self.use_ensemble,
                        "dynamic_risk": self.use_dynamic_risk,
                        "optimal_threshold": self.optimal_threshold,
                        },
                        "training_metrics": self.training_metrics,
                    "backtest_results": self.backtest_results,
                    }

        return report


def main():
    """Test complet du SuperTradingBot"""
    print("🚀 TEST COMPLET - SuperTradingBot avec toutes les améliorations")
    print("=" * 70)

    try:
        # Charger les données
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        print(f"📊 Données chargées: {len(df)} échantillons")

        # Créer le super bot avec toutes les améliorations
        super_bot = SuperTradingBot(
            initial_capital=10000,
                optimal_threshold=0.68,  # Seuil optimisé trouvé précédemment
            use_enhanced_features=True,
                use_ensemble=True,
                    use_dynamic_risk=True,
                    )

        # Entraîner
        super_bot.train_model(df)

        # Backtester
        super_bot.backtest_enhanced(df)

        # Générer le rapport
        report = super_bot.generate_report()

        # Sauvegarder
        os.makedirs("artifacts/super_bot", exist_ok=True)

        super_bot.save_model("artifacts/super_bot/super_trading_bot.pkl")

        with open("artifacts/super_bot/complete_report.json", "w") as f:
            json.dump(report, f, indent=2, default=str)

        # Affichage des résultats
        print("\n🎯 RÉSULTATS FINAUX - SuperTradingBot:")
        print(f"{'='*50}")

        if "total_return" in super_bot.backtest_results:
            results = super_bot.backtest_results

            print(f"💰 Capital initial: ${super_bot.initial_capital:,}")
            print(f"💰 Capital final: ${results['final_capital']:,.0f}")
            print(f"📈 Retour total: {results['total_return']:.2%}")
            print(f"🎯 Nombre de trades: {results['n_trades']}")
            print(f"🏆 Taux de réussite: {results['win_rate']:.1%}")
            print(f"📊 Ratio de Sharpe: {results['sharpe_ratio']:.2f}")
            print(f"📉 Max Drawdown: {results['max_drawdown']:.2%}")

            # Comparaison avec le bot de base
            print("\n📊 COMPARAISON avec le bot de base:")
            print("Bot de base:    26.85% return, Sharpe=153.99")
            print(
                f"SuperBot:       {results['total_return']:.2%} "
                f"return, Sharpe={results['sharpe_ratio']:.2f}"
            )

            if results["total_return"] > 0.2685:
                improvement = (results["total_return"] - 0.2685) / 0.2685 * 100
                print(f"🎉 Amélioration: +{improvement:.1f}% de performance")

        else:
            print("⚠️  Pas de résultats de backtest disponibles")

        print("\n✅ Test complet terminé !")
        print("📁 Artefacts sauvegardés dans artifacts/super_bot/")

        return super_bot

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()
        return None


if __name__ == "__main__":
    main()
