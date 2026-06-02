# MT5 FTMO - Robot MOM20x3 Multi-Symbol + Intelligence Adaptative

## Architecture Intelligence
```
┌──────────────────────────────────────────────────────────┐
│ main.py          Boucle 15s, orchestre tout              │
├──────────────────────────────────────────────────────────┤
│ signals.py       MOM20x3 pur (règle technique)           │
│   - c[i] - c[i-20] > seuil×ATR → breakout                │
│   - Seuils: 2.5x trending / 2.0x ranging (ADX-based)    │
│   - Accepte overrides (seuil, SL/TP adaptatifs)          │
├──────────────────────────────────────────────────────────┤
│ adaptive_intelligence.py  ★ ADAPTATIF ★                  │
│   ├─ MarketRegime        Détection régime (ADX=20 pivot) │
│   ├─ OnlineLearner       Apprentissage en ligne          │
│   │  (rolling 200 trades, ajuste seuil + risque)         │
│   ├─ DLEnsemble          LSTM + Attention (31 features)  │
│   │  (61.5% accuracy, score≥0.60 → 73% accuracy)        │
│   ├─ LightGBMPredictor   ★ NOUVEAU 3ÈME MODÈLE ★       │
│   │  (50.1% accuracy, 620 features, poids faible)       │
│   └─ MLEnsemble ⚠️ DÉSACTIVÉ (45%, 581 MB RAM)          │
├──────────────────────────────────────────────────────────┤
│ meta_learner.py     ★ META-LEARNER ★                     │
│   - Combine 3 modèles (MOM20x3 + DL_LSTM + LGB)         │
│   - Poids calibrés par l'historique réel                 │
│   - Devil's Advocate : risque ×0.5 si fort désaccord     │
├──────────────────────────────────────────────────────────┤
│ ftmo_protector.py  Protections FTMO                      │
│   - ATR Trailing (peak-based niveaux par régime)         │
│   - Cooldown 30min, pause 3 pertes consécutives          │
│   - Corrélation 6 groupes, DD max 10%, daily 2%          │
│   - Consistency 30% max day / total profit               │
│   - Min 10 trading days avant PASS                       │
└──────────────────────────────────────────────────────────┘
```

## Flux de décision (calibré par historique)
```
MOM20x3 brut → OnlineLearner (seuil adapté)
             → MarketRegime (SL/TP adaptés au régime)
             → DL LSTM (retraîné sur 2857 trades)
             → LightGBM (entraîné sur 7525 trades 4 symboles)
             → Meta-Learner (combinaison pondérée 3 modèles)
             → FTMO Protector (filtres finaux)
             → Exécution
```

## Validation DL sur historique compte réel (2857 trades)
```
DL avant retraining: 39.9% (pire que aléatoire)
DL après retraining: 61.5% accuracy
  Score ≥ 0.60: 73% accuracy, 63% WR ← FIABLE
  Score < 0.60: 33% accuracy ← IGNORER

MOM20x3 direction accuracy: 76.6%

QUAND DL & MOM20x3 SONT D'ACCORD → 66% WR, $4834 PnL
QUAND ILS DISENT L'INVERSE     → 47% WR, $-118 PnL

→ Agreement = signal fort pour le Meta-Learner (conf=max)
→ Désaccord = risque ×0.5 (Devil's Advocate)
```

## DL LSTM (retraîné sur le compte réel)
- **Avant retraining sur compte**: 39.9% accuracy (pire que aléatoire)
- **Après retraining sur 2857 trades réels**: 61.5% accuracy
- **Score ≥ 0.60**: 73% accuracy, 63% WR
- **Score [0.50, 0.60)**: 33% accuracy — IGNORER
- Modèle: `models/dl_lstm_all.pkl` (tous symboles)
- Features: momentum, volatilité, RSI, MACD, market structure, etc.
- Pipeline retraining: `_build_sequence` → `_train_step` → `torch.save`
- Seuil abaissé à 0.51/0.49 (plus jamais HOLD, toujours BUY/SELL)

## Régimes de marché (MarketRegime) — CORRIGÉ W/L 0.39
| Régime | Critère | SL | TP | Risque |
|--------|---------|----|----|--------|
| TREND_UP | ADX>20, MA>0.2% | 2.0×ATR | 5.0×ATR | 100% |
| TREND_DOWN | ADX>20, MA<-0.2% | 2.0×ATR | 5.0×ATR | 100% |
| HIGH_VOL | ATR%>80% | 2.0×ATR | 5.0×ATR | 70% |
| RANGING | ADX<=20 | 1.5×ATR | 4.0×ATR | 100% |
| LOW_VOL | ATR%<20% | 1.5×ATR | 4.0×ATR | 100% |

### Trailing stop (corrigé : 0.5→1.0 ATR first lock)
- profit >1.0×ATR → SL = peak − 0.50×ATR (RANGING)
- profit >2.0×ATR → SL = peak − 0.35×ATR
- profit >3.0×ATR → SL = peak − 0.20×ATR
- profit >5.0×ATR → SL = peak − 0.10×ATR

Niveaux par régime (dans ftmo_protector.py) :

| Régime | 1er lock | Niveau 1 | Niveau 2 | Niveau 3 | Niveau 4 |
|--------|----------|----------|----------|----------|----------|
| RANGING | 1.0×ATR | 0.50×ATR | 0.35×ATR | 0.20×ATR | 0.10×ATR |
| TREND_UP/DOWN | 1.0×ATR | 0.80×ATR | 0.50×ATR | 0.30×ATR | 0.15×ATR |
| HIGH_VOL | 1.0×ATR | 1.00×ATR | 0.70×ATR | 0.50×ATR | 0.25×ATR |
| LOW_VOL | 1.0×ATR | 0.40×ATR | 0.25×ATR | 0.15×ATR | 0.08×ATR |

## Seuils de signal (signals.py)
- ADX ≥ 25 (trending): thresh = 2.5×ATR
- ADX < 25 (ranging): thresh = 2.0×ATR
- Plafonné à 2.5×ATR max (évite les seuils trop hauts qui bloquent tout)
- Note: le MarketRegime utilise ADX>20 (plus sensible), les seuils de signal utilisent ADX≥25

## Session block
- 5-18h UTC uniquement (début Asie → fermeture NY)

## Apprentissage en ligne (OnlineLearner)
- Fenêtre: 200 derniers trades par symbole
- WR>82% → seuil -0.5 (plus agressif), risque +15%
- WR<70% → seuil +0 (neutre), risque -25%
- Expectancy<0 → risque -50%
- Pause après 3 pertes consécutives

## Meta-Learner
- 3 modèles suivis: MOM20x3 + DL_LSTM + LGB
- Poids dynamiques par régime (recalibrés si WR<50%)
- Meta peut inverser MOM si confiance > 0.65 (anti-flip supprimé)
- Devil's Advocate: vérifie si un modèle fort (poids>0.15) est en désaccord
  → si oui, risque divisé par 2

## Protection FTMO
- **ATR Trailing** (remplace peak-$10) : SL adaptatif par multiple d'ATR
  - profit >1.0×ATR → SL = peak − 0.50×ATR (RANGING)
  - profit >2.0×ATR → SL = peak − 0.35×ATR
  - profit >3.0×ATR → SL = peak − 0.20×ATR
  - profit >5.0×ATR → SL = peak − 0.10×ATR
  - Buffer BE après partial : varie par régime (RANGING=0.80, TREND_UP/DOWN=0.60, HIGH_VOL=1.00, LOW_VOL=0.50)
- **Règle de consistance FTMO** : stop si un jour >30% du profit total
- **10 jours de trading minimum** : pas de PASS avant min_trading_days
- Cooldown 30min après perte
- Pause après 3 pertes consécutives
- Corrélation: max 2 trades par direction dans un groupe
- DD max: 10% depuis peak
- Daily loss: 2%
- RR≥2.0 enforce avant execution (MIN_RR_RATIO dans la config)

## Configuration
```python
RISK_PER_TRADE = 0.004    # 0.4% par trade
COOLDOWN_MINUTES = 30
MAX_POSITIONS = 6
MAX_POSITIONS_PER_SYMBOL = 2  # max 2 par symbole
SYMBOL_MAX_POSITIONS = {}  # pas de limite par symbole (corrélation: max 2/direction/groupe)
MAX_TRADES_PER_DAY = 5
MAX_SPREAD_POINTS = 50
MIN_RR_RATIO = 2.0
CONSISTENCY_MAX_PCT = 0.30  # max 30% jour / total (FTMO 1-Step)
```

## Symboles et limites (actifs)
```
USDCAD:    max_lot=0.55, risk_mult=1.0, max_spread=50pts
GBPUSD:    max_lot=0.55, risk_mult=1.0, max_spread=50pts
EURUSD:    max_lot=0.55, risk_mult=0.8, max_spread=50pts
USDCHF:    max_lot=0.55, risk_mult=0.8, max_spread=50pts
```
Statut: 4 symboles, plus de restrictions directionnelles (régime adaptatif).

## Statut actuel
- **v2.1.1** — 305 tests ✓, 32/42 faiblesses résolues + 4 axes amélioration live
- **AXE 1 (EURUSD WR):** seuil breakout durée porté de ≥25 à ≥20 ADX ; EURUSD mult=1.25 ; DL ignoré en RANGING → risk/2 ;
  min_score=0.65 + adx_thresh=18 pour EURUSD dans `SYMBOL_LIMITS`
- **AXE 2 (Corrélation):** seuil max abaissé 0.70→0.65 ; TTL cache 4h→1h
- **AXE 3 (Pertes consécutives):** pause GLOBALE 30min après 3 pertes (pas que par-symbole) ;
  `consecutive_losses` ne se reset plus chaque jour
- **AXE 4 (LightGBM):** `base_weight=0.3` au lieu de 1.0 (poids réduit dans Meta-Learner)

## Commandes
```powershell
python main.py              # Lancer le robot
taskkill /F /IM python.exe  # Arrêter le robot
.\scripts\robot.ps1         # Lancer robot + moniteur
.\scripts\robot.ps1 -Status  # Voir l'état
.\scripts\robot.ps1 -Stop    # Arrêter tout
opencode                    # Lancer l'IA manager (mode interactif)
opencode "bilan"            # L'IA analyse et résume l'état du robot
```

## Règles
- Magic number: 999001
- 4 symboles, max 2 positions par symbole (corrélation: max 2/direction/groupe)
- Signal → Régime → DL → Meta (override possible) → FTMO → Trade
- 3 pertes consécutives = pause
- 15s cycle
- PID lock dans `runtime/robot.pid` (empêche les instances dupliquées)

## Trailing + Partial TP (corrections BE)
- `_check_partial_tp` → `_check_step_trailing` (ordre inverse)
- Partial TP ferme 50% à 60% du TP, set BE à `0.8×ATR` (pas 10 pips fixes)
- BE conditionnel : ne s'applique QUE si le SL actuel est plus faible (ne recule jamais)
- Trailing 4 niveaux ATR (0.5× → 0.35× → 0.20× → 0.10× du peak, premier lock à 1.0×ATR)
- Chaque cycle : 1) time-stop, 2) partial TP, 3) trailing reprend le SL

## Persistance challenge
- `challenge_initial_balance` sauvegardé dans `runtime/robot_state.json`
- Capturé une fois au premier lancement, jamais recapturé (même après restart)
- DD, profit_progress, daily loss calculés sur cette base invariante

## PID Lock
- `runtime/robot.pid` contient le PID du processus en cours
- Vérifié au démarrage : si un PID existant est encore actif → abandon immédiat
- Nettoie automatiquement le fichier à l'arrêt (même sur crash via finally)
- Empêche les instances dupliquées (cause racine des positions en double)

## Session Log — Diagnostic approfondi + Refactoring (Juin 2026)

### Diagnostic complet
- **Ruff**: 279 auto-fix + 124 style-only remaining (D, N, COM)
- **Mypy**: 2269→0 erreurs (production config avec ignores explicites)
- **Tests**: 454 pass (18s), assertions renforcées, 6 tests vides supprimés
- **Sécurité**: pickle→JSON migration (feature_store, rate_cache)
- **Architecture**: 40 modules engine_simple/ audités

### Correctifs appliqués
| Fix | Fichiers | Impact |
|-----|----------|--------|
| Syntax errors | main.py, check_optimization.py | Bloquant résolu |
| pickle→JSON + default=str | feature_store.py, rate_cache.py, main.py | Sécurité + robustesse |
| PID handle leak | mt5_connector.py | Stabilité |
| bare→spécifiques (21) | engine_simple/*.py | Fiabilité |
| STRATS mutable→MappingProxyType | signals.py | Sûreté |
| Version sync→3.2.0 | __init__.py, .bumpversion.cfg | Traçabilité |
| sqlite3 import manquant | retraining_pipeline.py | Bloquant résolu |
| monitor.py duplicate→retired/ | root→retired/ | Propreté |
| credentials.py créé | engine_simple/ | Sécurité |

### Refactoring commité
`7eab317f6` — 527 files, +44k/−60k lignes
- engine_simple/ (40 modules) + tests/ (26 fichiers)
- Config YAML + schema.py + Docker support
- ~400 fichiers legacy supprimés
- .venv retiré du tracking git

## Agents IA — Gestion Autonome du Projet

Le projet est maintenant géré par des agents opencode autonomes. Tu n'as plus besoin d'intervenir manuellement.

### Architecture des agents
```
┌─────────────────────────────────────────────────┐
│ Robot Manager (primary agent)                   │
│   Orchestre tout : surveillance, diag, fix, opt │
├─────────────────────────────────────────────────┤
│ Sous-agents (@mention) :                        │
│   @log-analyst   → Analyse les logs en détail   │
│   @auto-fixer    → Corrige les bugs seul        │
│   @monitor-agent → Vérifie santé 24/7           │
│   @optimizer     → Analyse les performances     │
├─────────────────────────────────────────────────┤
│ ai-manager.ps1 → Watchdog continu (2 min cycle) │
│   - Redémarre robot si crash                    │
│   - Nettoie PID lock zombie                     │
│   - Alerte si DD > 8% ou logs figés > 5 min     │
└─────────────────────────────────────────────────┘
```

### Commandes IA
```powershell
opencode                    # Mode interactif (Robot Manager actif)
opencode "bilan"            # Résumé complet de l'état du robot
opencode "@log-analyst analyse les 100 dernières lignes"
opencode "@optimizer fais un rapport de performance"
opencode "@monitor-agent check complet"
opencode "@auto-fixer corrige [bug]"
```

### Scripts
```powershell
.\scripts\ai-manager.ps1         # Démarre le watchdog AI (daemon)
.\scripts\ai-manager.ps1 -Status # Voir l'état du watchdog
.\scripts\ai-manager.ps1 -Stop   # Arrêter le watchdog
```

### Fichiers de config
- `opencode.json` — Configuration des agents opencode
- `.opencode/agents/prompts/robot-manager.md` — Prompt de l'orchestrateur
- `.opencode/agents/log-analyst.md` — Agent d'analyse de logs
- `.opencode/agents/auto-fixer.md` — Agent de correction automatique
- `.opencode/agents/monitor-agent.md` — Agent de surveillance
- `.opencode/agents/optimizer.md` — Agent d'optimisation
- `scripts/ai-manager.ps1` — Watchdog continu

### Principe
1. Le **Robot Manager** (opencode en mode build) est l'IA principale qui gère tout
2. Les **sous-agents** sont invoqués via `@mention` pour des tâches spécialisées
3. Le **watchdog** (`ai-manager.ps1`) tourne en arrière-plan et redémarre le robot si nécessaire
4. En cas de bug, l'IA le détecte dans les logs, le diagnostique via `@log-analyst`, le corrige via `@auto-fixer`, et redémarre

**Tu n'as plus qu'à lancer `opencode` et tout est géré.**
