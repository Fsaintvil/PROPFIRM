# Bot Telegram - Contrôle à distance du Robot MT5 FTMO

## 🎯 Fonctionnalités

Le bot Telegram bidirectionnel vous permet de contrôler votre robot de trading depuis votre téléphone :

- **📊 Consulter l'état** du robot en temps réel
- **📈 Voir les positions** ouvertes et leur PnL
- **🔒 Fermer une position** à distance
- **⛔ Arrêter/Redémarrer** le robot
- **🔔 Recevoir des notifications** automatiques

## 🚀 Installation rapide

### 1. Créer un bot Telegram

1. Ouvrez Telegram et recherchez **@BotFather**
2. Envoyez `/newbot`
3. Choisissez un nom (ex: `Robot MT5 FTMO`)
4. Choisissez un username (ex: `robot_mt5_ftmo_bot`)
5. Copiez le **token** fourni

### 2. Obtenir votre Chat ID

1. Ouvrez Telegram et recherchez **@userinfobot**
2. Envoyez `/start`
3. Copiez votre **Chat ID**

### 3. Configurer

Ajoutez dans votre fichier `.env` :

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=987654321
```

### 4. Démarrer

**Option 1 : Avec le robot principal** (recommandé)

Le bot Telegram démarre automatiquement avec le robot MT5.

**Option 2 : Seul**

```bash
python scripts/start_telegram_bot.py
```

Ou double-cliquez sur `scripts\start_telegram_bot.bat`

## 📱 Utilisation

### Sur votre téléphone

1. Ouvrez Telegram
2. Recherchez votre bot (ex: `@robot_mt5_ftmo_bot`)
3. Envoyez `/start`

### Commandes disponibles

| Commande | Description | Exemple |
|----------|-------------|---------|
| `/start` | Affiche l'aide | `/start` |
| `/status` | État complet du robot | `/status` |
| `/positions` | Positions ouvertes | `/positions` |
| `/close <ticket>` | Ferme une position | `/close 12345678` |
| `/stop` | Arrête le robot | `/stop` |
| `/restart` | Redémarre le robot | `/restart` |
| `/help` | Aide détaillée | `/help` |

### Exemple de réponse

```
📊 État du Robot

PID: 12345
Uptime: 45 min
Balance: $200,000
Equity: $199,500
DD: 0.25%
Trades: 15
PnL: +$500
WR: 65.0%
PF: 1.85
```

## 🔔 Notifications automatiques

Le bot envoie des notifications pour :

- **Trade exécuté** : Nouvelle position ouverte
- **Erreur critique** : Crash du robot
- **Alerte DD** : Drawdown > 5%
- **Statut** : État du robot au démarrage

## 🔒 Sécurité

### Authentification

- Seuls les `chat_id` autorisés peuvent interagir
- Configuration dans `.env` et `config/telegram_authorized.json`

### Rate Limiting

- **1 commande toutes les 5 secondes** par chat_id
- Protection contre les abus

### Commandes sensibles

- `/stop`, `/restart`, `/close` nécessitent une **confirmation**
- La confirmation expire après 30 secondes

## 🛠️ Dépannage

### Le bot ne répond pas

1. Vérifiez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env`
2. Vérifiez les logs : `logs/simple_robot.log`
3. Testez la connexion : `python scripts/test_telegram_bot.py`

### "Non autorisé"

1. Vérifiez votre `chat_id` dans `config/telegram_authorized.json`
2. Ou dans `TELEGRAM_CHAT_ID` du fichier `.env`

### Notifications non reçues

1. Vérifiez que le bot est démarré
2. Démarrez une conversation avec le bot
3. Testez avec `/start`

## 📁 Fichiers

```
MT5_FTMO_IA.7/
├── engine_simple/
│   └── telegram_bot.py          # Bot Telegram principal
├── scripts/
│   ├── start_telegram_bot.py    # Script de démarrage
│   ├── start_telegram_bot.bat   # Script Windows
│   └── test_telegram_bot.py     # Script de test
├── config/
│   └── telegram_authorized.json # Chat IDs autorisés
├── docs/
│   └── telegram_bot.md          # Documentation détaillée
└── .env                         # Configuration (à créer)
```

## 🔧 Intégration

Le bot Telegram est intégré au `TradingEngine` et démarre automatiquement avec le robot.

Notifications automatiques dans `trading_engine.py` :

```python
# Trade exécuté
send_trade_notification(symbol, direction, entry_price)

# Erreur critique
send_error_notification(error_message, context)

# Alerte DD
send_error_notification(f"DD {dd_pct}%", "Alerte Drawdown")
```

## 📝 Notes

- Le bot utilise le **long polling** (pas de webhook)
- Le bot est un **thread daemon** (s'arrête avec le robot)
- Messages limités à **4000 caractères** par Telegram
- Formatage **HTML** supporté

## 🆕 Version

- **1.0** : Version initiale
- Fonctionnalités : status, positions, close, stop, restart
- Sécurité : authentification, rate limiting, confirmation
