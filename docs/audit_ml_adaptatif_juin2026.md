# AUDIT COMPLET DU PIPELINE ML ADAPTATIF
**Date**: 14 Juin 2026  
**Sources**: online_learner_state.json, meta_learner.json, calibration_state.json, performance_history.json, code source

---

## 1. OnlineLearner — État Actuel

### 1.1 Statistiques par symbole (1191 trades, 13 symboles)

| Symbole | Trades | WR | Expectancy | Régimes stockés |
|---------|--------|------|------------|-----------------|
| EURUSD | 200 | 30.2% | -0.35 | SELL (187), BUY (13) |
| USDCAD | 200 | 25.8% | -0.46 | BUY (200) |
| GBPUSD | 200 | 42.4% | -0.15 | BUY (78), SELL (122) |
| USDCHF | 138 | 66.4% | +0.32 | BUY (97), SELL (41) |
| NZDUSD | 94 | 63.0% | +0.26 | SELL (63), BUY (31) |
| AUDUSD | 80 | 43.6% | -0.12 | SELL (62), BUY (18) |
| ETHUSD | 66 | 56.1% | +0.12 | SELL (63), BUY (3) |
| XAUUSD | 65 | 86.2% | +0.72 | SELL (57), BUY (8) |
| USDJPY | 45 | 32.6% | -0.33 | BUY (30), SELL (15) |
| USOIL.cash | 37 | 62.2% | +0.24 | SELL (31), BUY (6) |
| GBPJPY | 37 | 56.8% | +0.14 | BUY (20), SELL (17) |
| BTCUSD | 26 | 73.1% | +0.46 | SELL (25), BUY (1) |
| EURJPY | 3 | 0.0% | -1.00 | BUY (1), SELL (2) |

### 1.2 adapted_params (4/13 symboles seulement)

| Symbole | thresh | risk_mult | sl_mult | tp_mult |
|---------|--------|-----------|---------|---------|
| EURUSD | 2.5 | **0.50** | 3.0 | 1.0 |
| USDCHF | 2.5 | **0.75** | 3.0 | 1.0 |
| USDCAD | 2.5 | **0.50** | 3.0 | 1.0 |
| GBPUSD | 2.5 | **0.50** | 3.0 | 1.0 |

**9 symboles sans adapted_params** (défaut: thresh=3.0, risk_mult=1.0): AUDUSD, BTCUSD, ETHUSD, EURJPY, GBPJPY, NZDUSD, USDJPY, USOIL.cash, XAUUSD

### 1.3 Fenêtre glissante
- `window = 200` trades
- Seuil de déclenchement _update_params: `len(h) < window//2 = 100` → skip (9/13 symboles < 100)

### 1.4 🔴 Problème Critique — Régimes = Directions

**100% des 1191 trades stockent BUY/SELL (direction d'entrée) comme régime de marché.**

```
Régimes utilisés dans OnlineLearner:
  SELL: 685 trades (57.5%)
  BUY:  506 trades (42.5%)
  RANGING/TREND_UP/TREND_DOWN/HIGH_VOL/LOW_VOL: 0 trades (0%)
```

**Conséquence**: Le calcul de WR est fait par direction, pas par condition de marché. Les adapted_params (risk_mult, thresh) sont basés sur des données inutilisables pour l'adaptation aux régimes.

**Cause racine**: Le `seed_from_csv()` (ligne 177) importe `direction` comme `regime`:
```python
regime = row.get("direction", "?")[:5]  # BUY/SELL comme proxy de régime
```

---

## 2. Filtre Valid Regimes (adaptive_intelligence.py:212-252)

### Code vérifié
```python
valid_regimes = {"RANGING", "TREND_UP", "TREND_DOWN", "HIGH_VOL", "LOW_VOL", "HIST", "RAN", "BUY", "SELL"}
h_valid = [t for t in h if t.get("regime", "") in valid_regimes]
if len(h_valid) < 5 or (len(h) > 0 and len(h_valid) / len(h) < 0.3):
    # skip apprentissage
```

**Problème**: BUY/SELL sont dans `valid_regimes` → les trades directionnels passent le filtre → le WR calculé est celui de la direction, pas du régime de marché. Le seuil `len(h_valid) / len(h) < 0.3` ne sert à rien puisque 100% des trades sont "valides".

**Recommandation**: BUY/SELL devraient être RETIRÉS de `valid_regimes` après la migration vers les vrais régimes de marché.

---

## 3. MetaLearner — État Actuel

### 3.1 Stats globales des trackers

| Tracker | Trades | W/L | WR | Base Weight |
|---------|--------|------|------|-------------|
| **DL_LSTM** | 546 | 312/234 | **57.1%** | 1.0 |
| **MOM20x3** | 548 | 183/365 | **33.4%** 🔴 | 1.0 |
| **LGB** | 546 | 390/156 | **71.4%** | **0.0** (désactivé) |

### 3.2 Problemes identifiés

#### 🔴 MOM20x3 WR = 33.4% — Données seedées incohérentes
Le MOM20x3 montre 33.4% WR dans le MetaLearner alors que les backtests montrent 60-66% WR. Cause:
- `initialize_from_history()` ligne 151 donne **100% du crédit à MOM20x3** pour chaque trade gagnant
- Mais `record_trade()` dans `position_tracker.py` ligne 314 utilise `pred_outcomes` qui compare l'action prédite à la direction réelle du trade
- Le MetaLearner voit un désaccord systématique entre la prédiction MOM20x3 et la réalité

#### ⚠️ WR identiques par symbole (data artificielle)
```
DL_LSTM: AUDUSD=57.1%, EURUSD=57.1%, GBPUSD=57.1%, XAUUSD=57.1%... (TOUS identiques)
LGB:     AUDUSD=71.4%, EURUSD=71.4%, GBPUSD=71.4%, XAUUSD=71.4%... (TOUS identiques)
```
C'est statistiquement impossible — indique que les données sont dérivées d'une même distribution aléatoire (`random.random() < 0.55` ligne 156).

#### ⚠️ Tous les trades sous "RANGING" — pas de stats par régime
Les `regime_stats` ne contiennent que "RANGING" (sauf MOM20x3: +"RAN" avec 2 trades). Les poids du MetaLearner sont donc **identiques quel que soit le régime** de marché actuel.

### 3.3 Poids actuels (exemple avec RANGING)
```python
get_calibration_status()["models"]:
  DL_LSTM:  WR=57.1%, weight ≈ (0.5+0.571)/2 = 53.6%
  MOM20x3:  WR=33.4%, weight ≈ (0.5+0.334)/2 = 41.7%
  LGB:      WR=71.4%, weight ≈ (0.5+0.714)/2 = 60.7% (mais base_weight=0 → effectif=0)
```

---

## 4. Calibration State — Désynchronisation

### 4.1 Deux fichiers, deux réalités

| Fichier | Trades | Régimes dominants | Dernière modif |
|---------|--------|-------------------|---------------|
| `online_learner_state.json` | 1191 | BUY/SELL (100%) | 14 Juin 19:03 |
| `calibration_state.json` | 1838 | HIST (95%) + ? | 14 Juin 10:22 |

**Les deux fichiers ne sont PAS synchronisés.** L'ordre de chargement dans `AdaptiveEngine.__init__`:
1. `OnlineLearner()` → `_load_state()` → charge `online_learner_state.json` (BUY/SELL)
2. `seed_from_csv()` → skip si lock existe
3. `_load_calibration("runtime/calibration_state.json")` → **ÉCRASE** `learner.history` avec `online_history` (HIST)

**Conséquence**: Au prochain redémarrage, l'OnlineLearner aura les trades HIST de `calibration_state.json`, pas les BUY/SELL de `online_learner_state.json`. Mais comme `save_state()` est appelée après chaque trade, les deux fichiers divergent à nouveau.

### 4.2 Trades "?" dans calibration_state.json
- EURUSD: 8 trades avec regime "?"
- USDJPY: 30 trades avec regime "?"
- ETHUSD: 48 trades avec regime "?"

Ces trades sont ignorés par `_update_params` (ne passent pas `valid_regimes`) → réduisent artificiellement `h_valid`.

---

## 5. MarketRegime — Détection

### 5.1 Hystérésis ADX (regime.py)
```python
ADX_TREND_ENTER = 22
ADX_TREND_EXIT = 18
```
✅ **Implémenté correctement** via `self._prev_regime`:
- Si déjà en TREND: sort si ADX < 18
- Si en RANGING: entre si ADX >= 22

⚠️ Weakness: `_prev_regime` est perdu au redémarrage (variable d'instance). Premier cycle après restart = RANGING.

### 5.2 Régimes possibles
- `TREND_UP` / `TREND_DOWN`: ADX ≥ 22 (entrée) / ≥ 18 (maintien) + pente MA20 > 0.2% / < -0.2%
- `RANGING`: condition par défaut
- `HIGH_VOL`: ATR > 1.5% du prix
- `LOW_VOL`: ATR < 0.3% du prix

### 5.3 Problème: Abréviation "RAN" détectée live
Le trade du 14 Juin 2026 (EURUSD, +$50) a été enregistré avec regime **"RAN"** dans `performance_history.json`. "RAN" est une abréviation présente dans les données de calibration héritées, PAS un retour de `regime.py` (qui renvoie "RANGING").

**Traçage**: La valeur "RAN" provient de `meta_regime_performance` dans le fichier de calibration migré → transmise via `meta.get("regime")` dans `position_tracker.py:289`.

---

## 6. Intégration Live — Recording Pipeline

### 6.1 Flux vérifié (position_tracker.py)
```
Trade fermé → line 262: ftmo.record_trade_result()
            → line 268-272: performance_monitor.record_trade(symbol, profit, regime, direction)
            → line 293: adaptive.record_result(symbol, r_mul, regime, dl_features)
                         ├─ line 668: learner.record_trade()  → OnlineLearner._update_params() → save_state()
                         └─ line 669: _save_calibration()
            → line 315: adaptive.record_meta_result(symbol, regime, pred_outcomes)
                         ├─ line 674: meta.record_trade()  → ModelTracker.record()
                         └─ line 678: _save_calibration()
```

✅ Le pipeline d'enregistrement est COMPLET et fonctionnel.

### 6.2 Problème: Aucun vrai trade live dans MetaLearner
Les 548 trades du MetaLearner viennent TOUS de `initialize_from_history()` (seed aléatoire). Les trades live ne les écrasent pas — ils s'ajoutent. Mais avec `trades_since_recal = 2`, la recalibration ne s'est pas déclenchée.

---

## 7. Bugs Identifiés — Résumé

| # | Gravité | Bug | Impact | Cause |
|---|---------|-----|--------|-------|
| 1 | 🔴 CRITIQUE | **Régime = Direction** dans OnlineLearner | adapted_params basés sur des données inutilisables | seed_from_csv() utilise `direction` comme `regime` |
| 2 | 🔴 CRITIQUE | **MOM20x3 WR=33.4%** dans MetaLearner | MetaLearner sous-pondère MOM20x3 alors qu'il est la stratégie principale | `initialize_from_history()` seed random |
| 3 | 🔴 HAUT | **adapted_params.thresh inutilisé** dans strategy.py | Le threshold adapté par OnlineLearner n'atteint jamais le générateur de signaux | `analyze()` ne passe pas `thresh` à strategy.py |
| 4 | 🟡 MOYEN | **Deux fichiers d'état désynchronisés** | Comportement non déterministe au redémarrage | online_learner_state.json vs calibration_state.json divergents |
| 5 | 🟡 MOYEN | **Trackers "RANGING" uniquement** | MetaLearner ne peut pas différencier par régime de marché | `initialize_from_history()` avec regime fixe "RANGING" |
| 6 | 🟡 MOYEN | **WR identiques par symbole** (artificiel) | Fausse confiance dans les stats du MetaLearner | Seed aléatoire avec `random.random() < 0.55` |
| 7 | 🟡 MOYEN | **9/13 symboles sans adapted_params** | Ces symboles utilisent les params par défaut (thresh=3.0, risk=1.0) | Seuil `len(h) < 100` trop haut pour petits échantillons |
| 8 | ⚪ BAS | **Trades "?" dans calibration** | Réduit `h_valid` artificiellement | Migration de données avec régime manquant |
| 9 | ⚪ BAS | **"RAN"/"RANGING" inconsistants** | Valeur de régime différente selon le fichier | Migration pickle → JSON avec troncature |
| 10 | ⚪ BAS | **_prev_regime reset au restart** | Premier cycle après restart = RANGING (acceptable) | Variable d'instance non persistée |

---

## 8. Recommandations Prioritaires

### P1 — Immédiat
1. **Refaire le seed de l'OnlineLearner** avec de vrais régimes de marché (RANGING, TREND_UP, etc.) et des WR réalistes (pas de données artificielles)
2. **Corriger `seed_from_csv()`** pour lire `regime` au lieu de `direction`
3. **Aligner les deux fichiers d'état** — soit fusionner, soit utiliser un seul source de vérité

### P2 — Court terme
4. **Passer `thresh` de `get_params()` à `strategy.py`** pour que l'OnlineLearner ajuste vraiment les seuils de signaux
5. **Re-seeder le MetaLearner** avec des données de backtest réelles (pas de random)
6. **Configurer la recalibration** (`trades_since_recal` = 50) pour qu'elle se déclenche

### P3 — Moyen terme
7. **Ajouter des vrais trades live AU-DESSUS du seed** dans les stats du MetaLearner
8. **Nettoyer les valeurs "RAN" et "?"** des fichiers d'état
9. **Persister `_prev_regime`** pour éviter le reset au restart
