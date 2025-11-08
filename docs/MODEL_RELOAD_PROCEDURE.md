# Procédure sûre pour tester le reload de modèles LightGBM

Objectif
- Fournir une procédure reproductible, rollback-safe, pour tester si la production
  charge `best_lightgbm.txt` (pointer) plutôt que `best_lightgbm_large.txt`.

Principes
- Ne pas interrompre MT5 sans fenêtre d'arrêt prévue.
- Toujours sauvegarder avant modification.
- Préparer un rollback simple et testé.

Étapes (préparation, non exécutées automatiquement)

1) Dry-run et revue
   - Examiner `control/active_model.txt` et les logs (`logs/live_trading_*.log`, `logs/trading_engine.log`).
   - Vérifier que la tâche de surveillance (`watch_active_model.py`) est installée ou prête.

2) Préparer le backup + disable (script préparé)
   - Script : `scripts/ops/prepare_reload_safe.ps1`
   - Dry-run :
       pwsh -NoProfile -File .\scripts\ops\prepare_reload_safe.ps1
   - Exécution (après revue) :
       pwsh -NoProfile -File .\scripts\ops\prepare_reload_safe.ps1 -Execute

   Ce script :
   - copie `best_lightgbm_large.txt` dans `backups/model_reload_<ts>/`
   - renomme `best_lightgbm_large.txt` en `best_lightgbm_large.txt.disabled`
   - NE redémarre PAS les services.

3) Redémarrage contrôlé (manuellement)
   - Redémarrer uniquement la composante AI si le système le permet, sinon redémarrer la production
     pendant une fenêtre de faible charge. Exemple : utiliser `start_production.py` avec les flags
     appropriés et supervision.

4) Vérification immédiate après redémarrage
   - Vérifier `control/active_model.txt` (devrait être écrit) : contient le chemin du modèle chargé.
   - Chercher dans les logs la ligne "📥 Modèle LightGBM chargé: ..." pour confirmer.
   - Vérifier que MT5 est toujours connecté et que aucun ordre inattendu n'a été émis.

5) Rollback (si besoin)
   - Restaurer le fichier depuis `backups/model_reload_<ts>/best_lightgbm_large.txt` vers `artifacts/auto_improve/`.
   - Redémarrer la composante AI (ou la production) pour remettre le comportement précédent.

Notes et précautions
- Toujours tester la procédure sur un système de pré-prod ou instance locale avant de l'appliquer en production.
- Ne pas exécuter les étapes d'exécution (`-Execute`) sans une fenêtre de maintenance si vous n'acceptez pas d'éventuelles interruptions.
