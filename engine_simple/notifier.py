from __future__ import annotations

import logging
import os

logger = logging.getLogger("notifier")


class Notifier:
    def __init__(self) -> None:
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self.telegram_token and self.telegram_chat_id)

    def is_enabled(self) -> bool:
        return self._enabled

    def send(self, message: str) -> None:
        if not self._enabled:
            logger.info(f"[NOTIFIER] {message} (Telegram non configuré)")
            return
        try:
            import requests

            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            r = requests.post(url, json={"chat_id": self.telegram_chat_id, "text": message[:4000]}, timeout=5)
            if r.status_code != 200:
                logger.warning(f"Telegram API error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")
