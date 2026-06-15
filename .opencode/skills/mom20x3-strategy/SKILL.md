---
name: mom20x3-strategy
description: Génération de signaux MOM20x3, seuils ATR (2.0x ranging / 2.5x trending), filtres directionnels ADX slope/DI/EMA, adaptation par régime de marché. Utilise strategy.py et signals.py.
---

# MOM20x3 Strategy Skill

## Description
Expert en génération de signaux MOM20x3 : règle technique pure, breakout momentum sur 20 bougies, adaptation par régime de marché.

## Quand utiliser
- Pour analyser/modifier la logique de signal dans `signals.py` ou `strategy.py`
- Pour comprendre pourquoi un signal est (ou n'est pas) généré
- Pour ajuster les seuils (2.0×ATR ranging, 2.5×ATR trending)
- Pour déboguer un filtre directionnel ou un score insuffisant

## Architecture

### Flux de décision
```
rates → MarketRegime (ADX/ATR/MA) → MOM20x3 brut → Filtres directionnels → Score → FTMO Protector
```

### Génération du signal (strategy.py)
```python
mom = close[i] - close[i - period]  # momentum sur period bougies
if np.isnan(mom) or np.isinf(mom):
    continue  # NaN/Inf guard — skip silencieux avec log debug
is_trending = adx >= 25
thresh = 2.5 * atr if is_trending else 2.0 * atr
thresh = max(min(thresh, 2.5 * atr), 1.5 * atr)  # plafonné 2.5× / plancher 1.5×

if mom > thresh → BUY signal
if mom < -thresh → SELL signal
```

### Filtres appliqués
1. **NaN/Inf guard** : `np.isnan(mom) or np.isinf(mom)` → skip silencieux (log debug)
2. **ADX slope** > -3.5 (évite entrer en fin de tendance). Wilder's smoothing avec `half=len/3`. Si raw_score > 0.70, bypassé (pas de rejet ADX slope).
3. **+DI/-DI cross** (directionnel)
4. **Pullback EMA20** — vérifié APRÈS les filtres directionnels (NaN guard, ADX slope, DI). Bande ATR-based: 0.5×ATR en trending, 0.3×ATR en ranging. `pullback_active` doit être True.
5. **Score** > `min_score` par symbole (0.60 pour les 6 actifs)
6. **RR** ≥ 2.0 (vérifié avant execution)

### Périodes par symbole
| Symbole     | Period | Min Score |
|-------------|--------|-----------|
| XAUUSD      | 30     | 0.60      |
| BTCUSD      | 20     | 0.60      |
| US500.cash  | 24     | 0.60      |

### Seuils de signal
- ADX ≥ 25 → trending → thresh = 2.5×ATR
- ADX < 25 → ranging → thresh = 2.0×ATR
- Plafonné à 2.5×ATR max, plancher à 1.5×ATR

## Performances live
Voir `runtime/performance_history.json` pour les métriques live actualisées (WR, PnL, PF, drawdown par symbole et par fenêtre glissante).

## Configuration (config_simple.py)

### Per-symbol momentum periods (strategy.py)
```python
SYMBOL_MOMENTUM_PERIODS = {
    "XAUUSD": 30,     # Lent — WR 73.0%, PnL +$218K
    "BTCUSD": 20,     # Standard — WR 75.9%, PnL +$202K
    "US500.cash": 24, # Modéré — WR 74.6%, PnL +$18.5K
}
```

### Per-symbol ADX thresholds (config_simple.py)
```python
# AdxThresh par symbole — utilisé par main.py pour le ADX filter fix
"XAUUSD": 22, "BTCUSD": 20, "US500.cash": 22,
```

## ADX Threshold Fix (main.py:736-746)
Depuis la session du 8 Juin 2026 :
```python
# Correction ADX : override du regime si ADX réel est trop bas
if regime == "RANGING" and signal_adx < 12:
    # Régime forcé RANGING pour seuils prudents
    pass  # déjà RANGING, mais SL/TP adaptés
if signal and signal.get("score", 0) >= 0.80:
    # Bypass total — score très haut, on ignore le regime
    pass
```

## Pièges connus
- `if not self.rates:` plante sur numpy array — TOUJOURS utiliser `if self.rates is None`
- ICT/SMC dans `signals.py` est déprécié — les vrais signaux viennent de `strategy.mom20x3_signal()`
- Le backtest multi-TF utilise une version simplifiée (pas de ADX slope, pas de DI filter) → surestime les performances
- Un score < 0.60 coupe le signal même si MOM20x3 est valide (6 symboles actifs, min_score=0.60 pour tous)
- **Corrélation crypto** : BTC/SOL/LNK/BNB sont fortement corrélés (>0.70). Le contrôle via matrice Pearson + max 2 trades/direction/groupe limite mais ne supprime pas le risque de pertes simultanées
- **NaN/Inf guard** : si un momentum est NaN ou Inf, le signal est ignoré silencieusement (log debug). Ne pas confondre avec un vrai rejet de signal.
- **XAUUSD H1** reste surveillé : gagnant depuis 2021 (+$16K à +$26K/an) mais a subi -71% WR sur 2013-2020 (bear market de l'or). Surveillance active du drawdown.

## Fichiers clés
- `engine_simple/strategy.py` — MOM20x3 pur avec filtres complets
- `engine_simple/signals.py` — STRATS, score, dispatch ICT/MOM20x3
- `engine_simple/market_regime.py` — ADX/ATR/MA pour adaptation

## Tests
```powershell
python -m pytest tests/test_strategy.py -v
python -m pytest tests/test_signals.py -v
```

## Agents concernés
- `@auto-fixer` — pour corriger les bugs de signal
- `@optimizer` — pour ajuster les seuils ATR
- `@alpha-researcher` — pour analyser l'edge MOM20x3
- `@market-philosopher` — pour questionner la logique économique
- `@adversarial-trader` — pour tester les faux breakouts
