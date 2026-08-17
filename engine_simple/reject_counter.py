"""
Reject Counter — compteur silencieux de rejets par phase (16 Août 2026).
====================================================================
Instrumentation READ-ONLY : ne modifie AUCUNE décision. Compte simplement
pour chaque symbole POURQUOI un trade n'a pas eu lieu, sans log verbeux.

Phases suivies :
  - precheck   : risk_manager.pre_trade() échoue (spread, dd_warning, cooldown...)
  - strat_sel  : strategy_selector.should_trade() → score < min ou régime interdit
  - validator  : signal_validator → score < effective_min_score (dyn)
  - h4_dir     : H4 direction forte contre le signal
  - lot_calc   : calculate_lot échoue / lot invalide

Persistance : runtime/reject_counter.json (écrit périodiquement par
persist_if_stale() — max 1 écriture / 30s pour ne pas spammer le disque).

Usage (depuis le pipeline) :
    from engine_simple.reject_counter import count_reject, persist_if_stale
    count_reject(symbol, "precheck", "Spread too high")
"""

import json
import threading
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_OUT = _BASE / "runtime" / "reject_counter.json"

_LOCK = threading.Lock()
_COUNTER: dict = {
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "last_write": 0.0,
    "by_symbol": {},   # symbol -> {phase: count}
    "by_phase": {},    # phase -> count
    "total": 0,
}
_LAST_PERSIST = 0.0
_PERSIST_INTERVAL = 30.0


def count_reject(symbol: str, phase: str, reason: str = "") -> None:
    """Incrémente le compteur de rejet (thread-safe, aucune décision modifiée)."""
    global _COUNTER
    with _LOCK:
        if symbol not in _COUNTER["by_symbol"]:
            _COUNTER["by_symbol"][symbol] = {}
        sym = _COUNTER["by_symbol"][symbol]
        key = f"{phase}:{reason[:60]}" if reason else phase
        sym[key] = sym.get(key, 0) + 1
        _COUNTER["by_phase"][phase] = _COUNTER["by_phase"].get(phase, 0) + 1
        _COUNTER["total"] += 1


def persist_if_stale(force: bool = False) -> None:
    """Écrit le compteur sur disque si l'intervalle est dépassé."""
    global _LAST_PERSIST, _COUNTER
    now = time.time()
    if not force and now - _LAST_PERSIST < _PERSIST_INTERVAL:
        return
    with _LOCK:
        try:
            _OUT.parent.mkdir(parents=True, exist_ok=True)
            tmp = _OUT.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_COUNTER, f, ensure_ascii=False, indent=2)
            tmp.replace(_OUT)
            _COUNTER["last_write"] = now
            _LAST_PERSIST = now
        except Exception:
            pass  # silencieux — l'instrumentation ne doit jamais casser le robot


def snapshot() -> dict:
    """Retourne une copie du compteur (pour lecture par l'outil de monitoring)."""
    with _LOCK:
        return json.loads(json.dumps(_COUNTER))