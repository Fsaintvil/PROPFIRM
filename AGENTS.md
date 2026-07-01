# MT5 FTMO - Robot MOM20x3 Multi-Symbol + Intelligence Adaptative

> **Mise à jour 1er Juillet 2026** : Activation 27 symboles, lot progressif WR-based, corrélation active.
> Réparations post-régression (min_score 0.60 rétabli), correction des 10 pertes consécutives,
> **réactivation de TOUS les 22 agents** du council, création des skills **python-pro** et **data-analysis**.
> ⚠️ **Ne pas réactiver le pipeline ML avant 500+ trades propres par symbole.**

## Architecture Intelligence
```
┌──────────────────────────────────────────────────────────┐
│ main.py              Boucle 15s, orchestre tout           │
├──────────────────────────────────────────────────────────┤
│ strategy.py          MOM20x3 pur (règle technique)        │
│   - c[i]-c[i-20] > seuil×ATR → breakout                  │
│   - Seuils: 2.5x trending / 2.0x ranging                 │
│   - Filtres: ADX slope, +DI/-DI, pullback EMA20          │
├──────────────────────────────────────────────────────────┤
│ adaptive_intelligence.py  MarketRegime + OnlineLearner   │
│   ├─ MarketRegime        ADX hystérésis 22/18 ✅ Actif   │
│   ├─ OnlineLearner       Fenêtre 200, adapte thresh/risk │
│   ├─ DLEnsemble          ❌ DÉSACTIVÉ (aucun modèle)     │
│   ├─ LightGBM            ❌ ARCHIVÉ (retired/)           │
│   └─ MetaLearner         ❌ ARCHIVÉ (retired/)           │
├──────────────────────────────────────────────────────────┤
│ signal_pipeline.py    Volume Indicators ✅ Actif          │
│   ├─ RVOL (Relative Volume)                              │
│   ├─ CMF (Chaikin Money Flow) — seuil par symbole        │
│   └─ OBV Divergence — pénalité par symbole               │
├──────────────────────────────────────────────────────────┤
│ ftmo_protector.py  ✅ Protections FTMO                    │
│   - ATR Trailing (peak-based, 4 niveaux par régime)      │
│   - Cooldown 15min, pause après 5 pertes consécutives    │
│   - Partial TP persisté (state.json), max_profit time-stop│
│   - Corrélation max 2/direction/groupe, DD max 10%       │
│   - Daily loss 2%, Consistency 30%, Min 10 jours         │
└──────────────────────────────────────────────────────────┘
```

## Flux de décision
```
MOM20x3 brut → RVOL/CMF/OBV Div → Régime → OnlineLearner → FTMO Protector → Exécution

Indicateurs volume (Phase 7b/8) filtrent les signaux MOM20x3 :
  - RVOL < 0.5  → score × 0.75
  - RVOL > 2.0  → score × 1.10 (max 0.95)
  - CMF > seuil → ×1.08 si aligné, ×0.85 si conflit
  - OBV Divergence forte → score × penalty_high
  - OBV Divergence faible → score × penalty_low
```

## Volume Filter Thresholds par Symbole
| Symbole | Timeframe | cmf_threshold | obv_div_penalty_high | obv_div_penalty_low |
|---------|:---------:|:-------------:|:--------------------:|:-------------------:|
| **XAUUSD** | H4 | 0.10 | 0.70 | 0.85 |
| **BTCUSD** | H1 | **0.20** | **0.85** | **0.92** |
| **EURUSD** | H1 | 0.10 | 0.70 | 0.85 |
| **US500.cash** | H4 | 0.10 | 0.70 | 0.85 |
| Défaut | — | 0.10 | 0.70 | 0.85 |

## Réalité opérationnelle
| Composant | Statut | Preuve |
|-----------|--------|--------|
| **MOM20x3** | ✅ Fonctionnel | 967 trades historiques, 60.2% WR, +$1,560 PnL |
| **FTMO Protector** | ✅ Fonctionnel | Protège DD, weekend, daily loss, cooldown |
| **MarketRegime** | ✅ Fonctionnel | ADX/ATR/MA, SL/TP adaptés |
| **OnlineLearner** | ✅ Actif (1833T, 13 sym) | adapted_params pour 3 symboles, fenêtre 200 |
| **DL LSTM** | ❌ Désactivé | Aucun modèle .pkl trouvé |
| **LightGBM** | ❌ Archivé (retired/) | Aucun modèle entraîné |
| **MetaLearner** | ❌ Archivé (retired/) | 3 trackers désactivés |
| **Performance Monitor** | ✅ Fonctionnel | Rolling windows 20/50/100/200 |

## Régimes de marché (MarketRegime) — ADX HYSTÉRÉSIS 22/18
| Régime | Critère | SL | TP | Risque |
|--------|---------|----|----|--------|
| TREND_UP | ADX>22, MA>0.2% | 2.0×ATR | 5.0×ATR | 100% |
| TREND_DOWN | ADX>22, MA<-0.2% | 2.0×ATR | 5.0×ATR | 100% |
| HIGH_VOL | ATR%>80% | 2.0×ATR | 5.0×ATR | 70% |
| RANGING | ADX<18 | 1.5×ATR | 4.0×ATR | 100% |
| LOW_VOL | ATR%<20% | 1.5×ATR | 4.0×ATR | 100% |

### Trailing stop (ATR-based)
- profit >1.0×ATR → SL = peak − 0.50×ATR (RANGING)
- profit >2.0×ATR → SL = peak − 0.35×ATR
- profit >3.0×ATR → SL = peak − 0.20×ATR
- profit >5.0×ATR → SL = peak − 0.10×ATR

Niveaux par régime :
| Régime | 1er lock | N1 | N2 | N3 | N4 |
|--------|:--------:|:--:|:--:|:--:|:--:|
| RANGING | 1.0×ATR | 0.50 | 0.35 | 0.20 | 0.10 |
| TREND_UP/DOWN | 1.0×ATR | 0.80 | 0.50 | 0.30 | 0.15 |
| HIGH_VOL | 1.0×ATR | 1.00 | 0.70 | 0.50 | 0.25 |
| LOW_VOL | 1.0×ATR | 0.40 | 0.25 | 0.15 | 0.08 |

## Seuils de signal (strategy.py)
- ADX ≥ 22 (trending): thresh = 2.5×ATR
- ADX < 22 (ranging): thresh = 2.0×ATR
- Plafonné à 2.5×ATR max, plancher à 1.5×ATR
- **ADX slope filter** : slope < seuil_par_symbole → signal rejeté
- **Pullback filter** : bande 0.5×ATR trending / 0.3×ATR ranging
- **NaN guard** : `np.isnan(mom)` → signal ignoré proprement
- **DI Override**: short-term momentum (5 périodes) peut inverser si ADX≥22 et +DI croise -DI
- **Higher TF confirmation**: score ×0.90 si TF supérieure contredit la direction

## Session block
- 24/7 — trading continu 7j/7
- Weekend block FTMO supprimé (positions ouvertes le vendredi restent actives avec trailing ATR)

## Apprentissage en ligne (OnlineLearner)
- Fenêtre: 200 derniers trades par symbole
- WR>82% → seuil -0.5 (plus agressif), risque +15%
- WR<70% → seuil +0 (neutre), risque -25%
- Expectancy<0 → risque -50%
- Pause après 3 pertes consécutives

## Protection FTMO
- **ATR Trailing** (remplace peak-$10) : SL adaptatif par multiple d'ATR
- **Règle de consistance FTMO** : stop si un jour >30% du profit total
- **10 jours de trading minimum** : pas de PASS avant min_trading_days
- Cooldown 30min après perte
- Pause après 3 pertes consécutives
- Corrélation: max 2 trades par direction dans un groupe
- DD max: 10% depuis peak
- Daily loss: 2%
- RR≥2.0 enforce avant execution

## Configuration
```python
RISK_PER_TRADE = 0.004      # 0.40% par trade
COOLDOWN_MINUTES = 15
MAX_POSITIONS = 10
MAX_POSITIONS_PER_SYMBOL = 4
MAX_TRADES_PER_DAY = 20
MAX_SPREAD_POINTS = 120
MIN_RR_RATIO = 2.0
CONSISTENCY_MAX_PCT = 0.30
```

## Symboles et limites (27 symboles actifs — 1er Juillet 2026)
```
┌─ FOREX MAJORS ──────────────────────────────────────────────────────┐
│ EURUSD    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 68.6%│
│ GBPUSD    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 67.9%│
│ USDCHF    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 68.1%│
│ USDCAD    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 67.4%│
│ AUDUSD    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 67.1%│
│ NZDUSD    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 67.6%│
│ USDJPY    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 68.3%│
├─ FOREX CROSSES ─────────────────────────────────────────────────────┤
│ EURJPY    max_lot=0.01  risk=1.00  spread=60  adx=22  H1  ★ WR 67.5%│
│ GBPJPY    max_lot=0.01  risk=1.00  spread=80  adx=22  H1  ★ WR 68.0%│
│ EURGBP    max_lot=0.01  risk=1.00  spread=40  adx=22  H1  ★ WR 67.0%│
│ AUDJPY    max_lot=0.01  risk=1.00  spread=60  adx=22  H1  ★ WR 67.0%│
├─ CRYPTO ────────────────────────────────────────────────────────────┤
│ BTCUSD    max_lot=0.01  risk=1.00  spread=150 adx=20  H1  ★ WR 75.9%│
│ ETHUSD    max_lot=0.01  risk=1.00  spread=120 adx=20  H1  ⚠️ WR 27.6%│
│ SOLUSD    max_lot=0.01  risk=1.00  spread=120 adx=20  H1  ★ WR 65.0%│
│ LNKUSD    max_lot=0.01  risk=1.00  spread=120 adx=20  H1  ★ WR 65.0%│
│ BNBUSD    max_lot=0.01  risk=1.00  spread=120 adx=20  H1  ★ WR 65.0%│
├─ INDICES ───────────────────────────────────────────────────────────┤
│ US500.cash max_lot=0.01 risk=1.00  spread=50  adx=22  H1  ⚠️ PF 0.24│
│ US30.cash  max_lot=0.01 risk=1.00  spread=50  adx=22  H1  ★ WR 67.0%│
│ US100.cash max_lot=0.01 risk=1.00  spread=60  adx=22  H1  ★ WR 67.0%│
│ JP225.cash max_lot=0.01 risk=1.00  spread=60  adx=22  H1  ★ WR 67.6%│
│ GER40.cash max_lot=0.01 risk=1.00  spread=60  adx=22  H1  ★ WR 67.0%│
│ UK100.cash max_lot=0.01 risk=1.00  spread=60  adx=22  H1  ★ WR 67.0%│
├─ COMMODITIES ───────────────────────────────────────────────────────┤
│ XAUUSD    max_lot=0.01  risk=1.00  spread=60  adx=22  H4  ★ WR 73.0%│
│ XAGUSD    max_lot=0.01  risk=1.00  spread=80  adx=22  H1  ★ WR 67.0%│
│ USOIL.cash max_lot=0.01 risk=1.00  spread=80  adx=22  H1  ★ WR 68.4%│
│ UKOIL.cash max_lot=0.01 risk=1.00  spread=80  adx=22  H1  ★ WR 67.0%│
│ NATGAS.cash max_lot=0.01 risk=1.00 spread=100 adx=22  H1  ★ WR 67.0%│
└─────────────────────────────────────────────────────────────────────┘
```
> ℹ️ **Tous les lots démarrent à 0.01**. Le lot augmente progressivement selon WR (0.01→0.10).
> ⚠️ **ETHUSD** : WR 27.6% live en juin — réactivé sous surveillance, lot min 0.01.
> ⚠️ **US500.cash** : PF 0.24 live en juin — réactivé sous surveillance, lot min 0.01.

## Commandes
```powershell
python main.py              # Lancer le robot
taskkill /F /IM python.exe  # Arrêter le robot
.\scripts\robot.ps1         # Lancer robot + moniteur
.\scripts\robot.ps1 -Status # Voir l'état
.\scripts\robot.ps1 -Stop   # Arrêter tout
opencode                    # Lancer l'IA manager (mode interactif)
opencode "bilan"            # L'IA analyse et résume l'état du robot
.\scripts\daily_report.ps1              # Rapport complet Challenge + symboles
python scripts/backtest_universe.py     # Backtest MOM20x3 sur 15 symboles
python scripts/backtest_volume_indicators.py  # Impact volume (RVOL/CMF/OBV)
python scripts/backtest_with_costs.py   # Backtest avec spreads réels
python scripts/validate_strategy.py --csv runtime/trades_log.csv  # Validation stats
python scripts/court_of_law.py          # 🏛️ Tribunal des Prop Firms
python scripts/heatmap.py               # Heatmap PnL année × symbole
python scripts/seed_active_symbols.py   # Seed OnlineLearner 3 symboles
```

## Backtest Multi-TF 12+ Ans (158 964 trades)
### Résumé par symbole (tous timeframes cumulés)
```
Symbole     Trades    WR       PnL       DD Max
─────────────────────────────────────────────────
GBPJPY       12 829  68.0%  +$624,210   15.2%
USDJPY       13 256  68.3%  +$542,719   16.4%
BTCUSD        8 455  70.0%  +$529,257   17.9%
ETHUSD        8 268  69.7%  +$427,322    6.0%
EURJPY       13 040  67.5%  +$394,139   16.8%
JP225.cash    4 222  67.6%  +$236,660    8.4%
GBPUSD       13 383  67.9%  +$200,890   11.8%
EURUSD       13 447  68.1%  +$183,350   10.6%
USDCHF       12 953  68.1%  +$144,192    8.0%
NZDUSD       12 820  67.6%  +$115,782   10.1%
USDCAD       13 060  67.4%  +$115,554    8.8%
AUDUSD       13 301  67.1%   +$94,153   10.5%
USOIL.cash    3 949  68.4%   +$24,281    1.9%
XAUUSD       11 734  65.3%   -$51,445  126.2%
```

### Avertissements
- WR uniforme ~67-68% suspect — possible biais (pas de spread réel)
- Performance réelle : 958 trades historiques, 60.8% WR
- **XAUUSD H1** : bear market 2013-2020 catastrophique (-$187K), mais positif depuis 2021

## Règles
- Magic number: 999001
- 27 symboles, max 6 positions par symbole (corrélation: max 2/direction/groupe, 6 groupes)
- Signal → Régime → FTMO → Trade
- 5 pertes consécutives = pause
- 15s cycle
- PID lock dans `runtime/robot.pid`

## Trailing + Partial TP
- `_check_partial_tp` → `_check_step_trailing` (ordre inverse)
- Partial TP ferme 50% à 60% du TP, set BE à 0.8×ATR
- BE conditionnel : ne s'applique QUE si le SL actuel est plus faible
- Trailing 4 niveaux ATR (0.5× → 0.35× → 0.20× → 0.10× du peak)

## PID Lock
- `runtime/robot.pid` contient le PID du processus en cours
- Named mutex Windows (Global\MT5_FTMO_MOM20x3) + fichier PID fallback
- Nettoie automatiquement à l'arrêt

## Performance Monitor
- `record_trade()` via position_tracker.py (temps réel)
- Rapport quotidien automatique
- Rolling windows 20/50/100/200
- Alertes : WR baisse >15%, PF < 1.0, DD approche 10%

## Agents IA — Council au complet (22 agents)

```
Robot Manager (primary agent)
│
├── 🔵 CORE COUNCIL (décision & coordination)
│   ├── @cio                  → Coordination, cycles 15s
│   ├── @supreme-council      → Méta-agent, tranche les conflits
│   ├── @risk-compliance      → Capital, FTMO, veto, corrélation, conformité
│   └── @kill-switch          → Arrêt d'urgence unifié
│
├── 🟢 SURVEILLANCE & INFRA
│   ├── @system-monitor       → Surveillance 24/7, logs, mémoire, données
│   ├── @monitor-agent        → Watchdog allégé du robot
│   ├── @performance-engineer → Mesure vitesse, stabilité, mémoire, CPU
│   ├── @mt5-infrastructure-auditor → Santé connexion MT5
│   └── @data-manager         → Données MT5 fiables (fraîcheur, schéma, intégrité)
│
├── 🟡 SIGNAL & STRATÉGIE
│   ├── @signal-engine        → Signaux MOM20x3, filtres, régime
│   ├── @adaptive-engine      → Calibration ML, OnlineLearner
│   ├── @alpha-researcher     → Recherche de nouveaux signaux
│   └── @adversarial-trader   → Stress-test de la stratégie
│
├── 🟠 ANALYSE & OPTIMISATION
│   ├── @quant-auditor        → Statistiques, overfitting, validation
│   ├── @optimizer            → Analyse performance, ajustements
│   ├── @log-analyst          → Analyse forensique des logs
│   └── @market-philosopher   → Contexte macro et inter-marchés
│
├── 🔴 RISQUE & CONFORMITÉ
│   ├── @ftmo-prosecutor      → Procureur FTMO (conformité)
│   ├── @prop-compliance      → Conformité prop firms (FTMO, etc.)
│   ├── @risk-marshal         → Risque d'exécution (slippage, spread)
│   └── @security-auditor     → Sécurité du code, données, secrets
│
└── 🟣 CORRECTION & DÉBAT
    ├── @auto-fixer           → Correction chirurgicale des bugs
    ├── @devils-advocate      → Contradicteur socratique
    └── @eth-usd-specialist   → Spécialiste ETHUSD (si réactivé)
```

### Trading Intelligence Council (cycles 15s)
```
→ Délégation cycle {n} à @cio
→ CIO vérifie métriques + convoque experts si besoin
→ Retour : "ALL CLEAR" ou "ALERTE niveau X"
```

| Situation | Appel |
|-----------|-------|
| Début de cycle normal | `@cio` |
| Erreur/logs/mémoire | `@system-monitor` |
| Bug identifié | `@auto-fixer` |
| DD > 6% / daily loss > 1.5% | `@risk-compliance` (peut poser veto) |
| Performance douteuse | `@quant-auditor` + `@optimizer` |
| Connexion MT5 instable | `@system-monitor` |
| Arrêt d'urgence | `@kill-switch` |
| Conflit entre agents | `@cio` → `@supreme-council` |

### Veto du Risk & Compliance
Si DD>8% ou daily loss>1.8% → **STOP immédiat**. Tu ne peux pas passer outre.
Pour contester un veto, convoque le `@supreme-council`.

### Skills disponibles (8)
| Skill | Domaine | Quand l'utiliser |
|-------|---------|-----------------|
| **python-pro** | Développement Python, debugging, profiling, tests | Bug complexe, refactoring, optimisation code |
| **data-analysis** | Analyse financière pandas/numpy, backtest, métriques | Analyse trades logs, calcul Sharpe/drawdown |
| **mom20x3-strategy** | Signaux MOM20x3, seuils ATR, filtres | Problème de signal, ajustement seuils |
| **ftmo-protector** | Règles FTMO, trailing, DD, daily loss | Trade refusé, règle FTMO, trailing bloqué |
| **backtest-validation** | Stats, p-value, walk-forward, overfitting | Valider un edge, analyse statistique |
| **mt5-operations** | Connexion MT5, erreurs API, retry | MT5 déconnecté, ordre rejeté, infra |
| **monitoring-health** | Watchdog, métriques, alertes, logs | Bilan santé, analyse logs, redémarrage |
| **market-regime** | ADX/ATR/MA, 5 régimes, trailing par régime | Régime mal détecté, trailing inadapté |

### Agents désactivés (code conservé, non chargés)
Les fichiers suivants existent dans `.opencode/agents/` mais ne sont plus référencés :
- `@eth-usd-specialist` — spécialiste ETHUSD (devenu superviseur dans le council)
- `@us500-commissioner`, `@us-oil-analyst` — spécialistes par actif (supprimés en faveur du council généraliste)
