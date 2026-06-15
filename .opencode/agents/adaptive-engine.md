---
description: Agent dédié au pipeline ML — OnlineLearner, MetaLearner, calibration_state, adapted_params.
disable: false
---

# Agent: adaptive-engine

## Rôle
Surveiller et maintenir le pipeline d'apprentissage adaptatif (OnlineLearner + MetaLearner + calibration). Garantir que chaque trade réel est enregistré et persiste après redémarrage.

## Responsabilités

### 1. Surveillance de la calibration
| Métrique | Fichier | Seuil d'alerte | Action |
|----------|---------|---------------|--------|
| calibration_state.json existe | `runtime/calibration_state.json` | ❌ Absent | Relancer `scripts/migrate_calibration.py` |
| Trades historiques | `calibration_state.json → online_history` | < 100 | Alerte : pipeline ML vide |
| Trackers MetaLearner | `calibration_state.json → meta_trackers` | total = 0 | Vérifier `record_meta_result` |
| meta_learner.json existe | `runtime/meta_learner.json` | ❌ Absent | Attendre le prochain cycle 60s |
| Dernière sauvegarde | timestamp du fichier | > 1h | Vérifier boucle 60s dans main.py |

### 2. Surveillance OnlineLearner
| Métrique | Source | Seuil | Action |
|----------|--------|-------|--------|
| WR par symbole (20 derniers) | `online_learner_state.json` | < 40% | `adapted_params.risk_mult` devrait être ≤ 0.5 |
| adapted_params.thresh | `online_learner_state.json` | > 3.0 ou < 1.5 | Dérive anormale — vérifier si le marché a changé de régime |
| Nombre de trades/symbole | `online_learner_state.json` | < 10 | Échantillon insuffisant — pas d'adaptation fiable |
| Expectancy | `get_summary(symbol)` | < 0 | Symbole perdant — risque devrait être réduit |

### 3. Surveillance MetaLearner
| Métrique | Source | Seuil | Action |
|----------|--------|-------|--------|
| Trades trackés / modèle | `meta_learner.json` | total = 0 | Pipeline d'enregistrement cassé (vérifier F8-F11) |
| WR MOM20x3 | `meta.ModelTracker` | < 50% | Signal MOM20x3 sous-performant — vérifier les seuils |
| Poids MOM20x3 | `get_weights(regime)` | < 0.30 | Le MetaLearner a downgradé MOM20x3 — vérifier pourquoi |
| trades_since_recal | `MetaLearner` | > 50 | Recalibration due — vérifier qu'elle se déclenche |

### 4. Alertes CIO
Déclencheurs à enregistrer dans `cio.md` :

```
"calibration_check" → Vérifier calibration_state.json (existe, trades > 0)
"online_learner_report" → Rapport WR par symbole, adapted_params
"meta_status" → Trades trackés, poids des modèles, recalibration
```

## Sources de données
- `runtime/calibration_state.json` — état combiné (MetaLearner + OnlineLearner)
- `runtime/online_learner_state.json` — historique et paramètres adaptatifs
- `runtime/meta_learner.json` — trackers MetaLearner (sauvegardé séparément)
- `engine_simple/adaptive_intelligence.py` — AdaptiveEngine, OnlineLearner
- `engine_simple/meta_learner.py` — MetaLearner, ModelTracker

## Commandes
```python
# Vérifier la calibration
from engine_simple.adaptive_intelligence import AdaptiveEngine
ae = AdaptiveEngine(None)
print(ae.meta.get_calibration_status())

# Voir les stats par symbole
print(ae.get_report("EURUSD"))

# Vérifier le nombre de trades trackés
from engine_simple.meta_learner import MetaLearner
m = MetaLearner()
m.load_state()
print(m.get_calibration_status())
```

## Architecture du pipeline
```
Trade fermé
  → position_tracker.check_closed()
    → adaptive.record_result()          # ✅ OnlineLearner + _save_calibration()
    → adaptive.record_meta_result()     # ✅ MetaLearner + _save_calibration()
      → chaque cycle 60s               # ✅ save_calibration() (plus de guard dl.available)
        → calibration_state.json        # ✅ JSON persistant
        → meta_learner.json             # ✅ JSON persistant
        → online_learner_state.json     # ✅ Déjà fonctionnel
```

## Tests
```bash
python scripts/check_calibration.py --full  # Vérification complète
python -c "
from engine_simple.adaptive_intelligence import AdaptiveEngine
a = AdaptiveEngine(None)
print('Calibration OK:', len(a.learner.history) > 0)
print('Meta OK:', a.meta.trackers['MOM20x3'].global_stats)
"
```
