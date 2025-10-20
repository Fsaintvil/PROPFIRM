"""Optimisation du seuil de décision pour maximiser les métriques financières.

Ce script teste différents seuils de décision et sélectionne celui qui optimise
le ratio de Sharpe ou le rendement ajusté au risque plutôt que l'accuracy.
"""
from __future__ import annotations

import json
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path


def load_best_model():
    """Charge le meilleur modèle et les données"""
    model_file = Path("artifacts/auto_improve/best_lightgbm.txt")
    if not model_file.exists():
        raise FileNotFoundError("Model file not found.")

    model = lgb.Booster(model_file=str(model_file))

    # Charger la config pour le horizon
    best_file = Path("artifacts/auto_improve/best.json")
    with open(best_file, "r", encoding="utf-8") as f:
        best_config = json.load(f)

    # Recharger les données
    data_path = Path("data/features_sample.csv")
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)

    horizon = best_config["horizon"]
    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()

    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values

    return model, X, y, df, best_config


def simulate_trading(predictions, y, df, threshold=0.5):
    """Simule le trading avec un seuil donné et retourne les métriques"""
    signals = (predictions > threshold).astype(int)

    # Calculer les returns tick par tick
    returns = []
    positions = []

    for i in range(len(signals)):
        if signals[i] == 1:  # Signal d'achat
            # Calculer le return jusqu'au prochain signal ou fin
            if i + 1 < len(df):
                ret = (
                    df.iloc[i + 1]["close"] - df.iloc[i]["close"]
                ) / df.iloc[i]["close"]
                returns.append(ret)
                positions.append(1)
            else:
                returns.append(0)
                positions.append(0)
        else:
            returns.append(0)
            positions.append(0)

    returns = np.array(returns)

    # Métriques financières
    total_return = np.sum(returns)
    num_trades = np.sum(np.array(positions) == 1)

    if num_trades == 0:
        return {
            "threshold": threshold,
                "total_return": 0,
                    "sharpe": 0,
                    "max_drawdown": 0,
                    "win_rate": 0,
                    "num_trades": 0,
                    "avg_return": 0,
                    }

    win_rate = np.sum(returns > 0) / num_trades if num_trades > 0 else 0
    avg_return = np.mean(returns[returns != 0]) if num_trades > 0 else 0

    # Sharpe ratio (annualisé approximatif)
    if np.std(returns) > 0:
        sharpe = (
            np.mean(returns) / np.std(returns) * np.sqrt(252 * 24)
        )  # Approximation
    else:
        sharpe = 0

    # Max drawdown approximatif
    cumulative = np.cumsum(returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    max_drawdown = np.min(drawdown) if len(drawdown) > 0 else 0

    return {
        "threshold": threshold,
            "total_return": total_return,
                "sharpe": sharpe,
                "max_drawdown": max_drawdown,
                "win_rate": win_rate,
                "num_trades": num_trades,
                "avg_return": avg_return,
                }


def optimize_threshold(model, X, y, df):
    """Optimise le seuil de décision"""
    print("Optimisation du seuil de décision...")

    predictions = model.predict(X.values)

    # Tester différents seuils
    thresholds = np.arange(0.3, 0.8, 0.02)
    results = []

    for threshold in thresholds:
        metrics = simulate_trading(predictions, y, df, threshold)
        results.append(metrics)
        print(
            f"Seuil {threshold:.2f}: Return={metrics['total_return']:.4f}, "
            f"Sharpe={metrics['sharpe']:.2f}, Trades={metrics['num_trades']}"
        )

    results_df = pd.DataFrame(results)

    # Sauvegarder les résultats
    opt_dir = Path("artifacts/auto_improve/optimization")
    opt_dir.mkdir(exist_ok=True)
    results_df.to_csv(opt_dir / "threshold_optimization.csv", index=False)

    return results_df


def plot_optimization_results(results_df):
    """Génère les graphiques d'optimisation"""
    opt_dir = Path("artifacts/auto_improve/optimization")
    opt_dir.mkdir(exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))

    # Return total vs seuil
    axes[0, 0].plot(results_df["threshold"], results_df["total_return"], "b-o")
    axes[0, 0].set_xlabel("Seuil de décision")
    axes[0, 0].set_ylabel("Rendement total")
    axes[0, 0].set_title("Rendement total vs Seuil")
    axes[0, 0].grid(True, alpha=0.3)

    # Sharpe vs seuil
    axes[0, 1].plot(results_df["threshold"], results_df["sharpe"], "r-o")
    axes[0, 1].set_xlabel("Seuil de décision")
    axes[0, 1].set_ylabel("Ratio de Sharpe")
    axes[0, 1].set_title("Sharpe vs Seuil")
    axes[0, 1].grid(True, alpha=0.3)

    # Win rate vs seuil
    axes[1, 0].plot(results_df["threshold"], results_df["win_rate"], "g-o")
    axes[1, 0].set_xlabel("Seuil de décision")
    axes[1, 0].set_ylabel("Taux de réussite")
    axes[1, 0].set_title("Win Rate vs Seuil")
    axes[1, 0].grid(True, alpha=0.3)

    # Nombre de trades vs seuil
    axes[1, 1].plot(results_df["threshold"], results_df["num_trades"], "m-o")
    axes[1, 1].set_xlabel("Seuil de décision")
    axes[1, 1].set_ylabel("Nombre de trades")
    axes[1, 1].set_title("Nombre de Trades vs Seuil")
    axes[1, 1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(
        opt_dir / "threshold_optimization.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

    print("✅ Graphiques d'optimisation sauvegardés")


def find_optimal_thresholds(results_df):
    """Trouve les seuils optimaux selon différents critères"""
    # Filtrer les résultats avec au moins quelques trades
    valid_results = results_df[results_df["num_trades"] >= 5].copy()

    if len(valid_results) == 0:
        print("⚠️ Pas assez de trades pour optimiser")
        return None

    optimal_thresholds = {}

    # Meilleur return total
    best_return_idx = valid_results["total_return"].idxmax()
    optimal_thresholds["best_return"] = {
        "threshold": valid_results.loc[best_return_idx, "threshold"],
            "metrics": valid_results.loc[best_return_idx].to_dict(),
                }

    # Meilleur Sharpe
    best_sharpe_idx = valid_results["sharpe"].idxmax()
    optimal_thresholds["best_sharpe"] = {
        "threshold": valid_results.loc[best_sharpe_idx, "threshold"],
            "metrics": valid_results.loc[best_sharpe_idx].to_dict(),
                }

    # Meilleur win rate
    best_wr_idx = valid_results["win_rate"].idxmax()
    optimal_thresholds["best_winrate"] = {
        "threshold": valid_results.loc[best_wr_idx, "threshold"],
            "metrics": valid_results.loc[best_wr_idx].to_dict(),
                }

    # Score composite (return / |max_drawdown| + sharpe)
    valid_results["composite_score"] = (
        valid_results["total_return"]
        / (abs(valid_results["max_drawdown"]) + 0.01)
        + valid_results["sharpe"] * 0.1
    )
    best_composite_idx = valid_results["composite_score"].idxmax()
    optimal_thresholds["best_composite"] = {
        "threshold": valid_results.loc[best_composite_idx, "threshold"],
            "metrics": valid_results.loc[best_composite_idx].to_dict(),
                }

    return optimal_thresholds


def generate_optimization_report(optimal_thresholds, best_config):
    """Génère le rapport d'optimisation"""
    if optimal_thresholds is None:
        return

    report_content = f"""# Rapport d'optimisation du seuil de décision

## Configuration de base
- Horizon: {best_config['horizon']}
- Modèle: LightGBM (num_leaves={best_config['params']['num_leaves']}, lr={best_config['params']['learning_rate']})
- Seuil par défaut: 0.5

## Seuils optimaux trouvés

### 🎯 Meilleur rendement total
- **Seuil optimal:** {optimal_thresholds['best_return']['threshold']:.3f}
- Rendement total: {optimal_thresholds['best_return']['metrics']['total_return']:.4f} ({optimal_thresholds['best_return']['metrics']['total_return']*100:.2f}%)
- Sharpe: {optimal_thresholds['best_return']['metrics']['sharpe']:.2f}
- Win rate: {optimal_thresholds['best_return']['metrics']['win_rate']:.3f} ({optimal_thresholds['best_return']['metrics']['win_rate']*100:.1f}%)
- Nombre de trades: {optimal_thresholds['best_return']['metrics']['num_trades']}

### 📈 Meilleur ratio de Sharpe
- **Seuil optimal:** {optimal_thresholds['best_sharpe']['threshold']:.3f}
- Rendement total: {optimal_thresholds['best_sharpe']['metrics']['total_return']:.4f} ({optimal_thresholds['best_sharpe']['metrics']['total_return']*100:.2f}%)
- Sharpe: {optimal_thresholds['best_sharpe']['metrics']['sharpe']:.2f}
- Win rate: {optimal_thresholds['best_sharpe']['metrics']['win_rate']:.3f} ({optimal_thresholds['best_sharpe']['metrics']['win_rate']*100:.1f}%)
- Nombre de trades: {optimal_thresholds['best_sharpe']['metrics']['num_trades']}

### 🏆 Meilleur taux de réussite
- **Seuil optimal:** {optimal_thresholds['best_winrate']['threshold']:.3f}
- Rendement total: {optimal_thresholds['best_winrate']['metrics']['total_return']:.4f} ({optimal_thresholds['best_winrate']['metrics']['total_return']*100:.2f}%)
- Sharpe: {optimal_thresholds['best_winrate']['metrics']['sharpe']:.2f}
- Win rate: {optimal_thresholds['best_winrate']['metrics']['win_rate']:.3f} ({optimal_thresholds['best_winrate']['metrics']['win_rate']*100:.1f}%)
- Nombre de trades: {optimal_thresholds['best_winrate']['metrics']['num_trades']}

### ⭐ Score composite (recommandé)
- **Seuil optimal:** {optimal_thresholds['best_composite']['threshold']:.3f}
- Rendement total: {optimal_thresholds['best_composite']['metrics']['total_return']:.4f} ({optimal_thresholds['best_composite']['metrics']['total_return']*100:.2f}%)
- Sharpe: {optimal_thresholds['best_composite']['metrics']['sharpe']:.2f}
- Win rate: {optimal_thresholds['best_composite']['metrics']['win_rate']:.3f} ({optimal_thresholds['best_composite']['metrics']['win_rate']*100:.1f}%)
- Nombre de trades: {optimal_thresholds['best_composite']['metrics']['num_trades']}

## Recommandations

**Seuil recommandé pour la production:** {optimal_thresholds['best_composite']['threshold']:.3f}

Ce seuil offre le meilleur équilibre entre rendement, gestion du risque et nombre de trades.

## Fichiers générés
- `artifacts/auto_improve/optimization/threshold_optimization.csv`
- `artifacts/auto_improve/optimization/threshold_optimization.png`
- `artifacts/auto_improve/optimization/optimal_thresholds.json`

---
*Optimisation générée le {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

    # Sauvegarder le rapport
    opt_dir = Path("artifacts/auto_improve/optimization")
    with open(opt_dir / "optimization_report.md", "w", encoding="utf-8") as f:
        f.write(report_content)

    # Sauvegarder les seuils optimaux en JSON
    with open(opt_dir / "optimal_thresholds.json", "w", encoding="utf-8") as f:
        json.dump(optimal_thresholds, f, indent=2)

    print("📊 Rapport d'optimisation sauvegardé")


def main():
    """Fonction principale d'optimisation"""
    try:
        print("🔧 Optimisation du seuil de décision...")

        # Charger le modèle et les données
        model, X, y, df, best_config = load_best_model()
        print(f"✅ Modèle chargé: {len(X)} échantillons")

        # Optimiser le seuil
        results_df = optimize_threshold(model, X, y, df)
        print(f"✅ {len(results_df)} seuils testés")

        # Générer les graphiques
        plot_optimization_results(results_df)

        # Trouver les seuils optimaux
        optimal_thresholds = find_optimal_thresholds(results_df)

        if optimal_thresholds:
            # Générer le rapport
            generate_optimization_report(optimal_thresholds, best_config)

            print("\n🎯 Optimisation terminée!")
            print(
                "📄 Consultez: artifacts/auto_improve/optimization/optimization_report.md"
            )
            print(
                f"🎯 Seuil recommandé: {optimal_thresholds['best_composite']['threshold']:.3f}"
            )
        else:
            print("❌ Impossible de trouver des seuils optimaux")

    except Exception as e:
        print(f"❌ Erreur lors de l'optimisation: {e}")
        raise


if __name__ == "__main__":
    main()
