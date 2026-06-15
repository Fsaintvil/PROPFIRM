---
name: monitoring-health
description: Surveillance 24/7 du robot — watchdog ai-manager.ps1, performance monitor, alertes WR/PF/DD, logs, PID lock, rapport FTMO. Utilise performance_monitor.py et daily_report.py.
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

### Watchdog (ai-manager.ps1)
```
Boucle 2 min :
  1. Vérifier processus pythonw.exe
  2. Vérifier PID lock (runtime/robot.pid)
  3. Vérifier logs récents (pas de figé > 5 min)
  4. Vérifier DD < 8%
  5. Redémarrer si nécessaire
```

**Commandes :**
```powershell
.\scripts\ai-manager.ps1         # Démarrer le daemon
.\scripts\ai-manager.ps1 -Status # État du watchdog
.\scripts\ai-manager.ps1 -Stop   # Arrêter le daemon
```

> **Recommandé** : Utiliser `start_robot.ps1` pour lancer le robot (démarre le robot + watchdog automatiquement).
> ```powershell
> .\scripts\start_robot.ps1       # Démarre robot + watchdog
> .\scripts\start_robot.ps1 -Status  # Voir l'état
> .\scripts\start_robot.ps1 -Stop    # Arrêter tout
> ```

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
| **Council VETO** | risk-marshal pose veto | Stop immédiat des trades |
| **Council CRITICAL** | Un agent signale CRITICAL | Investigation immédiate |

## Pièges connus
- Le PID lock peut rester orphelin si le robot crashe sans cleanup → `ai-manager.ps1` nettoie automatiquement
- `performance_history.json` a été reset en Juin 2026 (suppression des 17K backtest signals) — l'état runtime est maintenant propre et ne contient que des trades réels
- Les logs ne sont pas rotés automatiquement — à configurer si le robot tourne > 30 jours
- Un processus `pythonw.exe` zombie peut bloquer le redémarrage → `taskkill /F /IM pythonw.exe`
- Ne pas modifier les fichiers de runtime (`state.json`, `performance_history.json`) manuellement pendant que le robot tourne — risque de corruption

## Fichiers clés
- `scripts/ai-manager.ps1` — watchdog continu
- `scripts/start_robot.ps1` — démarrage recommandé du robot
- `engine_simple/performance_monitor.py` — monitoring intégré
- `main.py` — boucle 15s, logging, ftmo_report
- `logs/simple_robot.log` — log principal
- `runtime/robot.pid` — PID lock

## Tests
```powershell
cd C:\Users\saint\Documents\MT5_FTMO_IA.7 && python -m pytest tests/ -q
```

## Agents concernés
- `@monitor-agent` — gardien 24/7
- `@log-analyst` — analyse les logs
- `@cio` — reçoit les rapports
- `@performance-engineer` — mesure la santé système
- `@mt5-infrastructure-auditor` — audite l'infra