# Règles opérationnelles centrales

Ce fichier centralise les règles opérationnelles obligatoires pour toutes les actions
sur ce dépôt. Il doit être consulté avant toute modification, déploiement ou action
opérationnelle.

## Principes généraux

- Toujours partir de la conversation opérationnelle en cours (la trace de décisions,
  diagnostics et corrections réalisées dans cette session). Cette conversation est la
  source de vérité pour la séance en cours.
- Toujours se référer aux README du dépôt avant d'agir. Les README sont des points
  d'entrée définitifs pour comprendre le contexte et la procédure.
- Ne pas créer de nouveaux scripts sans approbation explicite et justification
  documentée. Préférez modifier et étendre les scripts existants pour garder l'historique
  et éviter la duplication de logique.

## Règles techniques et opérationnelles spécifiques

- Backtests: effectuer systématiquement des backtests sur 7 ans. Exemples de pairs de
  référence: BTCUSD, ETHUSD, XAUUSD, USDCAD, AUDNZD, EURJPY, GBPCHF, NZDJPY, EURUSD, EURAUD,
  US500.cash, JP225.cash — appliquer le même horizon 7 ans à tous les symbols
  analysés lorsque possible.
- Multi-timeframe: analyses standard à exécuter sur les timeframe suivantes: M15, M30, H1,
  H4, D1. (Les outils et reports doivent intégrer ces granularités par défaut.)
- Risk/Reward: la cible minimale publique est définie à **1.5:3** (risk:reward) — toutes
  les stratégies doivent documenter la manière dont elles respectent ou ajustent cette cible.
- Modèles IA et validation: Toujours utiliser des modèles IA (lorsqu'applicables) et
  effectuer au moins un backtest 7 ans et/ou 7 indicateurs techniques et/ou 7 indicateurs
  institutionnels pour valider une stratégie avant mise en production.
- Amélioration continue: améliorer les modèles en live en utilisant les données live
  dès que possible — intégrer les métriques de production dans le pipeline de ré-entraînement.
- Données: utiliser en priorité toutes les données exploitables déjà présentes dans le
  dépôt ou les artefacts et enregistrer systématiquement les jeux de données produits
  pour accélérer les itérations futures.
- Outils: prioriser l'usage des commandes PowerShell pour les opérations et orchestration
  sur les environnements Windows; Python reste le langage d'implémentation mais les
  commandes opérationnelles favorisent PowerShell quand applicable.

## Procédures de changement

- Tout changement qui ajoute un nouveau script doit inclure dans le PR une justification
  claire et un lien vers la conversation ou ticket qui l'autorise.
- Les PRs modifiant des scripts existants doivent indiquer la conversation de référence
  dans la description (copier le lien ou l'ID de la session) et inclure des artefacts de
  test (résultats de backtest / logs / JSON d'exécution).

## Emplacement et usage

- Ce fichier est la source canonique des règles; ajoutez un lien vers `OPERATIONAL_RULES.md`
  dans tout README que vous modifiez.

---
Dernière mise à jour: 2025-11-08

## Règle opérationnelle additionnelle (obligatoire)

- Toujours travailler en mode "live" lorsqu'une connexion MT5 opérationnelle est disponible.
  Avant d'envoyer des ordres, vérifier que les variables d'environnement suivantes sont définies
  et valent '1' :

  - $env:ALLOW_MT5_SEND = '1'
  - $env:AUTO_APPLY = '1'
  - $env:AUTO_DEPLOY = '1'
  - $env:AUTO_LEARN = '1'
  - $env:AUTO_ADAPT = '1'
  - $env:AUTO_ENRICH = '1'

- Liste des symbols standards (utiliser prioritairement ces symbols pour les runs live) :
  BTCUSD, ETHUSD, XAUUSD, USDCAD, AUDNZD, EURJPY, GBPCHF, NZDJPY, EURUSD, EURAUD,
  US500.cash, JP225.cash

- Obligations analytiques minimales sur chaque stratégie live :
  - MTF convergence sur M15 (convergence multi-timeframe ciblée) ;
  - utiliser plusieurs indicateurs techniques et plusieurs indicateurs institutionnels ;
  - backtest sur 7 ans ;
  - tester/valider avec tous les modèles IA pertinents et sur l'historique live disponible
    depuis MT5 lorsque c'est possible.

- Prioriser l'usage de l'historique live et des artefacts existants pour accélérer les itérations
  (enregistrer systématiquement les jeux de données et les résultats de backtest).

## Règles temporelles et d'exposition (nouveau)

- Cadence d'envoi par symbole : pour éviter l'over-trading et les incohérences entre scripts,
  tout envoi automatique d'ordre doit respecter la cadence suivante : maximum 1 ordre par
  symbole toutes les 930 secondes (15 minutes et 30 secondes). Cette limite s'applique aux
  ordres d'ouverture envoyés automatiquement par les scripts/agents en production.

- Durée maximale d'exposition pour ordres automatiques : tout ordre ouvert automatiquement
  doit être clôturé automatiquement si ni le Stop Loss (SL) ni le Take Profit (TP) n'ont
  été atteints dans un délai de 30 minutes (1800 secondes) depuis l'exécution effective
  de l'ordre. Avant toute fermeture automatique, le script doit :
  - effectuer un `order_check` (préflight) pour s'assurer que la clôture est possible ;
  - utiliser la fonction centralisée sûre (`mt5_safe` / wrapper) pour exécuter la clôture ;
  - enregistrer un enregistrement NDJSON/JSON dans `artifacts/live_trading/` décrivant la
    raison et le résultat de la clôture automatique (timestamp, ticket, symbol, volume,
    prix d'ouverture, état SL/TP, retcode, commentaires).

- Exceptions et priorité : ces règles s'appliquent par défaut pour tous les symbols listés
  ci‑dessus. Toute dérogation temporaire doit faire l'objet d'une approbation documentée
  dans la conversation opérationnelle et d'un patch ou d'un PR faisant référence à cette
  conversation.

## Harmonisation et cohérence

- Toute règle ajoutée ci‑dessus doit être répercutée dans les README et dans les scripts
  qui implémentent la logique d'envoi/fermeture (ex : `scripts/auto_retry_close.py`,
  `scripts/close_current_positions_verified.py`, `src/utils/mt5_safe.py`). Les points
  suivants doivent être vérifiés et harmonisés :
  - intervalle d'envoi par symbole (930s) implémenté et testé ;
  - timeout d'exposition (30 minutes) implémenté avec journalisation ;
  - les variables d'environnement obligatoires (voir section obligatoire ci‑dessous) sont
    contrôlées et exigées avant tout envoi effectif ;
  - les artefacts de sortie (NDJSON/JSON) sont normalisés (champ `timestamp`, `ticket`,
    `symbol`, `action`, `result`, `retcode`, `comment`).


Ces contrôles sont obligatoires avant toute opération d'envoi automatique d'ordres en live.
