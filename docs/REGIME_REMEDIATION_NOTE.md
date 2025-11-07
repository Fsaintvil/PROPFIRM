Note technique — Remédiation ingestion (à envoyer à l'équipe ingestion)
======================================================================

Contexte
--------
Lors d'un run de staging sur le détecteur de régimes, nous avons observé de fortes anomalies dans les rendements produits en amont. Exemples tirés des diagnostics :

- `raw_validation_report` (extrait récent) :
  - n: 6600
  - n_na: 0
  - abs_gt_max: 4420
  - max_abs: 20454.749098767836
  - reason: "many_extreme_returns"

Autre dump plus ancien : `regime_input_validation.json` indiquait `max_abs` ~ 6934.0431 et `pct_99` très élevé.

Problème
--------
Ces valeurs indiquent des prix/rendements manifestement corrompus (sauts extrêmes, valeurs aberrantes). Entraîner un HMM ou d'autres modèles sur ces données provoque des modèles instables et décisions erronées.

Objectif
--------
Fournir des règles de validation simples côté ingestion pour éviter d'écrire dans le store/feature layer des séries trop corrompues.

Règles de validation recommandées
---------------------------------
1. Prix non-nuls et positifs
   - Reject si price <= 0 ou NaN.
2. Pas de saut de prix > X% intraperiod (X configurable, ex. 5000% pour attraper gross errors)
   - Example: if abs((p_t - p_{t-1})/p_{t-1}) > 50 then flag (50 correspond à 5000%). Ajuster selon univers.
3. Plausibilité des rendements (post-calcul): plafond brut
   - If abs(return) > 50 (i.e., > 5000%) => reject or quarantine
4. Pourcentage NA sur fenêtre glissante
   - Si > 20% NA dans fenêtre N → alerter et ne pas publier
5. Dédoublonnage & timestamps
   - Reject duplicate timestamps or non-monotonic timestamps
6. Tests de continuité
   - Vérifier qu'il n'y a pas de gaps massifs sur séries où cela n'est pas attendu
7. Log & quarantine
   - Écrire un message structuré JSON indiquant la raison et la portion de la série affectée. Stocker pour triage.

Snippet Python minimal (à intégrer côté ingestion)
---------------------------------------------------
```python
def validate_price_series(prices: pd.Series, max_return=50.0, max_na_pct=0.2):
    if prices.isna().all():
        return False, "all_missing"
    if (prices <= 0).any():
        return False, "non_positive_price"
    rets = prices.pct_change().abs()
    if (rets > max_return).any():
        return False, "extreme_return"
    if prices.isna().mean() > max_na_pct:
        return False, "too_many_missing"
    # More checks: duplicates, timestamp monotonicity, etc.
    return True, "ok"
```

Actions immédiates proposées
----------------------------
- Ajouter la validation ci-dessus dans le pipeline d'ingestion avant écriture du feature store.
- Pour les séries rejetées : écrire un dump JSON (timestamp, symbol, reason, sample head/tail) et créer une alerte ticket automatique.
- Ajouter tests automatisés qui simulent cas extrêmes (sauts, zeros, valeurs négatives).

Exemples d'entrées problématiques (pièces jointes)
-------------------------------------------------
Joindre les fichiers : `artifacts/diagnostics/last_validation_raw_*.json` et `artifacts/diagnostics/regime_input_validation.json` pour que l'équipe puisse reproduire localement.

Conclusion
----------
Ces règles sont conservatrices et conçues pour attraper les corruptions grossières rapidement. Une fois les incidents corrigés, vous pouvez adapter/affiner les seuils. Si vous voulez, je prépare un patch de validation à intégrer dans le composant ingestion (PR) et quelques tests unitaires pour le pipeline.