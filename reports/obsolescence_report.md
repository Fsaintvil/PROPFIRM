# Rapport d'obsolescence - Projet_Trading_IA

Généré automatiquement - 13 octobre 2025

Ce rapport liste les fichiers et dossiers candidats à l'archivage ou suppression. Il propose une recommandation par entrée. Revue manuelle recommandée avant suppression.

## Règles d'inclusion
- Fichiers `tmp_*`, `_tmp*` : utilitaires temporaires
- Scripts préfixés par `_` : variantes, outils manuels, examples
- Environnements virtuels (`.venv`, `.venv_mt5`) : ne doivent pas être dans le repo
- Dossiers `archive/`, `patches/`, `notebooks/` : vérifier contenu (archiver hors repo si volumineux)
- Fichiers `.bak`, `.tmp`, sauvegardes locales

---

## 1) Environnements virtuels (supprimer du dépôt)
- `.venv/`  (contenant packages locaux)
- `.venv_mt5/` (env contenant tentatives d'installation MT5)

Recommandation: supprimer du repo (git rm -r) et ajouter au `.gitignore` si nécessaire. Conserver `requirements*.txt` pour reproduire.

---

## 2) Scripts temporaires `scripts/tmp_*.py`
- `scripts/tmp_inspect_module.py`
- `scripts/tmp_inspect_control.py`
- `scripts/tmp_import_test.py`

Recommandation: déplacer vers `tools/` ou `archive/tools/` si utiles; sinon supprimer.

---

## 3) Scripts temporaires préfixés `_tmp*`
- `scripts/_tmp_force_with_levels.py`
- `scripts/_tmp_force_trade.py`
- `scripts/_tmp_force_trade_btc.py`

Recommandation: souvent tests ponctuels, déplacer ou archiver.

---

## 4) Scripts préfixés par `_` (variantes, exemples, potentiellement obsolètes)
 (Extraits — revoir individuellement)
- `scripts/_send_three_live_orders.py`
<!-- script papier supprimé dans la politique 100% live -->
- `scripts/_send_three_with_mt5_ticks.py`
- `scripts/_send_real_order.py`
- `scripts/_send_multiple_orders.py`
- `scripts/_one_shot_real_with_stops.py`
- `scripts/_paper_force_test.py`
- `scripts/_prepare_real_order.py`
- `scripts/_run_demo_invoker.py`
- `scripts/_resend_initial_orders.py`
- `scripts/_read_status.py`
- `scripts/_print_symbol_info.py`
- `scripts/_send_three_with_mt5_ticks.py`
- `scripts/_live_execute_best.py`

Recommandation: classer en 3 groupes:
- Keep: `*_live_execute_best.py`, `guarded` scripts utilisés par CI or main. Note: `_dry_run_live.py` supprimé (100% live).
- Archive: variants redondants / anciennes façons de faire -> `archive/scripts/`.
- Delete: scripts stale/duplicates.

---

## 5) Dossiers volumineux / archives
- `archive/` — vérifier et déplacer hors repo si volumineux
- `patches/` — peut contenir correctifs historiques; archiver dans documentation si besoin
- `models/` — conserver modèles nécessaires; si gros, mettre dans artefact store
- `notebooks/` — si notebooks de prototypage, déplacer vers `docs/` ou `archive/` si non utilisés

Recommandation: audit manuel et conservation minimale dans repo (ex: README + pointer vers artifacts storage)

---

## 6) Fichiers PowerShell & demo
- `install.ps1`, `run_demo.ps1`, `Fix_ProFirm.ps1`, `run_demo.ps1`

Recommandation: vérifier s'ils sont encore valides et documentés. Si non, archiver.

---

## 7) Fichiers suspects / à revoir
- `tradingview_ta.py`, `tvdatafeed.py`, `websocket.py` — implémentations expérimentales d'intégrations; vérifier si utilisées.
- `diag_import_from_root.py` — utilitaire local de diagnostic.

---

## Prochaines étapes proposées (choix)
1. Générer un rapport détaillé par fichier (taille, dernière modif, usages par grep) — je peux le produire.
2. Créer un répertoire `archive/obsolete/` et y déplacer automatiquement les fichiers listés après confirmation.
3. Nettoyer `.venv` et `.venv_mt5` du repo (git rm -r) immédiatement si vous confirmez.

Dites quelle option vous préférez (1=rapport détaillé, 2=archivage automatique, 3=supprimer les env virtuels, 4=annuler).