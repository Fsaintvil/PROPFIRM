"""Tests pour news_filter.py — calendrier économique + logique de blocage FTMO."""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from engine_simple.news_filter import NewsFilter, NewsEvent, is_news_blocked, STATIC_EVENTS


# ============================================================
# Tests de la classe NewsFilter
# ============================================================


def test_news_filter_init():
    """Test l'initialisation du filtre."""
    nf = NewsFilter()
    assert nf._pre_minutes == 15
    assert nf._post_minutes == 10
    assert nf._high_impact_block is True


def test_news_filter_static_events():
    """Test le chargement des événements statiques."""
    nf = NewsFilter()
    # Devrait avoir au moins quelques événements (aujourd'hui + demain)
    assert len(nf._events) > 0


def test_news_event_is_active():
    """Test la vérification d'activité d'un événement."""
    now = datetime.now(timezone.utc)

    # Événement il y a 5 minutes → actif
    past_event = NewsEvent(
        timestamp_utc=now - timedelta(minutes=5), impact="HIGH", symbols=["EURUSD"], description="Test Event"
    )
    assert past_event.is_active(now, pre_minutes=15, post_minutes=10) is True

    # Événement dans 20 minutes → pas encore actif (hors pre_minutes)
    future_event = NewsEvent(
        timestamp_utc=now + timedelta(minutes=20), impact="HIGH", symbols=["EURUSD"], description="Test Future"
    )
    assert future_event.is_active(now, pre_minutes=15, post_minutes=10) is False

    # Événement il y a 20 minutes → plus actif (hors post_minutes)
    old_event = NewsEvent(
        timestamp_utc=now - timedelta(minutes=20), impact="HIGH", symbols=["EURUSD"], description="Test Old"
    )
    assert old_event.is_active(now, pre_minutes=15, post_minutes=10) is False


def test_is_news_blocked_no_event():
    """Test qu'aucun blocage sans événement."""
    nf = NewsFilter()
    # Utiliser un symbole sans événements
    blocked, reason = nf.is_news_blocked("XXXUSD")
    assert blocked is False


def test_is_news_blocked_manual_block():
    """Test le blocage manuel."""
    nf = NewsFilter()
    nf.block_symbol("EURUSD", minutes=30)

    blocked, reason = nf.is_news_blocked("EURUSD")
    assert blocked is True
    assert "Manual block" in reason


def test_is_news_blocked_custom_config():
    """Test avec configuration personnalisée."""
    nf = NewsFilter(
        {
            "news_pre_minutes": 5,
            "news_post_minutes": 5,
            "news_high_impact_block": True,
            "news_medium_impact_block": False,
            "news_low_impact_block": False,
        }
    )
    assert nf._pre_minutes == 5
    assert nf._post_minutes == 5
    assert nf._medium_impact_block is False


def test_add_event():
    """Test l'ajout d'un événement dynamique."""
    nf = NewsFilter()
    initial_count = len(nf._events)

    now = datetime.now(timezone.utc)
    nf.add_event(
        timestamp_utc=now + timedelta(minutes=10), impact="HIGH", symbols=["BTCUSD"], description="Test Dynamic Event"
    )

    assert len(nf._events) == initial_count + 1


def test_get_upcoming_events():
    """Test la récupération des événements à venir."""
    nf = NewsFilter()
    events = nf.get_upcoming_events("EURUSD", hours=24)
    assert isinstance(events, list)


def test_get_status():
    """Test le statut du filtre."""
    nf = NewsFilter()
    status = nf.get_status()
    assert "total_events" in status
    assert "active_now" in status
    assert "blocked_symbols" in status


# ============================================================
# Tests de la fonction convenience
# ============================================================


def test_convenience_is_news_blocked():
    """Test la fonction convenience is_news_blocked."""
    blocked, reason = is_news_blocked("XXXUSD")
    assert isinstance(blocked, bool)


# ============================================================
# Tests des événements statiques
# ============================================================


def test_static_events_structure():
    """Test la structure des événements statiques."""
    assert len(STATIC_EVENTS) > 0

    for event in STATIC_EVENTS:
        assert len(event) == 5  # (hour, minute, impact, symbols, description)
        hour, minute, impact, symbols, desc = event
        assert 0 <= hour <= 23
        assert 0 <= minute <= 59
        assert impact in ("HIGH", "MEDIUM", "LOW")
        assert isinstance(symbols, list)
        assert len(symbols) > 0
        assert isinstance(desc, str)


def test_static_events_have_major_pairs():
    """Test que les principaux symboles sont couverts."""
    all_symbols = set()
    for _, _, _, symbols, _ in STATIC_EVENTS:
        all_symbols.update(symbols)

    # XAUUSD devrait être couvert (China data)
    assert "XAUUSD" in all_symbols
    # BTCUSD devrait être couvert (crypto events)
    assert "BTCUSD" in all_symbols
