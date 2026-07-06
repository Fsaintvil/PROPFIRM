# MT5 FTMO MOM20x3 — Robot de Trading Autonome

Robot de trading multi-symboles (27 actifs) pour challenges FTMO, basé sur la stratégie **MOM20x3** (Momentum 20 périodes × 3 filtres).

> **Statut** : 🟢 Production — Tourne 24/7 sur 27 symboles
> **Documentation complète** → [`AGENTS.md`](AGENTS.md)

---

## Démarrage rapide

```powershell
# Lancer le robot
python main.py

# Lancer avec moniteur
.\scripts\robot.ps1

# Voir l'état
.\scripts\robot.ps1 -Status

# Arrêter
.\scripts\robot.ps1 -Stop
```

## Tests

```powershell
python -m pytest tests/ -q          # 663 tests, ~28s
python -m pytest tests/ -q --tb=long  # Avec stack traces
```

## Structure du projet

```
├── main.py                          # Boucle principale 15s
├── config_simple.py                 # Config unifiée (YAML → Python)
├── engine_simple/                   # Moteur de trading
│   ├── strategy.py                  # Signal MOM20x3
│   ├── signal_pipeline.py           # Pipeline 12 phases
│   ├── ftmo_protector.py            # Protection FTMO
│   ├── trailer.py                   # Trailing ATR
│   ├── portfolio_controller.py      # Corrélation & limites
│   ├── trade_executor.py            # Exécution MT5
│   └── ...
├── config/
│   ├── default.yaml                 # Config par défaut
│   ├── production.yaml              # Surcharges production
│   └── schema.py                    # Validation Pydantic
├── scripts/                         # Utilitaires
│   └── robot.ps1                    # Moniteur PowerShell
├── tests/                           # 695 tests
├── data/                            # Données historiques
└── runtime/                         # État runtime (PID, logs, métriques)
```

## Architecture

```
MOM20x3 brut → RVOL/CMF/OBV → Régime ADX → OnlineLearner → FTMO Protector → Exécution
```

Voir [`AGENTS.md`](AGENTS.md) pour l'architecture complète, la config des 27 symboles,
le conseil des 22 agents IA, et les détails de la stratégie.

## Commandes utiles

```powershell
python -m pytest tests/test_trailer.py -q     # Tests trailing
python -m pytest tests/test_ftmo_protector.py -q  # Tests FTMO
python scripts/validate_strategy.py --csv runtime/trades_log.csv  # Stats live
.\scripts\daily_report.ps1                     # Rapport challenge
```

## Environnement

Copier `config/env.template` vers `.env` et configurer :
```
MT5_LOGIN=12345678
MT5_PASSWORD=xxxxx
MT5_SERVER=FTMO-Server
```

## Principes clés

- **Magie** : 999001 — tous les trades du robot
- **Trailing** : ATR peak-based, 4 niveaux par régime (RANGING/TREND/HIGH_VOL/LOW_VOL)
- **Protection** : Drawdown 10%, Daily Loss 2%, Consistance 30%, Corrélation max 2/direction/groupe
- **Learning** : OnlineLearner adapte seuils et risque toutes les 200 trades
- **24/7** : Pas de blocage weekend — trailing actif en continu
