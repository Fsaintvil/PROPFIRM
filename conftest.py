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
    return False
