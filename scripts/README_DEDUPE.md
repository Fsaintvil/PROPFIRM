# Déduplication des fichiers — guide d'utilisation

But
-----
Ce petit outil aide à contrôler la croissance incontrôlée du nombre de fichiers en repérant les fichiers dupliqués
dans le dépôt et en proposant des actions sûres : listing, remplacement par hardlinks (si possible), déplacement vers
un dossier d'archive ou suppression.

Fichiers créés
-------------
- `scripts/dedupe_files.ps1` : script principal (PowerShell, compatible pwsh).
- `.githooks/pre-commit.ps1` : exemple de hook git pour avertir avant commit de fichiers de backup.

Usage de base
------------
1. Ouvrez PowerShell (pwsh) à la racine du projet `C:\Users\saint\Documents\PROPFIRM`.
2. Lancer en mode lecture seule (rapport) :

```powershell
.\scripts\dedupe_files.ps1 -RootPath .
```

3. Pour appliquer une action (ex : création de hardlinks quand possible) :

```powershell
.\scripts\dedupe_files.ps1 -RootPath . -Apply -HardLink
```

4. Pour déplacer les doublons dans `archive/duplicates` :

```powershell
.\scripts\dedupe_files.ps1 -RootPath . -Apply -MoveToArchive "archive/duplicates"
```

Notes importantes
---------------
- Par défaut le script ne fait rien sur vos fichiers (mode "dry run"). Ajoutez `-Apply` pour exécuter les actions.
- Le remplacement par hardlink n'est possible que si les deux fichiers sont sur le même volume. Le script restaure
  en cas d'échec lors de la création du hardlink.
- Les fichiers et dossiers exclus par défaut : `.git`, `node_modules`, `__pycache__`, `env`, `venv`, `.venv`.
- Testez d'abord sur un sous-dossier avant d'appliquer au dépôt entier.

Exemple de hook git
-------------------
Un exemple (`.githooks/pre-commit.ps1`) est fourni ; pour l'activer vous pouvez soit :

1) Copier le script dans `.git/hooks/pre-commit` (Windows) :

```powershell
copy .githooks\pre-commit.ps1 .git\hooks\pre-commit
```

2) Ou définir `core.hooksPath` pour référencer `.githooks` :

```powershell
git config core.hooksPath .githooks
```

Suites possibles / améliorations
-------------------------------
- Ajouter une règle CI qui exécute `dedupe_files.ps1 -RootPath .` et échoue si des patterns interdits sont trouvés.
- Intégrer un outil plus avancé qui remplace les fichiers de backup par un mécanisme d'archivage rotatif (p.ex. garder N derniers backups).

Support
------
Si vous voulez que j'adapte le script pour :
- ignorer ou inclure d'autres répertoires spécifiques,
- rester strict sur certains patterns `.bak`, `*_backup_*`, etc.,
- ou automatiser une étape Git (hook strict) — dites-moi et je l'ajoute.
