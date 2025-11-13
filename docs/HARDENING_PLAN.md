# Plan de hardening pour la production PROPFIRM

Objectif: sécuriser, monitorer, tester et rendre reproductible le runner de production
afin d'atteindre une exécution 100% live, automatique et autonome avec garanties
de sécurité et observabilité.

Priorités
- Safety / Kill-switches (automatique + alerting)
- Observabilité (metrics + logs + dashboards)
- Robustesse retry / broker rules (symbol constraints)
- Tests (unitaires & intégration légère)
- CI / déploiement / tâches planifiées

Livrables (PR dans `feature/hardening-prod-20251113`)
- `tools/healthcheck.py` : vérifie l'accessibilité MT5, variables env et kill-switch
- `tools/generate_symbol_constraints.py` : déjà ajouté, produit `symbol_constraints.json`
- `tools/metrics_exporter.py` : exporter Prometheus (light)
- `tests/test_sl_tp.py` : tests unitaires pour compute_sl_tp
- `.github/workflows/ci.yml` : pipeline CI qui exécute lint, tests et checks de sécurité

Étapes recommandées
1. Merge branch et exécuter CI.
2. Installer `prometheus_client` sur l'hôte de production et activer METRICS_ENABLE=1.
3. Ajouter dashboards Grafana et alert rules (ex: alerts when consecutive 10016 > 5).
4. Automatiser backup & snapshots sur start (déjà partiellement présent).
5. Ajouter un job de reconciliation (daily) pour purger tickets stale.

Notes opérationnelles
- Valider `production_env.ps1` avant tout démarrage automatique.
- Utiliser un compte de service pour ScheduledTask avec droits minimaux.
