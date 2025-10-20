# Projet Trading IA PROPFIRM

**Système de trading automatisé avec IA et gestion de risque avancée**

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