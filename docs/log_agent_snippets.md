---
title: "Snippets configuration agents de logs — détection régimes"
---

Ce document fournit des extraits prêts à coller pour Promtail (Loki), Fluentd et Logstash afin de parser la ligne JSON compacte émise par le détecteur de régimes lorsque la validation RAW échoue :

Événement émis (one-line JSON, opt-in via `REGIME_VALIDATE_DUMP=1`):

```json
{"evt":"regime_validation_raw_fail","timestamp_utc":"20251107T194856Z","reason":"many_extreme_returns","abs_gt_max":4420}
```

Champs utiles : `evt`, `timestamp_utc`, `reason`, `abs_gt_max`.

Promtail (Loki) - pipeline scrape_config / pipeline_stages

Coller ce bloc dans la configuration de votre job Promtail (ou dans les `pipeline_stages` d'un `scrape_config`) :

```yaml
pipeline_stages:
  - json:
      expressions:
        evt: evt
        timestamp_utc: timestamp_utc
        reason: reason
        abs_gt_max: abs_gt_max
  - labels:
      evt: "${evt}"
      reason: "${reason}"
  - timestamp:
      source: timestamp_utc
      format: "20060102T150405Z"
```

Notes Promtail:
- Le stage `json` extrait les champs JSON encodés sur une ligne. Si la ligne contient d'autres données, adaptez avec `regex` en amont.
- On crée des labels `evt` et `reason` pour faciliter les requêtes et alertes sur Loki.

Exemple de requête Loki pour repérer les échecs de validation RAW (dernières 5m) :

```logql
{app="market_regime_detector", evt="regime_validation_raw_fail"} |= "regime_validation_raw_fail" | json | unwrap abs_gt_max
```

Exemple d'alerte Grafana/Loki (pseudo) :
- Condition : count_over_time({app="market_regime_detector", evt="regime_validation_raw_fail"}[5m]) > 0

Fluentd - Parser + filter

Extrait Fluentd `td-agent.conf` (parser JSON inline puis route) :

```xml
<source>
  @type tail
  path /var/log/market/market_regime_detector.log
  pos_file /var/log/td-agent/market_regime_detector.pos
  tag market.regime
  <parse>
    @type json
  </parse>
</source>

<filter market.regime>
  @type record_transformer
  enable_ruby true
  <record>
    evt ${record["evt"]}
    reason ${record["reason"]}
    abs_gt_max ${record["abs_gt_max"]}
  </record>
</filter>

<match market.regime>
  @type forward
  # ... votre destination (Elasticsearch, Loki, etc.)
</match>
```

Notes Fluentd:
- Utilisez le `@type json` parser si vos lignes sont du JSON pur. Si les lignes contiennent du texte préfixe, utilisez `@type multiline` ou un `format` adapté.

Logstash - pipeline

Exemple pipeline Logstash (pipeline.yml / config) :

```conf
input {
  file {
    path => "/var/log/market/market_regime_detector.log"
    start_position => "beginning"
    sincedb_path => "/var/lib/logstash/market_regime_detector.sincedb"
  }
}

filter {
  json {
    source => "message"
    target => "_json"
    remove_field => ["message"]
  }

  mutate {
    add_field => { "evt" => "%{[_json][evt]}" }
    add_field => { "reason" => "%{[_json][reason]}" }
    add_field => { "abs_gt_max" => "%{[_json][abs_gt_max]}" }
  }
}

output {
  elasticsearch { hosts => ["http://es:9200"] index => "logs-market-regime-%{+YYYY.MM.dd}" }
  # ou stdout pour debug
}
```

Alerting / bonnes pratiques

- Créez un log-based metric (ou un counter) sur les occurrences de `evt=="regime_validation_raw_fail"`.
- Exemple de seuils initiaux (à ajuster avec Ops/ingestion) :
  - Canary : alerte si count > 0 sur 30 minutes.
  - Production : alerte si count > 10 en 1h OU si abs_gt_max > 1000 (indique données très corrompues).

Exemple de requête Loki + alert (Grafana) pour déclencher si un événement survient sur canary :

```
expr: count_over_time({app="market_regime_detector", evt="regime_validation_raw_fail"}[30m]) > 0
```

Remarques opérationnelles

- Le script émet la ligne JSON uniquement si `REGIME_VALIDATE_DUMP=1` (opt-in). Assurez-vous d'activer cette variable seulement pour canary ou lors d'une période d'investigation.
- Les dumps complets (fichiers `last_validation_raw_<ts>.json`) sont écrits sous `artifacts/diagnostics/` ; configurez votre agent de collecte pour récupérer ces fichiers si nécessaire (SFTP/objet storage).
- Documentez avec Ops la procédure de rollback (désactiver `REGIME_VALIDATE_DUMP` et `REGIME_SAFE_CLEAN`) en cas de faux-positifs excessifs.

Si vous voulez, je peux :
- Ajouter des exemples prêts à l'emploi pour Prometheus Alertmanager ou un playbook d'escalation (email/SMS). Dites-moi lequel vous préférez.

Fin du document.
