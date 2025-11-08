Regime detection — guide d'activation progressive
===============================================

But
---
Ce document décrit la procédure recommandée pour activer les garde-fous de
détection de régimes (validation d'entrée, dump diagnostique, nettoyage safe)
sans perturber une production en cours.

Principe général
-----------------
- Tout changement est opt-in. Par défaut, le comportement existant ne change pas.
- Tester d'abord en staging avec un petit jeu de données (ex. `data/features_sample.csv`).
- Collecter des preuves (JSON dans `artifacts/diagnostics/`) avant toute activation en prod.

Variables d'environnement (opt-in)
---------------------------------
- `REGIME_VALIDATE_INPUT=1` : active la validation conservatrice des rendements
  (vérifie taux de NaN, nombre de rendements extrêmes, amplitude absurde).
- `REGIME_VALIDATE_DUMP=1` : si la validation échoue, écrit un JSON horodaté dans
  `artifacts/diagnostics/` contenant le rapport de validation.
- `REGIME_SAFE_CLEAN=1` : applique un nettoyage conservateur (clipping 1%/99% sur
  chaque feature et cap sur `returns` selon `REGIME_MAX_RETURN`) et utilise
  `RobustScaler` avant l'entraînement.

Commande recommandée en staging
-------------------------------
Depuis la racine du repo, lancer le helper PowerShell (fourni) :

```powershell
.\scripts\run_regime_staging.ps1
```

Ce script active `REGIME_VALIDATE_INPUT=1`, `REGIME_VALIDATE_DUMP=1` et
`REGIME_SAFE_CLEAN=1` uniquement pour la durée du processus lancé, sans
modifier la configuration globale.

Fichiers à inspecter après run
------------------------------
- `artifacts/diagnostics/` : contient les dumps `regime_validation_<timestamp>.json`
  (présents si `REGIME_VALIDATE_DUMP=1` et validation échouée).
- `artifacts/regime_detection_model.json` et `_history.csv` : résumé du modèle et
  de l'historique des labels.

Checklist opérationnelle (staging -> production)
-----------------------------------------------
1. Lancer en staging avec le helper PowerShell.
2. Vérifier les dumps dans `artifacts/diagnostics/` si présents.
3. Si les rapports montrent des entrées corrompues :
   - Priorité : corriger l'ingestion en amont (source des prix incorrects) ;
   - Si correction immédiate impossible, garder `REGIME_SAFE_CLEAN=1` en staging
     pour évaluer l'impact des nettoyages conservateurs.
4. Quand les rapports et tests sont satisfaisants, planifier une activation
   progressive en production (heures creuses, monitorage renforcé).

Conseils de sécurité
--------------------
- Ne jamais activer `REGIME_VALIDATE_INPUT` en production sans avoir validé
  d'abord en staging et collecté les preuves.
- `REGIME_SAFE_CLEAN` est un paliatif utile mais ne remplace pas la correction
  de l'ingestion des données — c'est une mesure temporaire.

Contact & suivi
---------------
Pour assistance, fournir :
- Les fichiers JSON trouvés dans `artifacts/diagnostics/` ;
- Un extrait des logs de production autour du run concerné ;
- Exemple d'une série de prix posant problème (si possible).

Fin
