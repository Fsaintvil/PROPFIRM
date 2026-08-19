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

### Périodes et seuils par symbole (source de vérité : strategy.py::SYMBOL_CONFIG)
Momentum period = **20 pour tous les symboles** (il n'y a plus de période par symbole — les anciennes valeurs XAUUSD=30/BTCUSD=20/ETHUSD=24 sont obsolètes).

| Symbole | Min Score | SL trending | TP trending | SL ranging | TP ranging | Min RR |
|---------|:---------:|:-----------:|:-----------:|:----------:|:----------:|:------:|
| US100.cash | 0.50 | 1.5 | 6.0 | 1.5 | 6.0 | 2.0 |
| US30.cash | 0.60 | 1.5 | 4.5 | 1.2 | 3.0 | 1.5 |
| JP225.cash | 0.50 | 1.5 | 6.0 | 1.5 | 6.0 | 2.0 |
| SOLUSD | 0.60 | 2.5 | 5.0 | 2.0 | 4.0 | 1.8 |
| BTCUSD | 0.50 | 1.5 | 6.0 | 1.5 | 6.0 | 2.0 |
| XAUUSD | 0.65 | 1.5 | 6.0 | 1.5 | 6.0 | 2.0 |
| **7 paires forex** (EURUSD/GBPUSD/USDJPY/USDCAD/AUDUSD/USDCHF) | 0.60 | **3.0** | **7.5** | **2.25** | **6.0** | 1.5 |
| NZDUSD | 0.60 | **3.0** | **7.5** | **2.25** | **6.0** | 1.3 |

> 🔧 **19 Août 2026 (Backtest Optimizations)** : les 7 paires forex majeures sont passées de SL 2.0/TP 5.0 (trending) et 1.5/4.0 (ranging) à **SL 3.0/TP 7.5** et **2.25/6.0** (RR 2.5/2.67 conservé). Backtest 7 paires H1 2012-2026 : WR 57.4→64.3%, DD 30.3→10.2%, pertes −76%. ⚠️ PF reste < 1.0 après coûts (forex structurellement perdant).
> 🔧 **19 Août 2026** : `preferred_hours` [13-17h GMT] filtrés par `ftmo_protector._check_session` sur les 7 paires forex (session LDN-NY).
> 🔧 **18 Août 2026** : filtre phase 1e `_phase1e_extension_filter` (signal_pipeline.py) — rejette BUY si prix étendu > `max_extension_atr` (activé AUDUSD à 1.5×ATR).

### Seuils de signal
- ADX ≥ 22 → trending → thresh = 2.5×ATR
- ADX < 22 → ranging → thresh = 2.0×ATR
- Plafonné à 2.5×ATR max, plancher à 1.5×ATR
- Min RR par symbole (voir tableau) — signal_validator L.235 vérifie que `tp-sl ≥ min_rr × sl_dist`

## Performances live
Voir `runtime/performance_history.json` pour les métriques live actualisées (WR, PnL, PF, drawdown par symbole et par fenêtre glissante).

## Configuration
Per-symbol parameters are in `engine_simple/strategy.py` (SYMBOL_CONFIG dict) and `config/default.yaml` (symbol_limits section).

## Pièges connus
- `if not self.rates:` plante sur numpy array — TOUJOURS utiliser `if self.rates is None`
- Le backtest multi-TF utilise une version simplifiée (pas de ADX slope, pas de DI filter) → surestime les performances
- Le min_score coupe le signal même si MOM20x3 est valide — le seuil varie par symbole (0.50-0.65, voir tableau)
- **Corrélation crypto** : BTC/ETH sont fortement corrélés (>0.75). Le contrôle via matrice Pearson + max 2 trades/direction/groupe (YAML) limite les pertes simultanées
- **NaN/Inf guard** : si un momentum est NaN ou Inf, le signal est ignoré silencieusement (log debug)
- **XAUUSD H4** gagnant depuis 2021 mais a subi -71% WR sur 2013-2020 (bear market or). Surveillance active du DD
- **Forex structurellement perdant après coûts** : même optimisé (SL 3.0, session LDN-NY), PF ≈ 0.78 en backtest avec coûts réels → le forex est collectionné pour la Règle d'Or (13 symboles) mais l'edge vient des indices/crypto/XAUUSD
- **SL/TP forex depuis le 19 Août** : 3.0/7.5 (trending) et 2.25/6.0 (ranging) — NE PAS revenir à 2.0/5.0 sans nouveau backtest
- **Filtre session LDN-NY** : les signaux forex générés hors [13-17h GMT] sont rejetés par `_check_session` (pas de signal forex hors session) → ne pas s'étonner de l'absence de trades forex le matin

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
