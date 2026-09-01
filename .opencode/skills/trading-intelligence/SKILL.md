---
name: trading-intelligence
description: Synthèse multi-agents du Trading Intelligence Council — coordination CIO,agrégation de rapports, détection de conflits, et recommandations d'action. Utilise tous les agents du council.
---

# Trading Intelligence Skill

## Description
Spécialiste de la coordination du Trading Intelligence Council : agrège les rapports de 22 agents, détecte les conflits, synthétise les recommandations, et propose des actions prioritaires. Point d'entrée unique pour le diagnostic global du robot.

## Quand utiliser
- Au démarrage de chaque cycle de supervision (toutes les 5-15 min)
- Après un événement critique (erreur, trade, alerte)
- Pour un bilan de santé complet du robot
- Pour résoudre des conflits entre agents
- Pour prioriser les fixes et optimisations

## Architecture du Council

### Hiérarchie des Agents
```
Robot Manager (primary)
├── CIO (coordinateur)
│   ├── @system-monitor      ← surveillance 24/7
│   ├── @risk-compliance     ← capital, FTMO, veto
│   ├── @signal-engine       ← signaux MOM20x3
│   ├── @adaptive-engine     ← calibration ML
│   ├── @auto-fixer          ← correction bugs
│   ├── @kill-switch         ← arrêt d'urgence
│   ├── @quant-auditor       ← statistiques
│   └── @optimizer           ← ajustements
└── @supreme-council         ← méta-agent (conflits)
```

### Flux de Décision
```
Cycle 15s
├── 1. Collecte métriques (system-monitor)
├── 2. Vérification FTMO (risk-compliance)
├── 3. Analyse signaux (signal-engine)
├── 4. Détection anomalies (log-analyst)
├── 5. Synthèse (CIO)
└── 6. Action (auto-fixer / kill-switch / optimize)
```

## Matrice de Délégation

| Situation | Agent Principal | Agent Secondaire |
|-----------|:---------------:|:----------------:|
| Début de cycle normal | @cio | — |
| Erreur/logs/mémoire | @system-monitor | @log-analyst |
| Bug identifié | @auto-fixer | @performance-engineer |
| DD > 6% / daily loss > 1.5% | @risk-compliance | @kill-switch |
| Performance douteuse | @quant-auditor | @optimizer |
| Connexion MT5 instable | @system-monitor | @mt5-infrastructure |
| Arrêt d'urgence | @kill-switch | @risk-compliance |
| Conflit entre agents | @cio | @supreme-council |
| Rapport hebdomadaire | @optimizer | @quant-auditor |

## Protocoles de Synthèse

### 1. Collecte de Rapports
```python
# Pour chaque agent, collecter:
# - status: GREEN | YELLOW | RED
# - confidence: 0.0 - 1.0
# - findings: list[str]
# - recommendations: list[str]
# - severity: INFO | WARNING | CRITICAL
```

### 2. Agrégation
```python
# Score de santé global:
health_score = (
    sum(agent.status_score * agent.confidence) 
    / total_agents
)

# Status global:
if health_score > 0.8:
    global_status = "GREEN"
elif health_score > 0.5:
    global_status = "YELLOW"
else:
    global_status = "RED"
```

### 3. Détection de Conflits
```python
# Conflit si deux agents recommandent des actions opposées:
# - risk-compliance: "FERMER TOUT"
# - optimizer: "AUGMENTER EXPOSITION"
# → Escalade au supreme-council
```

### 4. Priorisation
```python
# Priorité = severity × confidence × impact
priority = (
    severity_score 
    × agent.confidence 
    × estimated_impact
)

# Ordre:
# 1. CRITICAL (kill-switch, risk-compliance veto)
# 2. WARNING (performance, anomalies)
# 3. INFO (optimisations, suggestions)
```

## Types de Rapports

### 1. Rapport de Cycle (15s)
```json
{
  "cycle": 142,
  "timestamp": "2026-08-30T12:00:00Z",
  "global_status": "GREEN",
  "health_score": 0.85,
  "agents": {
    "system_monitor": {"status": "GREEN", "findings": ["RAM 85MB", "CPU 0%"]},
    "risk_compliance": {"status": "GREEN", "findings": ["DD 0.2%", "Daily loss $44"]},
    "signal_engine": {"status": "YELLOW", "findings": ["3 positions ouvertes"]}
  },
  "recommendations": ["Aucune action nécessaire"]
}
```

### 2. Rapport d'Incident
```json
{
  "type": "INCIDENT",
  "severity": "CRITICAL",
  "agent": "system-monitor",
  "finding": "GIL deadlock 2h42min",
  "impact": "Positions exposées sans supervision",
  "root_cause": "C-extension MT5 bloque le GIL",
  "recommendations": [
    "Implémenter watchdog externe",
    "Réduire fréquence appels MT5",
    "Ajouter timeout sur appels API"
  ]
}
```

### 3. Rapport Hebdomadaire
```json
{
  "period": "2026-08-24 → 2026-08-30",
  "trades": 45,
  "pnl": "+$234.50",
  "win_rate": 52.3,
  "profit_factor": 1.85,
  "max_dd": 0.35,
  "top_performers": ["BTCUSD +$180", "SOLUSD +$45"],
  "worst_performers": ["XAUUSD -$38"],
  "improvements": [
    "Wick Ratio Filter (+3-5% WR estimé)",
    "Momentum Acceleration Filter (+2-4% WR)",
    "Volatility Squeeze Filter (+2-4% WR)"
  ],
  "next_week_focus": [
    "Valider les 3 nouveaux filtres en live",
    "Surveiller concentration crypto",
    "Préparer scaling challenge"
  ]
}
```

## Résolution de Conflits

### Scénarios Courants

#### 1. Risk-Compliance vs Optimizer
```
Risk: "STOP — DD 7.8%"
Optimizer: "CONTINUER — edge prouvé"
→ Décision: STOP (veto risk-compliance non contestable)
```

#### 2. Signal-Engine vs Quant-Auditor
```
Signal: "BUY XAUUSD score 0.85"
Quant: "XAUUSD p=0.0008, pas d'edge"
→ Décision: REJECT (quant-auditor prioritaire sur edge)
```

#### 3. System-Monitor vs Auto-Fixer
```
Monitor: "RAM 95%, swap utilisé"
Fixer: "Bug identifié, fix disponible"
→ Décision: FIX IMMÉDIAT (performance avant fonctionnalité)
```

### Escalade au Supreme-Council
```python
# Quand deux agents sont en conflit:
if agent1.recommendation != agent2.recommendation:
    if agent1.severity == "CRITICAL" or agent2.severity == "CRITICAL":
        # Escalade immédiate
        escalate_to_supreme_council(agent1, agent2)
    else:
        # Délibération (5 min max)
        debate(agent1, agent2, timeout=300)
```

## Métriques de Performance du Council

### KPIs
- **response_time** : temps moyen de réponse du council
- **conflict_rate** : % de cycles avec conflits
- **resolution_time** : temps moyen de résolution
- **accuracy** : % de recommandations correctes

### Formules
```python
response_time = avg(timestamp_decision - timestamp_alert)
conflict_rate = cycles_with_conflict / total_cycles
resolution_time = avg(timestamp_resolution - timestamp_conflict)
```

## Intégration avec le Robot

### Hooks
- `main.py::cycle()` : appel au council toutes les 15s
- `trading_engine.py::_check_health()` : vérification santé
- `performance_monitor.py::record_trade()` : métriques temps réel

### Configuration
```yaml
# config/default.yaml
council:
  enabled: true
  cycle_interval: 15  # secondes
  conflict_timeout: 300  # 5 min
  auto_fix: true
  kill_switch_threshold: 0.08  # DD 8%
```

## Logs

```python
[COUNCIL] Cycle {n}: status={status}, health={score:.2f}
[COUNCIL] CONFLICT: {agent1} vs {agent2} → escalation
[COUNCIL] RESOLUTION: {decision} (confidence={conf:.2f})
[COUNCIL] ACTION: {agent} → {action} (priority={pri})
```
