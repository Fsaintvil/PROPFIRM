import os
import sys

# Ensure project root is first on sys.path so imports like `import scripts`
# resolve to the repo's `scripts/` package, not to similarly-named files
# in archive/ or other sibling directories accidentally added earlier.
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def pytest_ignore_collect(path, config):
    """Ignore collection of files inside archive directories or virtualenvs.

    This prevents pytest from importing archived helper scripts (for
    example under MT5_FTMO_IA/archive/obsolete/scripts) which can shadow
    the real package modules and cause import collisions.
    """
    p = str(path)
    if os.path.sep + "archive" + os.path.sep in p:
        return True
    if os.path.sep + "venv" in p or os.path.sep + ".venv" in p:
        return True
    # Ignore pytest collection inside temporary build/install dirs created
    # by local experiments (e.g. tmp/lgb_install_45) or generic tmp folders.
    if os.path.sep + "tmp" + os.path.sep in p:
        return True
    if "lgb_install_" in p:
        return True
    # Skip tests that refer to optional MT5 helper directories when those
    # directories are not present on this machine.
    if os.path.sep + "MT5_FTMO_IA" + os.path.sep in p:
        if not os.path.exists(os.path.join(PROJECT_ROOT, "MT5_FTMO_IA")):
            return True
    # Skip collection of ad-hoc test scripts that live under `scripts/`
    # (these are integration helpers, not unit tests) when they are
    # present as top-level script files.
    if os.path.sep + "scripts" + os.path.sep in p:
        name = os.path.basename(p)
        if name.startswith("test_") or name.endswith("_test.py"):
            return True
    # Ignore temporary ad-hoc test files that start with tmp_
    if os.path.basename(p).startswith("tmp_"):
        return True
    return False
