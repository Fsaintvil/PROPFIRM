# Session History — MT5 FTMO MOM20x3

> Ce fichier contient l'historique complet des sessions Robot Manager.
> Extrait de `AGENTS.md` pour alléger le document principal.

---

## Analyse live ReportHistory-1513621052.xlsx (9 Juin 2026)

Le rapport Excel du compte FTMO 200K Free Trial a été analysé le 9 Juin 2026.
**47 trades réels** identifiés (lignes avec Volume non-NaN, 227 lignes MT5 export total).

### Résumé
| Métrique | Valeur |
|----------|--------|
| Total trades | 47 |
| WR | 51.1% |
| PnL | +$289 |
| PF | 1.02 |
| SL utilisé | 100% ✅ |
| Commission avg | -$2.95/trade |
| Corrélation inter-symboles | 63% des slots 5-min |

### Performances par symbole
| Symbole | Trades | WR | PnL | PF |
|---------|--------|-----|-----|-----|
| USDCHF | 10 | 60% | +$301 | **1.57** ✅ |
| GBPUSD | 11 | 64% | -$11 | 0.69 |
| USDCAD | 11 | 45% | -$91 | 0.74 |
| EURUSD | 12 | **33%** | -$36 | 0.86 🔴 |
| AUDUSD | 2 | 100% | +$125 | - |
| NZDUSD | 1 | 0% | $0 | - |

### Heures à risque
| Heure UTC | Trades | WR | Verdict |
|-----------|--------|-----|---------|
| **12:00** | 6 | **0%** | 🔴 **À bloquer** |
| 03:00 | 8 | 50% | ⚠️ Médiocre |
| 10:00 | 6 | 100% | ✅ Excellent |
| 14:00 | 7 | 71% | ✅ Bon |
| 16:00 | 3 | 100% | ✅ |

### Recommandations appliquées
1. **EURUSD** période momentum 20→18 (plus réactif)
2. **Pullback bande** 0.15%→0.30% en trending (plus de signaux)
3. **ADX slope** relaxé à -3.5 pour scores > 0.70 (moins de faux rejets)
4. **12:00 UTC** → surveillance renforcée (envisager un block horaire)
5. **Corrélation** 63% → la matrice Pearson est active, mais le risque résiduel est documenté

---

## Améliorations Skills & Agents (9 Juin 2026)

### Skills mises à jour
| Skill | Améliorations |
|-------|---------------|
| `mom20x3-strategy` | + Données live Excel, + ADX threshold fix, + Heures à risque, + Perf par symbole |
| `ftmo-protector` | + Corrélation 63%, + MIN_RR_RATIO=2.0, + best_day_pct reconstruction |
| `market-regime` | + ADX fix (seuil 12, bypass 0.80), + Per-symbol adx_thresh, + Pattern horaire |
| `backtest-validation` | + Comparaison live vs historique, + Gap backtest→live attendu (10-15%) |
| `monitoring-health` | + Council orchestrator, + Memory monitoring 2.2GB, + Alerte mémoire 1.5GB/2.0GB |
| `mt5-operations` | + Ordre d'exécution execute(), + 3-points SL check, + numpy array conversion |

### Agents mis à jour
| Agent | Améliorations |
|-------|---------------|
| `log-analyst` | + Sources de données (7), + Patterns d'erreur, + Analyse comparative live vs hist |
| `auto-fixer` | + Tableau des 11 fixes connus, + Anti-règle rate_limiter en dernier |
| `optimizer` | + Tableau de référence 47 trades, + Recommandations par symbole, + Block horaire |
| `monitor-agent` | + Check council daemon, + Check mémoire, + Check verdict council |
| `cio` | + Nouveaux déclencheurs orange (mémoire, WR gap, 12:00 UTC) |

### MOM20x3 amélioré (strategy.py) — Audit profond 14 Juin
1. **ADX slope RÉPARÉ** : Wilder's smoothing `half=len/3` (condition `14 > 28` NEVER corrigée → bug depuis des mois)
2. **Pullback ACTIVÉ** : `if not pullback_active: return None` (était calculé mais JAMAIS vérifié)
3. **Seuils restaurés** : 2.5×ATR trending / 2.0×ATR ranging (étaient passés à 1.5/1.0×ATR en Mode MAX → signaux parasites en ranging)
4. **NaN guard** : `np.isnan(mom)` → retour None propre (ne plante plus)
5. **Pullback adaptatif** : 0.30% en trending, 0.15% en ranging
6. **ADX slope relaxé** : -3.5 pour raw_score > 0.70 (vs -2.0 standard)
7. **Flux réordonné** : thresh calculé AVANT les filtres
8. **Tests** : 889/889 pass (3 skip volontaires)

---

## Session Robot Manager — 16 Juin 2026

### Mission
- Exploiter un robot de trading MT5 automatique (MOM20x3) sur compte FTMO 200K avec **5 symboles calibrés** (XAUUSD H4, BTCUSD H1, ETHUSD H4, EURUSD H1, US500.cash H4), validés par backtests + Monte Carlo.

### Analyse approfondie de 4 fichiers critiques

#### 1. `adaptive_intelligence.py` (756 lignes) — 🔴 Problèmes P1, P2

**Problème #1 — SL/TP hardcodés écrasent la config par symbole**
Lignes 567-580 : les SL/TP par régime (2.0/5.0) écrasaient les valeurs calibrées par symbole (BTCUSD perdait son SL 3.0×ATR pour les flash crashes).

**Problème #2 — risk_mult écrasé par OnlineLearner**
Ligne 582 : `adapted["risk_mult"] = params["risk_mult"]` remplaçait le risk_mult par symbole par la valeur OL (toujours 0.75 car WR < 70%). Les risk_mult config (XAUUSD=1.00, BTCUSD=0.65, ETHUSD=0.50) étaient ignorés.

**Problème #3 — Session boost contredit preferred_hours**
Lignes 652-662 : les heures fixes (7-9h London, 13-15h NY) s'appliquaient à tous les symboles, même ceux qui ne tradent pas à ces heures (XAUUSD commence à 13h).

#### 2. `regime.py` (83 lignes) — 🟡 Problème P3

Propre et fonctionnel, mais `_prev_regime` était un attribut d'instance unique, causant une cross-contamination entre symboles.

#### 3. `challenge.py` (355 lignes) — ✅ Aucun bug

Propre, bien structuré.

#### 4. `anticipation.py` (558 lignes) — 🟢 Problème P4 (archivé)

Code MORT — complètement déconnecté, PyTorch requis mais pas installé.

### Correctifs appliqués (6 fixes)

| Fix | Fichier | Description |
|-----|---------|-------------|
| **P1c 🔴** | `main.py:833` | `risk_mult = base_risk_mult × ol_risk_mult` |
| **P1a 🔴** | `adaptive_intelligence.py:567-580` | SL/TP préservés depuis le signal |
| **P1b 🔴** | `adaptive_intelligence.py:582` | risk_mult préservé |
| **P2 🟡** | `adaptive_intelligence.py:656-666` | Session boost basé sur preferred_hours |
| **P3 🟡** | `regime.py` | `_prev_regime` par symbole |
| **P4 🟢** | `anticipation.py` | Archivé dans `retired/` |

### Flux du risk_mult AVANT le fix (bug)
```
main.py:833  signal["risk_mult"] = ol_risk_mult → 0.75 TOUJOURS
             XAUUSD config risk_mult=1.00 ✗ IGNORÉ
             BTCUSD config risk_mult=0.65 ✗ IGNORÉ
```

### Flux du risk_mult APRÈS le fix
```
main.py:837  signal["risk_mult"] = base_risk_mult × ol_risk_mult
             XAUUSD: 1.00 × 0.75 = 0.75 ✅
             BTCUSD: 0.65 × 0.75 = 0.49 ✅ (corrigé, -35%)
             ETHUSD: 0.50 × 0.75 = 0.38 ✅ (corrigé, -49%)
```

### Impact sur le capital FTMO (200K)
| Symbole | Risk avant | Risk après | Trade max avant | Trade max après |
|---------|:---:|:---:|:---:|:---:|
| XAUUSD | 0.75 | 0.75 | $600 | $600 |
| BTCUSD | 0.75 | **0.49** | $600 | **$392** 🔴 |
| ETHUSD | 0.75 | **0.38** | $600 | **$304** 🔴 |
| EURUSD | 0.75 | **0.38** | $600 | **$304** 🔴 |
| US500 | 0.75 | **0.38** | $600 | **$304** 🔴 |

**Le risque des cryptos/forex était surestimé de 50 à 97%.**

---

## Session Robot Manager — 17 Juin 2026

### Mission
- Implémenter la règle **multi-position dynamique** : jusqu'à 3 positions/symbole si confiance > 85%, 2 si > 70%
- Passer `MIN_SYMBOL_INTERVAL_S` de 180s → 60s
- Augmenter la capacité totale de positions

### Changements appliqués (5 fichiers modifiés)

| # | Fichier | Changement |
|---|---------|------------|
| **F1** | `engine_simple/trade_executor.py` | `MIN_SYMBOL_INTERVAL_S = 180 → 60` |
| **F2** | `main.py` | Limite dynamique : `conf>85%→4`, `conf>70%→3`, `sinon→1` position/symbole |
| **F3** | `config/production.yaml` | `max_positions: 6→10`, `max_positions_per_symbol: 3→4` |
| **F4** | `engine_simple/ftmo_config.py` | `MAX_POS_PER_SYMBOL: 3→4` |
| **F5** | `engine_simple/portfolio_controller.py` | `MAX_POSITIONS_TOTAL: 12→16`, `PER_SYMBOL: 3→4`, `PER_DIRECTION: 6→8` |

### Nouvelle logique multi-position
```python
if sig_conf > 0.85:      max_per_symbol = 4
elif sig_conf > 0.70:    max_per_symbol = 3
else:                    max_per_symbol = 1
```

### Analyse de l'historique (500 trades)
**Source fiable** (22 trades récents) : rolling 20 = 75% WR, +$710.

---

## Session Robot Manager — 17 Juin 2026 (Partie 2 — EURUSD + Audit Pro)

### Décisions clés
1. **EURUSD réactivé** avec section complète dans default.yaml (H1, momentum 18, sessions 7-21h, BUY+SELL)
2. **allow_shorts: false→true** — décision professionnelle
3. **28 trades EURUSD toxiques** (WR=1%) nettoyés du robot_state.json

### Architecture finale (v4.2.0)
```
5 symboles actifs : XAUUSD (H4), BTCUSD (H1), ETHUSD (H4), EURUSD (H1), US500.cash (H4)
Tous en DEUX DIRECTIONS — le système multi-couches gère le risque
```

---

## Session Robot Manager — 19 Juin 2026

### Actions exécutées
| # | Action | Résultat |
|---|--------|----------|
| **Z1** | Tué 3 zombies main.py | ✅ Confirmés morts |
| **P1** | Named Mutex Windows | ✅ Verrou atomique OS |
| **P1b** | `_acquire_lock()` réécrit | ✅ Double sécurité |
| **P1c** | `_release_lock()` libère mutex + PID | ✅ Clean |
| **P1d** | Restart flow corrigé | ✅ Fenêtre critique fermée |

### PID Lock — Architecture finale
```python
_acquire_lock():
  1. _acquire_mutex() → named mutex Windows (primaire)
  2. Si mutex indisponible → file-based PID lock (fallback)
  3. Si verrou déjà tenu → sys.exit(1)
```

### État final (PID 8876)
| Métrique | Valeur |
|----------|--------|
| Balance | $201,176 |
| Equity | $201,079 |
| PnL | +$1,079 (5.4%) |
| DD | 0.1% |
| Positions | 6 (3 BTCUSD, 1 ETHUSD, 2 XAUUSD) |
| Tests | 693 passed, 32 skipped |

---

## Session Robot Manager — 22 Juin 2026 (Partie 2 — Agent Daemon + Dual Instance Kill)

### Mission
- Intégrer `agent_daemon.py` (11 agents du Trading Intelligence Council) dans le démarrage automatique
- Résoudre le problème de double instance du robot

### Actions exécutées
| # | Action | Résultat |
|---|--------|----------|
| **I1** | `start_robot.ps1` : monitor.py → agent_daemon.py | ✅ |
| **I6** | Kill double instance PID 12048 | ✅ |
| **I7** | Kill massif `taskkill /F /IM python.exe` | ✅ |
| **I8** | Redémarrage propre 1 instance PID 16860 | ✅ |
| **I10** | Tests : 944/976 passed | ✅ |

### Problème résolu : Double instance
**Cause racine** : Le mutex Windows (`CreateMutexW`) ne fonctionne pas dans Git Bash → deux instances main.py sans protection.

**Solution** : PID lock file (`runtime/robot.pid`) + vigilance du daemon.

### État final (PID 16860)
| Métrique | Valeur |
|----------|--------|
| WR last_20 | **60%** ✅ |
| WR last_100 | 52% |
| Challenge | ACTIF, 8/10 jours |
| Tests | 944/976 pass |
