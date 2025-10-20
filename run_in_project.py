#!/usr/bin/env python3
"""Run a module from the repository root with correct PYTHONPATH.

Usage:
  python run_in_project.py MT5_FTMO_IA.scripts._mt5_readiness

This helper ensures the repository root and the `MT5_FTMO_IA` package
are importable before running the requested module.
"""

import sys
import runpy
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_in_project.py <module.name> [args...]")
        sys.exit(2)

    module_name = sys.argv[1]

    # Insert repo root and MT5_FTMO_IA package path at front of sys.path
    repo_root = Path(__file__).resolve().parent
    mt5_pkg = repo_root / "MT5_FTMO_IA"
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(mt5_pkg))

    # Forward remaining args via sys.argv
    sys.argv = [module_name] + sys.argv[2:]

    try:
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
    except Exception:
        # Print full traceback for debugging and re-raise
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
