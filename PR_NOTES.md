# PR notes: cleanup candidates

Cette PR contient un petit résumé des artefacts d'analyse non-destructifs générés localement.

Artefacts (non committés en raison de leur taille):
- tmp/cleanup_candidates.csv (full list)
- tmp/cleanup_candidates.ndjson (full list)
- tmp/cleanup_candidates_preview_final.csv (Top 50 preview)
- tmp/tools_duplication_log.json
- tools_dup/ (duplicate of tools/ for review)

But: j'ai évité de committer les gros fichiers. Pour reproduire localement, exécutez :

    python tmp/generate_cleanup_candidates.py

ou ouvrez `tmp/cleanup_candidates_preview_final.csv` pour un aperçu rapide.

Merci de reviewer; la PR est en brouillon et contient un lien vers les artefacts locaux.
