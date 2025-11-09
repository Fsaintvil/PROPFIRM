## Proposition de migration — `MT5_FTMO_IA` -> structure canonique

Objectif
- Retirer la dépendance d'un package local éditable `MT5_FTMO_IA` et
  adapter les imports pour qu'ils utilisent la structure canonique du
  dépôt (scripts/, tools/, src/, etc.).

Stratégie (simulation / PR)
1. Générer un diff proposé (ce fichier) listant tous les fichiers où
   apparaissent `MT5_FTMO_IA` dans des importations ou chemins.
2. Pour chaque occurrence `from MT5_FTMO_IA.scripts.foo import Bar` ->
   proposer `from scripts.foo import Bar` si `scripts/foo.py` existe.
3. Pour `MT5_FTMO_IA.tools.*` -> `tools.*` ou `src.utils.*` selon la
   localisation réelle du module.
4. Laisser un shim de compatibilité (présent) pendant la période de
   transition, puis supprimer après validation et merge de la PR.

Exemples de remplacements proposés
- `from MT5_FTMO_IA.scripts.signal_mtf import generate_signals`
  -> `from scripts.signal_mtf import generate_signals`
- `from MT5_FTMO_IA.scripts.mt5_connector import initialize_mt5`
  -> `from scripts.mt5_connector import initialize_mt5`

Fichiers identifiés (extraits de la recherche) — vérifier avant apply
- `tests/test_mtf_signal_integration.py`
- `tests/test_mtf_integration.py`
- `tools/run_mtf_demo.py`
- `tools/repickle_models.py`
- `tools/verify_repickled.py`
- `tools/verify_strict_load.py`
- `tmp_diag.py` (diagnostics)
- scripts/ wrappers and ops files referencing `MT5_FTMO_IA` in paths

Proposition d'ordre de PR
1. PR 1 (docs + shim): ajouter shim (déjà appliqué), documenter la
   migration et ajuster README pour indiquer la compatibilité.
2. PR 2 (simulation/diff): proposer remplacements en mode preview (fichiers
   modifiés dans `patches/proposed/`), demander revue.
3. PR 3 (apply): appliquer les remplacements dans le code source,
   lancer la suite de tests, corriger conflits et warnings, supprimer shim.

Risques / notes
- Certains modules importés via `MT5_FTMO_IA` peuvent ne pas exister sous
  le nom `scripts.*`. Valider chaque mapping avant application.
- Tests unitaires doivent être exécutés après chaque série de remplacements.

---
Généré automatiquement le 2025-11-09 par l'agent — revue requise avant apply.
