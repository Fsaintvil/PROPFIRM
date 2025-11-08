# PROPFIRM — Guide opérationnel central

Ce dépôt contient plusieurs scripts et outils pour l'exécution et la
supervision d'une stratégie de trading live via MetaTrader5. Ce document
donne les points opérationnels essentiels et renvoie vers la doc
opérationnelle canonique `OPERATIONAL_RULES.md`.

Important — règle de sécurité
- Avant tout envoi réel vers le broker, vérifiez et suivez les règles dans
	`OPERATIONAL_RULES.md`.

Fichiers clefs
- `OPERATIONAL_RULES.md` : règles opérationnelles centralisées (autorisation
	runtime, cadence par symbole, fermeture automatique). Lire en priorité.
- `scripts/close_current_positions_verified.py` : script de fermeture safe
	(vérifie `order_check`, applique cadence, peut placer pending si demandé).
- `scripts/auto_retry_close.py` : boucle de réessai automatique (utilisée
	pour tenter la fermeture périodiquement tant que le marché est fermé).
- `scripts/monitor_auto_retry.py` : monitor léger qui lit
	`artifacts/live_trading/auto_retry_summary.json` et
	`close_after_diagnostics.json` et alerte en cas d'exécution acceptée.
- `src/utils/mt5_safe.py` : wrapper centralisé pour `order_send` (ajuste prix,
	SL/TP, arrondit volume, option pour appliquer la cadence).
- `src/utils/order_cadence.py` : utilitaire pour imposer 930s de cooldown par
	symbole et détecter les expositions âgées (>1800s).

Sécurité des credentials
- Ne commitez jamais `config/mt5_credentials.env`. Ce fichier est désormais
	listé dans `.gitignore`. Stockez les identifiants dans un coffre à secrets
	ou en variables d'environnement CI/CD.
- Voir `SECURE_CREDENTIALS_README.md` pour la procédure de suppression/rotation
	des credentials si nécessaire.

Surveillance (mode recommandé)
- Laisser `scripts/auto_retry_close.py` et `scripts/monitor_auto_retry.py`
	tourner pendant une période de maintenance/d'urgence. Le monitor alertera
	automatiquement si un envoi est accepté (`order_send.retcode == 100`) ou si
	toutes les positions sont fermées.

Commande rapide pour monitor (PowerShell)
```powershell
cd 'C:\Users\saint\Documents\PROPFIRM'
Start-Process -NoNewWindow -FilePath python -ArgumentList 'scripts/monitor_auto_retry.py' -PassThru
```

CONFIRMATION PRODUCTION
- Les envois en production ne doivent être autorisés qu'après confirmation
	explicite. Pour autoriser une tentative de fermeture en production,
	répondez exactement `CONFIRME_PRODUCTION` lorsque le processus de contrôle
	vous le demande.

Tests
- Tests unitaires ciblés pour la cadence : `tests/test_order_cadence.py`.
	Lancez `pytest -q tests/test_order_cadence.py` pour valider localement.

Support & actions supplémentaires
- Pour les actions sensibles (purges d'historique git, rotation de clés,
	suppression massive de fichiers), demandez une approbation explicite. Les
	scripts fournissent des comportements conservateurs (fail-open pour éviter
	blocage produit).

Pour toute question opérationnelle ou demande d'automatisation supplémentaire,
ouvrez une issue ou demandez une PR ciblée.
