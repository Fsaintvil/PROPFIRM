---
name: mom20x3-strategy
description: Génération de signaux MOM20x3, seuils ATR (2.0x ranging / 2.5x trending), filtres directionnels ADX slope/DI/EMA, adaptation par régime de marché. Utilise strategy.py.
---

# MOM20x3 Strategy Skill

## Description
Expert en génération de signaux MOM20x3 : règle technique pure, breakout momentum sur 20 bougies, adaptation par régime de marché.

## Quand utiliser
- Pour analyser/modifier la logique de signal dans `strategy.py`
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
is_trending = adx >= 22
thresh = 2.5 * atr if is_trending else 2.0 * atr
thresh = max(min(thresh, 2.5 * atr), 1.5 * atr)  # plafonné 2.5× / plancher 1.5×

if mom > thresh → BUY signal
if mom < -thresh → SELL signal
```

### Filtres appliqués
1. **NaN/Inf guard** : `np.isnan(mom) or np.isinf(mom)` → skip silencieux (log debug)
2. **ADX slope** > -3.5 (évite entrer en fin de tendance). Wilder's smoothing avec `half=len/3`. Si raw_score > 0.70, bypassé (pas de rejet ADX slope).
3. **+DI/-DI cross** (directionnel)
4. **Pullback EMA20** — vérifié APRÈS les filtres directionnels. Bande ATR-based: 0.5×ATR en trending, 0.3×ATR en ranging. Pour score < 0.65, pullback requis.
5. **Score** > `min_score` par symbole (0.60)
6. **RR** ≥ 2.0 (vérifié avant execution)

### Périodes par symbole
| Symbole     | Period | Min Score |
|-------------|--------|-----------|
| XAUUSD      | 30     | 0.60      |
| BTCUSD      | 20     | 0.60      |
| ETHUSD      | 24     | 0.60      |
| US500.cash  | 24     | 0.60      |

### Seuils de signal
- ADX ≥ 22 → trending → thresh = 2.5×ATR
- ADX < 22 → ranging → thresh = 2.0×ATR
- Plafonné à 2.5×ATR max, plancher à 1.5×ATR

## Performances live
Voir `runtime/performance_history.json` pour les métriques live actualisées (WR, PnL, PF, drawdown par symbole et par fenêtre glissante).

## Configuration
Per-symbol parameters are in `engine_simple/strategy.py` (SYMBOL_CONFIG dict) and `config/default.yaml` (symbol_limits section).

## Pièges connus
- `if not self.rates:` plante sur numpy array — TOUJOURS utiliser `if self.rates is None`
- Le backtest multi-TF utilise une version simplifiée (pas de ADX slope, pas de DI filter) → surestime les performances
- Un score < 0.60 coupe le signal même si MOM20x3 est valide
- **Corrélation crypto** : BTC/ETH sont fortement corrélés (>0.75). Le contrôle via matrice Pearson + max 1 trade/direction/groupe limite les pertes simultanées
- **NaN/Inf guard** : si un momentum est NaN ou Inf, le signal est ignoré silencieusement (log debug)
- **XAUUSD H4** gagnant depuis 2021 mais a subi -71% WR sur 2013-2020 (bear market or). Surveillance active du DD

## Fichiers clés
- `engine_simple/strategy.py` — MOM20x3 pur avec filtres complets
- `engine_simple/indicators.py` — EMA, RSI, ADX, ATR, OBV
- `engine_simple/ftmo_config.py` — Constants de trailing, BE buffer

## Tests
```powershell
python -m pytest tests/test_strategy.py -v
```

## Agents concernés
- `@auto-fixer` — pour corriger les bugs de signal
- `@optimizer` — pour ajuster les seuils ATR
- `@signal-engine` — pour la logique de signal
