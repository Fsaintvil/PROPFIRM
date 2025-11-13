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
