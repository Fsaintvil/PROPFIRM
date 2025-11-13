# Projet Trading IA PROPFIRM

**Système de trading automatisé avec IA et gestion de risque avancée**

## Prérequis

- PowerShell (préférer PowerShell Core `pwsh.exe`) est requis pour l'exécution des scripts d'exploitation et des tâches d'administration.
- Pour les opérations administratives (persistance des variables MACHINE, création de tâches planifiées), ouvrez une session PowerShell élevée (Run as Administrator).
- Exemple d'ouverture d'une console PowerShell Core élevée sous Windows:

```powershell
# Ouvrir Windows Terminal / PowerShell en mode administrateur
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process pwsh -Verb runAs"
```

Les scripts situés dans `tools/*.ps1` appellent `tools/ensure_pwsh.ps1` pour valider l'environnement.

## 🧭 Politique d’exploitation: 100% live — pas de mode paper

- Trading uniquement en temps réel via MT5. Aucun mode paper/dry-run/simulation en production.
- Les artefacts `paper_trades.*` et outils dry_run ont été supprimés et ne doivent pas être réintroduits.
- Pour l’analyse de performance, utilisez les journaux réels (`logs/trades.json[l]`) et `tools/performance_aggregator.py`.
- Les contributions (PR) doivent respecter cette politique; tout fallback de simulation est refusé.
- Les anciennes mentions de « mode mock/paper » dans des scripts ou docs sont obsolètes et en cours de nettoyage.

## 🚀 Démarrage Rapide

### Installation
```bash
# Installer les dépendances
pip install -r requirements.txt
```

### Configuration
1. Configurer MetaTrader5 si disponible
2. Valider la connexion: `python -c "from src.utils.mt5_connector import mt5_health_check; print(mt5_health_check())"`

### Lancement
```bash
# Robot principal (recommandé)
python scripts/simplified_trading_robot.py

# Monitoring avancé
python scripts/advanced_monitoring.py
```

## 📁 Structure Projet

```
PROPFIRM/
├── config/          # Configuration et constantes
├── data/            # Données et backtests
├── logs/            # Journaux système
├── reports/         # Rapports et analyses
├── scripts/         # Scripts de trading
├── src/             # Code source organisé
│   ├── utils/       # Utilitaires (mt5_connector)
│   └── __init__.py  # Module init
└── requirements.txt # Dépendances
```

## 🤖 Robot Principal: Simplified Trading Robot

Le robot `simplified_trading_robot.py` implémente:

### ✅ Fonctionnalités
- **Signaux MA + RSI** : Moyennes mobiles avec RSI pour confirmation
- **Multi-timeframe** : Analyse M15, H1, H4 simultanée  
- **Métriques réalistes** : Sharpe 0.85, Win rate 35%
- **Gestion de risque** : SL/TP automatiques
- **Fallbacks MT5** : Fonctionne sans MetaTrader5 (mode mock)

### 📊 Performance
- **Backtests validés** sur instruments majeurs
- **Signal accuracy** : ~52% (réaliste)
- **Architecture robuste** : 500 lignes optimisées
- **Gestion d'erreurs** : Fallbacks complets

## 🛡️ Sécurité MT5

### Import sécurisé
```python
from src.utils.mt5_connector import get_mt5, mt5_health_check

# Auto-fallback si MT5 indisponible
mt5 = get_mt5()  # Retourne mock si nécessaire
status = mt5_health_check()  # Diagnostic complet
```

### Règles FTMO intégrées
- Drawdown quotidien max: 4.5%
- Drawdown global max: 10%  
- Risk/Reward minimum: 1:2
- Journalisation complète

## 🔧 Scripts Disponibles

- `simplified_trading_robot.py` - Robot principal
- `advanced_monitoring.py` - Système de monitoring
- `setup_live_data_collection.py` - Collecte données
- `walk_forward_validation.py` - Validation temporelle

## 📋 Tests et validation

```bash
# Test du robot principal
python scripts/simplified_trading_robot.py

# Test monitoring
python scripts/advanced_monitoring.py

# Vérification santé MT5
python -c "from src.utils.mt5_connector import mt5_health_check; print(mt5_health_check())"
```

## 📈 Monitoring

Le système de monitoring inclut:
- Alertes intelligentes temps réel
- Détection dégradation performance  
- Arrêt d'urgence automatique
- Dashboard HTML avec métriques
- Analyse du drift des modèles

## ⚠️ Notes importantes

- MetaTrader5 requis pour trading réel (Windows uniquement)
- Mode mock disponible pour développement/test
- Tous les scripts gèrent les imports manquants
- Structure unifiée sous PROPFIRM/ uniquement