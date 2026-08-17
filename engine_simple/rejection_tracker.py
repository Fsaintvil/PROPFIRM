#!/usr/bin/env python3
"""
Rejection Tracker — compteur observationnel des rejets de signaux par phase.
Créé 16 Août 2026 (Robot Manager) — Phase 1, outil read-only.

But : identifier précisément POURQUOI chaque symbole ne produit pas de trade,
sans modifier la logique de décision. Enregistre (symbole, phase, raison) et
persiste périodiquement dans runtime/rejections.json.

Usage :
    from engine_simple.rejection_tracker import count_rejection, flush_rejections
    count_rejection("US30.cash", "pre_trade", "Spread too high")
    flush_rejections()   # écrit le JSON (appelé périodiquement par le robot)

Le fichier runtime/rejections.json est ensuite analysé par scripts/check_gr_symbols.py
ou directement pour le rapport de goulots.
"""

import json
import threading
import time
from collections import defaultdict
from pathlib import Path

RUNTIME_DIR = Path(__file__).parent.parent / "runtime"
REJECTIONS_FILE = RUNTIME_DIR / "rejections.json"

# Compteurs en mémoire : {phase: {symbol: {reason: count}}}
_counters: dict[str, dict[str, dict[str, int]]] = defaultdict(
    lambda: defaultdict(lambda: defaultdict(int))
)
_lock = threading.Lock()
_last_flush = time.time()
_flush_interval = 300  # 5 min — flush périodique

# Réduit le bruit : raisons trop détaillées (ex: chiffres) normalisées
def _normalize_reason(reason: str, max_len: int = 80) -> str:
    if not reason:
        return "unknown"
    # Coupe la raison au premier '=' suivi d'un nombre (ex: 'DD 1.0% proche')
    return reason[:max_len]


def count_rejection(symbol: str, phase: str, reason: str | None = None) -> None:
    """Enregistre un rejet. Ne lève jamais d'exception (fail-open)."""
    try:
        reason = _normalize_reason(reason or "unknown")
        with _lock:
            _counters[phase][symbol][reason] += 1
    except Exception:
        pass


def flush_rejections(force: bool = False) -> dict:
    """Écrit les compteurs dans runtime/rejections.json. Retourne le snapshot."""
    global _last_flush
    try:
        now = time.time()
        if not force and (now - _last_flush) < _flush_interval:
            return {}
        with _lock:
            snapshot = {
                phase: {
                    sym: dict(reasons)
                    for sym, reasons in syms.items()
                }
                for phase, syms in _counters.items()
            }
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with open(REJECTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "phases": snapshot,
            }, f, ensure_ascii=False, indent=2)
        _last_flush = now
        return snapshot
    except Exception:
        return {}


def get_rejections() -> dict:
    """Retourne le snapshot courant (lecture seule, sans flush)."""
    with _lock:
        return {
            phase: {
                sym: dict(reasons)
                for sym, reasons in syms.items()
            }
            for phase, syms in _counters.items()
        }


def load_from_disk() -> dict:
    """Charge le dernier état persisté (pour analyse offline)."""
    try:
        if REJECTIONS_FILE.exists():
            return json.loads(REJECTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}