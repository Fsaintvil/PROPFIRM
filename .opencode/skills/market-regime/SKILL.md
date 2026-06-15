---
name: market-regime
description: Détection de régimes de marché — ADX/ATR/MA, classification en 5 regimes (TREND_UP/DOWN, RANGING, HIGH_VOL, LOW_VOL), adaptation SL/TP/risque par régime. Utilise market_regime.py.
---

# Market Regime Skill

## Description
Expert en détection de régimes de marché : ADX, ATR, MA, classification en 5 régimes, adaptation des paramètres SL/TP/risque par régime.

## Quand utiliser
- Pour analyser/modifier `market_regime.py`
- Pour comprendre pourquoi un régime spécifique est détecté
- Pour ajuster les seuils ADX/ATR par régime
- Pour déboguer le trailing ATR qui ne se déclenche pas

## Architecture

### Détection du régime (market_regime.py)
```python
adx = calculate_adx(close, high, low, period=14)
atr = calculate_atr(close, high, low, period=14)
ma_short = sma(close, 20)
ma_long = sma(close, 50)
ma_slope = (ma_short[-1] - ma_short[-5]) / ma_short[-5]
```

### 5 régimes
| Régime | Critère | SL | TP | Risque |
|--------|---------|----|----|--------|
| TREND_UP | ADX>22, MA>0.2% | 2.0×ATR | 5.0×ATR | 100% |
| TREND_DOWN | ADX>22, MA<-0.2% | 2.0×ATR | 5.0×ATR | 100% |
| HIGH_VOL | ATR%>1.5% | 2.0×ATR | 5.0×ATR | 70% |
| RANGING | ADX<18 | 1.5×ATR | 4.0×ATR | 100% |
| LOW_VOL | ATR%<0.3% | 1.5×ATR | 4.0×ATR | 100% |

**Note :** Le MarketRegime utilise ADX hystérésis (entrée 22, sortie 18), tandis que les seuils de signal MOM20x3 utilisent ADX≥25. C'est délibéré — le régime sert à ajuster le risque, pas à générer des signaux.

### Trailing ATR par régime
| Régime | 1er lock | N1 | N2 | N3 | N4 |
|--------|----------|----|----|----|----|
| RANGING | 1.0×ATR | 0.50 | 0.35 | 0.20 | 0.10 |
| TREND_UP | 1.0×ATR | 0.80 | 0.50 | 0.30 | 0.15 |
| TREND_DOWN | 1.0×ATR | 0.80 | 0.50 | 0.30 | 0.15 |
| HIGH_VOL | 1.0×ATR | 1.00 | 0.70 | 0.50 | 0.25 |
| LOW_VOL | 1.0×ATR | 0.40 | 0.25 | 0.15 | 0.08 |

### Partial TP par régime (buffer BE)
| Régime | Buffer BE |
|--------|-----------|
| RANGING | 0.80×ATR |
| TREND_UP | 0.60×ATR |
| TREND_DOWN | 0.60×ATR |
| HIGH_VOL | 1.00×ATR |
| LOW_VOL | 0.50×ATR |

### Exécution dans main.py
```python
regime = market_regime.detect(usd_rates)  # Détection sur USD index

# Ajustement SL/TP selon régime
sl_mult = MARKET_REGIMES[regime]["sl"]
tp_mult = MARKET_REGIMES[regime]["tp"]
risk_mult = MARKET_REGIMES[regime]["risk"]
```

## Performances live par heure (3 symboles actifs)

Observations basées sur les 3 symboles actifs (XAUUSD, BTCUSD, US500.cash) :

| Heure UTC | WR | PnL | Régime typique | Verdict |
|-----------|-----|-----|----------------|---------|
| 10:00 | 100% | +$318 | TRENDING | ✅ Trader cette heure |
| 12:00 | **0%** | **-$687** | RANGING | 🔴 **Bloquer cette heure** |
| 14:00 | 71% | +$261 | TRENDING | ✅ |
| 16:00 | 100% | +$450 | HIGH_VOL | ✅ |
| 03:00 | 50% | +$206 | RANGING | ⚠️ Médiocre |
| 22:00 | 50% | +$39 | LOW_VOL | ⚠️ |
| 23:00 | 60% | +$10 | RANGING | ⚠️ |

## Pièges connus
- ADX est un oscillateur retardé — il peut mettre plusieurs bougies à détecter un changement de régime
- HIGH_VOL a priorité sur TREND (vérifié en premier dans le code)
- Le régime de référence est calculé sur l'USD index, PAS par symbole individuellement
- La corrélation entre cryptos (BTC, SOL, LNK, BNB) >0.70 peut créer des régimes simultanés — les trades sont limités par la règle de corrélation (max 2/direction/groupe)
- En période de LOW_VOL prolongée, le trailing est très serré (0.40×ATR au N1) → plus de sorties rapides
- Le régime RANGING (ADX<18) génère des trades mais avec RR plus faible (1.5×/4.0× au lieu de 2.0×/5.0×)
- **12:00 UTC = trou noir** — 0% WR historique sur forex. Surveillance active sur les 3 symboles (XAUUSD, BTCUSD, US500.cash)
- XAUUSD H1 peut alterner entre TREND_UP et RANGING brutalement en fonction des news macro — le trailing ATR protège mais les gaps restent un risque
- Le bypass score ≥ 0.80 peut laisser passer des trades en range serré — risque de faux breakout

## Fichiers clés
- `engine_simple/market_regime.py` — détection régime
- `engine_simple/ftmo_protector.py` — trailing ATR par régime
- `engine_simple/position_tracker.py` — partial TP par régime
- `main.py` — boucle de détection et application

## Tests
```powershell
python -m pytest tests/test_market_regime.py -v
python -m pytest tests/test_position_tracker.py -v
```

## Agents concernés
- `@market-philosopher` — questionne la logique économique des régimes
- `@adversarial-trader` — teste les changements de régime brutaux
- `@optimizer` — ajuste les seuils par régime
- `@alpha-researcher` — analyse la performance par régime
- `@quant-auditor` — valide statistiquement les régimes
