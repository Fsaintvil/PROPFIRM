---
name: lot-sizing
description: Optimisation du sizing des positions — risque par trade, corrélation, volatilité, drawdown adaptatif. Utilise ftmo_protector.py, ftmo_config.py et trade_executor.py.
---

# Lot Sizing Skill

## Description
Spécialiste de l'optimisation du sizing des positions : calcul du risque par trade, adaptation au drawdown, corrélation entre symboles, et volatilité. Maximise le profit tout en respectant les contraintes FTMO.

## Quand utiliser
- Pour ajuster le `risk_per_trade` ou `max_risk_amount`
- Pour optimiser le sizing par symbole (max_lot)
- Pour calculer le sizing adapté au drawdown actuel
- Pour gérer la corrélation entre positions
- Pour diagnostiquer les bugs de sizing (comme le triple clamp du 29/08)

## Architecture du Sizing

### 1. Calcul de Base
```python
# trade_executor.py::_calc_lot()
risk_amount = balance × risk_per_trade  # ex: $200k × 0.003 = $600
risk_per_01 = abs(sl_distance) × pip_value  # risque par 0.01 lot
lot = risk_amount / risk_per_01  # lot brut
```

### 2. Triple Clamp (FIX 29 Août 2026)
```python
# Clamp 1: global_max_lot depuis config
lot = min(lot, global_max_lot)  # ex: 0.06

# Clamp 2: per-symbol max_lot depuis symbol_limits (hot-reload)
lot = min(lot, symbol_max_lot)  # ex: BTCUSD 0.045

# Clamp 3: per-symbol max_lot depuis YAML direct
lot = min(lot, yaml_max_lot)  # safety net
```

### 3. Adaptation au Drawdown
```python
# ftmo_protector.py::_get_risk_multiplier()
if dd_pct > 5%:
    risk_mult = 0.5  # réduit de 50%
elif dd_pct > 3%:
    risk_mult = 0.75  # réduit de 25%
else:
    risk_mult = 1.0  # normal
```

## Paramètres Clés

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `risk_per_trade` | 0.003 (0.3%) | Risque par trade en % du balance |
| `max_risk_amount` | $600 | Risque max absolu par trade |
| `MAX_POSITIONS` | 30 | Positions simultanées max |
| `MAX_POSITIONS_PER_SYMBOL` | 3 | Positions par symbole max |
| `MAX_POSITIONS_PER_DIRECTION` | 4 | Positions par direction max |
| `CONSISTENCY_MAX_PCT` | 0.30 | Max 30% du PnL total par symbole |

## Matrice de Sizing par Symbole

| Symbole | max_lot | risk_mult | Calcul | Justification |
|---------|:-------:|:---------:|:------:|:-------------:|
| BTCUSD | 0.045 | 1.0 | $600 / risk | Edge prouvé (PF 10.51) |
| SOLUSD | 0.06 | 1.0 | $600 / risk | Edge prouvé (PF 6.48) |
| GBPUSD | 0.06 | 1.0 | $600 / risk | Edge prouvé (PF 2.69) |
| XAUUSD | 0.03 | 1.0 | $600 / risk | Perdant (p=0.0008) |
| EURUSD | 0.04 | 0.0 | $0 | risk_mult=0.0 (perdant) |
| USDJPY | 0.04 | 0.0 | $0 | risk_mult=0.0 (perdant) |
| AUDUSD | 0.05 | 0.0 | $0 | risk_mult=0.0 (perdant) |

## Drawdown Adaptatif

### Niveaux de Réduction
| DD Actuel | Risk Multiplier | Impact |
|-----------|:---------------:|--------|
| 0% - 2% | 1.00 | Normal |
| 2% - 3% | 0.75 | -25% sizing |
| 3% - 5% | 0.50 | -50% sizing |
| 5% - 8% | 0.25 | -75% sizing |
| > 8% | 0.00 | STOP (veto risk-compliance) |

### Formule
```python
risk_mult = max(0, 1.0 - (dd_pct - 0.02) / 0.06)  # linéaire de 2% à 8%
```

## Corrélation et Groupes

### Groupes de Corrélation
| Groupe | Symboles | Règle |
|--------|----------|-------|
| FOREX_MAJORS | EURUSD, GBPUSD, USDCHF | max 2/direction |
| FOREX_CROSSES | EURJPY, GBPJPY, EURGBP | max 2/direction |
| CRYPTO | BTCUSD, SOLUSD, ETHUSD | max 2/direction |
| INDICES | US500.cash, US30.cash, US100.cash | max 2/direction |
| COMMODITIES | XAUUSD, XAGUSD, USOIL.cash | max 2/direction |
| ASIA | JP225.cash, HK50.cash | max 1/direction |

### Règle de Corrélation
```python
# ftmo_protector.py::_check_correlation()
positions_in_group = count_positions(group, direction)
if positions_in_group >= 2:
    reject("correlation_limit")
```

## Bugs Connus et Fixes

### 1. Triple Clamp Bypass (29 Août 2026)
- **Bug** : SOLUSD 1.47 lot = ×24 max, BTCUSD 0.71 lot = ×23 max
- **Cause** : `isinstance` guard sur les clamps existants
- **Fix** : Triple défense (global + per-symbol + YAML direct)

### 2. Lot Safety Clamp (22 Août 2026)
- **Bug** : risk_amount/risk_per_01 produisait des lots 28-30× le max
- **Cause** : pas de clamp AVANT le MAX_LOT_ABSURDITY_FACTOR
- **Fix** : Clamp `lot = max_lot` avant le calcul d'absurdity

### 3. Commission Penalty (30 Août 2026)
- **Bug** : signaux avec commission > $1 non pénalisés
- **Cause** : pas de filtre commission dans signal_validator
- **Fix** : penalty −0.02 si commission > $1

## Métriques de Performance

### KPIs à Surveiller
- **avg_lot_size** : taille moyenne des lots
- **lot_utilization** : % du max_lot utilisé
- **risk_utilization** : % du risk_amount utilisé
- **clamp_frequency** : nombre de fois que le clamp s'active

### Formules
```python
lot_utilization = avg_lot / max_lot × 100
risk_utilization = avg_risk / max_risk × 100
```

## Optimisations Possibles

### 1. Kelly Criterion
```python
kelly = (win_rate × avg_win - (1-win_rate) × avg_loss) / avg_win
optimal_lot = kelly × max_lot
```

### 2. Volatility-Adjusted Sizing
```python
vol_adj = atr_ref / atr_current  # normalise par volatilité
lot = base_lot × vol_adj
```

### 3. Equity Curve Sizing
```python
if equity_curve > sma_equity:
    lot = base_lot × 1.2  # increase
else:
    lot = base_lot × 0.8  # decrease
```

## Logs

```python
[LOT SAFETY] {symbol}: lot={lot} > per-symbol max_lot={max_lot} → clamp
[LOT CALC] {symbol}: risk_amount=${risk}, risk_per_01=${r01}, lot={lot}
[RISK MULT] DD={dd:.1f}% → risk_mult={mult:.2f}
[CORRELATION] {group}: {count}/{max} positions {direction} → REJECT
```
