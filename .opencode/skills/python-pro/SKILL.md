# Skill Python Pro — Développement, Debug & Optimisation

## Quand l'utiliser
- Bug complexe nécessitant une analyse de stack trace, debugging pas-à-pas
- Profiling de performance (CPU, mémoire)
- Refactoring de code legacy
- Revue de code et patterns Python avancés
- Tests : fixtures, mocking, property-based testing

## 1. Debugging

### Lire une stack trace
```
Traceback (most recent call last):
  File "engine_simple/signal_pipeline.py", line 661, in _phase12_adaptive_params
    adapted = self.adaptive_params.get(symbol)
AttributeError: 'NoneType' object has no attribute 'get'
```
➡️ Cause racine : `self.adaptive_params` est `None` → pas initialisé dans `__init__`

### Techniques de debug
```python
# 1. Logging ciblé (ne JAMAIS utiliser print en prod)
logger.debug(f"[DEBUG] variable={variable} type={type(variable)}")

# 2. Vérification de type
if not isinstance(variable, dict):
    logger.error(f"Type inattendu: {type(variable)}, attendu dict")

# 3. Guard NaN/None
if variable is None:
    logger.warning(f"[GUARD] {symbol}: variable None, fallback à 0")
    return 0.0
```

### Profiling mémoire avec psutil
```python
import psutil
proc = psutil.Process()
mem_before = proc.memory_info().rss / 1024 / 1024
# ... code à profiler ...
mem_after = proc.memory_info().rss / 1024 / 1024
logger.info(f"[MEM] delta: {mem_after - mem_before:.1f}MB")
```

### Timing précis
```python
import time
t0 = time.perf_counter()
# ... code à timer ...
dt = time.perf_counter() - t0
if dt > 1.0:
    logger.warning(f"[SLOW] {fonction} a pris {dt:.2f}s")
```

## 2. Patterns Python indispensables

### NaN/None Guards (critique pour les calculs financiers)
```python
import numpy as np

def safe_divide(a, b, default=0.0):
    """Division sécurisée avec protection NaN/Zero/None."""
    if a is None or b is None:
        return default
    if abs(b) < 1e-12:  # évite division par zéro
        return default
    result = a / b
    if np.isnan(result) or np.isinf(result):
        return default
    return result
```

### Singleton avec lazy initialization
```python
class SymbolParamManager:
    """Singleton thread-safe pour les paramètres par symbole."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
```

### Cache avec TTL
```python
from functools import lru_cache
from datetime import datetime, timedelta

def ttl_cache(seconds=60):
    """Cache avec expiration temporelle."""
    def decorator(func):
        cache = {}
        def wrapper(*args, **kwargs):
            key = (args, tuple(kwargs.items()))
            now = datetime.utcnow()
            if key in cache:
                result, timestamp = cache[key]
                if (now - timestamp).total_seconds() < seconds:
                    return result
            result = func(*args, **kwargs)
            cache[key] = (result, now)
            return result
        return wrapper
    return decorator
```

### Rate Limiter (pour API MT5)
```python
import time
from collections import deque

class PerSymbolRateLimiter:
    """Limite les appels API par symbole (ex: 3 appels/10s)."""
    def __init__(self, max_calls=3, window=10.0):
        self.max_calls = max_calls
        self.window = window
        self._calls: dict[str, deque] = {}

    def acquire(self, symbol: str) -> bool:
        now = time.time()
        if symbol not in self._calls:
            self._calls[symbol] = deque()
        dq = self._calls[symbol]
        # Nettoyer les entrées trop vieilles
        while dq and dq[0] < now - self.window:
            dq.popleft()
        if len(dq) >= self.max_calls:
            return False  # Rate limit atteint
        dq.append(now)
        return True
```

## 3. Tests

### Fixtures pytest
```python
import pytest

@pytest.fixture
def mock_mt5():
    with patch('engine_simple.mt5_connector.MT5Connector') as mock:
        mock.initialize.return_value = True
        mock.account_info.return_value = MagicMock(balance=200000, equity=200000)
        yield mock

class TestSignalPipeline:
    def test_high_confidence_signal(self, mock_mt5):
        pipe = SignalPipeline(mock_mt5)
        result = pipe.process("EURUSD", {"score": 0.95, "adx": 30})
        assert result["action"] in ("BUY", "SELL")
        assert result["confidence"] >= 0.80
```

### Mocking avancé avec patch
```python
from unittest.mock import patch, MagicMock

class TestFTMOProtector:
    @patch('engine_simple.ftmo_protector.datetime')
    def test_cooldown_expiry(self, mock_dt):
        mock_dt.utcnow.return_value = datetime(2026, 7, 1, 12, 0, 0)
        protector = FTMOProtector(...)
        result = protector.can_trade("EURUSD")
        assert result is True  # cooldown expiré
```

### Property-based testing
```python
from hypothesis import given, strategies as st

@given(
    st.floats(min_value=0.001, max_value=100.0),  # ATR
    st.floats(min_value=0.0001, max_value=0.1),  # threshold
)
def test_signal_threshold_consistency(atr, threshold):
    """Vérifie que le signal est toujours dans [0, 1]."""
    strategy = MOM20x3Strategy()
    score = strategy.compute_score(momentum=atr * 1.5, atr=atr, thresh=threshold)
    assert 0.0 <= score <= 1.0
```

## 4. Profiling et Optimisation

### Identifier les goulots d'étranglement
```bash
python -m cProfile -s cumulative main.py 2>&1 | head -30
```

### Vérification mémoire
```python
import tracemalloc
tracemalloc.start()
# ... code à vérifier ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)
```

### Optimisation pandas (pour les données historiques)
```python
# Lent
for row in df.iterrows():
    process(row[1])

# Rapide (vectorisé)
df['signal'] = np.where(df['mom'] > df['thresh'], 1, -1)

# Mémoire (catégories au lieu de strings)
df['symbol'] = df['symbol'].astype('category')
```

## 5. Refactoring

### Extraction de fonctions pures
```python
# Avant (fonction impure, difficile à tester)
def process_trade(symbol):
    data = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 100)
    mom = data[-1]['close'] - data[-21]['close']
    if abs(mom) > 2.5 * np.std([d['close'] for d in data]):
        execute_trade(symbol)

# Après (séparation données / logique)
def compute_momentum(data: np.ndarray, period: int = 20) -> float:
    """Calcule le momentum sur une période. Fonction PURE, testable."""
    return float(data[-1] - data[-(period + 1)])

def should_trade(momentum: float, threshold: float) -> bool:
    return abs(momentum) > threshold
```

### Typage strict
```python
from typing import Optional, Union, Dict, List, Tuple

def get_signal_params(
    symbol: str,
    config: Dict[str, Union[float, int, str]]
) -> Optional[Dict[str, float]]:
    """Retourne les paramètres de signal pour un symbole.
    
    Args:
        symbol: Nom du symbole (ex: 'EURUSD')
        config: Configuration globale
        
    Returns:
        Dict avec 'threshold', 'min_score' ou None si symbole inconnu
    """
    if symbol not in config:
        logger.warning(f"Symbole {symbol} non trouvé dans la config")
        return None
    return {
        "threshold": config[symbol].get("threshold", 2.0),
        "min_score": config[symbol].get("min_score", 0.60),
    }
```

## 6. Commandes utiles

```bash
# Tests avec coverage
python -m pytest tests/ --tb=short -q --cov=engine_simple --cov-report=term

# Un seul test
python -m pytest tests/test_ftmo_protector.py::TestFTMO::test_can_trade -v

# Linting
python -m flake8 engine_simple/ --max-line-length=120

# Vérification des types
python -m mypy engine_simple/ --ignore-missing-imports

# Recherche de patterns dangereux
grep -rn "except:\|except Exception:" engine_simple/ --include="*.py"
grep -rn "print(" engine_simple/ --include="*.py"
```

## 7. Anti-patterns à éviter

```python
# ❌ Mauvais : except nu (cache toutes les erreurs)
try:
    result = risky_operation()
except:
    pass

# ✅ Bon : except spécifique
try:
    result = risky_operation()
except (ValueError, TypeError) as e:
    logger.error(f"Erreur opération: {e}")
    result = None

# ❌ Mauvais : mutation d'arguments modifiables
def process(data=[]):  # 👻 La liste par défaut est partagée !
    data.append(1)
    return data

# ✅ Bon
def process(data=None):
    if data is None:
        data = []
    data.append(1)
    return data

# ❌ Mauvais : comparaison flottante directe
if mom == 0.0:  # 👻 Les floats ne sont jamais exacts

# ✅ Bon
if abs(mom) < 1e-10:
```

## Liens vers le projet
- `engine_simple/signal_pipeline.py` — 12 phases de filtrage, profiling essentiel
- `engine_simple/adaptive_intelligence.py` — OnlineLearner, optimisation mémoire
- `engine_simple/ftmo_protector.py` — Règles FTMO, beaucoup de edge cases
- `tests/` — 649 tests, fixtures dans `tests/conftest.py`
