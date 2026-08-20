# MT5 FTMO - Robot MOM20x3 Multi-Symbol + Intelligence Adaptative

> **Mise à jour 20 Août 2026 (18:45)** : 🔧 **ALIGNEMENT CONFIG `preferred_hours` strategy.py ↔ YAML (6 divergences corrigées)** —
> - **Contexte** : audit de cohérence — `_check_session` (ftmo_protector.py L.1219) lit `preferred_hours` depuis **symbol_limits (YAML)** alors que `SymbolParamManager` lit strategy.py. Le YAML étant consommé par le filtre de session, toute divergence a un impact réel sur le trading.
> - **Divergence CRITIQUE corrigée — US30.cash** (symbole GR ACTIF) : YAML `[0-23]` (24/7) vs strategy.py `[13-21]` (session NY) → le filtre de session laissait trader US30 24/7 alors que la stratégie le restreint à la session NY. **Corrigé : YAML → [13-21]**.
> - **5 divergences corrigées — symboles inactifs** (cohérence documentaire, évite les surprises si réactivés) : EURGBP → [8-17], UKOIL.cash → [8-21], NATGAS.cash → [13-21], GER40.cash → [8-17], UK100.cash → [8-17]. Toutes alignées sur strategy.py.
> - **Non-bug documenté** : les divergences `adx_slope_threshold` YAML (ex: BTCUSD -3.0) sont **cosmétiques** — ce champ n'est lu QUE depuis strategy.py (`strategy.py` L.1011, via SymbolParamManager), jamais depuis le YAML.
> - **Tests** : suite complète **1222 passed, 33 skipped** (aucune régression). Le hot-reload (~15 min) propagera les nouvelles valeurs au pipeline (dict partagé symbol_limits). Robot inchangé : PID **21856**, watchdog **296**, balance 199 708, DD 0.3%.

> **Mise à jour 20 Août 2026 (09:00)** : 🔧🔧 **FIX CRITIQUE PRICE-DEDUP INERTE (offset serveur non appliqué) + RÉVISION DÉCOUVERTE PnL LEGACY (direction inversée, PAS le PnL)** —
> - **Contexte** : l'analyse forensique approfondie (log-analyst @session `ses_fe2400721ffeklthEE6razotdt` + vérification indépendante Robot Manager) a révélé que le fix 2b du matin (price-dedup 120s→600s) restait **INERTE** : la garde `0 < pos_age < 600` ajoutée le 10/08 (anti-faux-rejets USDJPY) rendait le price-dedup **TOTALEMENT inopérant** — `pos.time` (API MT5) est en TEMPS SERVEUR (+3h) donc `pos_age ≈ −10800s` → `0 < age` TOUJOURS FAUX → **JAMAIS de blocage** (0 occurrence "[DOUBLON]" dans le log malgré les doublons XAUUSD 04:53 et 08:28 du 20/08 = −338$ + −113$). Le fix 10/08 avait échangé "faux rejets" contre "jamais de rejet".
> - **Fix — offset serveur appliqué au price-dedup** (`engine_simple/trade_executor.py` L.308-317) : `server_offset` lu depuis `self.ftmo._server_offset_s` (mesuré au démarrage, fix time-stop du 19/08) et soustrait à `pos.time` AVANT de calculer `pos_age` → l'age réel est restauré (`now − (pos.time − offset)`). Garde `0 < pos_age < 600` **conservée** : si offset = 0 (fail-open/non mesuré), comportement identique au fix 10/08 (pas de faux rejet). Test dédié `test_price_dedup_blocks_with_server_offset_realistic` (offset absent → non bloqué ; offset 10800s + position 5 min → bloqué).
> - **RÉVISION — PnL legacy : PAS signe-inversé, c'est la colonne `direction` qui est inversée** (données 14/06→28/07, 746 trades) : vérification indépendante (script dédié, 3 passes) — le **montant** du PnL legacy est CORRECT (ratio |pnl|/|Δprix×volume| = 100.0 identique legacy/moderne) ; la **direction** CSV ne correspond qu'à 7.8% de la réalité (99% moderne) car le fix 29/07 (`DEAL type ≠ position direction`) a corrigé un label inversé. **⚠️ Corrige le rapport log-analyst** qui concluait à un PnL signe-inversé (SELL = +3953$) : en réalité les "SELL" CSV legacy étaient des **BUY** réels. **Conséquences** : « XAGUSD trou noir −1484$ » **CONFIRMÉ** (−1483.55$ toutes périodes, désactivation valide) ; « SELL bannis WR 34% = −2925$ » reste **défendable** (le vrai WR SELL legacy = 53.6%, PnL −939$ — toujours perdant). **AUCUNE modification du CSV legacy** (risque de fausser les rapports GR en cours) — décision utilisateur : ne pas toucher, documenter.
> - **Tests** : +1 (price-dedup offset serveur réaliste) → **1222 passed, 33 skipped**.
> - **Robot redémarré** : PID **21856** (avant 28104), watchdog **296**. SERVER_OFFSET **+3.00h** mesuré au démarrage (08:29:27). 2 positions reprises (BTCUSD, SOLUSD). Balance 199 676, DD 0.3%. Le fix price-dedup est désormais ACTIF en production (ancien code : doublons possibles).

> **Mise à jour 20 Août 2026 (08:30)** : 🔧🔧 **FIX WATCHDOG FLAG RÉSIDUEL + FIX DOUBLONS INTRA-SYMBOLE (XAUUSD −338$)** —
> - **Contexte** : analyse forensique des 48h (18/08 07:20 → 20/08 07:20) : 52 trades, PnL −121.46$, PF 0.66. Deux anomalies critiques identifiées et corrigées :
>   1. **Robot gelé 1h52 sans résurrection** (05:21→07:13, "WATCHDOG: 6705s since last cycle") — cause : `runtime/robot.stop.flag` résiduel écrit par l'ancien PID 16972 à 23:52:10 (fix 16/08 qui écrit le flag) mais **jamais nettoyé au démarrage** quand le robot est lancé hors robot.ps1 → le watchdog externe restait en *"Auto-restart disabled (monitoring only)"* → pas de résurrection pendant le gel. `robot.ps1` (L.200-205) nettoie les flags mais seul un lancement via robot.ps1 le fait.
>   2. **Doublons intra-symbole** : le robot rejoue le même signal MOM20x3 tant qu'il reste actif — XAUUSD BUY 02:02 + 02:07 (5 min d'écart, prix 4515.49/4515.12) → 2 SL = −166$ + −172$ = **−338$**. Même pattern 19/08 : XAUUSD 18:00/18:05, SOLUSD 3× en 11 min, BTCUSD 3× en 15 min.
> - **Fix 1 — nettoyage flags au démarrage** (`main.py::_clean_watchdog_flags`, L.72-101) : `robot.stop.flag` + `robot.halt.flag` supprimés au démarrage AVANT `_acquire_lock()`/`robot.start()` (pattern aligné robot.ps1). `stop_for_day.flag` **volontairement conservé** (décision kill-switch — un démarrage manuel ne doit pas annuler un stop d'urgence DD>10%/daily loss>1.8%). Le flag périmé a été supprimé en live pour réactiver la résurrection du PID 23848 immédiatement.
> - **Fix 2a — cap bypass par symbole** (`engine_simple/ftmo_config.py::BYPASS_MAX_PER_SYMBOL` = `{"XAUUSD": 1}` défaut 4 + `signal_pipeline.py` L.294-302) : le central bypass (score ≥ 0.90 + raw_mom ≥ 0.85) plafonne désormais à 1 position/signal pour XAUUSD (SL 1.5×ATR serré → doublon = risque doublé). Autres symboles : comportement historique conservé (4).
> - **Fix 2b — price-dedup élargi** (`engine_simple/trade_executor.py` L.285-315) : fenêtre 120s→**600s** + tolérance 0.03%→**0.05%** → un même signal rejoué dans les 10 min est bloqué comme doublon. La garde du fix 10/08 (age négatif offset serveur → pas de faux rejet) est **conservée** (`0 < pos_age < 600`).
> - **Tests** : +6 (3 main flags, 1 bypass cap XAUUSD/USDJPY, 1 price-dedup 5min bloqué, 1 régression age négatif) → **1221 passed, 33 skipped**.
> - **Robot redémarré** : PID **28104** (avant 23848), watchdog **29164** actif en résurrection (flag nettoyé). SERVER_OFFSET +3.00h. 2 positions reprises (BTCUSD, SOLUSD). Balance 199 676, DD 0.3%.

> **Mise à jour 20 Août 2026 (00:15)** : 🔍 **AUDIT APPROFONDI TRAILING & BE — symétrie SELL fixée + vérification stops_level broker + doc divergence peak** —
> - **Contexte** : analyse en profondeur de la mécanique trailing/BE (skill ftmo-protector + code + logs live). La séquence réelle (time_stop → progressive_be → partial_tp → step_trail → structure) fonctionne correctement : BE progressif monte d'abord, le trailing N1 domine dès son lock (garde `sl_improves`), les logs live confirment (BTCUSD BE 1.98→2.64×ATR puis TrailATR 1.50×ATR, XAUUSD lock 1.5×ATR trail 0.80×ATR).
> - **Fix 1 — symétrie BUY/SELL du force BE** (`engine_simple/trailer.py::_check_step_trailing`) : le SELL ne forçait JAMAIS le breakeven sur retracement > 1 ATR (le BUY le faisait L.479-480) — un SELL qui retraçait fort restait exposé avec SL au-dessus de l'entrée. **Fix** : bloc `_force_breakeven` ajouté côté SELL (condition inverse : `position.sl > position.price_open`). Impact actuel nul (BUY-only partout) mais dette défensive éliminée. Test dédié : `test_sell_retracement_forces_breakeven`.
> - **Vérif 2 — `trade_stops_level` broker** : mesuré en live sur FTMO-Demo = **0 pts pour TOUS les symboles actifs** (XAUUSD, BTCUSD, EURUSD, SOLUSD, GBPUSD, USDCAD, AUDUSD, USDJPY). → `min_gap = trail_distance` dans `_check_step_trailing`, le trailing N'EST JAMAIS bridé par le broker. Risque écarté. Bonus : XAUUSD ATR H4 réel = **$36.95** (pas $20 estimé dans la doc ftmo_config — ATR live plus élevé, le trailing 0.80×ATR = $29.5).
> - **Doc 3 — divergence peak H1 vs ATR H4** (`trailer.py::_reconstruct_peak`) : la reconstruction du peak utilise H1 en dur (plus fin, capture les wicks H1) alors que `_get_atr` utilise le timeframe du symbole (H4 XAUUSD). **Intentionnel et documenté** : peak H1 légèrement plus haut = protection renforcée (SL un peu plus loin), ATR H4 reste la référence pour les multiplicateurs.
> - **Tests** : +1 (SELL force BE) → **1215 passed, 33 skipped**. Robot inchangé (PID 23848, pas de redémarrage — le fix SELL est défensif, aucun impact live en BUY-only). Le nouveau code sera chargé au prochain redémarrage naturel.

> **Mise à jour 19 Août 2026 (23:55)** : 🔧🔧 **TRANCHÉ RECOMMANDATIONS DIFFÉRÉES + SÉCURISATION XAUUSD (décision utilisateur « tranche maintenant »)** —
> - **Contexte** : le conseil d'agents (07:55) avait laissé 4 recommandations différées + la demande de sécuriser les gains XAUUSD. Collecte data-driven avant décision : GR state **82/100** (WR 57.3%, PF 1.733, PnL +291.85) — SOLUSD n'est PLUS un perdant (15 trades, PF 5.25, +32.65$, les données 0/5 du matin étaient périmées) ; USDCAD confirme le perdant structurel (3 trades, PF 0.034, −4.79$).
> - **Décision 1 — SOLUSD** (`config/default.yaml`) : `max_lot` **conservé à 0.06** (contre la reco du matin 0.06→0.03 — SOLUSD est désormais un contributeur positif), `min_score` **0.60→0.65** (WR 46.7% faible → filtrer les signaux faibles).
> - **Décision 2 — USDCAD** (`config/default.yaml`) : `max_lot` **0.06→0.04** (perdant structurel confirmé PF 0.034 — même pattern AUDUSD 18/08).
> - **Décision 3 — Purge adapted_params périmés** (`runtime/calibration_state.json`) : les 5 params (GBPJPY 2.5625, NZDUSD 2.6176, US30.cash 2.6667, USDJPY 2.75, USOIL.cash 2.0, tous risk_mult 1.0) supprimés — ils dataient de la phase pré-GR et seront ré-appris proprement quand OnlineLearner s'activera (≥20 trades valides).
> - **Décision 4 — Suppression de la double porte de score** (`engine_simple/signal_pipeline.py::_phase6_strategy_selector`) : avant, la phase 6 rejetait les signaux score < min_score par régime (0.55-0.60) PUIS le signal_validator ré-appliquait son plancher (MIN_SIGNAL_SCORE 0.60 + cfg_score par symbole 0.65 XAUUSD/SOLUSD + dyn_score WR) → deux portes redondantes (la phase 6 ne bloquait jamais un signal que le validator n'aurait pas bloqué de toute façon, 352 rejets "strat_sel" en 6h = bruit). **Fix** : la phase 6 conserve UNIQUEMENT le filtre de risque HIGH_VOL/ADX>35 (le validator n'a pas ce garde-fou d'exécution) ; `should_trade` de strategy_selector reste intact (tests dédiés). Le signal_validator est désormais LA seule porte de score.
> - **Décision 5 — Sécurisation XAUUSD** (WR GR 28.6% mais gros winners : +164/+165/+178 vs losers −112/−133/−142 ; time_stop −104.1 en 6.17h et −28.7 en 3.6h) : **2 nouveaux champs per-symbole** (schema.py `extra="allow"` → `partial_tp_progress` + `time_stop_max_hours_profit`) :
>   - `partial_tp_progress: 0.55` XAUUSD (défaut 0.65) → verrouille 75% du volume PLUS tôt (55% du chemin vers TP au lieu de 65%) ;
>   - `time_stop_max_hours_profit: 8.0` XAUUSD (défaut 12h) → time-stop profit réduit à 8h (sécurise les gains avant retracement, `trailer.py::_check_time_stop`).
>   - Autres symboles : inchangés (défauts 0.65 / 12h conservés).
> - **Tests** : +4 (partial TP XAUUSD 0.55 déclenche à 0.60 ×1, EURUSD garde 0.65 ×1, time-stop XAUUSD 8h ferme à 9h ×1, EURUSD garde 12h ×1) + 1 test config USDCAD 0.04 adapté + 1 test offset serveur tolérance ±1s (micro-latence mock ~2ms) → **1214 passed, 33 skipped**.
> - **Robot redémarré** : PID 23848 (avant 16972), watchdog 16952. SERVER_OFFSET +2.98h mesuré au démarrage. Balance 199 861, 6 positions récupérées (EURUSD ×2, XAUUSD ×2, BTCUSD ×2), Fl +383.

> **Mise à jour 19 Août 2026 (07:55)** : 🔧🔧 **CONSEIL D'AGENTS (10 experts) — 2 bugs critiques corrigés : time-stop offset serveur +3h + kill-switch flags** —
> - **Contexte** : sur directive « lance tous les skill » + « lance tous les agent », le Trading Intelligence Council complet a audité le robot (9 rapports + verdicts). Synthèse : 7 pertes consécutives (circuit breaker TRIP 07:17, lot ×0.25, HARD STOP à 10), GR 49/100 WR 53.1% PF 1.204 (échantillon non significatif, p=0.78), time_stop = fuite n°1 (9 trades 0% WR, −69.93$ = 18% des pertes brutes), BTCUSD seul edge significatif (p=0.02). Le council a identifié 2 bugs réels à corriger.
> - **Fix 1 — BUG TIME-STOP OFFSET SERVEUR ~3h** (`engine_simple/ftmo_protector.py`) : `pos.time` (API MT5) est en TEMPS SERVEUR (FTMO-Demo décalé de +3h vs UTC local). `_reconcile_positions` et `check_invariants` convertissaient via `datetime.utcfromtimestamp(raw)` SANS correction → `elapsed = utcnow() - open_time` sous-estimé de 3h → les time-stops fermaient **3h trop tard** (limite 4h réelle → ~7h, 12h → ~15h). Impact : positions perdantes tenues trop longtemps, profits pas sécurisés à temps. **Fix** : `_measure_server_offset()` (mesure via tick EURUSD, pattern de check_price_staleness — `account_info()` n'a pas `.time` dans l'API Python) → `_server_offset_s` soustrait à l'ingestion dans `_reconcile_positions` + `check_invariants`. Vérifié en live : `[SERVER_OFFSET] temps serveur MT5 décalé de +3.00h vs UTC`. Le fix du 10/08 (price-dedup `0 < age < 120`) reste intact.
> - **Fix 2 — GAP KILL-SWITCH** (`scripts/process_watchdog.py::is_graceful_stop_requested`) : le watchdog externe ne respectait QUE `robot.stop.flag`. Le kill-switch et ai-manager écrivent `stop_for_day.flag` puis tuent le robot → le watchdog **ressuscitait le robot ~2.5 min après un arrêt d'urgence** (DD>10%, daily loss>1.8%), annulant la décision de stop. **Fix** : `is_graceful_stop_requested` retourne True si `robot.stop.flag` OU `stop_for_day.flag` existe. `robot.halt.flag` reste géré séparément (arrêt opérateur, pas un stop gracieux).
> - **Tests** : **1210 passed, 33 skipped** (+7 : offset serveur ×4 via tick, watchdog flags ×4). Robot redémarré **PID 16972** + watchdog **26964** (nouveau code). Position SOLUSD reprise (lot 0.06, cooldown OK).
> - **Recommandations non appliquées (décision différée)** : SOLUSD max_lot 0.06→0.03 + min_score 0.65 (risk-compliance : 0/5 GR, PF 0.0) ; USDCAD max_lot 0.06→0.04 (PF GR 0.013) ; purge des 5 adapted_params périmés (NZDUSD/US30/USDJPY/GBPJPY/USOIL) ; suppression double porte de score (phase 6 selecteur 0.60 + validator MIN_SIGNAL_SCORE 0.65). À trancher à la prochaine session.

> **Mise à jour 19 Août 2026 (02:20)** : 🔍 **AUDIT GLOBAL ROBOT MANAGER — 2 anomalies de risque corrigées (GBPUSD/NZDUSD max_lot)** —
> - **Contexte** : audit complet sous directive « amélioration globale » (robot PID 13252 sain : aucune ERROR/CRITICAL, RAM 96MB, watchdog OK). Analyse trades_log GR (49/100, WR 53.1%, PF 1.204, PnL +78.7) + rejets + calibration OL.
> - **Fix 1 — GBPUSD max_lot 0.17→0.06** (`config/default.yaml`) : 0.17 = le plus gros lot du portefeuille GR (3.4× les autres forex 0.05-0.06). Origine : héritage config PIC + augmentations 10% (17/08). GBPUSD a produit le time_stop à −21.60$ (vol 0.15). Or le forex est structurellement perdant après coûts (backtest 19/08, PF 0.78 combo) → pas de scaling supérieur. Aligné sur EURUSD/USDJPY/USDCAD (0.06).
> - **Fix 2 — NZDUSD max_lot 0.11→0.05** (`config/default.yaml`) : 0.11 = 2e plus gros lot alors qu'il a le **pire PF GR (0.161, −4.70$ sur 2 trades)**. Même pattern que le fix AUDUSD du 18/08 (0.13→0.05) : un perdant structurel ne doit pas scaler. Aligné sur AUDUSD (0.05).
> - **JP225 écarté — pas un bug** : les 66 rejets « Spread too high » (limit=12.00, spread réel 13.54) à 01:30 UTC = marché TSE fermé/pré-ouverture → spread élargi. Comportement défensif correct, pas de modification.
> - **Autres vérifications saines** : OnlineLearner fallback propre (base_thresh depuis strategy.py, pas de bug) ; min_score dynamique déjà actif (monte quand WR baisse, ex AUDUSD +0.10) ; filtre extension AUDUSD actif (119 rejets) ; time_stop cooldown 300s OK ; `_check_spread` = max points + ratio ATR 15% par défaut.
> - **Impact attendu** : les 2 time_stop perdants GBPUSD/AUDUSD (vol 0.15/0.12) seraient ~2-3× moins coûteux avec les nouveaux plafonds → moins d'hémorragie sur les symboles forex faibles, collecte GR plus propre.
> - **Tests** : **1203 passed, 33 skipped** (aucune régression). Hot-reload config ~15 min → propagation automatique au pipeline (dict partagé symbol_limits).
> - **État GR** : 49/100 trades, WR 53.1%, PF 1.204 — reste EN_COURS (échantillon insuffisant), 4 pertes >$20 depuis le 13/08 (XAUUSD ×2 SL, GBPUSD/XAUUSD time_stop).

> **Mise à jour 19 Août 2026 (01:30)** : 🧪 **BACKTEST OPTIMISATIONS — SL 3.0× + PTP 75% + session LDN-NY appliqués au forex** (veille externe → validation → config) —
> - **Processus** : consultation de sources externes (FTMO officiel, études momentum/trailing/session — Claude/ChatGPT non accessibles via API, remplacés par recherche web) → **backtest paramétrique** `scripts/backtest_optimizations.py` sur 7 paires forex H1 2012-2026 avec coûts réels (spread+slippage+commission), moteur replica prod `backtest_full.py` (monkey-patch propre, rapport `runtime/backtest_optimizations.json`).
> - **Résultats clés** (8971 trades baseline) : **SL 3.0×** → WR 57.4→67.1%, PF 0.73→0.78, pertes −13% (TP scale pour RR≈2.5 constant) ; **PTP 75%** → léger gain (−1.6% pertes, DD −0.2pt) ; **Session LDN-NY [13-17h GMT]** → DD 30.3→9.0% (−70%), pertes −74% mais seulement 26% des trades. **Combo complet** : WR 64.3%, PF 0.78, DD 10.2%, pertes −76%. ⚠️ PF reste < 1.0 → le forex est **structurellement perdant après coûts** (confirme AGENTS.md EURUSD PF 0.74), l'optimisation réduit l'hémorragie sans créer d'edge.
> - **Décision utilisateur** : « exécute la meilleure solution professionnelle » → **combo complet appliqué**.
> - **Fix 1 — SL/TP forex élargis** (`engine_simple/strategy.py::SYMBOL_CONFIG` + `config/default.yaml::symbol_limits`) : les 7 paires forex majeures (EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, AUDUSD, NZDUSD) passent de SL 2.0/TP 5.0 (trending) et 1.5/4.0 (ranging) à **SL 3.0/TP 7.5** et **2.25/6.0** (RR 2.5/2.67 conservé, min_rr 1.5 respecté). Source de vérité = strategy.py (résolu via `SymbolParamManager`), YAML pour cohérence/hot-reload.
> - **Fix 2 — partial TP 50%→75%** (`engine_simple/trailer.py::_check_partial_tp`) : `close_vol = volume * 0.75` au lieu de `/2`. Quantisation lot_step conservée (0.10→0.075→0.08). Aligné sur la recherche (fraction 75% optimale, arXiv 2604.27150).
> - **Fix 3 — session LDN-NY** : `preferred_hours` [13,14,15,16,17] GMT sur les 7 paires forex (strategy.py + YAML). Filtre strict dans `ftmo_protector._check_session` (signal ≠ None). Indices (US100/US30/JP225), crypto (BTCUSD/SOLUSD) et XAUUSD restent 24/7. ⚠️ Trade-off : −74% de trades forex → collecte GR plus lente mais trades de meilleure qualité.
> - **Tests** : +10 (config SL/TP ×6, session blocage/autorisation ×2, partial 75% ×1, RR conservé ×1) + 8 tests existants adaptés (heures mockées 11h/12h→14h UTC pour EURUSD/NZDUSD) → **1203 passed, 33 skipped**.
> - **Robot redémarré** : PID **13252** (avant 10372), watchdog 18700. Filtre extension AUDUSD actif en live (signaux SELL rejetés à 1.87-2.09×ATR > 1.5×ATR). Balance 199 662, DD 0.3%.
> - **Backups** : `config/backup_strategy_20260819_*_avant_opt_backtest.yaml` (strategy.py), `config/default.yaml` versionné par git.

> **Mise à jour 18 Août 2026 (00:30)** : 🔧 **SOLUTION PRO AUDUSD — max_lot réduit + filtre anti-fin-de-tendance + alerte PF symbole** (commit `5214d9ff6`) —
> - **Contexte** : AUDUSD est le pire symbole de la collecte GR (8 trades : WR 37,5 %, PF 0,34, PnL −38,34 $).
>   Asymétrie R : winners +0,58R / losers −0,64R → il faut WR ≥ 52,5 % juste pour le breakeven. Pattern
>   identifié : les 4 losers du 17/08 entrés à 0,711-0,712 (achat en fin de tendance après montée depuis
>   0,708), les 3 winners entrés à ~0,708 (extension négative = bon pullback). La tendance H1 AUDUSD était
>   ascendante mais le prix était DÉJÀ étendu au-delà de l'EMA20 → momentum épuisé.
> - **Fix 1 — max_lot AUDUSD 0.13→0.05** (`config/default.yaml`) : un perdant structurel ne doit pas scaler
>   au même niveau que les gagnants. Aligné sur les autres paires à risque (EURUSD/USDJPY/USDCAD/XAUUSD
>   0.05-0.06). Backups : `config/backup_*_20260818_000656_avant_filtre_extension.yaml`.
> - **Fix 2 — nouveau filtre phase 1e anti-fin-de-tendance** (`signal_pipeline.py::_phase1e_extension_filter`) :
>   rejette les signaux BUY dont le prix est étendu de plus de `max_extension_atr` ×ATR au-dessus de l'EMA20
>   (achat en fin de tendance = momentum épuisé). Configurable par symbole (nouveau champ `max_extension_atr`
>   dans `config/schema.py::SymbolLimit`, défaut None = filtre inactif), activé AUDUSD à **1.5×ATR**.
>   Validation empirique sur les 7 trades GR AUDUSD : **4/4 losers rejetés** (extensions 1.67-3.09×ATR),
>   **3/3 winners conservés** (extension négative). Fail-open par défaut (si pas de rates → pas de rejet).
>   Rejet compté via `reject_counter` (clé `extension`). Log `[EXTENSION]`.
> - **Fix 3 — alerte SYMBOL_PF_LOW** (`performance_monitor.py`) : PF < 0.7 sur ≥ 15 trades par symbole →
>   alerte WARNING (perdant structurel probable, détection précoce avant qu'il ne pèse sur la collecte GR).
>   Seuils `pf_symbol_below=0.7`, `pf_symbol_min_trades=15`.
> - **Fix 4 — hygiène** : `check_gr_symbols.py::GR_SYMBOLS` porté à **13 symboles** (aligné sur
>   `golden_rule.py`, la source de vérité — il restait bloqué sur les 5 du repositionnement) ; rotation du
>   log `watchdog_external.log` (> 10 MB → `.log.1`) dans `trading_engine.py`.
> - **Tests** : +10 (6 filtre phase 1e, 3 alerte PF, 1 non-déclenchement) → **1193 passed, 33 skipped**.
> - **Robot redémarré** : PID 10372 (avant : 21956), watchdog 8188 (code fixé, log dédié `watchdog_8188.log`),
>   rotation vérifiée (`watchdog_external.log` 0 MB + `.log.1` 20,6 MB). La config est chargée au démarrage ;
>   le hot-reload (~15 min) propage les changements de symbol_limits au pipeline (même dict partagé).
> - **État GR** : 31/100 trades, WR 48,4 %, PF 1,993 — la cible Règle d'Or (WR ≥ 60 %, PF ≥ 1,1) reste
>   inchangée (contrat utilisateur), la solution pro surveille honnêtement et réduit l'exposition du pire
>   contributeur au lieu de déplacer la cible.

> **Mise à jour 18 Août 2026 (00:05)** : 🔧🔧🔧 **TRIO DE FIXES LOG ANALYST — BE sur peak, fermeture pré-weekend, agrégation closes partielles** —
> - **Contexte** : l'audit de la session précédente (ticket AUDUSD 519685971) a révélé 3 bugs réels
>   faussant les stats GR et exposant le capital le week-end.
> - **Fix 1 — BE progressif sur PEAK** (`engine_simple/trailer.py::_check_progressive_be`) : le calcul de
>   `profit_atr` utilisait `price_current` au lieu du **peak** — même bug que le partial TP avant son fix
>   du 30 Juillet. Un pic à 1.06×ATR puis retracement sous 1.00×ATR faisait RATER le BE (SL figé à
>   l'entrée, exposé au week-end — logs TRAIL figés 15/08 01:15 → 16/08 23:08). Fix : `profit_price =
>   max/min(trailing_peaks[ticket], price_current)` (même pattern que `_check_partial_tp`).
> - **Fix 2 — fermeture pré-weekend** (`trailer.py::_is_weekend_close_window` + `_check_time_stop`) : le
>   time-stop échouait en 10018 (marché fermé) pendant le week-end → T3 AUDUSD exposé 52h. Pour les
>   symboles `weekend_trading=false` (config `symbol_limits`), `max_hours` est réduit à
>   `WEEKEND_CLOSE_MAX_HOURS` (défaut 2h) pendant la fenêtre du vendredi (≥ `WEEKEND_CLOSE_HOUR_UTC`,
>   défaut 16h UTC) → fermeture AVANT la clôture. Crypto 24/7 (BTCUSD/SOLUSD, `weekend_trading=true`)
>   non affectés.
> - **Fix 3 — agrégation des closes partielles** (`engine_simple/position_tracker.py::_find_closing_deal`) :
>   un partial TP génère 2-3 deals OUT sur le MÊME position_id → avant, seul le PREMIER deal profit≠0
>   était compté (T4 AUDUSD : 3 closes, 1 seule comptée → stats GR fausses). Désormais `_is_out_deal`
>   (filtre entry==1, exclut swaps/rollovers entry=0/2, tolérant aux mocks) + `_aggregate_closing_deals`
>   (somme profits+volumes, attributs du dernier deal).
> - **Tests** : +8 (BE peak après retracement ×2, weekend window ×2, agrégation ×2, non-close deals ×2)
>   → **1183 passed, 33 skipped**. Robot toujours en live (PID 23604 + watchdog 22004).

> **Mise à jour 17 Août 2026 (20:40)** : 🔧 **FIX BE PROGRESSIF — montée PLUS rapide du SL + doc nuance par symbole** —
> - **Problème** : le SL restait FIXE à entry+0.15×ATR entre 1.30×ATR et le lock N1 → zone morte
>   pouvant rendre ~2×ATR de profit (ex: BTCUSD à 2.27×ATR avait encore SL=entry+0.15×ATR).
> - **Fix** : paliers `BE_PROGRESSIVE_LEVELS` (trailer.py) tous les 0.30×ATR → SL +0.15×ATR par palier
>   (1.60→0.30, 1.90→0.45, 2.20→0.60, 2.50→0.75×ATR). **Uniforme pour tous les symboles** sauf
>   `NO_TRAILING_SYMBOLS` {US500.cash, US100.cash, JP225.cash} (Solution A). Raccord au trailing N1
>   automatique via `sl_improves` (le trailing domine dès son lock, toujours > entry+0.75×ATR).
> - **Tests** : +2 (paliers + montée fonctionnelle) → **1177 passed, 33 skipped**. Robot redémarré pour
>   activation en live (le code en cours d'exécution avant ce fix ne l'avait pas).

> **Mise à jour 17 Août 2026 (19:00)** : 🔧🔧 **FIX BUG RACINE WATCHDOG (2nd) — boucle TURBO découverte + test résurrection réel réussi** —
> - **Découverte en test réel (18:45)** : kill du robot 14708 → son watchdog fixé 22112 ne le ressuscite pas
>   et son log dédié explose (14 MB en 2 min, loop_count=356935, CPU 190s). Cause : **`_next_check` était
>   défini UNE SEULE fois avant la boucle → après la 1ère attente de 30s il restait dans le passé → la boucle
>   interne `_remaining ≤ 0` cassait immédiatement → boucle TURBO (milliers de checks/s)**. Ce bug existait
>   DÈS AVANT le fix 18:35 (vérifié dans HEAD~1) — c'est LUI la vraie cause racine du watchdog inopérant
>   observé depuis des semaines : le watchdog vérifiait en continu mais la détection process (faussée par
>   PID reuse) + heartbeat stale (jamais atteint car la boucle ne se reposait pas) ne déclenchaient jamais.
> - **Fix 2nd (commit `ab…`)**: `_next_check = time.monotonic() + check_interval` réinitialisé à la FIN de
>   chaque itération de boucle → le watchdog attend réellement 30s entre les checks.
> - **Test résurrection RÉEL réussi (18:52)** : watchdog fixé 11060 (timeout 60s) surveille un fake robot
>   → fake meurt → 1er check détecte (`get_process_status=False`) → `attempt_restart` → **spawn main.py
>   (PID 13316) → handoff → robot 13316 opérationnel (4 positions récupérées) + watchdog fixé 12280**.
> - **Race connue (non bloquante)** : au démarrage le robot tue tous les watchdogs orphelins
>   (`_kill_orphan_watchdogs`) Y COMPRIS le watchdog qui vient de le spawner → les logs "CRITICAL DEAD /
>   Spawned" du spawner sont perdus (kill entre spawn et flush). La résurrection fonctionne malgré tout.
> - **État actuel (19:00)** : robot **PID 13316** actif (4 positions), watchdog **12280** (code fixé, log
>   dédié `runtime/watchdog_12280.log`, création capturée). Tests : **1175 passed, 33 skipped**.

> **Mise à jour 17 Août 2026 (18:35)** : 🔧 **FIX WATCHDOG — résurrection fiable + logs durables** —
> - **Découverte (18:07)** : le watchdog 22048 n'a PAS relancé le robot 3060 tué à 18:07:49. Pendant 10h
>   (07:59→18:07) le robot tournait normalement, mais au kill le watchdog est resté muet : aucun "CRITICAL
>   DEAD" dans `watchdog_external.log` (dernier write 07:59:19), aucun update de `watchdog_restarts.txt`.
>   Le robot est resté down ~4 min jusqu'à l'apparition du PID 6756 (18:12:07, origine indéterminée).
> - **Cause racine 1 — PID reuse** : après le kill, `OpenProcess(3060)` retournait VIVANT sur un PID
>   **recyclé** par Windows → `get_process_status` croyait le robot vivant → pas de résurrection.
> - **Cause racine 2 — logs perdus** : le watchdog écrivait dans le handle stderr hérité du robot parent
>   (Popen stdout/stderr=_wd_err). Quand le parent meurt, les écritures partent dans le vide → impossible
>   de diagnostiquer. Le log `watchdog_external.log` n'a plus rien capturé après 07:59:19.
> - **Fix appliqué** (`scripts/process_watchdog.py`) :
>   1. **Anti-PID-reuse** : `_get_process_creation_time()` capture le FILETIME du process cible au
>      démarrage ; `get_process_status()` compare via `GetProcessTimes` à chaque check → PID recyclé = mort.
>   2. **Log dédié par watchdog** : chaque watchdog ouvre `runtime/watchdog_<pid>.log` (append, en plus de
>      stderr) → les "CRITICAL DEAD"/"Spawned" survivent à la mort du parent. Diagnostic possible.
>   3. **Log périodique ALIVE** : toutes les 5 itérations (~2.5 min), preuve que la boucle tourne → un gel
>      du watchdog devient visible en 2.5 min (au lieu de 10h).
> - **Validation** : test isolation (fake robot mort → watchdog détecte 3 stalls heartbeat → "CRITICAL" →
>   "Spawned new main.py" → handoff), log dédié écrit. Tests : **1175 passed, 33 skipped**.
> - **État actuel** : robot PID 6756 actif (4 positions : BTCUSD/US100/XAUUSD), watchdog 21236 le surveille.
>   Le watchdog actuel utilise DÉJÀ le code fixé ? Non — le 21236 a été spawné AVANT le fix ; il faudra le
>   redémarrer au prochain redémarrage du robot pour bénéficier du nouveau code (le fix sera actif au
>   prochain spawn naturel).

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

### Breakeven progressif (31 Juillet 2026 + FIX 17 Août 2026)
Séquence de sécurisation des profits AVANT que le trailing N1 ne s'active :
```
profit > 1.00×ATR → SL = entry (breakeven pur, zéro perte garantie)
profit > 1.30×ATR → SL = entry ± 0.15×ATR (petit gain garanti)
profit > 1.60×ATR → SL = entry ± 0.30×ATR
profit > 1.90×ATR → SL = entry ± 0.45×ATR
profit > 2.20×ATR → SL = entry ± 0.60×ATR
profit > 2.50×ATR → SL = entry ± 0.75×ATR   (dernier palier — raccord trailing)
```
> 🔧 FIX 31 Juillet 2026 (Quant Auditor) : les seuils précédents (0.80/0.50×ATR) coupaient
> 62% des gagnants à <0.5R avant même le lock N1. En repoussant à 1.00/1.30×ATR, les trades
> faibles ont une chance d'atteindre la zone N1 au lieu d'être stoppés net sur le bruit.
> 🔧 FIX 17 Août 2026 (montée PLUS rapide) : avant, le SL restait FIXE à entry+0.15×ATR entre
> 1.30×ATR et le lock N1 (zone morte — un BTCUSD à 2.27×ATR de profit avait encore
> SL=entry+0.15×ATR). Paliers `BE_PROGRESSIVE_LEVELS` (trailer.py, module-level) ajoutés :
> +0.15×ATR de buffer tous les 0.30×ATR de profit. La montée est désormais quasi-linéaire.

> ⚠️ **Nuance importante — application par symbole** : les paliers `BE_PROGRESSIVE_LEVELS`
> sont **UNIFORMES pour tous les symboles** (aucun seuil par symbole). Deux exceptions :
> 1. **`NO_TRAILING_SYMBOLS` = {US500.cash, US100.cash, JP225.cash}** → BE **désactivé**
>    (Solution A, indices optimisés FTMO sans trailing, garde `is_trailing_disabled`).
> 2. **Raccord au trailing N1 varie par symbole** : le dernier palier BE (2.50×ATR) dépasse
>    le lock N1 de certains symboles (fallback=1.80×ATR, XAUUSD TREND=1.50×ATR). Pas de
>    conflit : la garde `sl_improves` garde le **meilleur** SL, et le trailing N1
>    (peak−trail_dist) est toujours ≥ entry+0.75×ATR dès que son lock est atteint
>    (ex: 1.80−1.00=0.80×ATR > 0.75×ATR) → le trailing domine naturellement.

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
