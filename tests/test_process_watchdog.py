"""Tests for scripts/process_watchdog.py — flag d'arrêt gracieux (FIX 19 Août 2026)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from process_watchdog import is_graceful_stop_requested


def _make_hb(tmp_path) -> Path:
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("alive")
    return hb


def test_no_flag_not_graceful(tmp_path):
    assert is_graceful_stop_requested(_make_hb(tmp_path)) is False


def test_robot_stop_flag_graceful(tmp_path):
    hb = _make_hb(tmp_path)
    (tmp_path / "robot.stop.flag").write_text("stopped")
    assert is_graceful_stop_requested(hb) is True


def test_stop_for_day_flag_graceful(tmp_path):
    """🔧 FIX 19 Août 2026 (Kill Switch): stop_for_day.flag doit bloquer le respawn."""
    hb = _make_hb(tmp_path)
    (tmp_path / "stop_for_day.flag").write_text("daily loss > 1.8%")
    assert is_graceful_stop_requested(hb) is True


def test_robot_stop_flag_ignores_halt(tmp_path):
    """robot.halt.flag seul n'est PAS un stop gracieux (c'est un arrêt opérateur)."""
    hb = _make_hb(tmp_path)
    (tmp_path / "robot.halt.flag").write_text("halted")
    # halt.flag est géré séparément (voir attempt_restart) — is_graceful_stop
    # ne doit pas le confondre avec un arrêt volontaire de l'opérateur.
    assert is_graceful_stop_requested(hb) is False