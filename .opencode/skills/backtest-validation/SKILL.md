---
name: backtest-validation
description: Validation statistique des backtests — p-value binomiale, walk-forward 5 splits, IC 95% Wald, détection d'overfitting, analyse par symbole. Utilise validate_strategy.py, backtest_multi_tf.py.
---

# Backtest Validation Skill

## Description
Expert en validation statistique des backtests : p-value, walk-forward, intervalles de confiance, détection d'overfitting, analyse par symbole et timeframe.

## Quand utiliser
- Pour valider statistiquement une stratégie
- Pour analyser les résultats d'un backtest
- Pour détecter l'overfitting
- Pour vérifier si un symbole a un edge statistique

## Scripts de validation

### 1. validate_strategy.py — Validation statistique complète
```powershell
python scripts/validate_strategy.py --csv runtime/trades_log.csv
python scripts/validate_strategy.py --backtest
```

**Tests statistiques :**
- **P-value** (binomial, approximation normale) : H0 = WR = 50%
- **IC 95%** (Wald) : `WR ± 1.96 * sqrt(WR*(1-WR)/n)`
- **Walk-forward** (5 splits, expanding window) :
  - WR diff train-test < 8% → LOW overfitting
  - WR diff 8-15% → MEDIUM overfitting
  - WR diff > 15% → HIGH overfitting
- **Z-score** : `(WR - 0.5) / sqrt(0.25/n)`

**Seuils de significativité :**
```
p < 0.001 → *** Très hautement significatif
p < 0.01  → ** Hautement significatif
p < 0.05  → * Significatif
p ≥ 0.05  → ns Non significatif
```

**Verdict final :**
- EDGE CONFIRMED : p < 0.05 ET overfitting = LOW
- EDGE POTENTIAL : p < 0.05 mais overfitting MEDIUM/HIGH
- NO EDGE : p ≥ 0.05

### 2. backtest_multi_tf.py — Backtest 12+ ans (H1/H4/D1)
```powershell
python scripts/backtest_multi_tf.py  # 158K trades, 55s — tests: 889/889 pass
```

**Données :** 45 fichiers Parquet (15 symboles × 3 TF)
**Stratégie :** MOM20x3 inlined (pas de filtre ADX slope, pas de DI)
**Métriques :** WR, PnL, PF, DD max, p-value

⚠️ **Biais connus** :
- Pas de spread réel, pas de slippage
- Pas de filtre ADX slope (contrairement à la prod)
- Résultats trop uniformes (67-68% WR partout)
- XAUUSD H1 catastrophique (-$187K, DD 126%) mais H4 viable (+$113K, DD 6.9%)

### 3. report_backtest_multi.py — Rapports par année/mois
```powershell
python scripts/report_backtest_multi.py        # Rapport complet
python scripts/report_backtest_multi.py --summary  # Tableau seulement
```

**Export :** `runtime/backtest_report.csv` + `runtime/backtest_report.json`
**Analyse :** par symbole × TF × année, avec breakdown mensuel (5 dernières années)

### 4. backtest_all_symbols.py — H1 2026 uniquement
```powershell
python scripts/backtest_all_symbols.py
```

**Différence clé :** utilise la VRAIE stratégie prod (`mom20x3_signal()`) avec tous les filtres
**Spread :** filtré (max 50 points)
**Recommandations :** basées sur WR, PF, DD combinés

## Métriques calculées
| Métrique | Formule | Seuil |
|----------|---------|-------|
| Win Rate | wins / n | > 55% |
| Profit Factor | gross_pnl / max(gross_loss,1) | > 1.3 |
| Max DD | peak-to-trough | < 10% |
| P-value | binomial approx | < 0.05 |
| Expectancy | WR×avg_win - (1-WR)×avg_loss | > 0 |
| Avg RR | avg_win / avg_loss | > 2.0 |

## Performances réelles vs Backtest

### Comparaison live (Excel 47 trades) vs historique (967 trades) vs backtest (2657 trades)

| Source | Trades | WR | PnL | PF | Fiabilité |
|--------|--------|-----|-----|-----|-----------|
| **Backtest H1 2026** | 2657 | 66.4% | +$70K | 1.15 | ⚠️ Pas de spread/slippage |
| **Historique réel** | 967 | 60.8% | +$1,560 | 1.08 | ✅ Données MT5 réelles |
| **Excel 8-9 Juin** | 47 | 51.1% | +$289 | 1.02 | ✅ **Vrai live — snapshot** |
| **Corrupted_bak** | 582 | ~60% | ~$3,500 | ~1.10 | ⚠️ USDJPY 400 trades 70% |

**Écart backtest → live attendu : ~15% de WR en moins** en moyenne.
Le backtest 66% donne 51-60% en live. C'est dans la norme pour une stratégie momentum.

### Par symbole (comparaison)

| Symbole | Backtest WR | Historique WR | Live WR (Excel) | Verdict |
|---------|-------------|---------------|-----------------|---------|
| USDCAD | 69.4% | **69.2%** ✅ | 45.5% | 🔴 Live décevant vs historique |
| USDCHF | 65.9% | 54.8% | **60.0%** | ✅ Live meilleur qu'historique |
| GBPUSD | 64.3% | 56.2% | 63.6% | ✅ Live correct |
| EURUSD | 66.8% | 49.0% | 33.3% | 🔴🔴 Pire en live |
| AUDUSD | 66.4% | 56.2% | 100% (2t) | ⚠️ Échantillon trop petit |
| NZDUSD | 65.7% | 36.6% | 0% (1t) | 🔴 Symbole faible en réel |

### Le "gap backtest→live"
Les causes de l'écart :
1. **Spread réel** non modélisé dans le backtest (mange le petit profit des trades gagnants)
2. **Slippage** sur les entrées/sorties (particulièrement sur XAUUSD et USDJPY)
3. **ADX slope filter** — actif en prod, absent du backtest multi-TF
4. **DI filter** — idem, absent du backtest
5. **PerSymbolRateLimiter** — réduit le nombre de trades en prod (max 1/min/symbole)
6. **Corrélation** — en live, moins de trades simultanés qu'en backtest (limit 2/direction/groupe)

## Pièges connus
- Le backtest multi-TF surestime les performances (version simplifiée de MOM20x3)
- Le walk-forward dans `validate_strategy.py` utilise expanding window, pas purged — peut surestimer la stabilité
- Minimum 5 trades pour un test valide, 50+ pour walk-forward fiable
- Seul USDCAD montre un edge robuste en validation live (531 trades, 69.3% WR, p<0.05) MAIS live récent = 45.5% WR ⚠️
- XAUUSD H1 bear (2013-2020) = -$187K — depuis 2021 il est redevenu profitable
- **Écart backtest→live attendu = 10-15% de WR en moins**. Ne pas sur-optimiser sur backtest
- **Le corrupted_bak** (582 trades) montre USDJPY à 70% WR — ce symbole a été retiré après nettoyage, mais mérite ré-analyse

## Fichiers clés
- `scripts/validate_strategy.py` — validation stats
- `scripts/backtest_multi_tf.py` — backtest multi-TF
- `scripts/report_backtest_multi.py` — rapports
- `scripts/backtest_all_symbols.py` — backtest H1 2026
- `AGENTS.md` — résultats complets dans la doc

## Tests
```powershell
python -m pytest tests/test_walk_forward_validator.py -v
```

## Agents concernés
- `@quant-auditor` — validation statistique, overfitting
- `@alpha-researcher` — analyse des edges par symbole
- `@devils-advocate` — remet en cause les résultats trop beaux
- `@optimizer` — utilise les résultats pour ajuster les paramètres
