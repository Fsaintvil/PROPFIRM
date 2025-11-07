Titre : [PATCH] Regime detection — validation opt-in + safe-clean + diagnostics

Résumé
------
Ajoute des protections non-intrusives et opt-in pour la détection de régimes :
- Validation légère pré- et pré-entraînement (ENV: REGIME_VALIDATE_RAW, REGIME_VALIDATE_INPUT)
- Dump diagnostics (ENV: REGIME_VALIDATE_DUMP)
- Nettoyage conservateur opt-in (ENV: REGIME_SAFE_CLEAN + REGIME_MAX_RETURN)
- Fallback KMeans si HMM échoue ou si validation (si activée) détecte des anomalies

Fichiers modifiés / ajoutés
--------------------------
- `scripts/market_regime_detection.py` (modifié) : ajout de la validation pré-clean non intrusive, dumps diagnostics, safe-clean et fallback.
- `tests/test_regime_validation.py` (ajouté) : test unitaire minimal pour _validate_regime_input
- `docs/REGIME_DEPLOY_PLAYBOOK.md` (ajouté) : procédure canary + metrics à surveiller
- `docs/REGIME_REMEDIATION_NOTE.md` (ajouté) : note technique résumant les anomalies observées et recommandations pour ingestion

Pourquoi ce patch
------------------
Le patch vise à réduire le risque d'instabilité du HMM entraîné sur des données corrompues tout en conservant un comportement par défaut inchangé en production (toutes les nouvelles protections sont opt-in). Il fournit de la traçabilité (dumps diagnostics) pour assister l'équipe ingestion.

Test & validation
-----------------
Exécuter en staging (PowerShell) :

```powershell
$env:REGIME_VALIDATE_RAW='1'
$env:REGIME_VALIDATE_DUMP='1'
$env:REGIME_VALIDATE_INPUT='1'
$env:REGIME_SAFE_CLEAN='1'
$env:REGIME_MAX_RETURN='0.5'
python -m scripts.market_regime_detection
```

CI checklist suggéré
--------------------
- ✅ pytest (incl. `tests/test_regime_validation.py`)
- ✅ Vérifier que l'exécution en staging n'introduit pas d'exceptions non gérées
- ✅ Vérifier que les diagnostics sont écrits sous `artifacts/diagnostics/` quand attendu

Rollback
--------
- Revenir au commit précédent (git revert) si comportement inattendu en canary
- Désactiver les flags opt-in en production (unset des variables d'environnement)

Relecteurs suggérés
-------------------
- équipe Platform/ingestion
- équipe Quant/Stratégies
- équipe Ops

Notes finales
-------------
Toutes les protections sont opt-in. Merci de vérifier les diagnostics produits en staging avant activation en prod.