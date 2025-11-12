# PROPFIRM Monitor Parse & Enrich — Import Task

Ce fichier explique comment importer et configurer la tâche planifiée Windows fournie :
`tools/PROPFIRM_MonitorParseEnrich_task.xml`.

But: the task exécute le script Python :
`tools/monitor_parse_and_enrich.py` (cadence 15 minutes).

Pré-requis
- Windows 10/11
- Python 3.x installé. Le script utilisera l'exécutable défini dans la variable d'environnement `$env:PYTHON` si elle existe, sinon il tentera d'utiliser `python` disponible via le PATH (ou votre virtualenv).
- Accès en lecture/écriture au dossier du projet (pour écrire dans `artifacts/live_trading/`).
- Si vous voulez que la tâche tourne même lorsque vous n'êtes pas connecté : il faudra fournir le mot de passe du compte lors de l'import.

Étapes d'import (UI)
1. Ouvrir le Planificateur de tâches (Task Scheduler).
2. Action → Import Task...
3. Choisir le fichier : `tools/PROPFIRM_MonitorParseEnrich_task.xml`.
4. Pendant l'import, l'UI vous proposera de sélectionner le compte sous lequel la tâche s'exécutera.
   - Recommandation professionnelle : choisir le compte de service / votre compte utilisateur et cocher "Run whether user is logged on or not".
   - L'UI demandera le mot de passe si vous choisissez cette option. Entrez le mot de passe du compte.
5. Vérifier dans l'onglet Actions que le chemin Python et les arguments correspondent à votre installation.
6. Vérifier l'onglet Triggers : la tâche est paramétrée pour répéter toutes les 15 minutes pendant 365 jours (P365D). Vous pouvez ajuster la durée ou la boundary de départ (Start time) dans l'UI.
7. Tester la tâche : sélectionner la tâche puis Run (Exécuter). Vérifier que le script démarre et consulter `artifacts/live_trading/` pour nouveaux fichiers.

Remarques importantes
- L'option "Run whether user is logged on or not" est cochée par défaut dans le XML fourni (LogonType=Password). Si vous importez sans fournir de mot de passe l'UI peut refuser l'opération ou basculer sur un autre type de logon (InteractiveToken).
- Si votre Python est dans un autre dossier (par ex. environnement virtuel), modifiez l'action (Command) dans l'UI pour pointer vers l'exécutable python adéquat.
- La tâche est conçue pour exécuter uniquement le parseur+enrichisseur (lecture/journalisation). Elle ne lance aucune opération de trading.

Dépannage rapide
- Si la tâche ne démarre pas : vérifiez l'historique (Task Scheduler -> View History) et consultez les logs stdout/stderr du terminal si la tâche est configurée pour écrire dans un fichier.
- Permissions : si l'import échoue en UI, essayez d'ouvrir Task Scheduler en tant qu'administrateur (clic droit -> Run as Administrator) puis importer le XML.

Fichiers concernés
- `tools/monitor_parse_and_enrich.py` — le script exécuté périodiquement
- `tools/PROPFIRM_MonitorParseEnrich_task.xml` — XML d'import
- `tools/PROPFIRM_MonitorParseEnrich_README.md` — ce fichier

Si vous voulez, je peux :
- générer une variante du XML qui exécute Python depuis un virtualenv (si vous me fournissez le chemin),
- ou fournir un petit script PowerShell d'export/import automatique qui vous demandera le mot de passe et importera la tâche (requiert l'exécution en mode Admin).
