---
name: backtest-walkforward
description: Validation robustesse des stratégies via walk-forward analysis, optimisation paramétrique, et détection d'overfitting. Utilise validate_strategy.py, backtest_multi_tf.py, et walk_forward_check.py.
---

# Backtest Walk-Forward Skill

## Description
Spécialiste de la validation robustesse des stratégies : walk-forward analysis, optimisation paramétrique, détection d'overfitting, et analyse de stabilité. Garantit que les edges discover sont réels et pas des artefacts de sur-apprentissage.

## Quand utiliser
- Avant de valider un edge pour le trading live
- Après une optimisation de paramètres
- Pour diagnostiquer un échec de walk-forward
- Pour comparer des variantes de stratégie
- Pour préparer un challenge FTMO

## Méthodologie Walk-Forward

### 1. Walk-Forward Analysis (5 splits)
```
Données totales: 100%
├── Split 1: Train 70% → Test 30%
├── Split 2: Train 70% → Test 30% (décalé)
├── Split 3: Train 70% → Test 30% (décalé)
├── Split 4: Train 70% → Test 30% (décalé)
└── Split 5: Train 70% → Test 30% (décalé)

Résultat: 5 PF test → moyenne ± écart-type
```

### 2. Critères de Validation
| Métrique | Seuil Pass | Seuil Fragile | Seuil Fail |
|----------|:----------:|:-------------:|:----------:|
| **PF moyen** | ≥ 1.20 | 1.00 - 1.20 | < 1.00 |
| **WR moyen** | ≥ 52% | 48% - 52% | < 48% |
| **DD max** | < 15% | 15% - 25% | > 25% |
| **Stabilité PF** | CV < 0.15 | 0.15 - 0.30 | > 0.30 |
| **P-value** | < 0.05 | 0.05 - 0.10 | > 0.10 |
| **IC 95%** | [1.05, ∞] | [0.95, 1.05] | [-∞, 0.95] |

### 3. Formules Clés

```python
# Profit Factor
PF = sum(winning_trades) / abs(sum(losing_trades))

# Win Rate
WR = winning_trades / total_trades

# Coefficient of Variation (stabilité)
CV = std(PF_splits) / mean(PF_splits)

# P-value (test binomial)
from scipy.stats import binom_test
p_value = binom_test(wins, total, 0.5, alternative='greater')

# Interval de Confiance 95% (Wald)
IC_95 = WR ± 1.96 * sqrt(WR * (1-WR) / total)
```

## Types de Walk-Forward

### 1. Walk-Forward simple
- Découpage chronologique fixe
- Train sur N%, test sur le reste
- **Limite** : pas de variabilité temporelle

### 2. Walk-Forward rolling
- Fenêtre glissante
- Train sur N dernières périodes, test sur la suivante
- **Avantage** : capture l'évolution du marché

### 3. Walk-forward optimal
- Teste différentes tailles de fenêtre
- Sélectionne la meilleure combinaison train/test
- **Risque** : plus d'overfitting possible

## Détection d'Overfitting

### Signes d'Overfitting
| Signal | Description | Sévérité |
|--------|-------------|:--------:|
| **PF train >> PF test** | Sur-apprentissage | 🔴 CRITIQUE |
| **WR train >> WR test** | Même chose | 🔴 CRITIQUE |
| **DD test >> DD train** | Instabilité | 🟡 ÉLEVÉE |
| **Trop peu de trades** | Échantillon insuffisant | 🟡 ÉLEVÉE |
| **PF variance élevé** | Instabilité paramètres | 🟡 ÉLEVÉE |

### Métriques d'Overfitting
```python
# Overfitting Ratio
OR = PF_train / PF_test
# OR > 1.5 = overfitting probable
# OR > 2.0 = overfitting certain

# Degrees of Freedom
DOF = len(data) / len(parameters)
# DOF < 10 = trop de paramètres
```

## Paradoxe de l'Optimisation

### Le Problème
```
Plus on optimise, plus on risque l'overfitting.
Mieux on fitting les données passées, moins on généralise.
```

### La Solution
```
1. Walk-Forward sur données hors-échantillon
2. Validation sur période différente
3. Test sur marché réel (paper trading)
4. Monitoring continue post-déploiement
```

## Pipeline de Validation

### Étape 1: Backtest Initial
```bash
python scripts/backtest_multi_tf.py --symbol BTCUSD --period 5y
```

### Étape 2: Walk-Forward
```bash
python scripts/walk_forward_check.py --symbol BTCUSD --splits 5
```

### Étape 3: Validation Statistique
```bash
python scripts/validate_strategy.py --csv runtime/trades_log.csv
```

### Étape 4: Paper Trading
```bash
python main.py --mode paper --duration 30d
```

### Étape 5: Go Live
```bash
python main.py --mode live --risk conservative
```

## Métriques de Performance

### KPIs Walk-Forward
| Métrique | Formule | Cible |
|----------|---------|:-----:|
| **PF moyen** | mean(PF_splits) | ≥ 1.20 |
| **PF std** | std(PF_splits) | < 0.30 |
| **WR moyen** | mean(WR_splits) | ≥ 52% |
| **DD max** | max(DD_splits) | < 15% |
| **Trades/split** | total_trades / splits | ≥ 20 |
| **P-value** | binom_test(...) | < 0.05 |

### Score de Robustesse
```python
robustness_score = (
    0.3 * (PF_avg / 1.2) +           # PF contribution
    0.2 * (WR_avg / 0.52) +           # WR contribution
    0.2 * (1 - CV) +                  # Stabilité
    0.2 * (1 - DD_max / 0.25) +       # DD contribution
    0.1 * (1 - p_value)               # Significativité
)
# Score > 0.8 = ROBUSTE
# Score 0.6-0.8 = ACCEPTABLE
# Score < 0.6 = FRAGILE
```

## Matrice de Décision

| PF Test | WR Test | DD Test | Décision |
|:-------:|:-------:|:-------:|:--------:|
| ≥ 1.20 | ≥ 52% | < 15% | ✅ GO LIVE |
| ≥ 1.20 | ≥ 48% | < 20% | ⚠️ PAPER TRADE |
| 1.00-1.20 | ≥ 48% | < 20% | ⚠️ OPTIMISER |
| < 1.00 | < 48% | > 25% | ❌ ABANDONNER |
| ≥ 1.20 | ≥ 52% | > 25% | ⚠️ RÉDUIRE RISK |

## Intégration avec le Robot

### Hooks
- `scripts/walk_forward_check.py` : walk-forward automatique
- `scripts/validate_strategy.py` : validation statistique
- `runtime/walk_forward_results.json` : résultats persistés
- `performance_monitor.py::check_walk_forward()` : vérification périodique

### Configuration
```yaml
# config/default.yaml
walk_forward:
  enabled: true
  splits: 5
  min_pf: 1.20
  min_wr: 0.52
  max_dd: 0.25
  min_trades_per_split: 20
```

## Logs

```python
[WF] {symbol}: split {n}/5, PF={pf:.2f}, WR={wr:.1f}%, DD={dd:.1f}%
[WF] {symbol}: MOYENNE PF={avg_pf:.2f} ± {std_pf:.2f}, CV={cv:.3f}
[WF] {symbol}: VALIDÉ (PF≥1.20, WR≥52%, DD<25%, CV<0.30)
[WF] {symbol}: REJETÉ (PF={avg_pf:.2f} < 1.20)
```
