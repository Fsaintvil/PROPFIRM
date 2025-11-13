Watchdog SF_IA.7
=================

But
----
Ce dossier contient le watchdog `watchdog_sf_ia7.ps1` utilisé pour surveiller et relancer le processus de production (bot). Le script inclut des protections (lockfile, PID, cooldown, notifications) et a été testé en "dry-run" avec `tools/run_production.ps1`.

Fichiers clés
-------------
- `watchdog_sf_ia7.ps1` : le watchdog principal.
- `run_production.ps1` : stub de test (dry-run) qui imprime des heartbeats.
- `dryrun_watchdog_runner.ps1` : lance le watchdog, attend, crée le fichier STOP et vérifie l'arrêt.
- `run_pssa_watchdog.ps1` : helper pour exécuter PSScriptAnalyzer et écrire `tools/pssa_watchdog_report.json`.
- `pssa_watchdog_report.json` : rapport PSScriptAnalyzer (résultats d'analyse).
- `register_watchdog_task.ps1` : (optionnel) helper pour créer une tâche planifiée Windows pour lancer le watchdog.

Exécution manuelle
------------------
Lancer le watchdog (foreground) :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\watchdog_sf_ia7.ps1
```

Lancer en dry-run (test complet) :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\dryrun_watchdog_runner.ps1
```

Arrêt contrôlé
-------------
Créer le fichier `STOP_WATCHDOG.SF_IA.7` à la racine du dépôt pour demander un arrêt propre :

```powershell
New-Item -Path .\STOP_WATCHDOG.SF_IA.7 -ItemType File -Force
```

Installer comme tâche planifiée (Task Scheduler)
-----------------------------------------------
Le moyen recommandé sous Windows est d'utiliser le Planificateur de tâches. Le script `register_watchdog_task.ps1` fourni crée une tâche qui exécute PowerShell au démarrage de l'utilisateur (ou à intervalles réguliers si demandé).

Exemple d'utilisation (exécuter en tant qu'administrateur si vous voulez "Run with highest privileges") :

```powershell
# Créer une tâche qui s'exécute au démarrage de l'utilisateur
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\register_watchdog_task.ps1 -TaskName "Watchdog_SF_IA7" -TriggerType Logon

# Créer une tâche qui s'exécute toutes les 30 minutes
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\register_watchdog_task.ps1 -TaskName "Watchdog_SF_IA7" -TriggerType DailyInterval -IntervalMinutes 30
```

Notes de sécurité et recommandations
-----------------------------------
- Le watchdog peut lancer des opérations qui affectent l'environnement (création de lockfile, lancement de processus). Vérifiez les flags d'environnement (ex. `ALLOW_MT5_SEND`) avant d'autoriser le watchdog à lancer le vrai bot.
- Le code a été analysé avec PSScriptAnalyzer ; le rapport est disponible dans `tools/pssa_watchdog_report.json`. Un dernier ajustement d'encodage BOM peut être appliqué si vous voulez « clean » l'avertissement d'encodage.
- Si vous préférez exécuter le watchdog en tant que service Windows, utilisez un wrapper (ex. NSSM) ou créez un service .NET qui exécute PowerShell. Le Planificateur de tâches est la méthode la plus portable et simple.

Support
-------
Si vous voulez que je :
- active l'installation automatique (créer la tâche par défaut), ou
- ré-encode `watchdog_sf_ia7.ps1` en UTF-8 BOM pour supprimer l'avertissement PSSA,
dites lequel et je l'exécute.
Watchdog utilities for the PROPFIRM project
==========================================

tools/watchdog_sf_ia7.ps1
-------------------------
Script PowerShell: surveillance et relance du bot SF_IA.7.

Usage (PowerShell):

```powershell
# exécuter le watchdog avec les paramètres par défaut
.\tools\watchdog_sf_ia7.ps1

# ou lancer en arrière-plan depuis pwsh
Start-Process -FilePath pwsh -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','tools\watchdog_sf_ia7.ps1' -WindowStyle Hidden
```

Notes:
- Le script crée ses artefacts dans `artifacts/live_trading`.
- Il utilise un lockfile pour empêcher plusieurs instances concurrents.
- Par défaut il envoie des notifications vers `NotificationWebhook` si fourni, sinon utilise `FallbackEmail`.
- Avant de l'exécuter en production, vérifiez `tools/run_production.ps1` existe et est compatible.

Security & safety:
- Le script écrit des PID/logs/lockfiles dans `artifacts/live_trading`.
- Testez d'abord en environnement de staging avant autoriser des relances automatiques.

Contact: équipe dev
