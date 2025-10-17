"""Compatibility proxy package.

This top-level `scripts` package forwards imports to
`MT5_FTMO_IA.scripts` so older import styles work when running from the
repository root.
"""
from importlib import import_module
import sys

_internal = import_module("MT5_FTMO_IA.scripts")

# Re-export common names
globals().update(
    {
        k: getattr(_internal, k)
        for k in dir(_internal)
        if not k.startswith("__")
    }
)

# Ensure sys.modules mapping points to the internal package for submodule
# imports
sys.modules["scripts"] = _internal

"""Package marker for scripts to make imports stable in tests."""
