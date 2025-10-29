# Canary Plan — Promotion prudente en production

Date générée: 2025-10-29
Auteur: assistant (préparation)

But
----
Déployer le robot en production sur un périmètre réduit (canary) pour valider le comportement réel et les opérateurs avant une promotion complète.

Objectifs
---------
- Vérifier intégration MT5 en conditions réelles sur 1–2 symboles.
- Limiter le risque (taille réduite, limites strictes, stop-loss obligatoires).
- Valider monitoring, remontées d'alerte et rollback automatique (circuit-breakers).

Périmètre recommandé
---------------------
- Symboles canary: `EURUSD`, `USDCAD` (haute liquidité, représentatifs FX). Choix alternatif: `EURUSD` + `AUDNZD`.
- Horizon: 4 heures (observation continue), extensible à 24h après approbation.
- Taille: factor_risk = 0.2 (20% des volumes nominaux) — réduit l'impact réel.

Critères d'arrêt / rollback automatique
--------------------------------------
1. Perte cumulée canary > 1% du capital de test opérateur → exécuter `tools/close_all_positions.py` et set `artifacts/EMERGENCY_STOP`.
2. Drawdown intra-canary > 0.5% en 1h → pause automatique et alerte.
3. Toute erreur bloquante du connecteur MT5 (en logs `mt5_send_errors.log`) → rollback immédiat.
4. Circuit-breaker symbolique appliqué pour le symbole (skip_reason == "circuit_open") → ne pas ouvrir de nouvelles positions sur ce symbole.

Checklist opérateur (avant lancement)
-------------------------------------
- [ ] Vérifier `artifacts/OK_GO_LIVE` présent sur l'hôte opérateur.
- [ ] Backup: `tools/deploy_live_pipeline.py` créera un backup `artifacts/backup_apply_*` automatiquement.
- [ ] Variables d'environnement: MT5 credentials (MT5_LOGIN, MT5_PWD, MT5_SERVER, MT5_ACCOUNT, MT5_PASSWORD) et `FTMO_ACCOUNT_BALANCE` si la pré-check FTMO est utilisée.
- [ ] Confirmations: être prêt à taper exactement `APPLY LIVE` au prompt.
- [ ] Confirmer que `config/canary.json` contient les paramètres souhaités.

Exécution (opérateur)
---------------------
1. Sur l'hôte opérateur, créer le marker si nécessaire :

   python tools/ok_go_live.py --on --by "OperatorName" --note "Canary run" --phrase "OK GO LIVE"

2. Lancer le canary en DRY_RUN d'abord (contrôle) :

   $env:DRY_RUN='1'; $env:ALLOW_MT5_SEND='0'; python tools/deploy_live_pipeline.py

3. Inspecter `artifacts/live_training/*.ndjson` et logs (`logs/execute_live_trades_safe_dryrun_*.json`) pour valider le plan.

4. Si tout OK, lancer réel (sur l'hôte opérateur, après avoir retiré DRY_RUN):

   $env:DRY_RUN='0'; $env:ALLOW_MT5_SEND='1'; python tools/deploy_live_pipeline.py

   Confirmer `APPLY LIVE` quand demandé.

Monitoring & reporting
----------------------
- Surveillez `tools/live_monitor.py` et `logs/monitoring_*.log`.
- Remontées d'alerte: `alerts.json` et `monitor_alerts.log`.
- Après canary, produire un rapport sommaire et décider rollback ou extension.

Post-mortem & rollback
----------------------
- Si rollback: créer `artifacts/EMERGENCY_STOP` via:

  python tools/set_emergency_stop.py --by "OperatorName" --reason "Canary rollback"

- Collectez les artefacts: `artifacts/backup_apply_*`, `artifacts/live_training/*`, `logs/*` pour l'analyse.

Annexes
-------
- Fichier de configuration du canary: `config/canary.json` (valeurs par défaut proposées).
- Helper scheduler PS1: `tools/create_canary_task.ps1` (crée une tâche planifiée Windows qui lance le canary sur l'hôte opérateur).
