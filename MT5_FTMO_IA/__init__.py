"""Compatibility shim package for legacy imports of `MT5_FTMO_IA`.

This module makes it easy to run the repository even when other code
imports `MT5_FTMO_IA.*`. The shim adjusts the package search path
so that subpackages like `MT5_FTMO_IA.scripts` and
`MT5_FTMO_IA.tools` can resolve to top-level `scripts/` and
`tools/` directories in this repository.

The shim is intentionally conservative:
- it does not modify source files elsewhere,
- it only adjusts `__path__` entries when the expected target
  directories exist, and
- it exposes `__shim_info__` for diagnostics.

This is a low-risk compatibility layer intended as a short-term
measure to keep tests and scripts working while a planned migration
is prepared.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

# repo root is the parent directory of this package dir
_ROOT = Path(__file__).resolve().parents[1]

# Candidate locations that we want Python to search for subpackages
# when someone imports `MT5_FTMO_IA.scripts.*` or `MT5_FTMO_IA.tools.*`.
_CANDIDATES: List[str] = [
    str(_ROOT),  # allow import discovery against repo root
    str(_ROOT / "scripts"),
    str(_ROOT / "tools"),
    str(_ROOT / "MT5_FTMO_IA"),
]

# Replace __path__ with the subset of candidate directories that exist.
__path__: List[str] = [p for p in _CANDIDATES if Path(p).exists()]

# Provide a small diagnostic object that callers / tests can inspect.
__shim_info__ = {
    "shim_active": True,
    "repo_root": str(_ROOT),
    "candidates": _CANDIDATES,
    "resolved_path": list(__path__),
}


def shim_summary() -> str:
    """Return a short human readable summary of what the shim configured."""
    lines = [f"MT5_FTMO_IA shim active (repo_root={_ROOT})"]
    lines.append("Resolved __path__ entries:")
    for p in __path__:
        lines.append(f" - {p}")
    return "\n".join(lines)


__all__ = ["__shim_info__", "shim_summary"]
"""Compatibility shim package for legacy imports named `MT5_FTMO_IA`.

This package provides lightweight namespace packages that forward
subpackages (notably `scripts`, `models`, `control`, `data`) to
top-level folders in the repository so existing imports like
`MT5_FTMO_IA.scripts.mt5_connector` continue to work while the
repository migrates to a canonical layout.

This shim is intentionally minimal and non-invasive. It emits a
single informational note when first imported to remind operators
that this is a compatibility shim.
"""
from pathlib import Path
import sys
import warnings

__all__ = ["scripts", "models", "control", "data"]

# Best-effort repo root detection: two levels up from this file
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Small, one-time informational message (avoid spamming during tests)
if not getattr(sys.modules.get(__name__), "_MT5_SHIM_WARNED", False):
    warnings.warn(
        "MT5_FTMO_IA compatibility shim is active. Prefer migrating imports to the canonical layout.",
        UserWarning,
    )
    # mark warned
    setattr(sys.modules[__name__], "_MT5_SHIM_WARNED", True)
