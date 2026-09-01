---
name: news-impact
description: Filtrage et analyse des événements macroéconomiques — NFP, CPI, FOMC, BCE, BOJ. Impact sur les marchés, timing des trades, et protection anti-gap. Utilise news_filter.py et ftmo_protector.py.
---

# News Impact Skill

## Description
Spécialiste de l'analyse d'impact des événements macroéconomiques : calendrier économique, impact historique, timing des trades, et protection contre les gaps. Protège le robot des pertes dues aux annonces imprévues.

## Quand utiliser
- Avant chaque trade pour vérifier le calendrier économique
- Avant le week-end pour évaluer les risques d'événements
- Pour ajuster les stop-loss avant une annonce majeure
- Pour décider de fermer ou maintenir des positions
- Pour analyser l'impact d'un événement passé

## Calendrier Économique — Événements Majeurs

### 1. Banques Centrales

| Banque | Événement | Impact | Fréquence |
|--------|-----------|:------:|:---------:|
| **FED** | FOMC Rate Decision | 🔴 ÉLEVÉ | 8×/an |
| **FED** | FED Chair Press Conference | 🔴 ÉLEVÉ | 8×/an |
| **ECB** | ECB Rate Decision | 🔴 ÉLEVÉ | 6×/an |
| **ECB** | ECB Press Conference | 🟡 MOYEN | 6×/an |
| **BOJ** | BOJ Rate Decision | 🟡 MOYEN | 8×/an |
| **BOE** | BOE Rate Decision | 🟡 MOYEN | 8×/an |
| **SNB** | SNB Rate Decision | 🟢 FAIBLE | 4×/an |
| **RBA** | RBA Rate Decision | 🟡 MOYEN | 8×/an |
| **BOC** | BOC Rate Decision | 🟡 MOYEN | 8×/an |

### 2. Inflation

| Indicateur | Pays | Impact | Fréquence |
|------------|------|:------:|:---------:|
| **CPI** | US | 🔴 ÉLEVÉ | Mensuel |
| **CPI** | EU | 🔴 ÉLEVÉ | Mensuel |
| **CPI** | UK | 🟡 MOYEN | Mensuel |
| **CPI** | Japan | 🟡 MOYEN | Mensuel |
| **PCE** | US | 🔴 ÉLEVÉ | Mensuel |
| **PPI** | US | 🟡 MOYEN | Mensuel |
| **Core CPI** | US | 🔴 ÉLEVÉ | Mensuel |

### 3. Emploi

| Indicateur | Pays | Impact | Fréquence |
|------------|------|:------:|:---------:|
| **NFP** | US | 🔴 ÉLEVÉ | Mensuel |
| **Unemployment Rate** | US | 🔴 ÉLEVÉ | Mensuel |
| **Non-Farm Payrolls** | US | 🔴 ÉLEVÉ | Mensuel |
| **Average Hourly Earnings** | US | 🟡 MOYEN | Mensuel |
| **ADP Employment** | US | 🟡 MOYEN | Mensuel |
| **Jobless Claims** | US | 🟢 FAIBLE | Hebdo |
| **Employment Change** | UK | 🟡 MOYEN | Mensuel |

### 4. Croissance

| Indicateur | Pays | Impact | Fréquence |
|------------|------|:------:|:---------:|
| **GDP** | US | 🔴 ÉLEVÉ | Trimestriel |
| **GDP** | EU | 🟡 MOYEN | Trimestriel |
| **Retail Sales** | US | 🟡 MOYEN | Mensuel |
| **Industrial Production** | US | 🟡 MOYEN | Mensuel |
| **PMI Manufacturing** | US | 🟡 MOYEN | Mensuel |
| **PMI Services** | US | 🟡 MOYEN | Mensuel |
| **ISM Manufacturing** | US | 🔴 ÉLEVÉ | Mensuel |
| **ISM Services** | US | 🔴 ÉLEVÉ | Mensuel |

## Matrice d'Impact par Symbole

| Symbole | NFP | FOMC | CPI | BOJ | Brexit |
|---------|:---:|:----:|:---:|:---:|:------:|
| **EURUSD** | 🔴 | 🔴 | 🔴 | 🟡 | 🟡 |
| **GBPUSD** | 🔴 | 🔴 | 🔴 | 🟡 | 🔴 |
| **USDJPY** | 🔴 | 🔴 | 🔴 | 🔴 | 🟢 |
| **XAUUSD** | 🔴 | 🔴 | 🔴 | 🟡 | 🟡 |
| **BTCUSD** | 🟡 | 🟡 | 🟡 | 🟢 | 🟢 |
| **US100.cash** | 🔴 | 🔴 | 🔴 | 🟡 | 🟡 |
| **US30.cash** | 🔴 | 🔴 | 🔴 | 🟡 | 🟡 |

## Protocoles de Protection

### 1. Filtrage Avant Annonce
```python
# news_filter.py::check_news_impact()
if event.impact == "HIGH" and time_to_event < 30min:
    reject("news_event_imminent")
elif event.impact == "MEDIUM" and time_to_event < 15min:
    reject("news_event_near")
```

### 2. Réduction d'Exposition
```python
# Avant FOMC/NFP/CPI
if event.impact == "HIGH":
    max_positions = min(max_positions, 2)
    max_lot = max_lot * 0.5
    # Ou fermer toutes les positions
    close_all_positions()
```

### 3. Ajustement Stop-Loss
```python
# Élargir les SL avant annonces volatiles
if event.impact == "HIGH":
    sl_distance = sl_distance * 1.5  # +50% buffer
    # Ou activer trailing plus agressif
    force_trail_n1()
```

### 4. Fermeture Pré-Événement
```python
# Fermer 15min avant FOMC/NFP
if event.impact == "HIGH" and time_to_event < 15min:
    close_all_positions()
    # Réouvrir 30min après l'annonce
    if time_since_event > 30min:
        reopen_positions()
```

## Historique d'Impact

### NFP (Non-Farm Payrolls)
```
Impact moyen EURUSD: ±80 pips en 15 min
Impact moyen GBPUSD: ±100 pips en 15 min
Impact moyen USDJPY: ±60 pips en 15 min
Impact moyen XAUUSD: ±$15 en 15 min
Impact moyen BTCUSD: ±2% en 15 min
```

### FOMC (Fed Rate Decision)
```
Impact moyen EURUSD: ±120 pips en 30 min
Impact moyen GBPUSD: ±150 pips en 30 min
Impact moyen USDJPY: ±100 pips en 30 min
Impact moyen XAUUSD: ±$25 en 30 min
Impact moyen BTCUSD: ±3% en 30 min
```

### CPI (Consumer Price Index)
```
Impact moyen EURUSD: ±60 pips en 15 min
Impact moyen GBPUSD: ±80 pips en 15 min
Impact moyen USDJPY: ±50 pips en 15 min
Impact moyen XAUUSD: ±$12 en 15 min
Impact moyen BTCUSD: ±1.5% en 15 min
```

## Decision Tree

```
Trade proposé?
├── OUI → Calendrier économique vérifié?
│   ├── OUI → Événement HIGH impact dans 30min?
│   │   ├── OUI → REJETER (news_event_imminent)
│   │   └── NON → Événement MEDIUM impact dans 15min?
│   │       ├── OUI → RÉDUIRE exposition (max_lot × 0.5)
│   │       └── NON → AUTORISER trade
│   └── NON → TRADE AUTORISÉ (pas d'événement)
└── NON → Rien à faire
```

## Intégration avec le Robot

### Hooks
- `ftmo_protector.py::_check_news()` : vérification avant trade
- `signal_pipeline.py::_phase4_news_filter()` : filtre dans pipeline
- `trailer.py::_check_news_event()` : ajustement trailing

### Configuration
```yaml
# config/default.yaml
news_filter:
  enabled: true
  high_impact_block_minutes: 30
  medium_impact_reduce_minutes: 15
  close_before_high_impact: true
  reopen_after_minutes: 30
```

## Sources de Données

### APIs Gratuites
- **Forex Factory** : calendrier complet
- **Investing.com** : données historiques
- **DailyFX** : analyse impact

### APIs Payantes
- **Bloomberg Terminal** : temps réel
- **Reuters Eikon** : analyse avancée
- **Refinitiv** : données institutionnelles

## Métriques de Performance

### KPIs
| Métrique | Formule | Cible |
|----------|---------|:-----:|
| **News avoidance rate** | trades évités / events | > 80% |
| **Post-news WR** | WR après événement | > 55% |
| **Gap protection** | gaps évités / gaps totaux | > 90% |

## Logs

```python
[NEWS] {symbol}: event={event}, impact={impact}, time_to_event={time}min → BLOCK
[NEWS] {symbol}: event={event}, impact={impact}, time_to_event={time}min → REDUCE
[NEWS] POST_EVENT: {symbol} reopened after {event} (impact faded)
[NEWS] GAP_RISK: {symbol} exposed to weekend gap ({gap_pct:.2f}%)
```
