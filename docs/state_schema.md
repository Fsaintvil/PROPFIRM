# State Schema — MT5 FTMO Robot

> Documentation complète de tous les fichiers persistants du robot.
> Dernière mise à jour: 28 Août 2026

---

## Table des matières

1. [robot_state.json](#1-robot_statejson) — État maître
2. [ol_state.json](#2-ol_statejson) — OnlineLearner
3. [calibration_state.json](#3-calibration_statejson) — Calibration
4. [trades_log.csv](#4-trades_logcsv) — Journal CSV
5. [trading_journal.db](#5-trading_journaldb) — Journal SQLite
6. [performance_history.json](#6-performance_historyjson) — Métriques
7. [reject_counter.json](#7-reject_counterjson) — Compteur rejets
8. [adaptive_*.json](#8-adaptive_json) — Paramètres adaptatifs
9. [Autres fichiers](#9-autres-fichiers)
10. [Cycle de vie](#10-cycle-de-vie)
11. [Thread Safety](#11-thread-safety)

---

## 1. robot_state.json

**Writer**: `trading_engine.py:_save_state()` via `state_manager.py:save_full_state()`
**Reader**: `trading_engine.py:_load_state()`
**Fréquence**: Chaque cycle (~15s) + arrêt
**Taille typique**: ~55 KB
**Atomicité**: tmp + fsync + .bak backup + replace

### Schéma complet

```json
{
  "peak_equity": float,                          // FTMO: equity maximale atteinte (DD tracker)
  "consecutive_losses": int,                     // Compteur pertes consécutives global
  "partial_closed": [int, ...],                  // Tickets avec partial TP déjà fait
  "trailing_peaks": {                            // Pic de profit par position (ticket → float)
    "12345": 45.67
  },
  "position_regime": {                           // Régime d'entrée par position (ticket → string)
    "12345": "TREND_UP"
  },
  "peak_profit": {                               // Pic de profit brut par position (ticket → float)
    "12345": 120.50
  },
  "challenge_initial_balance": 200000.0,         // Balance initiale FTMO ($)
  "restart_count": int,                          // Nombre total de redémarrages
  "restart_timestamps": [float, ...],            // Timestamps epoch des 7 derniers restarts
  "daily_profit_reduced": bool,                  // Flag daily profit limit
  "trade_history": [                             // Derniers 200 trades (était 500)
    {
      "symbol": "BTCUSD",
      "profit": 38.04,
      "time": "2026-08-27T14:10:43",            // ISO datetime
      "historical": false,
      "action": "BUY"
    }
  ],
  "daily_pnl_by_date": {                         // PnL cumulé par jour
    "2026-08-27": 145.30,
    "2026-08-28": -23.50
  },
  "trading_days_list": ["2026-08-27", ...],     // Jours de trading (ISO dates)
  "challenge_status": "ACTIVE",                  // ACTIVE | FAILED_DD | PASSED | EXPIRED
  "consistency_violated": bool,                  // Règle consistance FTMO (30%)
  "daily_stats": {                               // Stats du jour en cours
    "day": "2026-08-28",                         // String → converti en date au load
    "opened": 3,
    "pnl": 45.20
  },
  "daily_start_equity": float|null,              // Equity au début de la journée
  "cooldowns": {                                 // Cooldowns par symbole (ISO datetime)
    "BTCUSD": "2026-08-28T10:30:00"
  },
  "global_cooldown_until": "..."|null,           // Cooldown global
  "symbol_consecutive_losses": {                 // Pertes consécutives par symbole
    "BTCUSD": 2
  },
  "opened_today": int,                           // Trades ouverts aujourd'hui
  "win_rate_checked": bool,                      // Si WR risk_mult a été appliqué
  "last_symbol_trade_time": {                    // Timestamp epoch du dernier trade/symbole
    "BTCUSD": 1724758243.5
  },
  "connected": bool,                             // État connexion MT5
  "last_restart_utc": "2026-08-28T10:00:00+00:00" // Dernier restart (ISO)
}
```

### Champs critiques FTMO (validés au load — R5)

Ces champs déclenchent un fallback `.bak` s'ils manquent :
- `peak_equity`
- `trading_days_list`
- `daily_pnl_by_date`

---

## 2. ol_state.json

**Writer**: `OnlineLearner.save_state()`
**Reader**: `OnlineLearner._load_state()`
**Fréquence**: Chaque trade fermé + batch flush
**Taille typique**: ~10 KB (après purge burst)
**Atomicité**: tmp + fsync + replace

### Schéma

```json
{
  "window": 200,                                 // Taille fenêtre glissante
  "history": {                                   // Historique trades par symbole
    "BTCUSD": [
      {
        "r": 1.5,                                // R-multiple
        "regime": "TREND_UP",                    // Régime au moment du trade
        "time": "2026-08-27T14:10:43+00:00",    // ISO datetime (optionnel)
        "profit": 38.04,                         // Profit USD (optionnel)
        "win": true                              // Win/loss (optionnel)
      }
    ]
  },
  "adapted_params": {                            // Paramètres adaptés par symbole
    "BTCUSD": {
      "thresh": 2.0,                             // Seuil MOM20x3 (×ATR)
      "risk_mult": 1.0,                          // Multiplicateur risque
      "sl_mult": 2.0,                            // Multiplicateur SL
      "tp_mult": 5.0                             // Multiplicateur TP
    }
  }
}
```

### Garde anti-burst

`_is_burst_history()` rejette les histories avec >50% de gaps <1s (contamination replay historique).

### Garde min_trades

Les adapted_params sont purgés pour les symboles avec <15 trades valides.

---

## 3. calibration_state.json

**Writer**: `AdaptiveEngine._save_calibration()`
**Reader**: `AdaptiveEngine._load_calibration()`
**Fréquence**: Chaque trade fermé
**Taille typique**: ~10 KB
**Atomicité**: tmp + replace

### Schéma

```json
{
  "online_history": {                            // Même structure que ol_state.history
    "BTCUSD": [...]
  },
  "adapted_params": {                            // Même structure que ol_state.adapted_params
    "BTCUSD": {...}
  }
}
```

> **Note**: Ce fichier est un backup redondant de `ol_state.json`. La source de vérité est `ol_state.json`.

---

## 4. trades_log.csv

**Writer**: `trade_journal.py:_write_csv_backup()`
**Reader**: `golden_rule.py`, `daily_checkpoint.py`, `performance_monitor.py`
**Fréquence**: Chaque trade fermé
**Format**: CSV append-only, UTF-8

### Colonnes (14)

```
timestamp, symbol, direction, volume, entry_price, sl_price, tp_price,
exit_price, sl_atr, tp_atr, pnl, reason, duration_h, atr_h1
```

| Colonne | Type | Description |
|---------|------|-------------|
| timestamp | ISO datetime | Heure de fermeture |
| symbol | string | Symbole (ex: BTCUSD) |
| direction | string | BUY ou SELL |
| volume | float | Lot size |
| entry_price | float | Prix d'entrée |
| sl_price | float | Stop Loss (vide si non défini) |
| tp_price | float | Take Profit (vide si non défini) |
| exit_price | float | Prix de fermeture |
| sl_atr | float | SL en multiple d'ATR (vide si N/A) |
| tp_atr | float | TP en multiple d'ATR (vide si N/A) |
| pnl | float | Profit/Perte en USD |
| reason | string | sl, tp, time_stop, structure, partial_tp, kill_switch |
| duration_h | float | Durée en heures |
| atr_h1 | float | ATR H1 au moment de l'entrée |

---

## 5. trading_journal.db

**Writer**: `trade_journal.py:record_trade()`
**Reader**: `trade_journal.py:get_trades()`
**Format**: SQLite WAL mode
**Taille typique**: ~12.6 MB
**Thread Safety**: `threading.Lock`

### Tables

#### `trades`
```sql
CREATE TABLE trades (
    ticket INTEGER PRIMARY KEY,
    symbol TEXT, action TEXT, lot REAL,
    entry_price REAL, exit_price REAL,
    profit REAL, rr REAL, regime TEXT,
    adx REAL, atr REAL, dl_score REAL,
    entry_time TEXT, exit_time TEXT,
    duration_min REAL
);
```

#### `decisions`
```sql
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, action TEXT, reason TEXT
);
```

---

## 6. performance_history.json

**Writer**: `performance_monitor.py:_save()`
**Reader**: `performance_monitor.py:_load_history()`
**Fréquence**: Chaque trade fermé
**Taille typique**: ~16 KB
**Thread Safety**: `threading.RLock`

### Schéma

```json
{
  "daily": {
    "2026-08-28": {
      "trades": 5, "wins": 3, "losses": 2,
      "pnl": 45.20, "gross_profit": 120.50, "gross_loss": -75.30,
      "symbols": {
        "BTCUSD": {"trades": 2, "wins": 2, "losses": 0, "pnl": 38.04}
      }
    }
  },
  "rolling": {
    "last_20": {"trades": 20, "wins": 12, "losses": 8, "pnl": 145.30, "wr": 0.60, "avg": 7.27},
    "last_50": {...},
    "last_100": {...},
    "last_200": {...}
  },
  "symbols": {
    "BTCUSD": {
      "trades": 34, "wins": 22, "losses": 12,
      "pnl": 1029.71, "gross_profit": 1850.00, "gross_loss": -820.29,
      "regime_stats": {"TREND_UP": {"trades": 20, "wins": 15, "pnl": 800.00}},
      "direction_stats": {"BUY": {"wins": 22, "losses": 12, "pnl": 1029.71}}
    }
  },
  "alerts": [
    {"level": "WARNING", "metric": "pf_symbol_below", "message": "...", "value": 0.7, "threshold": 0.7, "date": "...", "symbol": "XAUUSD"}
  ],
  "challenge": {
    "start_balance": 200000, "peak_equity": 201800,
    "trading_days": 12, "last_status": "ACTIVE",
    "balance": 201000, "equity": 200500,
    "profit_progress_pct": 0.5, "days_remaining": 8,
    "estimated_days_to_target": "~15 days", "on_track": false
  },
  "recent_trades": [
    {"profit": 38.04, "symbol": "BTCUSD", "regime": "TREND_UP", "direction": "BUY", "ts": "2026-08-27T14:10:43"}
  ]
}
```

---

## 7. reject_counter.json

**Writer**: `reject_counter.py:persist_if_stale()`
**Reader**: Interne (logging)
**Fréquence**: Max 1×/30s
**Thread Safety**: `threading.Lock`

### Schéma

```json
{
  "started_at": "2026-08-28 10:00:00",
  "last_write": 1724848800.0,
  "by_symbol": {
    "BTCUSD": {"precheck:Spread too high": 5, "h4_dir:H4 contre signal": 3}
  },
  "by_phase": {"precheck": 15, "validator": 8, "adx": 3},
  "total": 26
}
```

---

## 8. adaptive_*.json (22 fichiers)

**Writer**: `AdaptiveParameters._save_state()`
**Reader**: `AdaptiveParameters._load_state()`
**Fréquence**: Chaque trade par symbole
**Taille**: 354 B – 9.6 KB par fichier

### Schéma

```json
{
  "params": {
    "threshold_mult": 1.0, "risk_mult": 1.0,
    "sl_mult": 1.0, "tp_mult": 1.0, "trailing_mult": 1.0,
    "win_rate": 0.52, "profit_factor": 4.36,
    "avg_pnl": 14.15, "sample_size": 63,
    "last_update": 1724848800.0, "confidence": 0.0
  },
  "trades": [
    {"pnl": 38.04, "win": true, "time": 1724848800.0, "regime": "TREND_UP"}
  ],
  "last_update": 1724848800.0
}
```

> **Note**: Ces fichiers sont alimentés mais le risk_mult est **ignoré** par le pipeline (OnlineLearner est la source autoritaire).

---

## 9. Autres fichiers

| Fichier | Format | Writer | Fréquence | Description |
|---------|--------|--------|-----------|-------------|
| `robot.pid` | Text | `trading_engine.py` | Startup/shutdown | PID lock (mutex Windows) |
| `heartbeat.txt` | Text | `trading_engine.py` | Chaque cycle | Preuve de vie (15s) |
| `ftmo_report.json` | JSON | `trading_engine.py` | Chaque cycle | Rapport FTMO synthétique |
| `last_signals.json` | JSON | `trading_engine.py` | Chaque batch signaux | Derniers signaux debug |
| `recorded_positions.json` | JSON | `position_tracker.py` | Chaque trade fermé | Positions enregistrées |
| `auto_state.json` | JSON | `auto_stop.py` | Check ADX | État auto-stop/resume |
| `golden_rule/state.json` | JSON | `golden_rule.py` | Quotidien 20:00 | État Règle d'Or |
| `daily_checkpoint/*.json` | JSON | `daily_checkpoint.py` | Quotidien 20:00 | Checkpoint quotidien |
| `rate_cache.db` | SQLite | `rate_cache.py` | Chaque fetch rates | Cache rates (TTL 15s) |
| `position_features.db` | SQLite | `feature_store.py` | Trade open/close | Métadonnées positions |
| `robot.stop.flag` | Text | `trading_engine.py` | Arrêt gracieux | Flag stop (nettoyé au restart) |
| `stop_for_day.flag` | Text | kill-switch | Arrêt urgence | Flag daily stop (conservé) |

---

## 10. Cycle de vie

### Démarrage

1. `_clean_orphan_tmp_files()` — nettoie *.tmp
2. `_clean_watchdog_flags()` — supprime stop/halt flags
3. `_acquire_lock()` — mutex Windows + écriture robot.pid
4. `_load_state()` — charge robot_state.json (+ fallback .bak)
5. `AdaptiveEngine.__init__()` — charge calibration + ol_state
6. `import_history()` — importe deals MT5 48h

### Pendant le trading (chaque cycle ~15s)

1. `_heartbeat()` → heartbeat.txt
2. Trades → 6+ fichiers mis à jour
3. `_save_state()` → robot_state.json (atomique)
4. `_log_ftmo_report()` → ftmo_report.json

### Arrêt

1. Écrit `robot.stop.flag`
2. `_save_state()` final
3. Ferme SQLite connections
4. Déconnecte MT5
5. Supprime `robot.pid`

### Survie au crash

| État | Survie | Mécanisme |
|------|--------|-----------|
| robot_state.json | ✅ | Écrit toutes les 15s + backup .bak |
| OL history | ✅ | Écrit à chaque trade fermé |
| Trade journal | ✅ | WAL SQLite + append CSV |
| Positions ouvertes | ✅ | `import_history()` re-importe deals 48h |
| robot.pid | ⚠️ Zombie | Détection par watchdog |
| In-memory counters | ⚠️ | Reconstruit depuis robot_state.json |

---

## 11. Thread Safety

| Ressource | Lock | Type |
|-----------|------|------|
| robot_state.json | `state_manager._STATE_LOCK` | RLock |
| ol_state.json | `OnlineLearner._lock` | RLock |
| performance_history.json | `PerformanceMonitor._lock` | RLock |
| reject_counter.json | `RejectCounter._lock` | Lock |
| adaptive_*.json | Module-level lock | Lock |
| trading_journal.db | `TradeJournal._lock` | Lock |
| robot.pid | Named Mutex Windows | System |
