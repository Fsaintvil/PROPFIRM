---
name: monitoring-health
description: Surveillance 24/7 du robot — watchdog process_watchdog.py (anti-PID-reuse, log dédié), performance monitor, alertes WR/PF/DD, logs, PID lock, rapport FTMO. Utilise performance_monitor.py et daily_report.py.
---

# Monitoring & Health Skill

## Description
Expert en surveillance 24/7 du robot : logs, métriques, alertes, watchdog, performance monitoring, et détection d'anomalies.

## Quand utiliser
- Pour faire un bilan de santé du robot
- Pour analyser les logs et détecter des patterns d'erreur
- Pour vérifier le bon fonctionnement du watchdog
- Pour interpréter le rapport FTMO

## Architecture

### Watchdog (scripts/process_watchdog.py — FIX 17 Août 2026)
```
Boucle 30s (check_interval) :
  1. Vérifier le process cible (anti-PID-reuse : compare le FILETIME de création
     via GetProcessTimes → un PID recyclé par Windows = mort)
  2. Vérifier le heartbeat (stale > timeout → mort)
  3. Vérifier le PID lock (runtime/robot.pid)
  4. Si mort → CRITICAL DEAD + attempt_restart (spawn main.py → handoff)
  5. Log périodique ALIVE toutes les 5 itérations (~2.5 min)
```

**Points clés du watchdog fixé :**
- **Anti-PID-reuse** : `_get_process_creation_time()` capture le FILETIME au démarrage ; `get_process_status()` compare à chaque check → un PID recyclé n'est PAS considéré vivant
- **Log dédié par watchdog** : `runtime/watchdog_<pid>.log` (append, en plus de stderr) — les "CRITICAL DEAD"/"Spawned" survivent à la mort du parent (avant, les écritures partaient dans le vide car stderr hérité du robot parent)
- **Boucle fixée** : `_next_check = time.monotonic() + check_interval` réinitialisé à la FIN de chaque itération (avant, la boucle TURBO faisait des milliers de checks/s et le watchdog n'attendait jamais)
- **Race connue (non bloquante)** : au démarrage le robot tue les watchdogs orphelins (`_kill_orphan_watchdogs`) Y COMPRIS celui qui vient de le spawner → les logs "CRITICAL DEAD/Spawned" du spawner peuvent être perdus
- **Résurrection RÉELLE testée (17/08 18:52)** : watchdog 11060 → fake robot mort → détecté → spawn main.py (PID 13316) → handoff → robot opérationnel

**Commandes :**
```powershell
.\scripts\robot.ps1            # Démarre robot + watchdog
.\scripts\robot.ps1 -Status    # Voir l'état (robot + FTMO report)
.\scripts\robot.ps1 -Logs      # Voir les logs
.\scripts\robot.ps1 -Stop      # Arrêter tout
.\scripts\robot.ps1 -LaunchMT5 # Lancer MT5
```

> ⚠️ **Vérifier le watchdog actif** : `Get-Process python -ErrorAction SilentlyContinue` → 2 processus attendus (robot + watchdog). Le log dédié `runtime/watchdog_<pid>.log` contient la preuve d'activité. Si un seul PID python tourne, le watchdog est mort → redémarrer via `robot.ps1`.

### Performance Monitor (intégré dans le robot)
**Métriques trackées :**
| Fenêtre | Usage |
|---------|-------|
| 20 trades | Court terme, détection rapide |
| 50 trades | Short term, tendance récente |
| 100 trades | Medium term, fiabilité |
| 200 trades | Long terme, vue d'ensemble |

**Alertes :**
| Seuil | Niveau | Action |
|-------|--------|--------|
| WR baisse > 15% sur 50 trades | ⚠️ | Vérifier seuils MOM20x3 |
| PF < 1.0 sur 50/100 trades | 🔴 | Stopper, analyser pertes |
| PF < 1.2 sur 50/100 trades | ⚠️ | Surveiller tendance |
| Symbole: PnL < -$50 et WR < 40% | ⚠️ | Désactiver ou réduire |
| Challenge J+15 < 30% target | ⚠️ | Augmenter risque symboles forts |

### Logs
**Fichiers :**
- `logs/simple_robot.log` — log principal (rotation automatique?)
- `runtime/ftmo_report.json` — métriques challenge en temps réel
- `runtime/performance_history.json` — historique performance (365 jours)
- `runtime/robot_state.json` — état persistant (balance, trades, cooldown)

**Patterns d'erreur critiques :**
```
ERROR - [MOM20x3] → problème de génération de signal
ERROR - Exception in strategy → stack trace complète
CRITICAL - max_drawdown → DD > 10%, arrêt immédiat
ERROR - Order rejected → ordre MT5 refusé
ERROR - Connection lost → MT5 déconnecté
```

### Rapport journalier
```powershell
.\scripts\daily_report.ps1               # Rapport complet
.\scripts\daily_report.ps1 -Status       # Statut rapide
.\scripts\daily_report.ps1 -Watch        # Monitoring continu
python scripts/daily_report.py           # Version Python
```

## Vérification rapide (5s)
```powershell
# 1. Process en vie ?
Get-Process -Name pythonw -ErrorAction SilentlyContinue

# 2. PID lock ok ?
Get-Content -Path runtime/robot.pid -ErrorAction SilentlyContinue

# 3. Log récent ?
Get-Item -Path logs/simple_robot.log | Select-Object LastWriteTime

# 4. Dernières erreurs ?
Get-Content -Path logs/simple_robot.log -Tail 20 | Select-String -Pattern "ERROR|CRITICAL"

# 5. Métriques challenge ?
Get-Content -Path runtime/ftmo_report.json -Raw | ConvertFrom-Json | Select-Object balance, drawdown, trades_today

# 6. Council verdict ?
if (Test-Path runtime/council/latest_verdict.json) { 
    Get-Content runtime/council/latest_verdict.json -Raw | ConvertFrom-Json | Select-Object -ExpandProperty verdict
}

# 7. Mémoire ?
python -c "import psutil; print(f'RAM: {psutil.Process().memory_info().rss/1024/1024:.0f} MB')"
```

## Alertes configurables (depuis la session Juin 2026)

| Alarme | Déclencheur | Action |
|--------|-------------|--------|
| WR Drop | WR baisse > 15% sur 50 trades | Vérifier seuils MOM20x3 |
| PF Critical | PF < 1.0 sur 50/100 trades | Stopper, analyser pertes |
| PF Warning | PF < 1.2 sur 50/100 trades | Surveiller tendance |
| Symbole Weak | PnL < -$50 ET WR < 40% | Désactiver ou réduire risk |
| Challenge Lag | J+15 < 30% target | Augmenter risque symboles forts |
| **Memory High** | RAM > 1.5 GB | Warning logger (toutes les 15 min) |
| **Memory Critical** | RAM > 2.0 GB | Alerte logger (toutes les 15 min) |
| **Council VETO** | risk-compliance pose veto | Stop immédiat des trades |
| **Council CRITICAL** | Un agent signale CRITICAL | Investigation immédiate |

## Pièges connus
- Le PID lock peut rester orphelin si le robot crashe sans cleanup → le watchdog nettoie automatiquement
- **PID reuse Windows** : après un kill, `OpenProcess(pid)` peut retourner VIVANT sur un PID recyclé → le watchdog compare le FILETIME de création pour détecter le recyclage
- **Logs watchdog** : si `watchdog_external.log` n'a plus d'écriture depuis longtemps, le watchdog écrit désormais aussi dans `runtime/watchdog_<pid>.log` (dédié, append) — toujours vérifier les DEUX
- **Boucle TURBO** : si le log watchdog dédié explose (>10 MB en minutes) avec loop_count élevé → `_next_check` non réinitialisé (bug fixé 17/08, vérifier que le code actuel le fait bien)
- `performance_history.json` a été reset en Juin 2026 (suppression des 17K backtest signals) — l'état runtime est maintenant propre et ne contient que des trades réels
- Le log `watchdog_external.log` est roté (> 10 MB → `.log.1`) dans `trading_engine.py` (FIX 18 Août)
- Les logs principaux ne sont pas rotés automatiquement — à configurer si le robot tourne > 30 jours
- Un processus `pythonw.exe` zombie peut bloquer le redémarrage → `taskkill /F /IM pythonw.exe`
- Ne pas modifier les fichiers de runtime (`state.json`, `performance_history.json`) manuellement pendant que le robot tourne — risque de corruption
- **Fuseau horaire** : les logs `simple_robot.log` utilisent l'heure locale (UTC+3), les timestamps `audit_trail` en UTC → un écart de +3h est NORMAL

## Fichiers clés
- `scripts/process_watchdog.py` — watchdog continu (anti-PID-reuse, log dédié, résurrection)
- `scripts/robot.ps1` — gestion du robot (start/stop/status/logs)
- `engine_simple/performance_monitor.py` — monitoring intégré + alerte SYMBOL_PF_LOW (PF < 0.7 sur ≥ 15 trades, FIX 18 Août)
- `engine_simple/trading_engine.py` — rotation watchdog_external.log
- `main.py` — boucle 15s, logging, ftmo_report
- `logs/simple_robot.log` — log principal
- `runtime/robot.pid` — PID lock
- `runtime/golden_rule/state.json` — état Règle d'Or (13 symboles, WR ≥ 60%, PF ≥ 1.1, 100 trades)

## Tests
```powershell
cd C:\Users\saint\Documents\MT5_FTMO_IA.7 && python -m pytest tests/ -q
```

## Agents concernés
- `@system-monitor` — gardien 24/7 (logs, mémoire, processus)
- `@cio` — reçoit les rapports
- `@risk-compliance` — veto et conformité