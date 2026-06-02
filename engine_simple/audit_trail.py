"""Audit Trail — décision logger structuré au format JSON

Chaque décision de trading est enregistrée avec :
  - timestamp ISO 8601
  - décision_id unique (uuid)
  - type : signal | risk_check | execution | error | state_change
  - contexte complet (symbol, price, params, risk_metrics)
  - chaîne de décision traçable

Usage:
    audit = AuditTrail()
    audit.log_decision("execution", {"symbol": "EURUSD", "action": "BUY", ...})
    audit.log_risk_check("PASS", {"daily_loss": 0.5, "dd": 3.2})
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("robot.audit")


class AuditTrail:
    def __init__(self, log_dir="logs/audit", max_bytes=50 * 1024 * 1024, backup_count=30):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._audit_logger = logging.getLogger("audit_trail")
        self._audit_logger.setLevel(logging.INFO)
        self._audit_logger.handlers.clear()
        handler = RotatingFileHandler(
            self.log_dir / "decisions.jsonl",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._audit_logger.addHandler(handler)
        self._decisions = []
        self._max_memory = 1000

    def _entry(self, decision_type, context, status=None):
        return {
            "decision_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": decision_type,
            "status": status,
            "context": context,
        }

    def log_decision(self, decision_type, context, status=None):
        entry = self._entry(decision_type, context, status)
        self._audit_logger.info(json.dumps(entry))
        self._decisions.append(entry)
        if len(self._decisions) > self._max_memory:
            self._decisions = self._decisions[-self._max_memory // 2:]

    def log_signal(self, symbol, action, score, confidence, regime, details=None):
        self.log_decision("signal", {
            "symbol": symbol,
            "action": action,
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "regime": regime,
            "details": details,
        })

    def log_risk_check(self, result, symbol=None, metrics=None):
        self.log_decision("risk_check", {
            "symbol": symbol,
            "result": result,
            "metrics": metrics or {},
        })

    def log_execution(self, symbol, action, entry, sl, tp, lot, status="sent", retcode=None):
        self.log_decision("execution", {
            "symbol": symbol,
            "action": action,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "status": status,
            "retcode": retcode,
        }, status=status)

    def log_error(self, source, message, exc_info=None):
        self.log_decision("error", {
            "source": source,
            "message": message,
            "exception": str(exc_info) if exc_info else None,
        }, status="ERROR")

    def log_state_change(self, key, old_value, new_value):
        self.log_decision("state_change", {
            "key": key,
            "from": old_value,
            "to": new_value,
        })

    def recent_decisions(self, n=10, decision_type=None):
        filtered = self._decisions
        if decision_type:
            filtered = [d for d in filtered if d["type"] == decision_type]
        return filtered[-n:]

    def flush(self):
        for h in self._audit_logger.handlers:
            h.flush()

    def close(self):
        self.flush()
        for h in self._audit_logger.handlers:
            h.close()
