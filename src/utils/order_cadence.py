"""Utilities to enforce per-symbol send cadence and exposure age checks.

This module stores a small JSON file mapping symbol -> last_send_ts (epoch
seconds) under `artifacts/live_trading/last_send_by_symbol.json` and exposes
lightweight wrappers used by scripts to decide if a send is permitted and to
record sends.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

OUT_DIR = Path('artifacts') / 'live_trading'
OUT_DIR.mkdir(parents=True, exist_ok=True)
LAST_FILE = OUT_DIR / 'last_send_by_symbol.json'


def _read() -> Dict[str, float]:
    try:
        if LAST_FILE.exists():
            return json.loads(LAST_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return {}


def _write(data: Dict[str, float]) -> None:
    tmp = LAST_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(LAST_FILE)


def can_send(
    symbol: str, cooldown_s: int = 930, now: Optional[float] = None
) -> bool:
    """Return True if we may send an order for ``symbol`` now.

    ``cooldown_s`` defaults to 930 seconds (15m30s).
    """
    if now is None:
        now = time.time()
    data = _read()
    last = float(data.get(symbol, 0))
    return (now - last) >= float(cooldown_s)


def record_send(symbol: str, now: Optional[float] = None) -> None:
    """Record that a send occured for `symbol` at `now` (epoch seconds)."""
    if now is None:
        now = time.time()
    data = _read()
    data[symbol] = float(now)
    _write(data)


def is_exposure_aged(
    created_ts: float, max_age_s: int = 1800, now: Optional[float] = None
) -> bool:
    """Return True if an order created at ``created_ts`` (epoch secs) is
    older than ``max_age_s``.

    Default ``max_age_s`` is 1800 seconds (30 minutes).
    """
    if now is None:
        now = time.time()
    return (now - float(created_ts)) >= float(max_age_s)
