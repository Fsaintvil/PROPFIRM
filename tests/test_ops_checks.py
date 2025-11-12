import os
import pytest

from src.utils import ops_checks as ops


def test_require_env_flags_noop_under_pytest(monkeypatch):
    # When running under pytest, the function is intentionally a no-op.
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "something")
    # should not raise
    ops.require_env_flags_or_raise()


def test_require_env_flags_enforced(monkeypatch):
    # Simulate non-pytest runtime by removing the pytest env marker
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    # set all required flags to '1' -> no raise
    for f in ops.REQUIRED_FLAGS:
        monkeypatch.setenv(f, "1")
    ops.require_env_flags_or_raise()

    # unset one required flag -> should raise OpsCheckError
    monkeypatch.delenv(ops.REQUIRED_FLAGS[0], raising=False)
    with pytest.raises(ops.OpsCheckError):
        ops.require_env_flags_or_raise()
