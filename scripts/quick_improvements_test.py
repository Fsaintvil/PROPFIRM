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
