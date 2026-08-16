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

# Format: (hour_utc, minute_utc, impact, symbols, description, days)
#   days = jours de la semaine où l'événement s'applique (0=Lundi .. 6=Dimanche).
#   Une liste vide [] = événement NON statique (nécessite un calendrier dynamique).
# 🔧 FIX M-ML2 (16 Août 2026): Les événements mensuels/hebdomadaires étaient
# chargés pour AUJOURD'HUI à la même heure CHAQUE jour → XAUUSD/US500.cash
# bloqués TOUS LES JOURS 12:15-12:40 et 13:45-14:10 UTC (faux positifs massifs).
# Le champ `days` restreint le blocage aux seuls jours où l'événement a lieu.
# ⚠️ NFP (1er vendredi 12:30 UTC) et FOMC (8x/an 19:00 UTC) nécessitent un
# calendrier DYNAMIQUE (fetch de calendrier économique) — HORS SCOPE de ce fix.
STATIC_EVENTS = [
    # ── US Events ──
    # CPI US (monthly 12:30 UTC)
    (12, 30, "HIGH", ["XAUUSD", "US500.cash"], "US CPI", [0]),
    # PPI US (monthly 12:30 UTC)
    (12, 30, "MEDIUM", ["XAUUSD", "US500.cash"], "US PPI", [0]),
    # Unemployment Claims (hebdomadaire — jeudi 12:30 UTC)
    (12, 30, "MEDIUM", ["US500.cash"], "US Unemployment Claims", [3]),
    # ISM Manufacturing (monthly, 1er jour ouvré 14:00 UTC)
    (14, 0, "MEDIUM", ["US500.cash"], "US ISM Manufacturing", [0, 1]),
    # ISM Services (monthly, 1er jour ouvré 14:00 UTC)
    (14, 0, "MEDIUM", ["US500.cash"], "US ISM Services", [0, 1]),
    # Retail Sales US (monthly 12:30 UTC)
    (12, 30, "MEDIUM", ["US500.cash"], "US Retail Sales", [0]),
    # Fed Chair Speech (variable, approximé) — horaires imprévisibles → calendrier
    # dynamique requis. days=[] = ne bloque JAMAIS en statique (évite faux positif quotidien)
    (14, 0, "HIGH", ["XAUUSD", "US500.cash"], "Fed Chair Speech", []),
    # ── Gold Events ──
    # China Data (02:00 UTC) — GDP/PMI publiés en début de mois
    (2, 0, "MEDIUM", ["XAUUSD"], "China GDP/PMI", [0, 1]),
    # ── UK / EU Events (EURGBP — ajoutés 06 Aout 2026, condition risk-compliance) ──
    # BOE Rate Decision (monthly, jeudi 12:00 UTC, approx)
    (12, 0, "HIGH", ["EURGBP"], "BOE Rate Decision", [3]),
    # ECB Rate Decision (bi-monthly, jeudi 13:15 UTC, approx)
    (13, 15, "HIGH", ["EURGBP"], "ECB Rate Decision", [3]),
    # UK CPI (monthly, mercredi 06:00 UTC, approx)
    (6, 0, "HIGH", ["EURGBP"], "UK CPI", [2]),
    # UK GDP (monthly, mercredi/vendredi 06:00 UTC, approx)
    (6, 0, "MEDIUM", ["EURGBP"], "UK GDP", [2, 4]),
    # UK Retail Sales (monthly, vendredi 06:00 UTC, approx)
    (6, 0, "MEDIUM", ["EURGBP"], "UK Retail Sales", [4]),
    # UK Unemployment (monthly, mardi 06:00 UTC, approx)
    (6, 0, "MEDIUM", ["EURGBP"], "UK Unemployment", [1]),
    # German CPI / EU events (EURGBP sensibilité)
    (6, 0, "MEDIUM", ["EURGBP"], "German CPI", [0]),
    # ── Crypto Events ──
    # CME BTC Futures Settlement (16:00 UTC Fri)
    (16, 0, "MEDIUM", ["BTCUSD"], "CME Crypto Futures Settlement", [4]),
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
        """Charge les événements statiques (aujourd'hui et demain).

        🔧 FIX M-ML2 (16 Août 2026): Les événements mensuels/hebdomadaires
        (CPI, PPI, ISM, BOE, ECB...) étaient chargés pour AUJOURD'HUI à la même
        heure CHAQUE jour → XAUUSD/US500.cash bloqués tous les jours 12:15-12:40
        et 13:45-14:10 UTC (faux positifs massifs). Chaque événement porte un
        champ `days` (weekdays 0=Lundi..6=Dimanche) : il n'est chargé que si le
        jour courant (ou demain) y figure. days=[] → jamais chargé en statique.
        """
        now = datetime.now(timezone.utc)
        today = now.date()

        for hour, minute, impact, symbols, desc, days in STATIC_EVENTS:
            # Aujourd'hui — seulement si le jour de la semaine correspond
            if days and now.weekday() in days:
                ts = datetime(today.year, today.month, today.day, hour, minute, tzinfo=timezone.utc)
                if ts > now - timedelta(hours=1):
                    self._events.append(
                        NewsEvent(timestamp_utc=ts, impact=impact, symbols=symbols, description=desc, source="static")
                    )

            # Demain
            tomorrow = today + timedelta(days=1)
            if days and tomorrow.weekday() in days:
                ts_tomorrow = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=timezone.utc)
                self._events.append(
                    NewsEvent(timestamp_utc=ts_tomorrow, impact=impact, symbols=symbols, description=desc, source="static")
                )

    def _purge_expired(self, now: datetime | None = None) -> None:
        """Supprime les événements passés (sortis de la fenêtre post_minutes).

        🔧 FIX M-ML2 (16 Août 2026): Sans purge, _events accumulait les événements
        du jour+demain sans jamais les retirer → après ~36-48h TOUS les événements
        étaient dans le passé → PLUS AUCUN blocage (faux négatif silencieux).
        """
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=self._post_minutes)
        before = len(self._events)
        self._events = [e for e in self._events if e.timestamp_utc >= cutoff]
        if len(self._events) != before:
            logger.debug(f"  [NEWS] Purged {before - len(self._events)} événements expirés")

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

        # 🔧 FIX M-ML2: purger les événements passés avant de scanner
        self._purge_expired(now)

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
        self._purge_expired(now)
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
