#!/usr/bin/env python3
"""
VALIDATION SYSTÈME - Robot de Trading
Validation réelle des performances au lieu de claims non prouvés

Teste les composants sur données existantes pour obtenir métriques réelles
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime
import warnings

warnings.filterwarnings("ignore")

# Import local
try:
    from simplified_trading_robot import SimplifiedTradingRobot

    ROBOT_AVAILABLE = True
except ImportError:
    ROBOT_AVAILABLE = False


class PerformanceValidator:
    """Validateur de performance réelle du robot"""

    def __init__(self):
        self.results = {}

    def load_test_data(self):
        """Charger données de test disponibles"""
        try:
            # Chercher fichiers de données existants
            data_files = [
                "data/sample_data.csv",
                    "MT5_FTMO_IA/data/sample_data.csv",
                        ]

            for file_path in data_files:
                if Path(file_path).exists():
                    data = pd.read_csv(
                        file_path, index_col=0, parse_dates=True
                    )
                    if len(data) > 100:
                        print(
                            f"✅ Données test: {file_path} "
                            f"({len(data)} barres)"
                        )
                        return data

            # Générer données synthétiques si aucun fichier
            print("⚠️ Génération données test synthétiques")
            dates = pd.date_range(start="2025-01-01", periods=1000, freq="H")

            # Prix EURUSD réaliste avec tendance et volatilité
            np.random.seed(42)
            returns = np.random.normal(0, 0.0001, 1000)
            prices = 1.1000 + np.cumsum(returns)

            data = pd.DataFrame(
                {
                    "close": prices,
                        "high": prices + np.random.uniform(0, 0.0005, 1000),
                            "low": prices - np.random.uniform(0, 0.0005, 1000),
                            "volume": np.random.randint(100, 1000, 1000),
                            },
                            index=dates,
                        )

            return data

        except Exception as e:
            print(f"❌ Erreur chargement données: {e}")
            return None

    def validate_signal_accuracy(self, data):
        """Valider précision des signaux"""
        if not ROBOT_AVAILABLE:
            return {"accuracy": 0.0, "error": "Robot non disponible"}

        try:
            robot = SimplifiedTradingRobot()

            correct_predictions = 0
            total_predictions = 0

            # Test sur 100 barres récentes
            test_data = data.tail(200)

            for i in range(100, len(test_data) - 1):
                current_data = test_data.iloc[: i + 1]
                signal = robot.calculate_simple_signal(current_data)

                if signal["action"] in ["buy", "sell"]:
                    # Vérifier si prédiction correcte
                    current_price = test_data.iloc[i]["close"]
                    next_price = test_data.iloc[i + 1]["close"]

                    actual_direction = 1 if next_price > current_price else -1
                    predicted_direction = (
                        1 if signal["action"] == "buy" else -1
                    )

                    if actual_direction == predicted_direction:
                        correct_predictions += 1

                    total_predictions += 1

            accuracy = (
                correct_predictions / total_predictions
                if total_predictions > 0
                else 0
            )

            return {
                "accuracy": accuracy,
                    "correct_predictions": correct_predictions,
                        "total_predictions": total_predictions,
                        "sample_size": len(test_data),
                        }

        except Exception as e:
            return {"accuracy": 0.0, "error": str(e)}

    def calculate_backtest_performance(self, data):
        """Calculer performance backtest simple"""
        try:
            if not ROBOT_AVAILABLE:
                return {"sharpe": 0.0, "error": "Robot non disponible"}

            robot = SimplifiedTradingRobot()

            # Simulation backtest simple
            capital = 10000
            equity_curve = [capital]
            trades = []

            test_data = data.tail(500)  # 500 dernières barres

            for i in range(100, len(test_data) - 10):
                current_data = test_data.iloc[: i + 1]
                signal = robot.calculate_simple_signal(current_data)

                if (
                    signal["action"] in ["buy", "sell"]
                    and signal["confidence"] > 0.6
                ):
                    entry_price = test_data.iloc[i]["close"]

                    # Simuler sortie après 10 barres
                    exit_price = test_data.iloc[i + 10]["close"]

                    # Calculer P&L (1% risque par trade)
                    risk_amount = capital * 0.01

                    if signal["action"] == "buy":
                        pnl = (
                            risk_amount
                            * (exit_price - entry_price)
                            / entry_price
                        )
                    else:
                        pnl = (
                            risk_amount
                            * (entry_price - exit_price)
                            / entry_price
                        )

                    # Limiter pnl à +/- 2% du capital (SL/TP simulation)
                    pnl = max(-capital * 0.02, min(capital * 0.02, pnl))

                    capital += pnl
                    equity_curve.append(capital)

                    trades.append(
                        {
                            "action": signal["action"],
                                "entry": entry_price,
                                    "exit": exit_price,
                                    "pnl": pnl,
                                    "confidence": signal["confidence"],
                                    }
                    )

            if len(trades) == 0:
                return {"sharpe": 0.0, "total_return": 0.0, "trades": 0}

            # Calculer métriques
            total_return = (capital - 10000) / 10000
            returns = pd.Series(equity_curve).pct_change().dropna()

            if len(returns) > 1 and returns.std() > 0:
                sharpe = returns.mean() / returns.std() * np.sqrt(252)
            else:
                sharpe = 0.0

            win_trades = [t for t in trades if t["pnl"] > 0]
            win_rate = len(win_trades) / len(trades)

            return {
                "sharpe_ratio": sharpe,
                    "total_return": total_return,
                        "win_rate": win_rate,
                        "total_trades": len(trades),
                        "final_capital": capital,
                        "equity_curve": equity_curve[-10:],  # Derniers points
            }

        except Exception as e:
            return {"sharpe": 0.0, "error": str(e)}

    def validate_all_components(self):
        """Validation complète de tous les composants"""
        print("🔍 VALIDATION PERFORMANCE RÉELLE")
        print("=" * 35)

        # Charger données
        data = self.load_test_data()
        if data is None:
            print("❌ Impossible de charger données de test")
            return

        print(f"📊 Données test: {len(data)} barres")

        # 1. Validation précision signaux
        print("\n🎯 Validation précision signaux...")
        signal_results = self.validate_signal_accuracy(data)
        self.results["signal_accuracy"] = signal_results

        if "accuracy" in signal_results:
            print(f"  ✅ Précision: {signal_results['accuracy']:.1%}")
            print(f"  📊 Tests: {signal_results.get('total_predictions', 0)}")
        else:
            print(f"  ❌ Erreur: {signal_results.get('error', 'Inconnue')}")

        # 2. Validation performance backtest
        print("\n📈 Validation performance backtest...")
        backtest_results = self.calculate_backtest_performance(data)
        self.results["backtest"] = backtest_results

        if "sharpe_ratio" in backtest_results:
            sharpe = backtest_results["sharpe_ratio"]
            total_return = backtest_results.get("total_return", 0)
            win_rate = backtest_results.get("win_rate", 0)
            trades = backtest_results.get("total_trades", 0)

            print(f"  ✅ Sharpe: {sharpe:.2f}")
            print(f"  📊 Return total: {total_return:.1%}")
            print(f"  🎯 Win rate: {win_rate:.1%}")
            print(f"  📈 Trades: {trades}")
        else:
            print(f"  ❌ Erreur: {backtest_results.get('error', 'Inconnue')}")

        # 3. Comparaison avec métriques claims
        print("\n⚖️ COMPARAISON CLAIMS vs RÉALITÉ:")
        print("=" * 35)

        if ROBOT_AVAILABLE:
            robot = SimplifiedTradingRobot()

            print("MÉTRIQUES CLAIMS vs MESURÉES:")

            # Sharpe
            claimed_sharpe = robot.real_metrics["avg_sharpe"]
            measured_sharpe = backtest_results.get("sharpe_ratio", 0)
            print(
                f"  Sharpe: {claimed_sharpe:.2f} (claim) vs "
                f"{measured_sharpe:.2f} (réel)"
            )

            # Accuracy
            claimed_accuracy = robot.real_metrics["accuracy"]
            measured_accuracy = signal_results.get("accuracy", 0)
            print(
                f"  Accuracy: {claimed_accuracy:.1%} (claim) vs "
                f"{measured_accuracy:.1%} (réel)"
            )

            # Win rate
            claimed_win_rate = robot.real_metrics["win_rate"]
            measured_win_rate = backtest_results.get("win_rate", 0)
            print(
                f"  Win rate: {claimed_win_rate:.1%} (claim) vs "
                f"{measured_win_rate:.1%} (réel)"
            )

            # Validation
            sharpe_valid = abs(claimed_sharpe - measured_sharpe) < 0.3
            accuracy_valid = abs(claimed_accuracy - measured_accuracy) < 0.1

            sharpe_status = '✅ OK' if sharpe_valid else '❌ ÉCART'
            accuracy_status = '✅ OK' if accuracy_valid else '❌ ÉCART'

            print(f"\n✅ Validation Sharpe: {sharpe_status}")
            print(f"✅ Validation Accuracy: {accuracy_status}")

        # Sauvegarder résultats
        self.save_validation_results()

        print("\n🎊 VALIDATION TERMINÉE")
        return self.results

    def save_validation_results(self):
        """Sauvegarder résultats validation"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        validation_report = {
            "timestamp": datetime.now().isoformat(),
                "validation_type": "real_performance_vs_claims",
                    "results": self.results,
                    "summary": {
                "signal_accuracy": self.results.get("signal_accuracy", {}).get(
                    "accuracy", 0
                ),
                    "backtest_sharpe": self.results.get("backtest", {}).get(
                        "sharpe_ratio", 0
                ),
                    "backtest_win_rate": self.results.get("backtest", {}).get(
                        "win_rate", 0
                ),
                    },
                        }

        # Sauvegarder
        report_dir = Path("artifacts/validation")
        report_dir.mkdir(parents=True, exist_ok=True)

        report_file = report_dir / f"validation_report_{timestamp}.json"
        with open(report_file, "w") as f:
            json.dump(validation_report, f, indent=2)

        print(f"📄 Rapport sauvé: {report_file}")


def main():
    """Lancer validation performance"""
    validator = PerformanceValidator()
    results = validator.validate_all_components()

    if results:
        print("\n🎯 RÉSUMÉ VALIDATION:")
        print("=" * 20)

        signal_acc = results.get("signal_accuracy", {}).get("accuracy", 0)
        backtest_sharpe = results.get("backtest", {}).get("sharpe_ratio", 0)
        backtest_return = results.get("backtest", {}).get("total_return", 0)

        print(f"Signal Accuracy: {signal_acc:.1%}")
        print(f"Backtest Sharpe: {backtest_sharpe:.2f}")
        print(f"Backtest Return: {backtest_return:.1%}")

        # Évaluation globale
        if signal_acc > 0.5 and backtest_sharpe > 0.5:
            print("\n✅ VALIDATION: Performance acceptable")
        elif signal_acc > 0.45 or backtest_sharpe > 0.3:
            print("\n⚠️ VALIDATION: Performance moyenne")
        else:
            print("\n❌ VALIDATION: Performance insuffisante")


if __name__ == "__main__":
    main()
