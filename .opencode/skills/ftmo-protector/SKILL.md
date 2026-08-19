---
name: ftmo-protector
description: Règles de protection FTMO — trailing ATR, drawdown 10%, daily loss 2%, consistance 30%, cooldown, corrélation, SL obligatoire. Utilise ftmo_protector.py et position_tracker.py.
---

# FTMO Protector Skill

## Description
Spécialiste des règles de protection FTMO : trailing stop ATR, drawdown, daily loss, consistance, cooldown, corrélation. Garantit la survie du compte financé.

## Quand utiliser
- Pour analyser/modifier `ftmo_protector.py`
- Pour comprendre pourquoi un trade est refusé
- Pour vérifier les règles de consistance FTMO
- Pour ajuster les niveaux de trailing ou de risque

## Architecture

### Protection FTMO — 7 barrières

### 1. ATR Trailing (remplace peak-fixe)
🔧 **Config 31 Juillet 2026 (Quant Auditor — R2)** : le trailing serré du 30/07 (N1 lock 1.20×ATR) avait été calibré sur un WR 35% corrompu (direction inversée dans le CSV). Preuve : 62.4% des gagnants sortaient à <0.5R, 95% n'atteignaient jamais le TP, payout 1.41 < breakeven 1.55. → revert vers la config du 21-22 Juillet validée en backtest.

### 2. Régimes → Niveaux trailing (Config 31 Juillet 2026 R2 — 5 paliers)
Format : (lock_en_×ATR, trail_distance_en_×ATR) — source `ftmo_config.py::TRAILING_BY_REGIME`
| Régime     | N1 lock | N1 trail | N2 lock | N2 trail | N3 lock | N3 trail | N4 lock | N4 trail | N5 lock | N5 trail |
|------------|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|
| RANGING    | 1.80    | 0.80     | 2.50    | 0.55     | 3.50    | 0.40     | 5.00    | 0.25     | 5.50    | 0.15     |
| TREND_UP   | 1.80    | 1.00     | 2.50    | 0.70     | 3.50    | 0.50     | 5.00    | 0.30     | 6.00    | 0.20     |
| TREND_DOWN | 1.80    | 1.00     | 2.50    | 0.70     | 3.50    | 0.50     | 5.00    | 0.30     | 6.00    | 0.20     |
| HIGH_VOL   | 1.80    | 1.20     | 2.50    | 0.90     | 3.50    | 0.65     | 5.00    | 0.40     | 6.00    | 0.25     |
| LOW_VOL    | 1.80    | 0.70     | 2.50    | 0.50     | 3.20    | 0.35     | 4.50    | 0.20     | 5.50    | 0.12     |

> ⚠️ **Trailing par SYMBOLE** (`TRAILING_BY_SYMBOL`) : XAUUSD (lock 1.0-1.5×ATR, trail serré) et BTCUSD (lock 2.0-2.5×ATR, trail large pour laisser respirer les wicks crypto) ont leurs propres niveaux qui OVERRIDENT le fallback par régime. La doc AGENTS.md détaille ces tableaux.
> ⚠️ **NO_TRAILING_SYMBOLS** = {US500.cash, US100.cash, JP225.cash} : pas de trailing ni partial TP (Solution A, indices optimisés FTMO : threshold 4.0×ATR, SL 1.5×ATR, TP 6.0×ATR).

### 2b. Breakeven progressif (FIX 17 Août 2026 — montée quasi-linéaire)
Sécurisation AVANT le trailing N1 (1.80×ATR). Paliers `BE_PROGRESSIVE_LEVELS` (trailer.py) :
```
profit > 1.00×ATR → SL = entry                (breakeven pur)
profit > 1.30×ATR → SL = entry ± 0.15×ATR
profit > 1.60×ATR → SL = entry ± 0.30×ATR
profit > 1.90×ATR → SL = entry ± 0.45×ATR
profit > 2.20×ATR → SL = entry ± 0.60×ATR
profit > 2.50×ATR → SL = entry ± 0.75×ATR    (raccord au lock N1 BTCUSD TREND)
```
> 🔧 FIX 17/08 : avant ce fix, le SL restait FIXE à entry+0.15×ATR entre 1.30×ATR et le lock N1 (zone morte). Uniforme pour tous les symboles SAUF NO_TRAILING_SYMBOLS (BE désactivé). Fonctionne avec la garde `sl_improves` : n'applique QUE si le nouveau SL est meilleur.

### 2c. Partial TP (31 Juillet R3 + FIX 19 Août)
- Déclenchement : progress ≥ **0.65** (65% du chemin vers le TP, calculé sur le PEAK pas price_current — FIX 30 Juillet)
- Fraction fermée : **75%** du volume (FIX 19 Août backtest : 50%→75%, pertes −1.6%, DD −0.2pt, arXiv 2604.27150), quantisée au lot_step (arrondi demi-vers-le-haut, FIX 16 Août)
- Puis set BE avec buffer par symbole/régime (`BE_BUFFER_BY_SYMBOL` / `BE_BUFFER_BY_REGIME`)
- Persisté dans `state.json` → évite les doubles TP au redémarrage

### 3. Règles de risque
| Règle | Valeur | Code |
|-------|--------|------|
| RISK_PER_TRADE | 0.003 (0.3%, production override) | Config |
| MAX_DD_PCT | 10% | FTMoprotector |
| MAX_DAILY_LOSS_PCT | 2% (5% FTMO 2026) | FTMoprotector |
| CONSISTENCY_MAX_PCT | 30% (⚠️ cap désactivé en mode preuve GR) | FTMoprotector |
| MIN_TRADING_DAYS | 10 | FTMoprotector |
| AUTO_PAUSE_LOSSES | 5 | FTMoprotector |
| COOLDOWN_MINUTES | 15 | FTMoprotector |
| MAX_POSITIONS | 8 | Config |
| MAX_POSITIONS_PER_SYMBOL | 4 | Config |
| MAX_TRADES_PER_DAY | 75 | Config |
| MAX_SPREAD_POINTS | 120 | Config |
| MIN_RR_RATIO | 2.0 (mais min_rr forex = 1.5 dans strategy.py) | FTMoprotector |
| MAX_CORRELATED_EXPOSURE | 1.5 | FTMoprotector |
| CIRCUIT_BREAKER_DD_PCT | 8% | FTMoprotector |

### 3b. Session LDN-NY (FIX 19 Août 2026)
- `preferred_hours` = [13,14,15,16,17] GMT sur les 7 paires forex majeures (EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, AUDUSD, NZDUSD) — backtest : DD 30.3→9.0%, pertes −74%
- Filtre strict dans `_check_session` (signal ≠ None) ; indices, crypto et XAUUSD restent 24/7

### 4. Corrélation (max 2/direction/groupe)
- Groupes : CRYPTO (BTCUSD/ETHUSD/SOLUSD/BNBUSD), COMMODITIES (XAUUSD/XAGUSD/USOIL/UKOIL/NATGAS), INDICES (6), FOREX_MAJORS (7), FOREX_CROSSES (4)
- Config YAML `correlation.max_trades_per_direction_in_group=2`, `max_trades_per_group=3`
- BTC/ETH corrélés 0.89, autres décorrélés (0.10-0.30)

### 5. Cooldown & Pause
- **Cooldown** : 15 min après une perte
- **Pause** : après 5 pertes consécutives (reset après X bougies sans trade)
- **Weekend** : fermeture pré-weekend pour symboles `weekend_trading=false` (max 2h avant clôture vendredi ≥ 16h UTC, FIX 18 Août)

### 6. SL obligatoire (3 points de contrôle)
1. `ftmo_protector.can_trade()` → refuse tout trade sans SL
2. `OrderValidator.validate()` → valide SL présent
3. `TradeExecutor.execute()` → refuse si SL absent

### 7. Règle de consistance
- Si un jour représente > 30% du profit total → le trade est refusé
- Calculé sur `challenge_initial_balance` (invariant)
- ⚠️ `CONSISTENCY_CAP_ENABLED=false` en mode preuve GR (compte démo) — reste `true` par défaut pour un vrai challenge

## Pièges connus
- `challenge_initial_balance` est capturé UNE SEULE fois au premier lancement (dans `robot_state.json`)
- Le cooldown est reset au changement de jour UTC
- La pause après 5 pertes consécutives est reset si pas de trade pendant X bougies
- Ne JAMAIS modifier `MIN_RR_RATIO` en dessous de 2.0 sans passer par le Risk Marshal (⚠️ sauf min_rr par symbole dans strategy.py : forex 1.5, NZDUSD 1.3, SOLUSD 1.8)
- **Le trailing utilise l'ATR du TIMEFRAME du symbole** (FIX 16 Août : H1 pour la plupart, H4 pour XAUUSD — avant, un ATR H1 était utilisé pour les positions XAUUSD signalées H4, cohérence cassée)
- **best_day_pct** reconstruit depuis l'historique (fix Juin 2026) — ne plus utiliser de valeur statique
- **Matrice de corrélation Pearson** calculée en temps réel depuis les données MT5 (groupes YAML, max 2 trades/direction/groupe)
- Le **CIRCUIT_BREAKER_DD_PCT** à 8% déclenche un arrêt d'urgence avant d'atteindre les 10% FTMO, donnant une marge de sécurité de 2%
- **Partial TP** persisté dans `state.json` — évite les doubles TP au redémarrage
- **Partial TP 75%** : quantisation au lot_step demi-vers-le-haut (FIX 16 Août — round() Python = arrondi bancaire biaisé)
- **Session LDN-NY** : `_check_session` ne filtre preferred_hours QUE si `signal is not None` — le pré-check `can_trade(symbol, check_danger_hours=False)` sans signal passe toujours (comportement normal)

## Fichiers clés
- `engine_simple/ftmo_protector.py` — toutes les règles (can_trade, _check_session, calculate_lot, refresh_symbol_limits)
- `engine_simple/trailer.py` — trailing ATR + partial TP 75% + BE progressif + time-stop + weekend close
- `engine_simple/ftmo_config.py` — TRAILING_BY_REGIME, TRAILING_BY_SYMBOL, BE_BUFFER_BY_REGIME/SYMBOL, NO_TRAILING_SYMBOLS, RISK_MULT_CAP
- `config_simple.py` — RISK_PER_TRADE, MAX_POSITIONS, MAX_DD_PCT, etc.
- `config/default.yaml` — source de vérité des limites par symbole (SL/TP, preferred_hours, max_lot)

## Tests
```powershell
python -m pytest tests/test_ftmo_protector.py -v
python -m pytest tests/test_position_tracker.py -v
```

## Agents concernés
- `@risk-compliance` — veto sur DD > 8%
- `@auto-fixer` — corrige les bugs de protection
- `@quant-auditor` — valide les métriques de protection