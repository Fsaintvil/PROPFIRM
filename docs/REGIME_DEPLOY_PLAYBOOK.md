REGIME Detection — Playbook de déploiement (canary)
=====================================================

Objectif
--------
Activer progressivement les protections (validation + safe-clean + dumps) en production de manière sûre, surveillée et réversible.

Prérequis
---------
- Accès aux logs et aux artefacts (`artifacts/diagnostics/`, `artifacts/regime_detection_*`)
- Possibilité d'exporter variables d'environnement pour le processus en prod
- Equipe Ops prête à surveiller les métriques listées

Étapes recommandées (canary)
---------------------------
1) Préparation (staging)
   - Exécuter le script en staging avec les flags opt-in et vérifier les diagnostics.
   - Commande (PowerShell) :

```powershell
$env:REGIME_VALIDATE_RAW='1'
$env:REGIME_VALIDATE_DUMP='1'
$env:REGIME_VALIDATE_INPUT='1'
$env:REGIME_SAFE_CLEAN='1'
$env:REGIME_MAX_RETURN='0.5'
python -m scripts.market_regime_detection
```

   - Vérifier : création de `artifacts/diagnostics/last_validation_raw_*.json`, `regime_validation_*.json`, et les fichiers `regime_detection_*`.

2) Canary 1% (ou un petit groupe de symboles)
   - Activer uniquement `REGIME_VALIDATE_RAW=1` et `REGIME_VALIDATE_DUMP=1` pour 1% du trafic
   - Monitorer 24-48h

3) Canary 10% (si 1% OK)
   - Activer `REGIME_VALIDATE_INPUT=1` et `REGIME_SAFE_CLEAN=1` pour 10% des symboles
   - Monitorer erreurs, fallback count, et divergence des probabilités

4) Rollout progressif (25% → 50% → 100%)
   - A chaque étape, vérifier les métriques (voir section suivante)
   - Arrêter et rollback si un seuil d'alerte est dépassé

Métriques et sondes à surveiller
-------------------------------
- Validation failure rate (nombre de diagnostics RAW/validation écrits / nombre de runs)
- abs_gt_max counts (dans les dumps) — augmentation anormale signifie ingestion cassée
- Taux de fallback vers KMeans (indique HMM instable)
- Score HMM (si disponible) et Silhouette score pour fallback
- Distribution des rendements (percentiles 1/99) — drift brutal
- Nombre d'exceptions non gérées dans logs

Alerting (seuils suggérés)
-------------------------
- Validation failure rate > 5% sur 1h → alerte
- Taux de fallback > 10% sur 1h → alerte
- Changements de distribution des rendements (pct_99 augmente de > 100x) → alerte

Rollback rapide
---------------
- Désactiver les flags en prod (unset ENV), ce qui ramène le comportement précédent.
- Option alternative : redéployer la version précédente du service.

Procédures post-mortem
----------------------
- Conserver les diagnostics `artifacts/diagnostics/*` et partager avec ingestion
- Indexer les runs problématiques (timestamp, symboles, dump filename)
- Ajouter tests d'ingestion couvrant les cas observés

Conseils opérationnels
----------------------
- Ne pas activer `REGIME_SAFE_CLEAN` globalement tant que l'équipe ingestion n'a pas corrigé les sources invalides. Préférer diagnostiquer d'abord.
- Documenter chaque activation canary et son résultat dans le ticket de déploiement.

Commandes utiles pour ops
-------------------------
- Lancer en staging (PowerShell) :

```powershell
$env:REGIME_VALIDATE_RAW='1'; $env:REGIME_VALIDATE_DUMP='1'; $env:REGIME_SAFE_CLEAN='1'; $env:REGIME_MAX_RETURN='0.5'; python -m scripts.market_regime_detection
```

- Lister les diagnostics récents :

```powershell
Get-ChildItem -Path artifacts\diagnostics -File | Sort-Object LastWriteTime -Descending | Select-Object -First 10
Get-Content -Path artifacts\diagnostics\last_validation_raw_*.json -Raw | ConvertFrom-Json
```

- Récupérer les 5 dernières lignes du history CSV :

```powershell
Get-Content artifacts\regime_detection_history.csv -Tail 5
```

Fin
---
Ce playbook est un guide minimal — adaptez les seuils et étapes à votre volumétrie et SLA.