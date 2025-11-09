# Rapport des incohérences documentaires — PROPFIRM

Ce fichier a été généré automatiquement et décrit les incohérences détectées
dans les fichiers markdown du dépôt (examen réalisé 2025-11-09). Il sert de
checklist pour harmoniser la documentation avec `OPERATIONAL_RULES.md`.

Résumé des points majeurs
- Variables d'environnement et gating : noms non uniformes (`ALLOW_MT5_SEND` vs
  `ALLOW_LIVE_SEND`, `AUTO_APPLY`/`AUTO_DEPLOY` vs `auto_approve`). Voir
  `DEPLOYMENT_RULES.md` (corrigé pour utiliser les noms canoniques) — vérifier
  les scripts legacy et ajouter un shim si nécessaire.
- Chemin/point d'exécution : références à `MT5_FTMO_IA` (legacy) et exemples
  qui exécutent depuis la racine `PROPFIRM`. Standardiser le workflow de
  déploiement et documenter clairement la commande à utiliser en dev vs prod.
- Multi-timeframe (MTF) : divergences (ex. `README_NEW.md` listait M15,H1,H4 —
  corrigé pour M15,M30,H1,H4,D1). Confirmer que le code applique ou calcule ces
  granularités et documenter les différences entre "exécution robot" et
  "validation opérationnelle".
- Confirmation production : `OPERATIONAL_RULES.md` exige la chaîne exacte
  `CONFIRME_PRODUCTION` — d'autres fichiers évoquent la nécessité d'une
  confirmation textuelle sans préciser la chaîne. Mettre à jour tous les
  templates PR et notes opérationnelles pour indiquer la phrase exacte.
- Artefacts & formats : usage de `artifacts/live_trading/` (NDJSON) est
  mentionné à plusieurs endroits. Standardiser le format et le schéma (champs
  obligatoires : `timestamp`, `ticket`, `symbol`, `action`, `result`,
  `retcode`, `comment`) et lister dans un fichier de spec si nécessaire.
- READMEs multiples : `README.md`, `README_NEW.md`, `README_OLD.md` contiennent
  du contenu redondant et parfois divergent → proposer fusion/archivage.

Fichiers ciblés (exemples de lignes problématiques)
- `DEPLOYMENT_RULES.md` : mentionne `ALLOW_LIVE_SEND` / `auto_approve` (corrigé)
- `README.md` : exemple PowerShell initial qui indiquait exécution depuis racine
  (ajout d'un caveat dev/prod).
- `README_NEW.md` : MTF list contradictoire (corrigé).
- `PATCH_NOTES/*`, `PR_*` : évoquent confirmation/ gating sans la chaîne exacte.
- `CHANGELOG.md`, `README_OLD.md` : références à `MT5_FTMO_IA` / chemins legacy.

Actions recommandées (ordre proposé)
1. Valider les noms de variables d'environnement canoniques dans le code
   (rechercher occurrences dans le codebase) et ajouter un petit shim pour
   compatibilité si des noms legacy sont encore lus par des scripts.
2. Choisir une stratégie de déploiement claire (exécution depuis la racine vs
   `MT5_FTMO_IA`) et l'appliquer : mise à jour des docs + checks CI.
3. Consolider les README (fusionner `README_NEW.md` → `README.md`, archiver
   `README_OLD.md`) dans une PR dédiée. Ajouter un sommaire racine clair.
4. Normaliser la déclaration de la confirmation `CONFIRME_PRODUCTION` dans tous
   les templates PR et notes opérationnelles.
5. Créer `docs/artefacts_spec.md` (ou section dans `OPERATIONAL_RULES.md`) qui
   décrit précisément le format NDJSON/JSON attendu pour `artifacts/live_trading/`.

Notes complémentaires
- Certaines incohérences détectées sont déjà corrigées (ex: MTF dans
  `README_NEW.md`, et variables dans `DEPLOYMENT_RULES.md`), mais il reste
  d'autres occurrences dans des fichiers de notes, changelogs et templates.
- Si vous le souhaitez, je peux préparer une PR automatique qui :
  - recherche et remplace les occurrences legacy de variables d'env,
  - propose la fusion des README (draft),
  - ajoute `docs/artefacts_spec.md` avec le petit schéma NDJSON.

---
Généré par l'agent — revue humaine recommandée avant merge.
