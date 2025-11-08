Sécurisation des identifiants MT5

Contexte
--------
Le fichier `config/mt5_credentials.env` contient des identifiants sensibles. Il est actuellement présent dans le dépôt. Le meilleur procédé professionnel est :

1. Retirer immédiatement le fichier du dépôt (git history si nécessaire),
2. Le stocker dans un coffre à secrets (Azure Key Vault, HashiCorp Vault, AWS Secrets Manager, ou au minimum dans des variables d'environnement CI/CD),
3. Faire une rotation des clés / mots de passe / tokens après suppression du fichier du dépôt.

Actions recommandées (PowerShell / Git)
-------------------------------------
# 1) Supprimer le fichier du suivi Git (safe, conserve le fichier localement)
git rm --cached config/mt5_credentials.env
git commit -m "chore(secrets): remove mt5 credentials from repository; add to .gitignore"

# 2) Ajouter à .gitignore (si pas déjà présent)
# (Le pattern `*.env` est déjà présent dans .gitignore du repo, mais on ajoute
# un message et on peut ajouter une entrée explicite si vous préférez.)
echo "config/mt5_credentials.env" >> .gitignore
git add .gitignore
git commit -m "chore: add config/mt5_credentials.env to .gitignore"

# 3) Pousser les changements
git push

# 4) Rotation des credentials
# Connectez-vous au panneau d'administration du broker et générez de nouvelles
# informations d'accès. Ensuite, mettez-les dans un coffre à secrets ou en
# variables d'environnement sur la machine de production. Ne les stockez pas
# en clair dans le dépôt.

Notes sur réécriture d'historique (optionnel, risqué)
----------------------------------------------------
Si vous devez purger le secret de tout l'historique Git (pour éviter fuite),
utilisez `git filter-repo` (recommandé) ou `git filter-branch` (moins
recommandé). Ces opérations réécrivent l'historique et nécessitent coordination
avec tous les contributeurs.

Exemple avec git filter-repo :

pip install git-filter-repo
python -m git_filter_repo --invert-paths --paths config/mt5_credentials.env

Après réécriture : force-push de la branche et prévenir l'équipe.

Aide
----
Si vous voulez, je peux préparer le commit `git rm --cached` + `.gitignore` et
un message de PR prêt à être revu, mais je ne supprimerai pas le fichier du
référentiel ni ne réécrirai l'historique sans votre confirmation explicite.
