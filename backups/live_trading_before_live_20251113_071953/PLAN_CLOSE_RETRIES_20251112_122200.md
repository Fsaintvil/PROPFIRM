# Plan d'actions ciblées — fermeture des positions (préparation sans exécution)

Généré: 2025-11-12T12:22:00Z

But: rapprocher les tickets détectés ouverts par `mt5.positions_get()` et les tentatives précédentes présentes dans `close_all_positions_result_manual.json`, puis proposer un plan de retries sûrs et ciblés. Aucun ordre réel ne sera envoyé sans confirmation explicite.

1) Résumé rapide
- Positions ouvertes détectées via l'API MT5: 25
- Tentatives de fermeture manuelle (dossier `closed`) recensées: 51 entrées (toutes avec retcode 10013 dans le fichier analysé)
- Discrépance: `close_all_positions_result_manual.json` signale `remaining_positions: 0` mais la vérification directe renvoie 25 positions ouvertes. Il y a donc un écart de contexte ou d'exécution.

2) Méthodologie utilisée
- Lecture de `artifacts/live_trading/close_all_positions_result_manual.json` (tentatives enregistrées).
- Lecture de `artifacts/live_trading/monitor/MT5_POSITIONS_CHECK_20251112_122009.json` (positions live via mt5.positions_get()).
- Pour chaque ticket retourné par MT5, je vérifie si ce ticket apparaît dans les tentatives précédentes et si oui, je copie le retcode/commentaire.

3) Résultats (extraits)
- Tickets ouverts (25): 346764084 (USDCAD), 346770863 (USDCAD), 346770879 (AUDNZD), 346772453 (USDCAD), 346772456 (AUDNZD), 346777196 (USDCAD), 346777200 (AUDNZD), 346779928 (USDCAD), 346779934 (AUDNZD), 346779939 (GBPCHF), 346782400 (USDCAD), 346782409 (AUDNZD), 346782424 (EURUSD), 346788158 (USDCAD), 346788192 (EURUSD), 346791844 (USDCAD), 346791880 (EURAUD), 346795193 (USDCAD), 346798891 (USDCAD), 346798923 (EURAUD), 346800636 (GBPCHF), 346807892 (GBPCHF), 346816237 (USDCAD), 346816241 (EURJPY), 346816265 (EURAUD).
- Tickets qui ont des tentatives précédentes dans `close_all_positions_result_manual.json` : (liste dans `plan_close_retries_20251112_122200.json` sous `closed_attempts_map`). Note: la plupart des tickets dans le fichier `closed` sont différents des tickets actuellement ouverts — cela suggère que la tentative manuelle ciblait d'autres tickets.

4) Recommandation avant toute action d'écriture (PRÉ-REQUIS SAFETY)
- Confirmer compte/credentials utilisés lors de la tentative `tmp_close_all_direct.py`. Si différent, identifier quel compte a été visé et quelle action réelle a lieu sur le compte courant.
- Laisser `control/disable_trading` en place jusqu'à autorisation explicite.
- Pour chaque ticket à fermer, exécuter d'abord des checks en lecture seule :
  - `mt5.account_info()` et `mt5.account_info().login` pour vérifier le compte
  - `mt5.symbol_info(symbol)` and check `trade_mode`, `tick_size`, `volume_min`, `volume_step`.
  - Vérifier que le ticket est bien présent via `mt5.positions_get(ticket=...)` et récupérer `volume`.

5) Plan de retries ciblés (par ticket)
- Pour chaque ticket listé ci-dessus (25 tickets), plan proposé :
  1. Pré-checks (lecture seule) : account, symbol info, position details.
  2. Si pré-check OK, envoyer un ordre de type `close` (market) pour la même `volume` (0.01) côté inverse — paramétrage minimal (sl/tp=0) pour fermer rapidement.
  3. Enregistrer retcode et deal/order retournés. Si retcode == 10013 => ne pas retry automatiquement; plutôt :
     - vérifier format `symbol` (ex. `US500.cash` vs `US500`), lot/volume constraints, et permissions.
     - demander intervention manuelle si le broker rejette systématiquement.
  4. Retry policy : up to 5 attempts with exponential backoff (1s, 2s, 4s, 8s, 16s). Stop on success (deal>0 or order>0) or on a definitive broker error.

6) Exemple d'entrée du plan (ticket 346764084)
- ticket: 346764084
- symbol: USDCAD
- volume: 0.01
- price_open: 1.40193
- recommended steps: pré-checks -> send close market order -> log retcode -> backoff retries

7) Actions que je peux faire maintenant (demandez par lettre)
A1) Générer et exécuter le script de pré-checks (lecture seule) pour tous les tickets et poster un rapport détaillé (sans envoyer d'ordres). (recommandé avant toute écriture)
A2) Exécuter les closes ciblés automatiquement (réell envoi) selon la politique de retries ci-dessus — REQUIERT CONFIRMATION EXPLICITE.
A3) Rapproche manuelle/visuelle : vous vérifiez sur le terminal MT5 et confirmez quelle action prendre.

8) Fichiers produits
- `artifacts/live_trading/plan_close_retries_20251112_122200.json` (détails machine‑readable)
- `artifacts/live_trading/PLAN_CLOSE_RETRIES_20251112_122200.md` (ce fichier lisible)

---

Je peux lancer l'option A1 (pré-checks lecture seule) maintenant et produire le rapport détaillé par ticket (very small, fast API calls). Voulez‑vous que je le lance ?

Répondez oui pour A1 ou dites si vous préférez une autre action (A2/A3).