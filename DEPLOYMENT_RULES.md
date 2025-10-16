# Règles de déploiement

Ce fichier formalise la règle opérationnelle :

- Toujours déployer et exécuter le projet depuis `MT5_FTMO_IA`.
- Ne jamais exécuter d'installation ou lancer les scripts depuis la racine
  `PROPFIRM` ou en mélangeant des fichiers entre `PROPFIRM` et
  `MT5_FTMO_IA`.

Pourquoi ?
- Prévention des erreurs d'import et des chemins relatifs cassés.
- Préservation des garde‑fous de sécurité (`control/kill_switch`,
  `ALLOW_LIVE_SEND`, `auto_approve`).
- Cohérence des environnements virtuels et dépendances.

Commandes recommandées (PowerShell) :

```powershell
Set-Location -LiteralPath 'C:\Users\saint\Documents\PROPFIRM\MT5_FTMO_IA'
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
```

Si tu veux, on peut ajouter une CI check qui échoue si une job essaie de
déployer depuis la racine du dépôt.
