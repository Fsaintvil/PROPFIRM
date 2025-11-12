## Proposition de migration - références `MT5_FTMO_IA`

Ce fichier contient la liste initiale des fichiers qui référencent
`MT5_FTMO_IA` et servira de base pour une PR de migration. La stratégie
proposée est :

- Fournir ce shim de compatibilité (déjà ajouté) pour assurer un fonctionnement
  immédiat.
- Préparer une PR « simulation » qui remplace progressivement les imports
  `MT5_FTMO_IA.scripts.xxx` par `scripts.xxx` (ou par l'emplacement canonique
  choisi), en vérifiant les tests après chaque lot de remplacements.

Fichiers détectés (extrait, non exhaustif) :

```
README_OLD.md
DEPLOYMENT_RULES.md
DOCS_INCONSISTENCIES.md
tools/archive_model_baks.py
tools/check_imports.py
tools/run_mtf_demo.py
tools/repickle_models.py
tools/verify_repickled.py
tests/test_mtf_signal_integration.py
tests/test_mtf_integration.py
tests/test_indicators_and_backtester.py
tests/test_indicators_7.py
test_run_session_cap.py
scripts/performance_validator.py
ops/start_periodic_live.ps1
ops/enable_live_run.ps1
... (liste complète disponible via recherche)
```

Étapes recommandées pour la PR de migration :
1. Remplacer les imports dans les tests et outils non critiques (petits lots).
2. Lancer la suite pytest et corriger les erreurs d'import / dépendances.
3. Ajouter un petit shim temporaire (si nécessaire) pour préserver compat.
4. Supprimer le shim et fusionner la PR lorsque tous les imports sont migrés.

---
Généré automatiquement comme proposition — revue humaine requise.
