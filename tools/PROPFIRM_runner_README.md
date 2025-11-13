## PROPFIRM runner: installation et supervision (guide rapide)

But: ces étapes peuvent activer de véritables envois MT5. Exécutez-les uniquement si vous comprenez les risques.

1) Préparer les variables d'environnement persistantes

   - Ouvrir PowerShell en mode Administrateur
   - Revue rapide (dry-run):

     pwsh -NoProfile -Command "& './tools/set_production_env.ps1' -WhatIf"

   - Pour appliquer les variables MACHINE (persistantes):

     pwsh -NoProfile -Command "& './tools/set_production_env.ps1'"

   Remarque: après avoir appliqué, il peut être nécessaire de redémarrer les services/processus
   ou de se déconnecter/reconnecter pour que les nouveaux variables soient visibles.

2) Installer les Scheduled Tasks (ne lance pas automatiquement le runner en mode LIVE par défaut)

   - Dry-run (voir les commandes qui seront créées):

     pwsh -NoProfile -Command "& './tools/install_production_schtask.ps1' -WhatIf"

   - Créer les tâches (par défaut la tâche runner NE passera PAS le token live):

     Start PowerShell as Administrator, then:
     pwsh -NoProfile -Command "& './tools/install_production_schtask.ps1'"

   - Si vous comprenez les conséquences et voulez que la tâche démarre en mode LIVE
     automatique (très sensible):

     pwsh -NoProfile -Command "& './tools/install_production_schtask.ps1' -EnableLive"

3) Comportement du monitor

   - La tâche `PROPFIRM_LiveRunner-Monitor` (créée par le script) vérifie toutes les 5 minutes
     si le runner détaché est actif et le relance (en mode DRY-RUN par défaut). Utilisez
     `-StartIfMissing` and `-EnableLive` si vous voulez qu'il redémarre automatiquement en live.

4) Sécurité et rollbacks

   - Contrôles supplémentaires disponibles dans le repo:
     - `control\apply_live.confirm` et `control\apply_live.auto.confirm` (fichiers de confirmation)
     - `control\disable_trading` ou `control\emergency_stop` pour bloquer immédiatement

   - Pour stopper toutes activités: créer un fichier `control\disable_trading` sur la machine.

5) Vérification

   - Après installation, vérifiez dans le Task Scheduler (Taskschd.msc) que les tâches existent.
   - Vérifiez les logs dans `artifacts\live_trading` (monitor_*.log, production_detached_*.pid, *.out.log)

6) Note sur les privilèges

   - La création de tâches planifiées système et l'écriture en scope MACHINE nécessitent des droits
     Administrateur. Ne lancez ces scripts que si vous êtes certain de vouloir opérer la machine.
