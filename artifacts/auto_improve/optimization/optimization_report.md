# Rapport d'optimisation du seuil de décision

## Configuration de base
- Horizon: 5
- Modèle: LightGBM (num_leaves=15, lr=0.1)
- Seuil par défaut: 0.5

## Seuils optimaux trouvés

### 🎯 Meilleur rendement total
- **Seuil optimal:** 0.540
- Rendement total: 5.0594 (505.94%)
- Sharpe: 18.32
- Win rate: 0.657 (65.7%)
- Nombre de trades: 251.0

### 📈 Meilleur ratio de Sharpe
- **Seuil optimal:** 0.660
- Rendement total: 4.9756 (497.56%)
- Sharpe: 18.61
- Win rate: 0.677 (67.7%)
- Nombre de trades: 217.0

### 🏆 Meilleur taux de réussite
- **Seuil optimal:** 0.760
- Rendement total: 4.4612 (446.12%)
- Sharpe: 17.55
- Win rate: 0.686 (68.6%)
- Nombre de trades: 175.0

### ⭐ Score composite (recommandé)
- **Seuil optimal:** 0.680
- Rendement total: 4.9257 (492.57%)
- Sharpe: 18.47
- Win rate: 0.681 (68.1%)
- Nombre de trades: 213.0

## Recommandations

**Seuil recommandé pour la production:** 0.680

Ce seuil offre le meilleur équilibre entre rendement, gestion du risque et nombre de trades.

## Fichiers générés
- `artifacts/auto_improve/optimization/threshold_optimization.csv`
- `artifacts/auto_improve/optimization/threshold_optimization.png`
- `artifacts/auto_improve/optimization/optimal_thresholds.json`

---
*Optimisation générée le 2025-10-20 10:58:27*
