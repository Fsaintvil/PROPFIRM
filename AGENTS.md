# MT5 FTMO - Robot MOM20x3 Multi-Symbol + Intelligence Adaptative

> **Mise à jour 17 Août 2026 (08:05→08:15)** : 🔍 **Consolidation instrumentation + décision GR élargie** —
> - **Commit `d7c61e903`** : consolidation du compteur de rejets — on garde `engine_simple/reject_counter.py`
>   (instrumentation signal_pipeline.py + signal_validator.py, flush silencieux → `runtime/reject_counter.json`),
>   suppression des doublons `rejection_tracker.py` et `scripts/check_gr_stalled.py` (réfs nettoyées dans
>   trading_engine.py / daily_checkpoint.py). Tests : 205 passed (signal/pipeline/validator).
> - **DÉCISION UTILISATEUR FINALE (17/08 08:15) : GR élargie à 13 symboles + cap consistance désactivé en preuve** —
>   1. **Périmètre GR étendu** (`scripts/golden_rule.py`) : les 13 symboles actifs comptent pour la collecte
>      des 100 trades (US100/US30/JP225/SOLUSD/BTCUSD + XAUUSD/EURUSD/GBPUSD/USDJPY/USDCAD/AUDUSD/NZDUSD/USDCHF),
>      pas seulement les 5 du repositionnement. Rational : XAUUSD/Forex font des trades réels qui doivent compter.
>   2. **Flag `CONSISTENCY_CAP_ENABLED=false`** (config/default.yaml risk) : le cap de consistance FTMO est
>      désactivé en mode preuve GR (compte démo, challenge expiré). Il reste disponible (default True) pour un
>      vrai challenge. Le cap bloquait TOUTE la collecte car XAUUSD (hors GR) avait fait +$344 = 58.9% du total
>      positif windowisé > 30% → 0 trade possible. Désormais la collecte continue.
>   3. Commit `1ad129734` : flag `consistency_cap_enabled` ajouté au schéma (RiskConfig), config_simple.py,
>      ftmo_protector.py (guard dans `_check_consistency_cap` L.1185), trading_engine.py. Tests : **1175 passed**.
>   4. **GR state après élargissement** : 16/100 trades (vs 6 avant), WR 68.8%, PF 10.78 (état 17/08 08:00 —
>      XAUUSD +$344.25 sur 2 trades = 97% du PnL, échantillon encore petit). Avertissement : le PF élevé est
>      dominé par XAUUSD, la vraie preuve d'edge reste à confirmer sur 100 trades.
> - **Fix tâche planifiée `MT5_FTMO_GRStallCheck`** : pointait vers `check_gr_stalled.py` (supprimé) → redirigée
>   vers `scripts/check_gr_symbols.py` (le diagnostic GR complet, run unique compatible). Testé OK.
> - **⚠️ Watchdog** : lors du redémarrage manuel (17/08 07:57), l'ancien watchdog n'a PAS relancé le robot après
>   kill (aucune entrée "CRITICAL DEAD" dans watchdog_external.log). Robot relancé manuellement (PID 3060), le
>   nouveau watchdog (PID 22048) surveille correctement. À surveiller : la résurrection automatique du watchdog.
> - **Config réelle des symboles GR** (source de vérité = config, pas la doc) : US100.cash min_score=0.5 /
>   US30.cash 0.6 / JP225.cash 0.5 / SOLUSD 0.6 (max_spread_atr_ratio=0.25) / BTCUSD 0.5. Tous BUY-only.
> **Mise à jour 16 Août 2026 (21:35)** : 🔧 **SOLUSD débloqué — `max_spread_atr_ratio` 0.15→0.25**
> (config/default.yaml:symbol_limits.SOLUSD). SOLUSD était bloqué au PRECHECK par le ratio ATR :
> spread réel 3pts (0.03 $) mais ratio ATR 17.4% > 15% défaut → `Spread too high`. Le pattern est
> identique aux fixes BTCUSD (0.25), USOIL (0.22), UK100 (0.25). Limites points déjà OK (0.03 < 1.20).
> Le robot recharge `symbol_limits` à chaud (refresh_symbol_limits ~15 min). SOLUSD reste BUY-only,
> lot 0.05, cooldown 20 min, auto_pause 3. **GR : 6/100 trades, WR 50%, PF 0.72** (state.json 16/08 21:01,
> uniquement US100.cash — US30/JP225/SOLUSD/BTCUSD = 0 trade, marché baissier mom20<0, pas un bug).
> ✅ Correction d'analyse : le robot N'est PAS gelé — MT5 montre 18 positions fermées + 2 ouvertes
> (13-14/08) sur 7 symboles (GBPUSD +22.35 $, AUDUSD +8.28 $), mais le compteur GR ne valide que les
> 5 symboles du repositionnement (décision utilisateur confirmée 16/08). Test `_check_spread` SOLUSD :
> ratio 16.3% < 25% ✅. Suite de tests : **1175 passed, 33 skipped**.
> **Mise à jour 14 Août 2026 (14:20)** : 🔥 **XAUUSD + 7 PAIRES PRIMAIRES réactivées** (décision
> utilisateur). **13 symboles actifs** : US100.cash, US30.cash, JP225.cash, SOLUSD, BTCUSD (repositionnement
> 13 Août) + **XAUUSD, EURUSD, GBPUSD, USDJPY, USDCAD, AUDUSD, NZDUSD, USDCHF**. Garde-fous :
> **BUY-only partout** (allow_shorts=false — SELL = WR 34% historique = −2 925 $), `XAUUSD risk_mult`
> 0.0→**1.0** (c'était un bloqueur de trades), `min_score` XAUUSD 0.75→**0.65** (cohérent mode preuve),
> max_lot XAUUSD 0.05 / EURUSD-USDJPY-USDCAD 0.05 / GBPUSD 0.15 / AUDUSD 0.12 / NZDUSD 0.10 / USDCHF 0.01.
> ⚠️ Le Forex est un **perdant structurel après coûts** (EURUSD PF 0.74, USDJPY 0.96) mais c'est une
> décision utilisateur explicite. Fallback `ACTIVE_SYMBOLS` dans trading_engine.py = `cfg.SYMBOLS`
> (le hardcode 5 symboles ignorait les nouveaux). `.env` `SYMBOLS` étendu à 13. Backups :
> `config/backup_default_20260814_avant_xauusd_forex.yaml`, `.env.bak_20260814_avant_xauusd_forex`.
> Suite de tests : **1166 passed**. Commit `c277b9db4`.
> **Mise à jour 13 Août 2026 (23:30)** : 🔥 **BTCUSD activé** (décision utilisateur) — groupe CRYPTO
> indépendant (2 slots de corrélation libres, PF backtest 1.18, spread live 100 pts = 0.4% ATR H1),
> BUY-only. **Paramètres assouplis** : `min_score` 0.70 → **0.65** (+~30% de signaux, risque WR
> légèrement bas assumé), `min_trade_interval_sec` 300 → **180s** (plus de doublons temporels assumé).
> Backups : `config/backup_*_20260813_avant_btc.yaml`. Suite de tests : **1162 passed**.
> **Mise à jour 13 Août 2026** : 🏆 **RÈGLE D'OR activée** (décision utilisateur) — remplace l'ancienne
> phase de preuve (obsolète). **AUCUN scaling ni re-tentative de challenge avant validation** de 100
> trades propres sur les 5 symboles du repositionnement : **US100.cash, US30.cash, JP225.cash, SOLUSD,
> BTCUSD** (edge démontré après coûts réels, 160 877 trades : PF 1.20 / 1.14 / 1.23 / 1.25 / 1.18).
> Forex retiré (EURUSD PF 0.74, USDJPY 0.96, EURGBP 0.64, USDCAD 0.74, USOIL 0.99 — perdants structurels).
> Critères RÈGLE D'OR : **≥ 100 trades ET WR ≥ 60% ET PF ≥ 1.1** → sinon STOP définitif.
> Suivi automatisé : `python scripts/golden_rule.py` (état dans `runtime/golden_rule/state.json`),
> intégré au checkpoint quotidien `MT5_FTMO_DailyCheckpoint` (20:00). Borne : 2026-08-13 21:20 heure
> journal (UTC+3). Challenge FTMO perdu (0 jour restant, 19 jours consommés) — compte démo en cours.
> **Mise à jour 06 Août 2026** : 🔧 **MODE PREUVE STRICT activé** (décision utilisateur "fait tout maintenant").
> But : prouver l'edge en réel sur 100+ trades propres AVANT tout scaling. **5 symboles BUY-only**
> (XAUUSD, EURUSD, USDJPY, EURGBP, USOIL.cash), `allow_shorts=false` partout, lots réduits 0.05 max,
> veto risk-compliance appliqué (per_trade 0.3%, max_pos 8, auto_pause 5, min_score 0.70, cooldown 15,
> max_risk $600). Fix signal_pipeline double pénalité H4 actif. **Checkpoint quotidien** :
> `scripts/daily_checkpoint.py` via tâche planifiée Windows `MT5_FTMO_DailyCheckpoint` (20:00).
> Rapports dans `runtime/daily_checkpoint/`. SELL bannis : WR 34% cumulé = -2 925$ sur 364 trades.
> XAGUSD désactivé (trou noir -1 484$). Backups config : `config/backup_*_20260806.yaml`.
> **Mise à jour 05 Août 2026** : Gate de régime STRICT (RANGING ADX<20 → TOUJOURS rejeté, plus d'exception
> score ≥ 0.85), fix `trading_days` v3 (union jours persistés + reconstruits — 12 jours compte FTMO conservés),
> fix label log `risk_per_01` (affichait sl_profit brut négatif → confusion, pas de bug de calcul),
> **capture stderr du watchdog externe** dans `logs/watchdog_external.log` (gel 4h du 05/08 02:39→06:39 non
> diagnostiqué car stderr partait dans le vide), XAGUSD désactivé.
> **Mise à jour 31 Juillet 2026** : min_score 0.70 enforce (signal_validator), **XAGUSD désactivé** (trou noir,
> ~$1,470 cumulés), circuit breaker progressif (lot ×0.25 à 7 pertes, HARD STOP à 10), **fix watchdog** (chemin
> heartbeat ABSOLU — protection anti-freeze GIL restaurée), recalibration risk_per_trade 0.005, 25 symboles actifs.
> **Mise à jour 1er Juillet 2026** : Activation 27 symboles, lot progressif WR-based, corrélation active.
> Réparations post-régression, correction des 10 pertes consécutives,
> **réactivation de TOUS les 22 agents** du council, création des skills **python-pro** et **data-analysis**.
> ⚠️ **Ne pas réactiver le pipeline ML avant 500+ trades propres par symbole.**
> ⚠️ **Tableau des symboles ci-dessous obsolète** : la colonne `max_lot=0.01` ne reflète plus la config
> réelle (default.yaml) — EURUSD 0.20, GBPUSD/USDJPY/USDCAD 0.15, AUDUSD 0.12, XAUUSD 0.10, etc. La source
> de vérité est `config/default.yaml` (le lot progressif WR-based a été désactivé le 16 Juillet).

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

### Trailing stop (ATR-based) — Config 31 Juillet 2026 (R2, backtest-validée)
> ⚠️ **Divergence historique corrigée le 14 Août 2026** : AGENTS.md documentait l'ancienne config
> du 30 Juillet (N1 lock 1.20×ATR / trail 0.80×ATR) mais le code réel (ftmo_config.py
> `TRAILING_BY_REGIME`) utilise la config 31 Juillet. La doc est désormais alignée sur le code.
> La config serrée du 30/07 avait été calibrée sur un WR 35% corrompu (direction inversée dans le CSV) ;
> le revert R2 du 31 Juillet laisse les gagnants respirer jusqu'au partial TP (65% du TP) avant de verrouiller.

Niveaux par régime (lock = profit en ×ATR pour activer, trail = distance SL/peak en ×ATR) :
| Régime | N1 lock | N1 trail | N2 lock | N2 trail | N3 lock | N3 trail | N4 lock | N4 trail | N5 lock | N5 trail |
|--------|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|:-------:|:--------:|
| RANGING | 1.80×ATR | 0.80×ATR | 2.50×ATR | 0.55×ATR | 3.50×ATR | 0.40×ATR | 5.00×ATR | 0.25×ATR | 5.50×ATR | 0.15×ATR |
| TREND_UP/DOWN | 1.80×ATR | 1.00×ATR | 2.50×ATR | 0.70×ATR | 3.50×ATR | 0.50×ATR | 5.00×ATR | 0.30×ATR | 6.00×ATR | 0.20×ATR |
| HIGH_VOL | 1.80×ATR | 1.20×ATR | 2.50×ATR | 0.90×ATR | 3.50×ATR | 0.65×ATR | 5.00×ATR | 0.40×ATR | 6.00×ATR | 0.25×ATR |
| LOW_VOL | 1.80×ATR | 0.70×ATR | 2.50×ATR | 0.50×ATR | 3.20×ATR | 0.35×ATR | 4.50×ATR | 0.20×ATR | 5.50×ATR | 0.12×ATR |

Note : ces valeurs incluent un jitter aléatoire ±10% pour éviter le hunting.
Exemple : GBPUSD TREND_UP, ATR=0.00096, peak_profit=1.82×ATR (>1.80) → N1: SL = peak − 1.00×ATR = 1.35463
Pour atteindre N2 (2.50×ATR), le trade doit gagner encore 0.68×ATR (~$0.00065 sur GBPUSD).

### Breakeven progressif (31 Juillet 2026)
Séquence de sécurisation des profits AVANT que le trailing N1 (1.80×ATR) ne s'active :
```
profit > 1.00×ATR → SL = entry (breakeven pur, zéro perte garantie)
profit > 1.30×ATR → SL = entry ± 0.15×ATR (petit gain garanti)
```
> 🔧 FIX 31 Juillet 2026 (Quant Auditor) : les seuils précédents (0.80/0.50×ATR) coupaient
> 62% des gagnants à <0.5R avant même le lock N1. En repoussant à 1.00/1.30×ATR, les trades
> faibles ont une chance d'atteindre la zone N1 au lieu d'être stoppés net sur le bruit.

Fonctionne avec la garde `sl_improves` : ne s'applique QUE si le nouveau SL est meilleur
que l'actuel (ne réduit jamais la protection déjà en place).
Appelé dans la séquence : time_stop → **progressive_be** → partial_tp → step_trail → structure

### Partial TP (31 Juillet 2026)
- Déclenchement : progress ≥ **0.65** (65% du chemin vers le TP, calculé sur le PEAK pas price_current)
- Ferme **50%** du volume (arrondi au lot_step), puis set BE avec buffer par symbole/régime (`BE_BUFFER_BY_SYMBOL`)
- 🔧 31 Juil 2026 (R3) : 0.40→0.65 — la config 40% fermait la moitié du trade dès 1.6R,
  coupant la course vers le TP. À 65% (=3.25R sur TP 5×ATR), la moitié close est déjà en
  zone rentable ET la moitié restante a une vraie chance d'atteindre le TP 4-6×ATR.
  Le backtest 158K trades (PF>1.1) n'avait PAS de partial TP à 40% — jamais validé.

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
- RR≥1.8 enforce avant execution (MIN_RR_RATIO configurable)

## Configuration
```python
RISK_PER_TRADE = 0.008      # 0.80% par trade (production.yaml)
COOLDOWN_MINUTES = 15
MAX_POSITIONS = 18           # production.yaml
MAX_POSITIONS_PER_SYMBOL = 6 # production.yaml
MAX_TRADES_PER_DAY = 75      # production.yaml
MAX_SPREAD_POINTS = 120
MIN_RR_RATIO = 1.8
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
- Partial TP ferme 50% à **65% du chemin vers le TP** (config 31 Juillet R3), set BE avec buffer par symbole/régime (`BE_BUFFER_BY_SYMBOL`)
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
