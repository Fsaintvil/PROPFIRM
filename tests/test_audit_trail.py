"""Tests dédiés pour AuditTrail — logging structuré des décisions"""

import json
import tempfile
from pathlib import Path

from engine_simple.audit_trail import AuditTrail


class TestAuditTrail:
    """🔧 FIX 16 Juillet 2026: tempfile.mkdtemp() → TemporaryDirectory avec cleanup.
    mkdtemp() créait des répertoires temporaires sans cleanup, laissant des résidus
    sur le disque après chaque exécution de test.
    """

    def setup_method(self):
        self._tmp_dirs = []

    def teardown_method(self):
        for d in self._tmp_dirs:
            try:
                d.cleanup()
            except Exception:
                pass
        self._tmp_dirs.clear()

    def _make_audit(self):
        """Helper: crée un AuditTrail avec un répertoire temporaire auto-nettoyant."""
        tmp_dir = tempfile.TemporaryDirectory()
        self._tmp_dirs.append(tmp_dir)
        return AuditTrail(log_dir=tmp_dir.name)

    def test_log_decision_creates_entry(self):
        audit = self._make_audit()
        audit.log_decision("test", {"k": "v"}, status="OK")
        assert len(audit._decisions) == 1
        entry = audit._decisions[0]
        assert entry["type"] == "test"
        assert entry["context"] == {"k": "v"}
        assert entry["status"] == "OK"
        assert "decision_id" in entry
        assert "timestamp" in entry
        audit.close()

    def test_log_decision_writes_to_file(self):
        audit = self._make_audit()
        d = self._tmp_dirs[-1].name  # répertoire temporaire de _make_audit
        audit.log_decision("test", {"k": "v"})
        audit.flush()
        audit.close()
        lines = list(Path(d, "decisions.jsonl").read_text().strip().splitlines())
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "test"

    def test_log_signal(self):
        audit = self._make_audit()
        audit.log_signal("EURUSD", "BUY", 0.75, 0.8, "TREND_UP", {"reason": "breakout"})
        entry = audit._decisions[0]
        assert entry["type"] == "signal"
        assert entry["context"]["symbol"] == "EURUSD"
        assert entry["context"]["action"] == "BUY"
        assert entry["context"]["score"] == 0.75
        assert entry["context"]["details"] == {"reason": "breakout"}
        audit.close()

    def test_log_signal_without_details(self):
        audit = self._make_audit()
        audit.log_signal("GBPUSD", "SELL", 0.6, 0.7, "RANGING")
        assert audit._decisions[0]["context"]["details"] is None
        audit.close()

    def test_log_risk_check(self):
        audit = self._make_audit()
        audit.log_risk_check("PASS", "EURUSD", {"dd": 3.2})
        entry = audit._decisions[0]
        assert entry["type"] == "risk_check"
        assert entry["context"]["result"] == "PASS"
        assert entry["context"]["metrics"] == {"dd": 3.2}
        audit.close()

    def test_log_risk_check_without_metrics(self):
        audit = self._make_audit()
        audit.log_risk_check("BLOCKED")
        assert audit._decisions[0]["context"]["metrics"] == {}
        audit.close()

    def test_log_execution(self):
        audit = self._make_audit()
        audit.log_execution("USDJPY", "BUY", 150.0, 149.5, 151.0, 0.1, "sent", 10009)
        entry = audit._decisions[0]
        assert entry["type"] == "execution"
        assert entry["context"]["symbol"] == "USDJPY"
        assert entry["context"]["lot"] == 0.1
        assert entry["status"] == "sent"
        audit.close()

    def test_log_execution_defaults(self):
        audit = self._make_audit()
        audit.log_execution("EURUSD", "SELL", 1.1, 1.12, 1.08, 0.5)
        entry = audit._decisions[0]
        assert entry["status"] == "sent"
        assert entry["context"]["retcode"] is None
        audit.close()

    def test_log_error_without_exception(self):
        audit = self._make_audit()
        audit.log_error("module_x", "something failed")
        entry = audit._decisions[0]
        assert entry["type"] == "error"
        assert entry["status"] == "ERROR"
        audit.close()

    def test_log_error_with_exception(self):
        audit = self._make_audit()
        audit.log_error("module_x", "boom", Exception("test error"))
        entry = audit._decisions[0]
        assert "test error" in entry["context"]["exception"]
        audit.close()

    def test_log_state_change(self):
        audit = self._make_audit()
        audit.log_state_change("mode", "dry_run", "live")
        entry = audit._decisions[0]
        assert entry["type"] == "state_change"
        assert entry["context"]["from"] == "dry_run"
        assert entry["context"]["to"] == "live"
        audit.close()

    def test_recent_decisions(self):
        audit = self._make_audit()
        for i in range(10):
            audit.log_decision("test", {"i": i})
        assert len(audit.recent_decisions(3)) == 3
        assert audit.recent_decisions(3)[0]["context"]["i"] == 7
        audit.close()

    def test_recent_decisions_filtered_by_type(self):
        audit = self._make_audit()
        audit.log_decision("signal", {"s": 1})
        audit.log_decision("risk", {"r": 2})
        audit.log_decision("signal", {"s": 3})
        assert len(audit.recent_decisions(5, "signal")) == 2
        assert len(audit.recent_decisions(5, "risk")) == 1
        audit.close()

    def test_max_memory_trimming(self):
        audit = self._make_audit()
        audit._max_memory = 10
        for i in range(20):
            audit.log_decision("test", {"i": i})
        assert len(audit._decisions) <= 10
        audit.close()

    def test_flush(self):
        audit = self._make_audit()
        audit.log_decision("test", {"k": "v"})
        audit.flush()
        audit.close()

    def test_close(self):
        audit = self._make_audit()
        audit.log_decision("test", {"k": "v"})
        audit.close()
