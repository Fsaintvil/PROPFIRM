---
name: mt5-operations
description: Infrastructure MT5 — connexion, reconnection, API error handling, OrderValidator, PerSymbolRateLimiter, PID lock, retry logic. Utilise mt5_connector.py et order_validator.py.
---

# MT5 Operations Skill

## Description
Expert en infrastructure MT5 : connexion, API, reconnection, gestion des erreurs, stability, resilience.

## Quand utiliser
- Pour diagnostiquer des problèmes de connexion MT5
- Pour analyser/modifier `mt5_connector.py`
- Pour comprendre pourquoi un ordre est rejeté
- Pour auditer la robustesse de l'infrastructure

## Architecture

### Connexion MT5
```python
# mt5_connector.py
def initialize():
    mt5.initialize(path=TERMINAL_PATH, timeout=30000, portable=True)
    
def _ensure_connection():
    if not mt5.terminal_info():
        mt5.shutdown()
        mt5.initialize()
```

**Points clés :**
- Timeout de 30s pour l'initialisation (était 60s, FIX #14)
- `portable=True` pour éviter les conflits de registre Windows
- Auto-reconnect dans la boucle 15s de `main.py`
- WaitForTerminal avant chaque opération
- Shutdown + reinitialize si terminal_info() échoue
- `symbol_select(True)` appelé pour tous les symboles à la connexion (FIX #31)
- `max_connect_attempts=5` (était 30, évite 22min de hang, FIX #14)

### Gestion des erreurs API

| Erreur | Cause | Action |
|--------|-------|--------|
| -1 (EROR_INTERNAL_ERROR) | Erreur interne MT5 | Retry ×3, pause 30s |
| 10004 (EROR_NO_MONEY) | Fonds insuffisants | Log critique, stop trades |
| 10006 (EROR_REQUOTE) | Prix changé | IOC→RETURN filling fallback, retry 1x |
| 10008 (EROR_INVALID_STOPS) | SL/TP trop serré | Rejeter trade |
| 10009 (TRADE_RETCODE_DONE) | Ordre exécuté | Vérifier avec _confirm_position() que la position MT5 existe |
| 10013 (EROR_INVALID_TRADE_VOLUME) | Lot invalide | Ajuster lot |
| 10014 (EROR_MARKET_CLOSED) | Marché fermé | Attendre prochain cycle |
| 10018 (EROR_TOO_MANY_REQUESTS) | Rate limit MT5 | IOC→RETURN filling fallback, pause 5s |
| 10025 (EROR_CONNECTION_LOST) | Connexion perdue | IOC→RETURN filling fallback, _ensure_connection() |

**IOC→RETURN filling fallback** : Pour les retcodes 10006/10018/10025, on retente avec `ORDER_FILLING_RETURN` au lieu de `ORDER_FILLING_IOC` (FIX #22).

### Retry logic
```python
def place_order(max_retries=3):
    for attempt in range(max_retries):
        try:
            result = mt5.order_send(request)
            if result.retcode == 10009:  # TRADE_RETCODE_DONE
                return result
            time.sleep(0.2)  # Intervalle de 0.2s entre tentatives
        finally:
            # Garantir la libération des slots rate limiter même en cas d'exception
            rate_limiter.release(request.symbol)
    return None
```

**Points clés :**
- 3 tentatives max par ordre
- 0.2s intervalle entre tentatives (était 1s)
- `try/finally` garanti pour libérer les slots du `PerSymbolRateLimiter` même en cas d'exception (FIX #22)

### OrderValidator
- Vérifie SL/TP présents
- Vérifie spread < max_spread_points (120 pts, augmenté pour crypto BTC/SOL/LNK/BNB)
- Vérifie lot dans limites
- Vérifie RR ≥ 2.0
- Vérifie direction (buy/short allowed)

### PerSymbolRateLimiter
- **Max 1 trade/min/symbole** (FIX #22, était 3)
- **Intervalle minimum 5 min entre deux trades sur le même symbole**
- Cause racine résolue des RATE LIMIT permanents (FIX #5+#8)

### PID Lock
- `runtime/robot.pid` contient le PID
- Vérifié au démarrage : si PID existant encore actif → abandon
- Nettoyé à l'arrêt (finally block)
- Empêche les instances dupliquées

## Ordre d'exécution dans execute() (FIX A1+A2 — Juin 2026)

L'ordre correct pour éviter les RATE LIMIT permanents :

```python
def execute(trade):
    # 1. Vérifier doublon (position déjà ouverte ?)
    if is_duplicate(trade):
        return None
    
    # 2. Vérifier prix (market open ?)
    if not check_price(trade):
        return None
    
    # 3. Vérifier SL/TP (OBLIGATOIRE)
    if not trade.sl or not trade.tp:
        return None  # Refusé par 3 points de contrôle
    
    # 4. Calculer lot
    lot = calculate_lot(trade)
    
    # 5. OrderValidator.validate()
    if not validator.validate(trade):
        return None  # Refusé (spread, RR, direction, etc.)
    
    # 6. **ENFIN** rate_limiter.allow()  ← DERNIÈRE barrière
    if not rate_limiter.allow(trade.symbol):
        return None  # Rate limit — timestamp PAS consommé
    
    # 7. place_order()
    return mt5.order_send(request)
```

**Pourquoi ?** Si `rate_limiter.allow()` est appelé trop tôt, le timestamp est consommé
même si le trade est ensuite refusé par `OrderValidator` → RATE LIMIT permanent.

## 3-points SL/TP Check (FIX #3 — Juin 2026)

Tout trade SANS Stop Loss est REFUSÉ par 3 points de contrôle indépendants :

1. `ftmo_protector.can_trade()` → vérifie SL présent
2. `OrderValidator.validate()` → valide SL > 0
3. `TradeExecutor.execute()` → refuse si SL absent

## Traitement des retours MT5 (numpy arrays)

Depuis le fix F (Juin 2026) :

```python
# Dans main.py, avant Anticipation Engine :
if isinstance(rates, np.ndarray):
    rates = pd.DataFrame(rates)  # ← Conversion explicite
```

**Pourquoi ?** MT5 retourne parfois `numpy.ndarray` au lieu de `list`. 
L'ancien code supposait `list` et plantait sur `'numpy.ndarray' object has no attribute 'values'`.

## Stratégie de retry (PerSymbolRateLimiter)

| Composant | Limite | Remplace |
|-----------|--------|----------|
| `PerSymbolRateLimiter` | 1 trade/min/symbole | PerSymbolRateLimiter |
| Intervalle min | 5 min entre 2 trades sur même symbole | - |
| Max retries | 3 tentatives par ordre | - |
| Pause après échec | 30s (cooldown MT5) | - |

## Pièges connus
- MT5 renvoie parfois des `tuple` ou `numpy.ndarray` au lieu de list — TOUJOURS convertir avec `list()` ou `pd.DataFrame()` avant traitement
- `mt5.copy_rates_from_pos()` peut retourner `None` sans erreur — vérifier avant usage
- Le terminal MT5 doit être en cours d'exécution — le robot ne le lance pas automatiquement
- Magic number = 999001 (ne pas changer sans coordination)
- **L'ordre d'exécution** dans `execute()` est CRITIQUE : `rate_limiter.allow()` DOIT être la dernière vérification avant l'envoi
- **Les SL/TP sont placés côté MT5** et survivent à un redémarrage du robot — mais PAS à un redémarrage du terminal
- **numpy.ndarray** peut arriver de MT5 à tout moment (surtout après une reconnexion) — toujours prévoir la conversion

## Fichiers clés
- `engine_simple/mt5_connector.py` — connexion, reconnection
- `engine_simple/order_validator.py` — validation ordres
- `engine_simple/rate_limiter.py` — PerSymbolRateLimiter
- `main.py:240-280` — _ensure_connection() dans la boucle

## Tests
```powershell
python -m pytest tests/test_mt5_connector.py -v
python -m pytest tests/test_order_validator.py -v
python -m pytest tests/test_rate_limiter.py -v
```

## Agents concernés
- `@mt5-infrastructure-auditor` — audite la résilience
- `@monitor-agent` — surveille la connexion
- `@auto-fixer` — corrige les bugs d'infra
- `@performance-engineer` — mesure les temps de cycle
- `@security-auditor` — chasse les fuites mémoire