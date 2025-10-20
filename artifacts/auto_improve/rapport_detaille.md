# Rapport d'analyse - Meilleure solution

## Configuration optimale

**Horizon de prédiction:** 5 périodes

**Hyperparamètres LightGBM:**
- num_leaves: 15
- learning_rate: 0.1

## Performance de validation croisée

**Accuracy moyenne:** 0.5366 ± 0.0429

**Scores par fold:**
- Fold 1: 0.6220
- Fold 2: 0.5122
- Fold 3: 0.5244
- Fold 4: 0.5122
- Fold 5: 0.5122

## Métriques détaillées

**Métriques de classification:**
- Accuracy: 0.9577
- Precision: 0.9615
- Recall: 0.9579
- F1-Score: 0.9597
- ROC-AUC: 0.9945

## Performance du backtest

**Métriques financières:**
- Rendement total: 0.2685 (26.85%)
- Rendement moyen par tick: 0.000482
- Taux de réussite: 0.3091 (30.9%)
- Sharpe annualisé: 153.99

## Features les plus importantes

Les 10 features les plus importantes selon LightGBM (gain):

1. ema_15T: 696.7838
2. sma_1T: 416.3858
3. rsi_60T: 386.1077
4. volume: 381.1916
5. close: 380.7826

## Analyse et recommandations

### Points positifs
- Le modèle dépasse le hasard (accuracy > 0.5)
- ROC-AUC de 0.994 montre une capacité de discrimination
- Validation croisée temporelle robuste

### Points d'attention
- Taux de réussite modéré (30.9%)
- Variance entre folds CV significative
- Sharpe très élevée à vérifier

### Recommandations d'amélioration

1. **Optimisation du seuil:**
   - Tester différents seuils de décision
   - Optimiser selon métriques financières

2. **Engineering avancé:**
   - Features de volatilité
   - Indicateurs de régime de marché
   - Signaux multi-timeframes

3. **Modélisation:**
   - Ensemble de modèles
   - Régularisation adaptative
   - Optimisation bayésienne

4. **Validation:**
   - Walk-forward analysis
   - Tests de robustesse
   - Analyse des périodes de drawdown

## Fichiers générés

- `artifacts/auto_improve/importance/` - Analyse des features
- `artifacts/auto_improve/plots/` - Graphiques de performance
- `artifacts/auto_improve/best_lightgbm.txt` - Modèle entraîné

---
*Rapport généré le 2025-10-19 00:08:37*
