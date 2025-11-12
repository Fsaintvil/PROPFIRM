"""Make the top-level ``scripts`` directory importable.

This file intentionally empty beyond a minimal docstring so that
imports like ``import scripts.foo`` or ``from scripts import bar``
work during the migration from legacy package layout.
"""

__all__ = []
"""Scripts package for PROPFIRM trading system.

This package contains all trading scripts and utilities.
"""

# Package marker for scripts
__version__ = "1.0.0"
