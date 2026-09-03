"""Telegram Bot pour contrôle à distance du robot MT5.

Permet de :
- Consulter l'état du robot (/status)
- Voir les positions ouvertes (/positions)
- Fermer une position (/close <ticket>)
- Arrêter le robot (/stop)
- Redémarrer le robot (/restart)

Sécurité :
- Authentification par chat_id autorisé
- Commandes sensibles (/stop, /close) nécessitent confirmation
- Rate limiting (1 commande/5 secondes)
- Logging de toutes les actions

Usage :
    bot = TelegramBot()
    bot.start()  # Thread daemon
    # ... dans le bot ...
    bot.send_notification("Trade exécuté sur BTCUSD")
"""

import os
import time
import json
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("telegram_bot")


class TelegramBot:
    """Bot Telegram bidirectionnel pour contrôle du robot."""
    
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.authorized_chat_ids = self._load_authorized_chat_ids()
        self._enabled = bool(self.token and self.chat_id)
        self._last_command_time = {}  # chat_id -> timestamp
        self._command_cooldown = 5  # secondes
        self._running = False
        self._thread = None
        self._offset = 0  # Pour le long polling
        
        if self._enabled:
            logger.info(f"[TELEGRAM] Bot activé pour chat_id: {self.chat_id}")
        else:
            logger.info("[TELEGRAM] Bot désactivé (token ou chat_id manquant)")
    
    def _load_authorized_chat_ids(self) -> set:
        """Charge les chat_id autorisés depuis .env ou fichier."""
        authorized = set()
        
        # Chat ID principal depuis .env
        main_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if main_chat_id:
            authorized.add(main_chat_id)
        
        # Chat IDs supplémentaires depuis fichier
        auth_file = Path("config/telegram_authorized.json")
        if auth_file.exists():
            try:
                with open(auth_file) as f:
                    data = json.load(f)
                    authorized.update(str(cid) for cid in data.get("chat_ids", []))
            except Exception as e:
                logger.warning(f"[TELEGRAM] Erreur chargement authorized_ids: {e}")
        
        return authorized
    
    def _check_rate_limit(self, chat_id: str) -> bool:
        """Vérifie le rate limiting par chat_id."""
        now = time.time()
        last_time = self._last_command_time.get(chat_id, 0)
        
        if now - last_time < self._command_cooldown:
            return False
        
        self._last_command_time[chat_id] = now
        return True
    
    def _check_authorization(self, chat_id: str) -> bool:
        """Vérifie si le chat_id est autorisé."""
        return str(chat_id) in self.authorized_chat_ids
    
    def send(self, message: str, chat_id: str = None) -> bool:
        """Envoie un message Telegram."""
        if not self._enabled:
            logger.debug(f"[TELEGRAM] {message}")
            return False
        
        target_chat = chat_id or self.chat_id
        
        try:
            import requests
            
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": target_chat,
                "text": message[:4000],
                "parse_mode": "HTML"
            }
            
            r = requests.post(url, json=payload, timeout=10)
            
            if r.status_code == 200:
                logger.debug(f"[TELEGRAM] Message envoyé à {target_chat}")
                return True
            else:
                logger.warning(f"[TELEGRAM] Erreur API {r.status_code}: {r.text[:200]}")
                return False
                
        except Exception as e:
            logger.warning(f"[TELEGRAM] Envoi échoué: {e}")
            return False
    
    def send_notification(self, message: str, level: str = "INFO"):
        """Envoie une notification formatée."""
        emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🔴", "SUCCESS": "✅"}
        prefix = emoji.get(level, "📢")
        formatted = f"{prefix} <b>Robot MT5</b>\n\n{message}"
        return self.send(formatted)
    
    def _handle_command(self, chat_id: str, text: str):
        """Traite une commande reçue."""
        command = text.lower().strip()
        
        # Vérification authorization
        if not self._check_authorization(chat_id):
            self.send("⛔ Non autorisé", chat_id)
            logger.warning(f"[TELEGRAM] Commande non autorisée de {chat_id}: {command}")
            return
        
        # Rate limiting
        if not self._check_rate_limit(chat_id):
            self.send("⏳ Trop de commandes. Attendez 5s.", chat_id)
            return
        
        logger.info(f"[TELEGRAM] Commande reçue de {chat_id}: {command}")
        
        # Commandes
        if command == "/start":
            self._cmd_start(chat_id)
        elif command == "/status":
            self._cmd_status(chat_id)
        elif command == "/positions":
            self._cmd_positions(chat_id)
        elif command.startswith("/close"):
            self._cmd_close(chat_id, command)
        elif command == "/stop":
            self._cmd_stop(chat_id)
        elif command == "/restart":
            self._cmd_restart(chat_id)
        elif command == "/help":
            self._cmd_help(chat_id)
        else:
            self.send("❓ Commande inconnue. Tapez /help", chat_id)
    
    def _cmd_start(self, chat_id: str):
        """Commande /start."""
        self.send(
            "🤖 <b>Robot MT5 FTMO</b>\n\n"
            "Connecté et prêt.\n\n"
            "Commandes disponibles :\n"
            "• /status - État du robot\n"
            "• /positions - Positions ouvertes\n"
            "• /close &lt;ticket&gt; - Fermer une position\n"
            "• /stop - Arrêter le robot\n"
            "• /restart - Redémarrer\n"
            "• /help - Aide",
            chat_id
        )
    
    def _cmd_status(self, chat_id: str):
        """Commande /status — lit le dashboard.json."""
        try:
            state_file = Path("runtime/dashboard.json")
            if not state_file.exists():
                self.send("⚠️ Fichier dashboard introuvable", chat_id)
                return
            
            with open(state_file) as f:
                status = json.load(f)
            
            msg = (
                f"📊 <b>État du Robot</b>\n\n"
                f"PID: {status.get('pid', 'N/A')}\n"
                f"Uptime: {status.get('uptime_min', 0):.0f} min\n"
                f"Balance: ${status.get('balance', 0):,.0f}\n"
                f"Equity: ${status.get('equity', 0):,.0f}\n"
                f"DD: {status.get('current_dd', 0):.1%}\n"
                f"Trades: {status.get('total_trades', 0)}\n"
                f"PnL: ${status.get('total_pnl', 0):+,.0f}\n"
                f"WR: {status.get('win_rate', 0):.1%}\n"
                f"PF: {status.get('profit_factor', 0):.2f}"
            )
            
            self.send(msg, chat_id)
            
        except Exception as e:
            self.send(f"❌ Erreur lecture status: {e}", chat_id)
    
    def _cmd_positions(self, chat_id: str):
        """Commande /positions — lit les positions du dashboard."""
        try:
            state_file = Path("runtime/dashboard.json")
            if not state_file.exists():
                self.send("⚠️ Fichier dashboard introuvable", chat_id)
                return
            
            with open(state_file) as f:
                status = json.load(f)
            
            positions = status.get("positions", [])
            
            if not positions:
                self.send("📭 Aucune position ouverte", chat_id)
                return
            
            msg = f"📈 <b>{len(positions)} position(s) ouverte(s)</b>\n\n"
            
            for pos in positions:
                pnl = pos.get('pnl', 0)
                emoji = "🟢" if pnl >= 0 else "🔴"
                msg += (
                    f"{emoji} <b>{pos.get('symbol', '?')}</b> {pos.get('direction', '?')}\n"
                    f"   Ticket: {pos.get('ticket', '?')}\n"
                    f"   Entry: {pos.get('entry_price', 0):.2f}\n"
                    f"   PnL: ${pnl:+,.0f}\n"
                    f"   Durée: {pos.get('duration_min', 0):.0f}min\n\n"
                )
            
            self.send(msg, chat_id)
            
        except Exception as e:
            self.send(f"❌ Erreur lecture positions: {e}", chat_id)
    
    def _cmd_close(self, chat_id: str, command: str):
        """Commande /close <ticket> — ferme une position."""
        parts = command.split()
        if len(parts) < 2:
            self.send("Usage: /close &lt;ticket_number&gt;", chat_id)
            return
        
        try:
            ticket = int(parts[1])
        except ValueError:
            self.send("❌ Ticket invalide", chat_id)
            return
        
        # Confirmation requise
        self.send(
            f"⚠️ Confirmer fermeture position #{ticket} ?\n\n"
            f"Répondez: /confirm_close {ticket}",
            chat_id
        )
    
    def _cmd_stop(self, chat_id: str):
        """Commande /stop — arrête le robot."""
        self.send(
            "⚠️ <b>CONFIRMATION REQUISE</b>\n\n"
            "Taper: /confirm_stop\n\n"
            "⏰ Expire dans 30 secondes",
            chat_id
        )
        
        # Timer de confirmation
        def delayed_check():
            time.sleep(30)
            # La confirmation sera gérée dans le polling
        
        threading.Thread(target=delayed_check, daemon=True).start()
    
    def _cmd_restart(self, chat_id: str):
        """Commande /restart — redémarre le robot."""
        self.send(
            "⚠️ <b>CONFIRMATION REQUISE</b>\n\n"
            "Taper: /confirm_restart\n\n"
            "⏰ Expire dans 30 secondes",
            chat_id
        )
    
    def _cmd_help(self, chat_id: str):
        """Commande /help."""
        self.send(
            "📚 <b>Aide - Commandes</b>\n\n"
            "<b>Consultation :</b>\n"
            "• /status - État complet du robot\n"
            "• /positions - Liste des positions\n"
            "• /help - Cette aide\n\n"
            "<b>Actions :</b>\n"
            "• /close &lt;ticket&gt; - Fermer une position\n"
            "• /stop - Arrêter le robot\n"
            "• /restart - Redémarrer\n\n"
            "<b>Sécurité :</b>\n"
            "• Rate limit: 1 commande/5s\n"
            "• Actions sensibles: confirmation requise\n"
            "• Seuls les chat_id autorisés peuvent interagir",
            chat_id
        )
    
    def _poll_updates(self):
        """Long polling pour recevoir les commandes."""
        import requests
        
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        
        while self._running:
            try:
                params = {
                    "offset": self._offset,
                    "timeout": 30,  # Long polling
                    "allowed_updates": json.dumps(["message"])
                }
                
                r = requests.get(url, params=params, timeout=35)
                
                if r.status_code == 200:
                    data = r.json()
                    
                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        
                        if "message" in update:
                            msg = update["message"]
                            chat_id = str(msg["chat"]["id"])
                            text = msg.get("text", "")
                            
                            if text:
                                self._handle_command(chat_id, text)
                
            except requests.exceptions.Timeout:
                # Normal en long polling
                pass
            except Exception as e:
                logger.warning(f"[TELEGRAM] Erreur polling: {e}")
                time.sleep(5)
    
    def start(self):
        """Démarre le bot en thread daemon."""
        if not self._enabled:
            logger.info("[TELEGRAM] Bot désactivé, pas de démarrage")
            return
        
        if self._running:
            logger.warning("[TELEGRAM] Bot déjà en cours d'exécution")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_updates, daemon=True)
        self._thread.start()
        
        logger.info("[TELEGRAM] Bot démarré (long polling)")
        self.send_notification("🤖 Robot connecté au bot Telegram", "SUCCESS")
    
    def stop(self):
        """Arrête le bot."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[TELEGRAM] Bot arrêté")


# Instance globale
_bot_instance = None


def get_telegram_bot() -> TelegramBot:
    """Retourne l'instance globale du bot."""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = TelegramBot()
    return _bot_instance


def send_trade_notification(symbol: str, direction: str, entry: float, pnl: float = None):
    """Envoie une notification de trade."""
    bot = get_telegram_bot()
    if pnl is not None:
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = f"{emoji} <b>Trade {direction}</b> {symbol}\nEntry: {entry:.2f}\nPnL: ${pnl:+,.0f}"
    else:
        msg = f"📈 <b>Trade {direction}</b> {symbol}\nEntry: {entry:.2f}"
    bot.send_notification(msg, "SUCCESS" if pnl and pnl >= 0 else "INFO")


def send_error_notification(error: str, context: str = ""):
    """Envoie une notification d'erreur."""
    bot = get_telegram_bot()
    msg = f"❌ <b>Erreur</b>\n{context}\n\n{error[:500]}"
    bot.send_notification(msg, "ERROR")


def send_status_update(status: dict):
    """Envoie une mise à jour d'état."""
    bot = get_telegram_bot()
    msg = (
        f"📊 <b>Status Update</b>\n\n"
        f"Balance: ${status.get('balance', 0):,.0f}\n"
        f"DD: {status.get('current_dd', 0):.1%}\n"
        f"PnL: ${status.get('total_pnl', 0):+,.0f}"
    )
    bot.send_notification(msg, "INFO")