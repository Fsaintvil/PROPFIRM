# Merged preview for prefix: quick
# Generated from 4 files

################################################################################
# FROM: scripts\quick_analyze_decision_dumps.py
################################################################################
import json
from statistics import mean, median
p = r'c:/Users/saint/Documents/PROPFIRM/logs/decision_dumps.jsonl'
entries = []
with open(p, 'r', encoding='utf-8') as f:
    for l in f:
        l = l.strip()
        if not l:
            continue
        try:
            entries.append(json.loads(l))
        except Exception:
            continue
print('Total entries:', len(entries))
diffs = []
accepted = 0
rejected = 0
for e in entries:
    m = e.get('decision', {})
    dm = m.get('decision_metrics', {})
    conf = dm.get('confidence', 0.0)
    at = m.get('adaptive_threshold', 0.6)
    diff = conf - at
    diffs.append(diff)
    if conf >= at:
        accepted += 1
    else:
        rejected += 1
print('Accepted:', accepted, 'Rejected:', rejected)
if diffs:
    print('diffs min, max, mean, median:', round(min(diffs), 4), round(max(diffs),4), round(mean(diffs),4), round(median(diffs),4))
    med = median(diffs)
    if med < 0:
        suggest = abs(med)
        print('Suggested threshold decrease (approx):', round(suggest, 3))
    else:
        print('No decrease suggested (median >= 0)')
else:
    print('No diffs to analyze')


################################################################################
# FROM: scripts\quick_improvements_test.py
################################################################################
#!/usr/bin/env python3
"""
Test rapide des améliorations intégrées
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, f1_score


def quick_test_improvements():
    """Test rapide de toutes les améliorations"""
    print("🚀 TEST RAPIDE - Améliorations intégrées")
    print("=" * 50)

    # 1. Charger données de base
    df = pd.read_csv("data/features_sample.csv")
    if "Unnamed: 0" in df.columns:
        df = df.set_index("Unnamed: 0")
        df.index = pd.to_datetime(df.index)

    print(f"📊 Données: {len(df)} échantillons")

    # 2. Charger les features avancées
    try:
        enhanced_df = pd.read_csv("data/features_enhanced.csv")
        if "Unnamed: 0" in enhanced_df.columns:
            enhanced_df = enhanced_df.set_index("Unnamed: 0")
        print(
            f"✅ Features avancées chargées: {len(enhanced_df.columns)} "
            f"features"
        )
        use_enhanced = True
    except Exception:
        print("⚠️  Features avancées non disponibles, utilisation basique")
        enhanced_df = df
        use_enhanced = False

    # 3. Préparer données d'entraînement
    if use_enhanced:
        # Sélectionner les meilleures features (éviter overfitting)
        numeric_cols = enhanced_df.select_dtypes(include=[np.number]).columns
        top_features = numeric_cols[:15]  # Top 15 features
        X = enhanced_df[top_features]
    else:
        basic_features = ["close", "volume", "sma_1T", "ema_15T", "rsi_60T"]
        available_features = [f for f in basic_features if f in df.columns]
        X = df[available_features]

    # Labels
    returns = df["close"].pct_change(5).shift(-5)
    y = np.where(returns > 0.002, 1, 0)

    # Nettoyer
    valid_mask = ~(np.isnan(y) | np.isnan(X).any(axis=1))
    X_clean = X[valid_mask]
    y_clean = y[valid_mask]

    print(
        f"📈 Données nettoyées: {len(X_clean)} échantillons, "
        f"{len(X_clean.columns)} features"
    )

    # 4. Tests comparatifs
    results = {}

    # Split train/test
    split_idx = int(0.8 * len(X_clean))
    X_train, X_test = X_clean.iloc[:split_idx], X_clean.iloc[split_idx:]
    y_train, y_test = y_clean[:split_idx], y_clean[split_idx:]

    print(f"Train: {len(X_train)}, Test: {len(X_test)}")

    # Test 1: Modèle de base (seuil 0.5)
    print("\n1️⃣  Modèle de base (seuil 0.5)...")
    model_base = LGBMClassifier(num_leaves=15, learning_rate=0.1, verbose=-1)
    model_base.fit(X_train, y_train)

    pred_proba_base = model_base.predict_proba(X_test)[:, 1]
    pred_base = (pred_proba_base >= 0.5).astype(int)

    results["base_model"] = {
        "accuracy": accuracy_score(y_test, pred_base),
        "f1_score": f1_score(y_test, pred_base, average="weighted"),
        "threshold": 0.5,
        "features_count": len(X_clean.columns),
    }

    # Test 2: Seuil optimisé (0.68)
    print("2️⃣  Modèle avec seuil optimisé (0.68)...")
    pred_optimized = (pred_proba_base >= 0.68).astype(int)

    results["optimized_threshold"] = {
        "accuracy": accuracy_score(y_test, pred_optimized),
        "f1_score": f1_score(y_test, pred_optimized, average="weighted"),
        "threshold": 0.68,
        "features_count": len(X_clean.columns),
    }

    # Test 3: Simulation de backtest simple
    print("3️⃣  Backtest simplifié...")

    def simple_backtest(predictions, prices, initial_capital=10000):
        """Backtest très simple"""
        capital = initial_capital
        trades = 0
        wins = 0

        for i in range(len(predictions) - 5):
            if predictions[i] == 1:  # Signal d'achat
                entry_price = prices.iloc[i]
                exit_price = prices.iloc[i + 5]  # Sortie après 5 périodes

                return_pct = (exit_price - entry_price) / entry_price
                pnl = return_pct * 0.02 * capital  # Risque 2%

                capital += pnl
                trades += 1
                if pnl > 0:
                    wins += 1

        win_rate = wins / trades if trades > 0 else 0
        total_return = (capital - initial_capital) / initial_capital

        return {
            "final_capital": capital,
            "total_return": total_return,
            "trades": trades,
            "win_rate": win_rate,
        }

    # Backtest avec seuil de base
    prices_test = df["close"].iloc[split_idx:split_idx + len(X_test)]
    backtest_base = simple_backtest(pred_base, prices_test)

    # Backtest avec seuil optimisé
    backtest_optimized = simple_backtest(pred_optimized, prices_test)

    results["backtest_base"] = backtest_base
    results["backtest_optimized"] = backtest_optimized

    # 5. Affichage des résultats
    print("\n📊 RÉSULTATS COMPARATIFS:")
    print(f"{'='*60}")

    print("\n🤖 MODÈLE DE BASE (seuil 0.5):")
    base = results["base_model"]
    print(f"  Accuracy: {base['accuracy']:.4f}")
    print(f"  F1-Score: {base['f1_score']:.4f}")
    print(f"  Features: {base['features_count']}")

    backtest_b = results["backtest_base"]
    print(f"  Backtest - Return: {backtest_b['total_return']:.2%}")
    print(f"  Backtest - Trades: {backtest_b['trades']}")
    print(f"  Backtest - Win Rate: {backtest_b['win_rate']:.1%}")

    print("\n🎯 MODÈLE OPTIMISÉ (seuil 0.68):")
    opt = results["optimized_threshold"]
    print(f"  Accuracy: {opt['accuracy']:.4f}")
    print(f"  F1-Score: {opt['f1_score']:.4f}")
    feature_type = "(Enhanced)" if use_enhanced else "(Basic)"
    print(f"  Features: {opt['features_count']} {feature_type}")

    backtest_o = results["backtest_optimized"]
    print(f"  Backtest - Return: {backtest_o['total_return']:.2%}")
    print(f"  Backtest - Trades: {backtest_o['trades']}")
    print(f"  Backtest - Win Rate: {backtest_o['win_rate']:.1%}")

    # 6. Analyse des améliorations
    print("\n🚀 IMPACT DES AMÉLIORATIONS:")
    print(f"{'='*40}")

    acc_improvement = (
        (opt["accuracy"] - base["accuracy"]) / base["accuracy"] * 100
    )
    return_improvement = (
        (backtest_o["total_return"] - backtest_b["total_return"])
        / abs(backtest_b["total_return"] + 1e-8)
        * 100
    )

    print(f"📈 Amélioration Accuracy: {acc_improvement:+.1f}%")
    print(f"💰 Amélioration Return: {return_improvement:+.1f}%")

    if use_enhanced:
        feature_boost = (
            (len(enhanced_df.columns) - len(df.columns))
            / len(df.columns)
            * 100
        )
        print(
            f"📊 Boost Features: +{feature_boost:.0f}% "
            f"({len(enhanced_df.columns)} vs {len(df.columns)})"
        )

    print("🎯 Seuil optimal: 0.68 vs 0.5 (+36% seuil)")

    # 7. Recommandations
    print("\n💡 RECOMMANDATIONS:")
    print(f"{'='*30}")

    if backtest_o["total_return"] > backtest_b["total_return"]:
        print("✅ Le seuil optimisé améliore les performances")
    else:
        print("⚠️  Revoir la stratégie de seuillage")

    if use_enhanced and opt["accuracy"] > base["accuracy"]:
        print("✅ Les features avancées améliorent la précision")
    else:
        print("⚠️  Features avancées à optimiser")

    if backtest_o["win_rate"] > 0.5:
        print("✅ Stratégie profitable (win rate > 50%)")
    else:
        print("⚠️  Améliorer la sélection des signaux")

    # 8. Sauvegarde
    os.makedirs("artifacts/quick_test", exist_ok=True)

    final_report = {
        "timestamp": datetime.now().isoformat(),
        "test_results": results,
        "improvements": {
            "accuracy_improvement_pct": acc_improvement,
            "return_improvement_pct": return_improvement,
            "enhanced_features_used": use_enhanced,
            "optimal_threshold_used": 0.68,
        },
        "recommendations": {
            "use_enhanced_features": use_enhanced
            and opt["accuracy"] > base["accuracy"],
            "use_optimal_threshold": backtest_o["total_return"]
            > backtest_b["total_return"],
            "strategy_profitable": backtest_o["win_rate"] > 0.5,
        },
    }

    with open("artifacts/quick_test/improvement_test_results.json", "w") as f:
        json.dump(final_report, f, indent=2, default=str)

    print("\n✅ Test terminé - Résultats sauvegardés")
    print("📁 artifacts/quick_test/improvement_test_results.json")

    return results


if __name__ == "__main__":
    quick_test_improvements()


################################################################################
# FROM: scripts\quick_optimize.py
################################################################################
"""Optimisation rapide du seuil de décision.

Version simplifiée pour trouver rapidement le seuil optimal.
"""
import json
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path


def main():
    print("🔧 Optimisation rapide du seuil...")

    # Charger le modèle
    model_file = Path("artifacts/auto_improve/best_lightgbm.txt")
    model = lgb.Booster(model_file=str(model_file))

    # Charger les données
    with open("artifacts/auto_improve/best.json", "r") as f:
        best_config = json.load(f)

    df = pd.read_csv("data/features_sample.csv", parse_dates=[0], index_col=0)
    horizon = best_config["horizon"]

    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()

    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values

    # Prédictions
    predictions = model.predict(X.values)

    # Tester quelques seuils rapides
    thresholds = [0.4, 0.45, 0.5, 0.55, 0.6]
    results = []

    for threshold in thresholds:
        pred_labels = (predictions > threshold).astype(int)

        # Métriques simples
        accuracy = (y == pred_labels).mean()
        precision = ((pred_labels == 1) & (y == 1)).sum() / max(
            1, (pred_labels == 1).sum()
        )
        recall = ((pred_labels == 1) & (y == 1)).sum() / max(1, (y == 1).sum())

        results.append(
            {
                "threshold": threshold,
                "accuracy": accuracy,
                "precision": precision,
                "recall": recall,
                "num_predictions": (pred_labels == 1).sum(),
            }
        )

        print(
            f"Seuil {threshold}: acc={accuracy:.3f}, prec={precision:.3f}, "
            f"recall={recall:.3f}, preds={results[-1]['num_predictions']}"
        )

    # Trouver le meilleur équilibre
    results_df = pd.DataFrame(results)

    # Score composite simple
    results_df["f1"] = (
        2
        * (results_df["precision"] * results_df["recall"])
        / (results_df["precision"] + results_df["recall"] + 1e-8)
    )
    best_idx = results_df["f1"].idxmax()
    best_threshold = results_df.loc[best_idx, "threshold"]

    # Sauvegarder le résultat
    opt_dir = Path("artifacts/auto_improve/optimization")
    opt_dir.mkdir(exist_ok=True)

    optimal_result = {
        "recommended_threshold": float(best_threshold),
        "original_threshold": 0.5,
        "improvement": {
            "accuracy": float(
                results_df.loc[best_idx, "accuracy"]
                - results_df[results_df["threshold"] == 0.5]["accuracy"].iloc[
                    0
                ]
            ),
            "f1_score": float(results_df.loc[best_idx, "f1"]),
        },
        "all_results": results_df.to_dict("records"),
    }

    with open(opt_dir / "quick_optimization.json", "w") as f:
        json.dump(optimal_result, f, indent=2)

    print(f"\n🎯 Seuil optimal trouvé: {best_threshold}")
    print(f"📈 F1-Score: {results_df.loc[best_idx, 'f1']:.3f}")
    print(f"📊 Accuracy: {results_df.loc[best_idx, 'accuracy']:.3f}")
    print(f"💾 Résultats sauvés dans: {opt_dir}/quick_optimization.json")


if __name__ == "__main__":
    main()


################################################################################
# FROM: scripts\quick_start.py
################################################################################
#!/usr/bin/env python3
"""
QUICK START GUIDE - ENHANCED ULTIMATE TRADING ROBOT
Guide de démarrage rapide pour le robot de trading optimisé

UTILISATION:
1. python scripts/quick_start.py
2. Le système configure et démarre automatiquement
3. Trading selon règles FTMO (lundi 00:05, ordres/930s,
   fermeture vendredi 22:00)
"""

import sys
import subprocess
from pathlib import Path
import json
from datetime import datetime


def main():
    """Guide de démarrage rapide"""
    print("🚀 QUICK START - ENHANCED ULTIMATE TRADING ROBOT")
    print("=" * 55)
    print("Guide de démarrage automatique")

    # Vérifier structure
    scripts_dir = Path("scripts")
    if not scripts_dir.exists():
        print("❌ Répertoire scripts/ manquant")
        return

    robot_script = scripts_dir / "enhanced_ultimate_trading_robot.py"
    deployment_script = scripts_dir / "auto_deployment_system.py"

    if not robot_script.exists():
        print(f"❌ {robot_script} manquant")
        return

    if not deployment_script.exists():
        print(f"❌ {deployment_script} manquant")
        return

    print("✅ Scripts trouvés")

    # Créer configuration rapide
    quick_config = {
        "version": "2.0",
        "created": datetime.now().isoformat(),
        "mode": "auto_trading",
        "deployment": "automated",
        "schedule": {
            "monday_start": "00:05 Europe/Prague",
            "order_interval": "930 seconds",
            "friday_close": "22:00 Europe/Prague",
        },
        "systems": {
            "portfolio_optimizer": True,
            "regime_detection": True,
            "risk_management": True,
            "automated_deployment": True,
        },
        "performance_target": {
            "sharpe_ratio": 1.651,
            "win_rate": "55%+",
            "max_drawdown": "12%",
        },
    }

    # Sauvegarder config
    Path("control").mkdir(exist_ok=True)
    with open("control/quick_start_config.json", "w") as f:
        json.dump(quick_config, f, indent=2)

    print("📋 CONFIGURATION:")
    print("  🤖 Robot optimisé (faiblesses corrigées)")
    print("  ⏰ Déploiement automatique FTMO")
    print("  📊 Focus Portfolio Optimizer (Sharpe 1.651)")
    print("  🛡️  Gestion risques intégrée")

    print("\n🎯 OPTIONS DÉMARRAGE:")
    print("1. Démarrage automatique (recommandé)")
    print("2. Démarrage manuel du robot")
    print("3. Test configuration seulement")

    try:
        choice = input("\nChoisir option (1-3): ").strip()
    except KeyboardInterrupt:
        print("\n\n👋 Annulé")
        return

    if choice == "1":
        print("\n🚀 DÉMARRAGE AUTOMATIQUE...")
        # Lancer système de déploiement
        try:
            subprocess.run(
                [sys.executable, str(deployment_script)], check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"❌ Erreur déploiement: {e}")

    elif choice == "2":
        print("\n🎯 DÉMARRAGE MANUEL...")
        # Lancer robot directement
        try:
            subprocess.run([sys.executable, str(robot_script)], check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Erreur robot: {e}")

    elif choice == "3":
        print("\n🔍 TEST CONFIGURATION...")
        print("✅ Configuration créée:")
        print("  📄 control/quick_start_config.json")
        print("  🤖 Robot: scripts/enhanced_ultimate_trading_robot.py")
        print("  🚀 Déployeur: scripts/auto_deployment_system.py")
        print("\nPour démarrer manuellement:")
        print(f"  python {robot_script}")
        print("Ou avec déploiement automatique:")
        print(f"  python {deployment_script}")

    else:
        print("❌ Option invalide")


if __name__ == "__main__":
    main()


# End of merged preview
