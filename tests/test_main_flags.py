"""Tests pour main.py — nettoyage des flags watchdog au démarrage.

🔧 FIX 20 Août 2026 (Auto-Fixer): un robot.stop.flag / robot.halt.flag résiduel
désactivait le watchdog externe (process_watchdog.py → "Auto-restart disabled,
monitoring only"), l'empêchant de ressusciter le robot en cas de gel
(gel 1h52 le 20/08 : 05:21→07:13 sans résurrection — robot.stop.flag datait
de 19/08 23:52, écrit par l'ancien PID 16972 via stop()).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as main_mod


class TestCleanWatchdogFlags:
    def test_cleans_stop_and_halt_flags(self, tmp_path, monkeypatch):
        """robot.stop.flag et robot.halt.flag sont supprimés au démarrage."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        (runtime / "robot.stop.flag").write_text("stopped 2026-08-19T23:52:10\n")
        (runtime / "robot.halt.flag").write_text("halted\n")
        (runtime / "stop_for_day.flag").write_text("daily loss > 1.8%\n")

        monkeypatch.chdir(tmp_path)
        main_mod._clean_watchdog_flags()

        assert not (runtime / "robot.stop.flag").exists()
        assert not (runtime / "robot.halt.flag").exists()
        # stop_for_day.flag = décision kill-switch/ai-manager → CONSERVÉ
        # (un démarrage manuel ne doit PAS annuler un stop d'urgence)
        assert (runtime / "stop_for_day.flag").exists()

    def test_clean_no_runtime_dir_no_error(self, tmp_path, monkeypatch):
        """Pas de dossier runtime/ → aucun crash."""
        monkeypatch.chdir(tmp_path)
        main_mod._clean_watchdog_flags()  # ne doit pas lever d'exception

    def test_clean_absent_flags_no_error(self, tmp_path, monkeypatch):
        """Aucun flag présent → aucun crash, aucun flag créé."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        monkeypatch.chdir(tmp_path)
        main_mod._clean_watchdog_flags()
        assert list(runtime.iterdir()) == []