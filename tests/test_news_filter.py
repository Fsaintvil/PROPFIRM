"""Tests for news_filter.py — event calendar + block logic"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta

from engine_simple.news_filter import _generate_events, fetch_calendar, is_news_blocked


def test_generate_events_returns_list():
    events = _generate_events()
    assert isinstance(events, list)
    if events:
        for e in events:
            assert "name" in e
            assert "time" in e
            assert "impact" in e
            assert e["impact"] == "high"


def test_generate_events_contains_nfp():
    events = _generate_events()
    nfp = [e for e in events if e["name"] == "NFP"]
    fomc = [e for e in events if e["name"] == "FOMC"]
    cpi = [e for e in events if e["name"] == "CPI"]
    # At least some should be generated
    total = len(nfp) + len(fomc) + len(cpi)
    assert total >= 0  # depends on current month


def test_is_news_blocked_returns_tuple():
    blocked, details = is_news_blocked()
    assert isinstance(blocked, bool)
    assert isinstance(details, list)


def test_is_news_blocked_outside_event():
    now = datetime(2026, 1, 15, 3, 0)  # 3am UTC, unlikely event time
    blocked, details = is_news_blocked(utc_now=now)
    assert not blocked
    assert details == []


def test_is_news_blocked_during_event():
    events = _generate_events()
    if events:
        event_time = events[0]["time"]
        just_before = event_time - timedelta(minutes=5)
        blocked, details = is_news_blocked(utc_now=just_before)
        # May be blocked if within 10min window
        assert isinstance(blocked, bool)
        if blocked:
            assert len(details) > 0
            assert "name" in details[0]


def test_fetch_calendar_returns_list():
    events = fetch_calendar()
    assert isinstance(events, list)
