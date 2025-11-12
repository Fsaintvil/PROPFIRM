"""Shim for `MT5_FTMO_IA.scripts` that forwards imports to repo `scripts/` folder.

When you `import MT5_FTMO_IA.scripts.foo`, Python will search this package
path and find modules under the repository's top-level `scripts/` folder.
"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in __path__:
    # prefer explicit scripts/ folder at repo root
    __path__.insert(0, str(_SCRIPTS))

__all__ = []
