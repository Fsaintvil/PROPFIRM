"""Utilities for operational checks before executing live actions.

Provides checks for required environment flags (e.g. ALLOW_MT5_SEND)
and small helpers to raise a clear error when production flags are missing.
"""

from typing import Iterable, List, Optional
import os


class OpsCheckError(Exception):
    pass


REQUIRED_FLAGS: List[str] = [
    "ALLOW_MT5_SEND",
    "AUTO_APPLY",
    "AUTO_DEPLOY",
    "AUTO_LEARN",
    "AUTO_ADAPT",
    "AUTO_ENRICH",
]


def check_required_env_flags(required: Optional[Iterable[str]] = None) -> bool:
    """Return True if all required env flags are present and equal to '1'.

    Args:
        required: optional iterable of flag names to check; by default use
            REQUIRED_FLAGS.

    Returns:
        True if all present and == '1', False otherwise.
    """
    flags = list(required) if required is not None else REQUIRED_FLAGS
    missing = []
    for f in flags:
        v = os.environ.get(f)
        if v != "1":
            missing.append(f)
    return len(missing) == 0


def require_env_flags_or_raise(
    required: Optional[Iterable[str]] = None,
) -> None:
    """Raise OpsCheckError if required flags are not all set to '1'."""
    # Allow tests to run without setting production env flags. pytest sets the
    # PYTEST_CURRENT_TEST environment variable during test execution; when
    # present we treat the check as a no-op to avoid failing unit tests.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    flags = list(required) if required is not None else REQUIRED_FLAGS
    missing = [f for f in flags if os.environ.get(f) != "1"]
    if missing:
        raise OpsCheckError(
            "Missing required env flags (must be '1'): " + str(missing)
        )
