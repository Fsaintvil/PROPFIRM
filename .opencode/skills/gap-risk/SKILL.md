---
name: gap-risk
description: Analyse du risque de gap (weekend, overnight, événements macro). Évalue l'exposition des positions ouvertes aux gaps de prix entre sessions. Utilise ftmo_protector.py et trailer.py.
---

# Gap Risk Skill

## Description
Spécialiste de l'analyse du risque de gap : weekend, overnight, événements macro (NFP, CPI, décisions banques centrales). Évalue l'exposition des positions ouvertes et recommande des actions de protection.

## Quand utiliser
- Avant le week-end (vendredi 16h UTC) pour évaluer les positions ouvertes
- Après un gap de lundi matin pour analyser l'impact
- Avant des événements macro à fort impact
- Pour ajuster les stop-loss avant les gaps potentiels
- Pour décider de fermer ou maintenir des positions le weekend

## Types de Gap

### 1. Weekend Gap (le plus fréquent)
- **Origine** : fermeture samedi 00:00 → réouverture lundi 00:00 (marchés forex/indices)
- **Amplitude typique** : 0.1% - 2.0% selon le symbole
- **Symboles à risque** : XAUUSD (geo-politique), GBPUSD (Brexit), AUDUSD (Chine)
- **Symboles safe** : USDJPY (BOJ intervention rare), EURUSD (liquidité forte)

### 2. Overnight Gap (jours de fête)
- **Origine** : fermeture 22:00 → réouverture 22:00 (marchés 24/7 fermés)
- **Amplitude typique** : 0.5% - 5.0%
- **Exemple** : US100.cash pendant Thanksgiving

### 3. Event Gap (NFP, CPI, FOMC)
- **Origine** : annonce macro → mouvement violent
- **Amplitude typique** : 0.5% - 3.0% en quelques secondes
- **Timing** : 14:30 UTC (NFP/CPI), 18:00 UTC (FOMC)

## Matrice de Risque par Symbole

| Symbole | Gap Weekend | Gap Event | Liquidité | Recommandation |
|---------|:-----------:|:---------:|:---------:|:--------------:|
| XAUUSD | ÉLEVÉ | ÉLEVÉ | Moyenne | Fermer ou trail serré |
| BTCUSD | MOYEN | ÉLEVÉ | Forte | Trail large (wicks) |
| GBPUSD | MOYEN | ÉLEVÉ | Forte | Fermer si NFP |
| EURUSD | FAIBLE | MOYEN | Très forte | Maintenir |
| US100.cash | MOYEN | ÉLEVÉ | Forte | Fermer ou hedger |
| AUDUSD | MOYEN | ÉLEVÉ | Moyenne | Fermer si Chine |

## Protocoles de Protection

### 1. Fermeture Pré-Weekend
```python
#.ftmo_protector.py::_is_weekend_close_window()
# Si weekend_trading=false ET friday ≥ 16h UTC:
#   max_hours = min(max_hours, 2h)  # force fermeture
```

### 2. Trail Serré Avant Gap
```python
# Si friday ≥ 14h UTC ET position profit > 0:
#   force trail N1 (lock 1.80×ATR, trail 0.80×ATR)
#   ou BE si profit < 1.80×ATR
```

### 3. Réduction d'Exposition
```python
# Si friday ≥ 15h UTC:
#   max_positions = min(max_positions, 3)
#   max_lot par symbole = max_lot * 0.5
```

## Calcul du Risque de Gap

### Formule
```
gap_risk = position_size × gap_amplitude_estimee × (1 + volatilité)
```

### Exemple
```
XAUUSD: 0.03 lot × $100 (gap typique) × 1.5 (vol) = $4.50 risque
BTCUSD: 0.03 lot × $500 (gap typique) × 2.0 (vol) = $30.00 risque
```

## Decision Tree

```
Position ouverte le vendredi 16h UTC?
├── OUI → Symbole weekend_trading=false?
│   ├── OUI → Fermer avant 18h UTC
│   └── NON → Profit > 0?
│       ├── OUI → Trail serré N1
│       └── NON → Évaluer gap risk
│           ├── gap_risk > 2% du capital → Fermer
│           └── gap_risk < 2% du capital → Maintenir avec BE
└── NON → Rien à faire
```

## Intégration avec le Robot

### Hooks
- `trailer.py::_is_weekend_close_window()` : détection vendredi 16h UTC
- `trailer.py::_check_time_stop()` : fermeture forçée avant weekend
- `ftmo_protector.py::_check_session()` : filtrage par session

### Métriques à Surveiller
- `positions_ouvertes_vendredi` : nombre de positions à risque
- `exposition_totale` : somme des lots × prix
- `gap_risk_estime` : risque en % du capital

## Alertes

| Niveau | Condition | Action |
|--------|-----------|--------|
| 🟢 LOW | gap_risk < 1% du capital | Aucune |
| 🟡 MEDIUM | 1% < gap_risk < 2% du capital | Trail serré |
| 🔴 HIGH | gap_risk > 2% du capital | Fermer positions |

## Logs

```python
[GAP_RISK] {symbol}: gap_risk={risk:.2f}% du capital, positions={count}
[GAP_RISK] FRIDAY_CHECK: {n_positions} positions ouvertes, exposition totale=${total}
[GAP_RISK] FORCE_CLOSE: {symbol} fermé avant weekend (gap_risk={risk:.2f}%)
```
