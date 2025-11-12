Live operational rules for AI-driven automated sends
===============================================

But : définir clairement la méthode d'exécution LIVE automatisée et ses garde‑fous.

Règles opérationnelles (appliquées par `tools/live_run_controller.py`)

- Scope des symbols :
  - BTCUSD, ETHUSD, XAUUSD, USDCAD, AUDNZD, EURJPY, GBPCHF, NZDJPY, EURUSD, EURAUD, US500.cash, JP225.cash

- Mode décisionnel (IA) :
  - MTF de référence : M15 (convergence multi-timeframe doit être utilisée par la logique IA).
  - Indicateurs : combinaison d'indicateurs techniques classiques (MA/EMA, RSI, MACD, ATR) et
    d'indicateurs « institutionnels » internes (si disponibles dans les modèles du repo).
  - Données historiques : backtests et vérifications doivent couvrir 7 ans lorsque cela est pertinent.
  - Modèles : exécuter l'ensemble des modèles IA disponibles (ensemble/stacking) pour prendre la décision.

- Paramètres par défaut d'exécution :
  - Volume par ordre : 0.01 (modifiable via `AI_VOLUME`).
  - Type d'ordre : MARKET (par défaut). Les ordres pending peuvent être utilisés si la stratégie le requiert.
  - SL/TP : heuristique par défaut (SL = 1% du prix, TP = 2% du prix) — ajustable par stratégie.

- Fréquence d'envoi et fermeture automatique :
  - Envoi : un envoi (si signal) pour chaque symbol toutes les 930 secondes.
  - Auto-close : fermer automatiquement les positions ouvertes depuis 31 minutes si SL/TP non atteints.

- Garde‑fous et confirmations requises :
  - Envois réels bloqués par défaut (DRY-RUN). Pour autoriser les envois automatiques IA il faut :
    1) `control/apply_live.confirm` contenant `APPLY LIVE` (opérateur primaire),
    2) `control/apply_live.auto.confirm` contenant `APPLY LIVE AUTO` (activation IA automatique),
    3) variable d'environnement `ALLOW_MT5_SEND=1` visible par le process qui envoie.
  - Sans ces trois éléments, le contrôleur génère des ordres mais n'effectue aucun envoi (preview).

- Audit, logs et sécurité :
  - Toutes les décisions IA, ordres proposés et résultats d'envoi sont loggés dans `artifacts/live_trading/`.
  - Les backups et historiques live MT5 doivent être préservés ; ne pas modifier les logs existants.

- Processus recommandé avant passage en AUTO :
  1) Exécuter un DRY-RUN complet (vérifier décisions IA et SL/TP pour chaque symbol).  
  2) Vérifier les résultats de backtest (7 ans) et le rapport de robustesse.  
  3) Activer `control/apply_live.auto.confirm` pour test sur une petite liste de symbols (p.ex. BTCUSD, ETHUSD, XAUUSD, USDCAD, AUDNZD, EURJPY, GBPCHF, NZDJPY, EURUSD, EURAUD, US500.cash, JP225.cash).  
  4) Graduellement augmenter la liste des symbols lorsque la stabilité est confirmée.

Notes :
- `tools/live_run_controller.py` implémente une heuristique IA de démonstration (M15). Pour une mise en
  production complète, intégrer vos modèles IA (ensembles) et la logique MTF détaillée dans un module
  dédié (ex : `src/ai/decision_engine.py`) puis référencer ce module depuis le contrôleur.

## Variables d'environnement et mode d'exécution LIVE

- Variables d'environnement exploitées par le contrôleur et `start_production.py`:
  - `ALLOW_MT5_SEND=1` : autorise l'envoi réel vers MetaTrader5 (doit être présent dans le process qui envoie).
  - `AI_AUTOMATE=1` et `AI_VOLUME` : activation de l'automatisation IA et volume par défaut.
  - `LIVE_ENGINE_LIGHT_MODE=1` : mode léger d'exécution de l'engine (si pris en charge par `start_production`).
  - `CONFIRME_DEPLACEMENT=YES_I_CONFIRM` : variable d'audit supplémentaire indiquant l'accord opérateur.
  - `AUTO_APPLY`, `AUTO_DEPLOY`, `AUTO_LEARN`, `AUTO_ADAPT`, `AUTO_ENRICH` : flags optionnels propagés au process de production.

### Processus recommandé pour lancement réel (CHECKLIST)
1. Vérifier en local que `AI_AUTOMATE` est en place et que les previews IA sont correctes (DRY-RUN).
2. Créer `control/apply_live.confirm` contenant exactement `APPLY LIVE` et `control/apply_live.auto.confirm` contenant `APPLY LIVE AUTO`.
3. Charger les variables d'environnement requises (ex : via `tools/run_live_production.ps1` fourni).
4. Exécuter `tools/run_live_production.ps1 -Force` depuis PowerShell (exécuté en tant qu'administrateur si nécessaire).

### Audit et traçabilité
- Tous les envois IA sont persistés sous `artifacts/live_trading/` : fichiers JSON horodatés et un log append `ai_send_roll.log`.
- Le contrôleur crée un verrou `control/ai_sending.lock` pour éviter les envois concurrents.

---

Document mis à jour automatiquement par l'outil d'orchestration (contrôleur).
