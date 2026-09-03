# Telegram Bot - Contrôle à distance du Robot MT5

## Fonctionnalités

Le bot Telegram bidirectionnel permet de :
- **Consulter l'état du robot** (`/status`)
- **Voir les positions ouvertes** (`/positions`)
- **Fermer une position** (`/close <ticket>`)
- **Arrêter le robot** (`/stop`)
- **Redémarrer le robot** (`/restart`)
- **Recevoir des notifications** en temps réel (trades, erreurs, alertes)

## Configuration

### 1. Créer un bot Telegram

1. Ouvrez Telegram et recherchez **@BotFather**
2. Envoyez `/newbot`
3. Choisissez un nom pour votre bot (ex: `Robot MT5 FTMO`)
4. Choisissez un username unique (ex: `robot_mt5_ftmo_bot`)
5. Copiez le **token** fourni par BotFather

### 2. Obtenir votre Chat ID

1. Ouvrez Telegram et recherchez **@userinfobot**
2. Envoyez `/start`
3. Copiez votre **Chat ID**

### 3. Configurer les variables d'environnement

Dans le fichier `.env`, ajoutez :

```bash
# Token du bot Telegram (obtenu via @BotFather)
TELEGRAM_BOT_TOKEN=votre_token_ici

# Chat ID principal (obtenu via @userinfobot)
TELEGRAM_CHAT_ID=votre_chat_id_ici
```

### 4. Autoriser d'autres utilisateurs (optionnel)

Modifiez le fichier `config/telegram_authorized.json` :

```json
{
  "chat_ids": ["123456789", "987654321"],
  "comment": "Chat IDs autorisés à interagir avec le bot"
}
```

## Commandes disponibles

| Commande | Description | Sécurité |
|----------|-------------|----------|
| `/start` | Affiche l'aide et les commandes | ✅ Public |
| `/status` | État complet du robot (PID, balance, DD, trades) | ✅ Public |
| `/positions` | Liste des positions ouvertes avec PnL | ✅ Public |
| `/close <ticket>` | Ferme une position par son ticket | ⚠️ Confirmation requise |
| `/stop` | Arrête le robot | ⚠️ Confirmation requise |
| `/restart` | Redémarre le robot | ⚠️ Confirmation requise |
| `/help` | Affiche l'aide détaillée | ✅ Public |

## Notifications automatiques

Le bot envoie automatiquement des notifications pour :

- **Trade exécuté** : Nouvelle position ouverte
- **Erreur critique** : Crash du robot
- **Alerte DD** : Drawdown > 5%
- **Statut** : État du robot au démarrage

## Sécurité

### Authentification
- Seuls les `chat_id` autorisés peuvent interagir avec le bot
- Le `chat_id` principal est défini dans `.env`
- Les `chat_id` supplémentaires sont dans `config/telegram_authorized.json`

### Rate Limiting
- **1 commande toutes les 5 secondes** par chat_id
- Évite les abus et les charges excessives sur l'API Telegram

### Commandes sensibles
- `/stop`, `/restart`, `/close` nécessitent une **confirmation**
- La confirmation expire après 30 secondes
- Empêche les arrêts accidentels

### Logging
- Toutes les commandes sont journalisées
- Les tentatives non autorisées sont alertées

## Intégration avec le Robot

Le bot Telegram est intégré au `TradingEngine` :

```python
# Dans trading_engine.py
from engine_simple.telegram_bot import get_telegram_bot

# Au démarrage
self.telegram_bot = get_telegram_bot()
if self.telegram_bot._enabled:
    self.telegram_bot.start()
```

### Notifications automatiques

```python
# Après un trade exécuté
send_trade_notification(symbol, direction, entry_price)

# En cas d'erreur
send_error_notification(error_message, context)

# Pour les alertes DD
send_error_notification(f"DD {dd_pct}%", "Alerte Drawdown")
```

## Dépannage

### Le bot ne répond pas

1. Vérifiez que `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` sont définis dans `.env`
2. Vérifiez que le token est valide (testez avec `curl` ou Postman)
3. Vérifiez les logs du robot : `logs/simple_robot.log`

### "Non autorisé"

1. Vérifiez que votre `chat_id` est dans `config/telegram_authorized.json`
2. Ou dans `TELEGRAM_CHAT_ID` du fichier `.env`

### Notifications non reçues

1. Vérifiez que le bot est démarré (logs : `[TELEGRAM] Bot bidirectionnel démarré`)
2. Vérifiez que vous avez bien démarré une conversation avec le bot
3. Testez avec `/start` pour vérifier la connectivité

## Exemple d'utilisation

### Depuis le téléphone

1. Ouvrez Telegram
2. Recherchez votre bot (ex: `@robot_mt5_ftmo_bot`)
3. Envoyez `/status`

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

4. Envoyez `/positions`

```
📈 2 position(s) ouverte(s)

🟢 BTCUSD BUY
   Ticket: 12345678
   Entry: 45,000.00
   PnL: +$250
   Durée: 15min

🔴 EURUSD SELL
   Ticket: 87654321
   Entry: 1.0850
   PnL: -$50
   Durée: 30min
```

## Notes importantes

- Le bot fonctionne en **long polling** (pas de webhook)
- Le bot est un **thread daemon** (s'arrête avec le robot)
- Les messages sont limités à **4000 caractères** par Telegram
- Le bot utilise `parse_mode=HTML` pour le formatage
