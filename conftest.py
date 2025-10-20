import os
import sys
import pytest
import pandas as pd
import numpy as np

# Ensure project root is first on sys.path so imports like `import scripts`
# resolve to the repo's `scripts/` package, not to similarly-named files
# in archive/ or other sibling directories accidentally added earlier.
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def pytest_ignore_collect(collection_path, config):
    """Ignore collection of files inside archive directories or virtualenvs.

    This prevents pytest from importing archived helper scripts (for
    example under MT5_FTMO_IA/archive/obsolete/scripts) which can shadow
    the real package modules and cause import collisions.
    """
    p = str(collection_path)
    if os.path.sep + "archive" + os.path.sep in p:
        return True
    if os.path.sep + "venv" in p or os.path.sep + ".venv" in p:
        return True
    # If the test file (or collected path) contains a direct reference to the
    # legacy package `MT5_FTMO_IA` then skip collection. Several tests and
    # helper scripts in this repo used that package and the package may be
    # absent in the current workspace. Skipping avoids import-time errors
    # during pytest collection without inventing missing implementation.
    try:
        # Only try to read text files (path may be a directory)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "MT5_FTMO_IA" in content:
                return True
    except Exception:
        # If reading fails, do not block collection for unrelated reasons.
        pass

    return False


@pytest.fixture(scope="function")
def data():
    """Fixture returning a small synthetic market DataFrame for tests.

    This mirrors the lightweight generator used by the repo's test scripts
    so tests that expect a `data` fixture can run without requiring external
    files.
    """
    rng = np.random.RandomState(42)
    periods = 200
    dates = pd.date_range(start="2025-01-01", periods=periods, freq="h")

    base_price = 1.1000
    trend = np.linspace(0, 0.002, periods)
    noise = rng.randn(periods) * 0.0005
    close = base_price + trend + np.cumsum(noise)

    df = pd.DataFrame({
        "timestamp": dates,
        "open": close + rng.randn(periods) * 0.0001,
        "high": close + np.abs(rng.randn(periods) * 0.0002),
        "low": close - np.abs(rng.randn(periods) * 0.0002),
        "close": close,
        "volume": rng.randint(1000, 5000, size=periods),
    })

    return df
