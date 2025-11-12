"""Shim for `MT5_FTMO_IA.models`.

Maps package path to `MT5_FTMO_IA/models` (if present) and to a top-level
`models/` directory as a fallback.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_candidates = [
    _ROOT / "MT5_FTMO_IA" / "models",
    _ROOT / "models",
]
for c in _candidates:
    if c.exists() and str(c) not in __path__:
        __path__.insert(0, str(c))

__all__ = []
