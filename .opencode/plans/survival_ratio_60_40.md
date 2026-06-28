# Plan : Rééquilibrage Survie 60% / Rendement 40%

## Objectif
Passer le système de risque de 84% Survie / 16% Rendement → 60% Survie / 40% Rendement.

## Fichier : `engine_simple/ftmo_protector.py`

### A1 — Supprimer ADX market filter (redondant avec strategy.py)
**Lignes 795-799 :**
```python
# AVANT:
# ADX Market Filter: si >50% des symboles ont ADX<22, risk × 0.50
adx_mult = self._adx_market_risk_mult()
if adx_mult < 1.0:
    risk_amount *= adx_mult
    logger.debug(f"  [ADX FILTER] risk × {adx_mult:.2f} = ${risk_amount:.2f}")

# APRÈS: supprimer ces 5 lignes
```

### A2 — Supprimer Daily profit limit ×0.25 (redondant avec Zone 2)
**Lignes 801-804 :**
```python
# AVANT:
# Daily profit limit: risk reduit a 25%
if self._daily_profit_reduced:
    risk_amount *= 0.25
    logger.debug("  [RISK REDUCED] daily profit limit atteint, risk_amount=25%")

# APRÈS: supprimer ces 4 lignes
```

### A4 — Alléger Lot safety SL tight (fallback silencieux au lieu de warning+force)
**Lignes 815-821 :**
```python
# AVANT:
if risk_per_01 < 1.0:
    # SL trop serré ou marché fermé → lot safety = LOT_SIZE
    logger.warning(
        f"[LOT SAFETY] {symbol}: risk_per_01=${risk_per_01:.2f} anormal "
        f"(marché fermé ou SL trop serré) → lot={lot_size}"
    )
    lot = lot_size

# APRÈS:
if risk_per_01 < 1.0:
    lot = lot_size  # fallback SILENCIEUX (marché fermé ou SL trop serré)
```

### B1 — Zone 2 daily loss ×0.50 → ×0.75
**Ligne 792 :**
```python
# AVANT:
risk_amount *= 0.50
logger.debug(f"  [ZONE 2] daily loss {daily_loss_amt / self.initial_balance:.2%} >= {zone2:.1%}, risk 50%")

# APRÈS:
risk_amount *= 0.75
logger.debug(f"  [ZONE 2] daily loss {daily_loss_amt / self.initial_balance:.2%} >= {zone2:.1%}, risk 75%")
```

### B2 — DD >7% ×0.60 → ×0.80
**Ligne 601 :**
```python
# AVANT:
mult *= 0.60  # DD > 7% → -40%
# APRÈS:
mult *= 0.80  # DD > 7% → -20%
```

### B3 — Pertes consécutives seuil ×0.30 → ×0.60
**Ligne 610 :**
```python
# AVANT:
mult *= 0.30  # Pause imminente → risque fortement réduit
# APRÈS:
mult *= 0.60  # Pause imminente → risque réduit
```

### C1 — Perf symbol WR>70% ×1.20 → ×1.35
**Ligne 726 :**
```python
# AVANT:
mult = 1.20
# APRÈS:
mult = 1.35
```

### C2 — Adaptive lot WR>65% ×1.15 → ×1.25
**Ligne 588 :**
```python
# AVANT:
mult *= 1.15  # Bon WR → lots +15%
# APRÈS:
mult *= 1.25  # Bon WR → lots +25%
```

### C3 — Challenge progress >70% ×1.20 → ×1.30
**Ligne 624 :**
```python
# AVANT:
mult *= 1.20
# APRÈS:
mult *= 1.30
```

---

## Fichier : `main.py`

### A3 — Supprimer degraded mode lot=0.01 (WR<40% déjà géré par perf mult ×0.50)
**Lignes 1187-1194 :**
```python
# AVANT:
# Mode dégradé : symbole avec WR < 40% → lot minimum (0.01 au lieu de désactiver)
if symbol in degraded_symbols:
    signal["_degraded"] = True
    # Throttle: log 1/60 cycles max par symbole
    _last_degraded = self._log_throttle.get("degraded", {}).get(symbol, 0)
    if self.cycle_count - _last_degraded >= 60:
        self._log_throttle.setdefault("degraded", {})[symbol] = self.cycle_count
        logger.debug(f"  [DEGRADED] {symbol}: mode lot minimum (WR < 40%)")

# APRÈS: supprimer tout ce bloc (8 lignes)
```

### B4 — Devil's Advocate ×0.50 → ×0.70 (conflit moins agressif)
**Ligne 1592 :**
```python
# AVANT:
signal["risk_mult"] = current_rm * 0.5
# APRÈS:
signal["risk_mult"] = current_rm * 0.7
```

### C4 — Relever les caps finaux (+20%)
**Ligne 1637 :**
```python
# AVANT:
_FINAL_CAP = {"XAUUSD": 1.25, "BTCUSD": 1.00, "US500.cash": 1.15, "ETHUSD": 1.00}
# APRÈS:
_FINAL_CAP = {"XAUUSD": 1.50, "BTCUSD": 1.25, "US500.cash": 1.30, "ETHUSD": 1.20}
```

---

## Post-exécution
```powershell
python -m pytest tests/ -x -q
```
