"""Session Filter — Filtre de session de marché pour 5 symboles actifs.

Gère les sessions de trading par symbole :
- XAUUSD: 24/7 (crypto-like, mais avec sessions Londres+NY plus actives)
- BTCUSD: 24/7 (crypto ne dort jamais)
- ETHUSD: 24/7 (crypto ne dort jamais)
- US500.cash: 24/7 (indices, mais sessions US plus actives)
- EURUSD: Londres + NY (8h-18h UTC)

Features:
- Détection de session actuelle
- Score d'activité par session
- Filtre de news (optionnel)
- Scoring combiné session + régime

Usage:
    sf = SessionFilter()
    score = sf.get_session_score("XAUUSD", current_hour_utc=14)
    is_active = sf.is_session_active("EURUSD", current_hour_utc=10)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger("session_filter")


@dataclass
class SessionInfo:
    """Informations sur la session actuelle."""

    symbol: str
    hour_utc: int
    session_name: str
    is_active: bool
    activity_score: float  # [0-1]
    description: str


# ============================================================================
# SESSION DEFINITIONS
# ============================================================================
# Sessions de marché (UTC)
SESSIONS = {
    "asian": {"hours": (0, 7), "name": "Asie (Tokyo)", "description": "Session asiatique — faible liquidité, range"},
    "london_open": {
        "hours": (7, 9),
        "name": "Ouverture Londres",
        "description": "Ouverture européenne — volatilité accrue",
    },
    "london": {"hours": (8, 12), "name": "Londres", "description": "Session européenne — haute liquidité"},
    "london_ny_overlap": {
        "hours": (13, 17),
        "name": "Overlap Londres-NY",
        "description": "Overlap — liquidité maximale",
    },
    "ny": {"hours": (17, 21), "name": "New York", "description": "Session américaine — bonne liquidité"},
    "ny_close": {"hours": (21, 23), "name": "Fermeture NY", "description": "Fin de session — liquidité décroissante"},
}

# Profils de session par symbole
SYMBOL_SESSIONS = {
    "XAUUSD": {
        "sessions": ["london_open", "london", "london_ny_overlap", "ny"],
        "peak_hours": [(8, 12), (13, 17)],
        "avoid_hours": [(0, 6)],  # Asie = faible liquidité or
        "base_activity": 0.6,  # Or trade 24/7 mais moins en Asie
        "multiplier": {
            "asian": 0.4,
            "london_open": 1.0,
            "london": 0.9,
            "london_ny_overlap": 1.0,
            "ny": 0.8,
            "ny_close": 0.5,
        },
    },
    "BTCUSD": {
        "sessions": ["asian", "london_open", "london", "london_ny_overlap", "ny", "ny_close"],
        "peak_hours": [(0, 23)],  # 24/7
        "avoid_hours": [],
        "base_activity": 0.9,  # Crypto 24/7 — élevée car toujours actif
        "multiplier": {
            "asian": 0.85,
            "london_open": 0.90,
            "london": 0.90,
            "london_ny_overlap": 1.0,
            "ny": 0.95,
            "ny_close": 0.85,
        },
    },
    "US500.cash": {
        "sessions": ["london_open", "london", "london_ny_overlap", "ny"],
        "peak_hours": [(13, 17), (17, 21)],  # US market hours
        "avoid_hours": [(0, 7)],  # Asie = faible liquidité
        "base_activity": 0.6,
        "multiplier": {
            "asian": 0.3,
            "london_open": 0.7,
            "london": 0.8,
            "london_ny_overlap": 1.0,
            "ny": 0.9,
            "ny_close": 0.6,
        },
    },
    "EURUSD": {
        "sessions": ["asian", "london_open", "london", "london_ny_overlap", "ny", "ny_close"],
        "peak_hours": [(7, 21)],  # Londres + NY (haute liquidité)
        "avoid_hours": [],  # Forex 24/5 — pas de blocage
        "base_activity": 0.85,  # Forex majeur — liquide 24h
        "multiplier": {
            "asian": 0.65,  # Asie = liquidité réduite
            "london_open": 0.90,
            "london": 1.0,  # Londres = liquidité max
            "london_ny_overlap": 1.0,  # Overlap = liquidité max
            "ny": 0.95,
            "ny_close": 0.70,  # Fin NY = liquidité réduite
        },
    },
}


class SessionFilter:
    """Filtre de session de marché pour les 5 symboles actifs."""

    def __init__(self):
        self._cache: dict[str, SessionInfo] = {}
        self._cache_ttl = 300  # 5 minutes

    def get_current_session(self, hour_utc: int) -> str:
        """Retourne la session actuelle basée sur l'heure UTC."""
        for session_name, session_def in SESSIONS.items():
            start, end = session_def["hours"]
            if start <= hour_utc < end:
                return session_name
        return "ny_close"  # Default

    def get_session_score(self, symbol: str, hour_utc: int | None = None) -> float:
        """Retourne le score d'activité pour un symbole à une heure donnée.

        Args:
            symbol: Nom du symbole (ex: "XAUUSD")
            hour_utc: Heure UTC (0-23). Si None, utilise l'heure actuelle.

        Returns:
            Score [0-1] où 1 = activité maximale
        """
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour

        session_config = SYMBOL_SESSIONS.get(symbol, SYMBOL_SESSIONS["XAUUSD"])
        current_session = self.get_current_session(hour_utc)

        # Base activity
        base = session_config["base_activity"]

        # Session multiplier
        mult = session_config["multiplier"].get(current_session, 0.5)

        # Check if in peak hours
        is_peak = any(start <= hour_utc < end for start, end in session_config["peak_hours"])
        peak_mult = 1.2 if is_peak else 0.8

        # Check if in avoid hours
        is_avoid = any(start <= hour_utc < end for start, end in session_config["avoid_hours"])
        avoid_mult = 0.3 if is_avoid else 1.0

        score = base * mult * peak_mult * avoid_mult
        return min(1.0, max(0.0, score))

    def is_session_active(self, symbol: str, hour_utc: int | None = None, min_score: float = 0.4) -> bool:
        """Vérifie si la session est active pour un symbole."""
        score = self.get_session_score(symbol, hour_utc)
        return score >= min_score

    def get_session_info(self, symbol: str, hour_utc: int | None = None) -> SessionInfo:
        """Retourne les informations détaillées de la session."""
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour

        current_session = self.get_current_session(hour_utc)
        session_def = SESSIONS.get(current_session, SESSIONS["ny_close"])
        score = self.get_session_score(symbol, hour_utc)

        return SessionInfo(
            symbol=symbol,
            hour_utc=hour_utc,
            session_name=session_def["name"],
            is_active=score >= 0.4,
            activity_score=round(score, 3),
            description=session_def["description"],
        )

    def get_trading_hours(self, symbol: str) -> list[tuple[int, int]]:
        """Retourne les heures de trading recommandées pour un symbole."""
        config = SYMBOL_SESSIONS.get(symbol, SYMBOL_SESSIONS["XAUUSD"])
        return config["peak_hours"]

    def get_avoid_hours(self, symbol: str) -> list[tuple[int, int]]:
        """Retourne les heures à éviter pour un symbole."""
        config = SYMBOL_SESSIONS.get(symbol, SYMBOL_SESSIONS["XAUUSD"])
        return config["avoid_hours"]

    def get_all_sessions_status(self, hour_utc: int | None = None) -> dict[str, SessionInfo]:
        """Retourne le statut de session pour tous les symboles."""
        if hour_utc is None:
            hour_utc = datetime.now(timezone.utc).hour

        status = {}
        for symbol in SYMBOL_SESSIONS:
            status[symbol] = self.get_session_info(symbol, hour_utc)

        return status


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_filter = SessionFilter()


def is_session_active(symbol: str, hour_utc: int | None = None) -> bool:
    """Vérifie si la session est active (fonction convenience)."""
    return _default_filter.is_session_active(symbol, hour_utc)


def get_session_score(symbol: str, hour_utc: int | None = None) -> float:
    """Retourne le score de session (fonction convenience)."""
    return _default_filter.get_session_score(symbol, hour_utc)
