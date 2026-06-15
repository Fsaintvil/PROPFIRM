"""Tests pour news_filter.py — calendrier économique + logique de blocage FTMO."""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from engine_simple.news_filter import (
    _generate_events, fetch_calendar, is_news_blocked,
    _load_manual_events, BLOCK_MINUTES_BEFORE, BLOCK_MINUTES_AFTER,
    MANUAL_EVENTS_FILE
)


# ============================================================
# Tests de génération d'événements
# ============================================================

def test_generate_events_returns_list():
    events = _generate_events()
    assert isinstance(events, list)
    assert len(events) > 0, "Doit generer au moins quelques evenements"
    for e in events:
        assert "name" in e
        assert "time" in e
        assert "impact" in e
        assert e["impact"] == "high"
        assert isinstance(e["time"], datetime)
        assert e.get("is_generated") is True


def test_generate_events_contains_major_events():
    events = _generate_events()
    names = [e["name"] for e in events]
    assert "NFP (US)" in names, "NFP doit etre genere"
    assert "CPI (US)" in names, "CPI US doit etre genere"
    assert "FOMC Rate Decision" in names, "FOMC doit etre genere"
    assert "CPI (UK)" in names, "CPI UK doit etre genere"


def test_generate_events_multi_currency():
    """Verifie que les 5 devises sont couvertes."""
    events = _generate_events()
    names = " ".join(e["name"] for e in events)
    assert "(US)" in names
    assert "(UK)" in names or "GDP (UK)" in names
    assert "(EU)" in names or "CPI (EU)" in names
    assert "(Canada)" in names
    assert "(Switzerland)" in names or "SNB" in names


def test_nfp_first_friday():
    """NFP doit tomber un vendredi entre 1er et 14 du mois."""
    events = _generate_events()
    nfps = [e for e in events if "NFP" in e["name"]]
    assert len(nfps) >= 1
    for nfp in nfps:
        t = nfp["time"]
        assert t.weekday() == 4, f"NFP pas un vendredi: {t}"
        assert 1 <= t.day <= 14, f"NFP jour invalide: {t.day}"


def test_block_window_size():
    """Verifie que la fenetre de blocage (importee depuis config)."""
    assert BLOCK_MINUTES_BEFORE == 5  # config_simple.NEWS_MINUTES_BEFORE
    assert BLOCK_MINUTES_AFTER == 5   # config_simple.NEWS_MINUTES_AFTER


# ============================================================
# Tests is_news_blocked
# ============================================================

def test_is_news_blocked_returns_tuple():
    blocked, details = is_news_blocked()
    assert isinstance(blocked, bool)
    assert isinstance(details, list)


def test_is_news_blocked_outside_event():
    """3h du matin — aucun evenement prevu."""
    now = datetime(2026, 1, 15, 3, 0)
    blocked, details = is_news_blocked(utc_now=now)
    assert not blocked
    assert details == []


def test_is_news_blocked_during_nfp():
    """Simule un moment pendant NFP."""
    events = _generate_events()
    nfps = [e for e in events if "NFP" in e["name"]]
    if nfps:
        event_time = nfps[0]["time"]
        # 1 minute avant NFP = bloqué
        just_before = event_time - timedelta(minutes=1)
        blocked, details = is_news_blocked(utc_now=just_before)
        assert blocked, f"Devrait etre bloque 1min avant NFP ({event_time})"
        assert any("NFP" in d["name"] for d in details)
        # Verifier seconds_remaining
        assert details[0]["seconds_remaining"] > 0


def test_is_news_blocked_edge_before():
    """En dehors de la fenêtre de blocage (20 min avant) — pas bloqué."""
    events = _generate_events()
    nfps = [e for e in events if "NFP" in e["name"]]
    if nfps:
        event_time = nfps[0]["time"]
        # 20 minutes avant (fenetre = 15min) = pas bloqué
        before_window = event_time - timedelta(minutes=20)
        blocked, details = is_news_blocked(utc_now=before_window)
        assert not blocked, f"Ne devrait PAS etre bloque 20min avant NFP"


def test_is_news_blocked_edge_after():
    """En dehors de la fenêtre de blocage (20 min après) — pas bloqué."""
    events = _generate_events()
    nfps = [e for e in events if "NFP" in e["name"]]
    if nfps:
        event_time = nfps[0]["time"]
        # 20 minutes après (fenetre = 15min) = pas bloqué
        after_window = event_time + timedelta(minutes=20)
        blocked, details = is_news_blocked(utc_now=after_window)
        assert not blocked, f"Ne devrait PAS etre bloque 20min apres NFP"


# ============================================================
# Tests du cache
# ============================================================

def test_fetch_calendar_returns_list():
    events = fetch_calendar()
    assert isinstance(events, list)
    assert len(events) > 0, "Le calendrier doit contenir des evenements"
    for e in events:
        assert "name" in e
        assert "time" in e
        assert "impact" in e


def test_cache_serialization():
    """Verifie que les evenements sont serialisables en JSON."""
    events = _generate_events()
    serializable = []
    for e in events:
        ev = dict(e)
        if isinstance(ev.get("time"), datetime):
            ev["time"] = ev["time"].isoformat()
        serializable.append(ev)
    # Doit pouvoir etre serialise sans erreur
    json_str = json.dumps(serializable)
    assert len(json_str) > 0
    # Et deserialise
    loaded = json.loads(json_str)
    assert len(loaded) == len(serializable)


# ============================================================
# Tests du fichier manuel
# ============================================================

def test_manual_events_file_exists():
    """Le fichier de config manuelle doit exister."""
    assert os.path.exists(MANUAL_EVENTS_FILE), f"{MANUAL_EVENTS_FILE} introuvable"


def test_manual_events_valid_json():
    """Le fichier manuel doit etre un JSON valide avec des evenements."""
    events = _load_manual_events()
    assert isinstance(events, list)
    if events:
        for e in events:
            assert "name" in e
            assert "time" in e
            assert isinstance(e["time"], datetime)
            assert e.get("manual") is True


# ============================================================
# Test d'intégration: ftmo_protector appelle is_news_blocked
# ============================================================

def test_news_filter_imports_cleanly():
    """Verifie que le module news_filter s'importe sans erreur dans le contexte du robot."""
    # On importe juste pour verifier qu'il n'y a pas de dependance manquante
    from engine_simple import news_filter
    assert hasattr(news_filter, "is_news_blocked")
    assert hasattr(news_filter, "fetch_calendar")
    assert hasattr(news_filter, "_generate_events")


# ============================================================
# Test de performance
# ============================================================

def test_is_news_blocked_fast():
    """is_news_blocked doit etre rapide (< 100ms)."""
    import time
    start = time.time()
    is_news_blocked()
    elapsed = (time.time() - start) * 1000
    assert elapsed < 100, f"Trop lent: {elapsed:.0f}ms"
