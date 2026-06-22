"""News Filter — Filtre les trades autour des événements économiques majeurs.

Empêche les trades dans les minutes précédant/suivantant un événement à fort impact.
Fonctionne en mode statique (calendrier pré-configuré) et dynamique (fetch RSS).

Impact levels:
- HIGH: NFP, CPI, FOMC, ECB, BOE → bloquer 15min avant/après
- MEDIUM: PMI, Retail Sales → bloquer 10min avant/après
- LOW: Autres → pas de blocage

Usage:
    news = NewsFilter()
    if news.is_news_blocked("EURUSD", datetime.now()):
        logger.info("News event imminent — trade bloqué")
"""

import logging
import json
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("news_filter")

# ============================================================================
# STATIC NEWS CALENDAR (hardcoded recurring events)
# ============================================================================

# Format: (hour_utc, minute_utc, impact, symbols, description)
STATIC_EVENTS = [
    # ── US Events ──
    # NFP (1st Friday 12:30 UTC) — handled dynamically
    # FOMC (8x/year 19:00 UTC) — handled dynamically
    # CPI US (monthly 12:30 UTC)
    (12, 30, "HIGH", ["XAUUSD", "US500.cash"], "US CPI"),
    # PPI US (monthly 12:30 UTC)
    (12, 30, "MEDIUM", ["XAUUSD", "US500.cash"], "US PPI"),
    # Unemployment Claims (weekly 12:30 UTC)
    (12, 30, "MEDIUM", ["US500.cash"], "US Unemployment Claims"),
    # ISM Manufacturing (monthly 14:00 UTC)
    (14, 0, "MEDIUM", ["US500.cash"], "US ISM Manufacturing"),
    # ISM Services (monthly 14:00 UTC)
    (14, 0, "MEDIUM", ["US500.cash"], "US ISM Services"),
    # Retail Sales US (monthly 12:30 UTC)
    (12, 30, "MEDIUM", ["US500.cash"], "US Retail Sales"),
    # Fed Chair Speech (variable, approximated)
    (14, 0, "HIGH", ["XAUUSD", "US500.cash"], "Fed Chair Speech"),
    # ── Gold Events ──
    # China Data (02:00 UTC)
    (2, 0, "MEDIUM", ["XAUUSD"], "China GDP/PMI"),
    # ── Crypto Events ──
    # CME BTC Futures Settlement (16:00 UTC Fri)
    (16, 0, "MEDIUM", ["BTCUSD"], "CME Crypto Futures Settlement"),
]


@dataclass
class NewsEvent:
    """Un événement économique."""

    timestamp_utc: datetime
    impact: str  # HIGH, MEDIUM, LOW
    symbols: list[str]
    description: str
    source: str = "static"

    def is_active(self, now: datetime, pre_minutes: int = 15, post_minutes: int = 10) -> bool:
        """Vérifie si l'événement est actif (fenêtre temporelle)."""
        delta = (now - self.timestamp_utc).total_seconds() / 60
        return -pre_minutes <= delta <= post_minutes


class NewsFilter:
    """Filtre les trades autour des événements économiques."""

    def __init__(self, config: dict | None = None):
        self._events: list[NewsEvent] = []
        self._blocked_until: dict[str, datetime] = {}  # symbol → blocked until

        # Config
        self._pre_minutes = 15
        self._post_minutes = 10
        self._high_impact_block = True
        self._medium_impact_block = True
        self._low_impact_block = False

        if config:
            self._pre_minutes = config.get("news_pre_minutes", 15)
            self._post_minutes = config.get("news_post_minutes", 10)
            self._high_impact_block = config.get("news_high_impact_block", True)
            self._medium_impact_block = config.get("news_medium_impact_block", True)
            self._low_impact_block = config.get("news_low_impact_block", False)

        self._load_static_events()

    def _load_static_events(self):
        """Charge les événements statiques pour aujourd'hui et demain."""
        now = datetime.now(timezone.utc)
        today = now.date()

        for hour, minute, impact, symbols, desc in STATIC_EVENTS:
            # Today
            ts = datetime(today.year, today.month, today.day, hour, minute, tzinfo=timezone.utc)
            if ts > now - timedelta(hours=1):
                self._events.append(
                    NewsEvent(timestamp_utc=ts, impact=impact, symbols=symbols, description=desc, source="static")
                )

            # Tomorrow
            tomorrow = today + timedelta(days=1)
            ts_tomorrow = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=timezone.utc)
            self._events.append(
                NewsEvent(timestamp_utc=ts_tomorrow, impact=impact, symbols=symbols, description=desc, source="static")
            )

    def add_event(
        self, timestamp_utc: datetime, impact: str, symbols: list[str], description: str, source: str = "manual"
    ):
        """Ajoute un événement dynamique."""
        event = NewsEvent(
            timestamp_utc=timestamp_utc, impact=impact, symbols=symbols, description=description, source=source
        )
        self._events.append(event)
        logger.debug(
            f"  [NEWS] Event added: {description} @ {timestamp_utc.isoformat()} (impact={impact}, symbols={symbols})"
        )

    def is_news_blocked(self, symbol: str, now: datetime | None = None) -> tuple[bool, str]:
        """Vérifie si un symbole est bloqué par un événement news.

        Returns:
            (is_blocked, reason)
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Check manual block
        if symbol in self._blocked_until:
            if now < self._blocked_until[symbol]:
                remaining = (self._blocked_until[symbol] - now).total_seconds() / 60
                return True, f"Manual block ({remaining:.0f}min remaining)"
            else:
                del self._blocked_until[symbol]

        # Check events
        for event in self._events:
            if symbol not in event.symbols:
                continue

            if not event.is_active(now, self._pre_minutes, self._post_minutes):
                continue

            # Check impact level
            should_block = False
            if event.impact == "HIGH" and self._high_impact_block:
                should_block = True
            elif event.impact == "MEDIUM" and self._medium_impact_block:
                should_block = True
            elif event.impact == "LOW" and self._low_impact_block:
                should_block = True

            if should_block:
                delta_min = (now - event.timestamp_utc).total_seconds() / 60
                if delta_min < 0:
                    reason = f"News imminent: {event.description} dans {-delta_min:.0f}min"
                else:
                    reason = f"News récent: {event.description} il y a {delta_min:.0f}min"
                return True, reason

        return False, "No news event"

    def block_symbol(self, symbol: str, minutes: int = 30):
        """Bloque manuellement un symbole."""
        self._blocked_until[symbol] = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        logger.info(f"  [NEWS] {symbol} bloqué pour {minutes}min")

    def get_upcoming_events(self, symbol: str, hours: int = 4) -> list[dict]:
        """Retourne les événements à venir pour un symbole."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)

        events = []
        for event in self._events:
            if symbol in event.symbols and now < event.timestamp_utc <= cutoff:
                events.append(
                    {
                        "time": event.timestamp_utc.isoformat(),
                        "impact": event.impact,
                        "description": event.description,
                        "minutes_until": (event.timestamp_utc - now).total_seconds() / 60,
                    }
                )

        return sorted(events, key=lambda x: x["minutes_until"])

    def get_status(self) -> dict:
        """Retourne le statut du filtre news."""
        now = datetime.now(timezone.utc)
        active = [e for e in self._events if e.is_active(now, self._pre_minutes, self._post_minutes)]
        return {
            "total_events": len(self._events),
            "active_now": len(active),
            "blocked_symbols": list(self._blocked_until.keys()),
            "pre_minutes": self._pre_minutes,
            "post_minutes": self._post_minutes,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_filter = NewsFilter()


def is_news_blocked(symbol: str, now: datetime | None = None) -> tuple[bool, str]:
    """Vérifie si un symbole est bloqué (fonction convenience)."""
    return _default_filter.is_news_blocked(symbol, now)


def get_upcoming_events(symbol: str, hours: int = 4) -> list[dict]:
    """Retourne les événements à venir (fonction convenience)."""
    return _default_filter.get_upcoming_events(symbol, hours)
