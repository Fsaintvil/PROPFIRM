"""Shim for `MT5_FTMO_IA.data` pointing to repo data folders.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_candidates = [
    _ROOT / "MT5_FTMO_IA" / "data",
    _ROOT / "data",
]
for c in _candidates:
    if c.exists() and str(c) not in __path__:
        __path__.insert(0, str(c))

__all__ = []
