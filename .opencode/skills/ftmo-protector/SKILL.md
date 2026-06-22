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
```python
# 4 niveaux progressifs par régime
profit > 1.0×ATR → SL = peak - 0.50×ATR (RANGING)
profit > 2.0×ATR → SL = peak - 0.35×ATR
profit > 3.0×ATR → SL = peak - 0.20×ATR
profit > 5.0×ATR → SL = peak - 0.10×ATR
```

### 2. Régimes → Niveaux trailing
| Régime     | 1er lock | N1    | N2    | N3    | N4    |
|------------|----------|-------|-------|-------|-------|
| RANGING    | 1.0×ATR  | 0.50  | 0.35  | 0.20  | 0.10  |
| TREND_UP   | 1.0×ATR  | 0.80  | 0.50  | 0.30  | 0.15  |
| TREND_DOWN | 1.0×ATR  | 0.80  | 0.50  | 0.30  | 0.15  |
| HIGH_VOL   | 1.0×ATR  | 1.00  | 0.70  | 0.50  | 0.25  |
| LOW_VOL    | 1.0×ATR  | 0.40  | 0.25  | 0.15  | 0.08  |

### 3. Règles de risque
| Règle | Valeur | Code |
|-------|--------|------|
| RISK_PER_TRADE | 0.004 (0.4%) | Config |
| MAX_DD_PCT | 10% | FTMoprotector |
| MAX_DAILY_LOSS_PCT | 2% | FTMoprotector |
| CONSISTENCY_MAX_PCT | 30% | FTMoprotector |
| MIN_TRADING_DAYS | 10 | FTMoprotector |
| AUTO_PAUSE_LOSSES | 5 | FTMoprotector |
| COOLDOWN_MINUTES | 15 | FTMoprotector |
| MAX_POSITIONS | 5 | Config |
| MAX_SPREAD_POINTS | 120 | Config |
| MIN_RR_RATIO | 2.0 | FTMoprotector |
| MAX_CORRELATED_EXPOSURE | 1.5 | FTMoprotector |
| CIRCUIT_BREAKER_DD_PCT | 8% | FTMoprotector |

### 4. Corrélation (max 2/direction/groupe)
- Groupe Crypto: BTCUSD (corrélations US500.cash 0.30)
- Groupe Métal: XAUUSD
- Groupe Indice: US500.cash
- Pas plus de 2 trades dans la même direction par groupe crypto

### 5. Cooldown & Pause
- **Cooldown** : 15 min après une perte
- **Pause** : après 5 pertes consécutives (reset après X bougies sans trade)
- **Weekend** : samedi-dimanche avant 22h UTC → bloqué

### 6. SL obligatoire (3 points de contrôle)
1. `ftmo_protector.can_trade()` → refuse tout trade sans SL
2. `OrderValidator.validate()` → valide SL présent
3. `TradeExecutor.execute()` → refuse si SL absent

### 7. Règle de consistance
- Si un jour représente > 30% du profit total → le trade est refusé
- Calculé sur `challenge_initial_balance` (invariant)

## Pièges connus
- `challenge_initial_balance` est capturé UNE SEULE fois au premier lancement (dans `robot_state.json`)
- Le cooldown est reset au changement de jour UTC
- La pause après 5 pertes consécutives est reset si pas de trade pendant X bougies
- Ne JAMAIS modifier `MIN_RR_RATIO` en dessous de 2.0 sans passer par le Risk Marshal
- **Le trailing est basé sur ATR H1** — si le timeframe n'est pas disponible, le calcul échoue
- **best_day_pct** reconstruit depuis l'historique (fix Juin 2026) — ne plus utiliser de valeur statique
- **Matrice de corrélation Pearson** calculée en temps réel depuis les données MT5 H1 (4 symboles actifs : XAUUSD, BTCUSD, ETHUSD, US500.cash) — BTC/ETH corrélés 0.89, autres décorrélés (0.10-0.30), limité à 2 trades/direction/groupe
- Le **CIRCUIT_BREAKER_DD_PCT** à 8% déclenche un arrêt d'urgence avant d'atteindre les 10% FTMO, donnant une marge de sécurité de 2%
- **Partial TP** persisté dans `robot_state.json` — évite les doubles TP au redémarrage

## Fichiers clés
- `engine_simple/ftmo_protector.py` — toutes les règles
- `engine_simple/position_tracker.py` — trailing + partial TP
- `config_simple.py` — RISK_PER_TRADE, MAX_DD_PCT, etc.

## Tests
```powershell
python -m pytest tests/test_ftmo_protector.py -v
python -m pytest tests/test_position_tracker.py -v
```

## Agents concernés
- `@risk-compliance` — veto sur DD > 8%
- `@auto-fixer` — corrige les bugs de protection
- `@quant-auditor` — valide les métriques de protection