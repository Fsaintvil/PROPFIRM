---
name: market-regime
description: Détection de régimes de marché — ADX/ATR/MA, classification en 5 regimes (TREND_UP/DOWN, RANGING, HIGH_VOL, LOW_VOL), adaptation SL/TP/risque par régime. Utilise regime.py (RegimeDetector) et ftmo_config.py.
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

### Détection du régime (engine_simple/regime.py — classe RegimeDetector)
```python
adx_val = self._calc_adx(high, low, close)   # ADX 14
atr_arr = atr(high, low, close, 14)
atr_pct = atr_val / np.mean(close[-20:])     # ratio ATR/prix (pas un percentile)
ma20 = np.mean(close[-20:])
ma20_prev = np.mean(close[-40:-20])
slope = (ma20 - ma20_prev) / ma20_prev      # pente MA20
```

### 5 régimes (seuils réels du code)
| Régime | Critère réel | Risque |
|--------|---------|-------|
| TREND_UP | ADX ≥ 22 (entrée) / < 18 (sortie, hystérésis) ET slope > +0.002 | 100% |
| TREND_DOWN | ADX ≥ 22 / < 18 ET slope < −0.002 | 100% |
| HIGH_VOL | `atr_pct ≥ 0.015` (ratio ATR/prix = 1.5%) | 70% |
| RANGING | ADX < 18 (sortie) ou slope entre ±0.002 | 100% |
| LOW_VOL | `atr_pct ≤ 0.003` (ratio ATR/prix = 0.3%) | 100% |

> ⚠️ **Nuances code réel** :
> - `VOL_HIGH_RATIO=0.015` et `VOL_LOW_RATIO=0.003` sont des **ratios ATR/prix FIXES**, PAS des percentiles (le champ `vol_percentile` dans le retour est une compat transformée `atr_pct/0.01`).
> - Hystérésis ADX par symbole : `_get_adx_thresholds(symbol)` lit `adx_thresh` du YAML (défauts entrée 22 / sortie 18). L'état `_prev_regime` est stocké PAR symbole pour éviter le bouncing.
> - Ordre de décision : d'abord `is_trending` (avec hystérésis), PUIS HIGH_VOL si `atr_pct ≥ 0.015`, PUIS LOW_VOL si `atr_pct ≤ 0.003`, sinon RANGING.
> - HIGH_VOL/LOW_VOL ne sont évalués QUE si pas trending.
> - Le régime MOM20x3 de signal utilise ADX ≥ 22 (seuils de signal), le régime de RISQUE utilise l'hystérésis entrée/sortie. C'est délibéré.

### Trailing ATR par régime (Config 31 Juillet 2026 R2 — 5 paliers, source ftmo_config.py)
Format : (lock_en_×ATR, trail_distance_en_×ATR)
| Régime | N1 lock | N1 trail | N2 lock | N2 trail | N3 lock | N3 trail | N4 lock | N4 trail | N5 lock | N5 trail |
|--------|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|
| RANGING | 1.80 | 0.80 | 2.50 | 0.55 | 3.50 | 0.40 | 5.00 | 0.25 | 5.50 | 0.15 |
| TREND_UP/DOWN | 1.80 | 1.00 | 2.50 | 0.70 | 3.50 | 0.50 | 5.00 | 0.30 | 6.00 | 0.20 |
| HIGH_VOL | 1.80 | 1.20 | 2.50 | 0.90 | 3.50 | 0.65 | 5.00 | 0.40 | 6.00 | 0.25 |
| LOW_VOL | 1.80 | 0.70 | 2.50 | 0.50 | 3.20 | 0.35 | 4.50 | 0.20 | 5.50 | 0.12 |

> ⚠️ Jitter ±10% FIXE par (ticket, palier) — tiré UNE fois quand le palier change (FIX 16 Août : avant, re-tiré chaque cycle, le ratchet poussait silencieusement le SL ~10% plus serré que la config backtestée).

### Partial TP buffer BE (Config 31 Juillet — BE_BUFFER_BY_REGIME)
| Régime | Buffer BE |
|--------|-----------|
| RANGING | 0.50×ATR |
| TREND_UP | 0.35×ATR |
| TREND_DOWN | 0.35×ATR |
| HIGH_VOL | 0.60×ATR |
| LOW_VOL | 0.30×ATR |

> Par symbole : XAUUSD 0.25-0.60, BTCUSD 0.35-0.80 selon régime (BE_BUFFER_BY_SYMBOL).

### Breakeven progressif (FIX 17 Août 2026)
Paliers BE_PROGRESSIVE_LEVELS (trailer.py) : 1.00×ATR→entry, 1.30→+0.15, 1.60→+0.30, 1.90→+0.45, 2.20→+0.60, 2.50→+0.75×ATR. Uniforme sauf NO_TRAILING_SYMBOLS.

### Exécution
```python
regime, meta = regime_detector.detect(high, low, close, adx_val, symbol)
# SL/TP par symbole via SymbolParamManager (strategy.py SYMBOL_CONFIG = source de vérité)
# trailing par régime via get_trailing_for_symbol(symbol, regime)
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
- HIGH_VOL a priorité sur RANGING, mais PAS sur TREND (l'ordre réel : is_trending d'abord, puis HIGH_VOL, puis LOW_VOL, sinon RANGING)
- Le régime est calculé PAR SYMBOLE (`_prev_regime[symbol]`), pas sur un USD index global
- La corrélation entre cryptos (BTCUSD, ETHUSD, corrélés 0.89) peut créer des régimes simultanés — les trades sont limités par la règle de corrélation (max 2/direction/groupe, YAML)
- En période de LOW_VOL prolongée, le trailing N1 est à 0.70×ATR → sorties relativement rapides
- **12:00 UTC = trou noir** — 0% WR historique sur forex. Depuis le 19 Août, le filtre `preferred_hours` LDN-NY [13-17h GMT] bloque les trades forex hors session (l'heure 12:00 UTC est donc exclue automatiquement)
- XAUUSD H1 peut alterner entre TREND_UP et RANGING brutalement en fonction des news macro — le trailing ATR protège mais les gaps restent un risque
- Les valeurs SL/TP par symbole ne viennent PAS du régime (contrairement à la doc obsolète) : elles sont lues depuis `strategy.py::SYMBOL_CONFIG` (source de vérité, ex: forex SL 3.0/TP 7.5 depuis le 19 Août) via `SymbolParamManager`
- `vol_percentile` dans le meta retourné = compat transformée (`atr_pct/0.01`), PAS un vrai percentile historique

## Fichiers clés
- `engine_simple/regime.py` — classe `RegimeDetector`, seuils ADX hystérésis + ratios ATR/prix
- `engine_simple/ftmo_config.py` — TRAILING_BY_REGIME, BE_BUFFER_BY_REGIME (source des tableaux ci-dessus)
- `engine_simple/ftmo_protector.py` — `_check_session` (preferred_hours), trailing ATR par régime
- `engine_simple/trailer.py` — trailing + partial TP + BE progressif + time-stop + weekend close
- `engine_simple/strategy.py` — SL/TP par symbole (source de vérité via SymbolParamManager)

## Tests
```powershell
python -m pytest tests/test_market_regime.py -v
python -m pytest tests/test_ftmo_protector.py -v
```

## Agents concernés
- `@optimizer` — ajuste les seuils par régime
- `@quant-auditor` — valide statistiquement les régimes
- `@signal-engine` — intègre le régime dans les signaux
