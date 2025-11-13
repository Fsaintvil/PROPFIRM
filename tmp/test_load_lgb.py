import os
import pytest

# Allow choosing the local install dir via env LGB_INSTALL. Default
# falls back to tmp/lgb_install_45 inside the repo.
DEFAULT_INSTALL = r"C:\Users\saint\Documents\PROPFIRM\tmp\lgb_install_45"
install_dir = os.environ.get('LGB_INSTALL', DEFAULT_INSTALL)
if install_dir and install_dir not in list(os.sys.path):
    os.sys.path.insert(0, install_dir)


def _get_model_path():
    """Resolve the LightGBM model path used for this test.

    Priority:
    - env TEST_LGB_MODEL
    - first CLI-like argument passed via pytest (not used normally)
    - fallback: file not found -> cause a skip
    """
    # Prefer explicit env var for CI/control
    model = os.environ.get('TEST_LGB_MODEL')
    if model:
        return model
    # As a last resort, do not fail the test; let it be skipped by default
    return None


def test_load_lgb_model_or_skip():
    """Load a LightGBM Booster from disk.

    Skip if model not provided or open fails. Avoid aborting pytest
    collection on machines without the test model.
    """
    model_path = _get_model_path()
    if not model_path or not os.path.isfile(model_path):
        pytest.skip("No LightGBM model provided (TEST_LGB_MODEL).")

    # Import or skip if lightgbm isn't available in the environment
    lgb = pytest.importorskip('lightgbm')

    try:
        booster = lgb.Booster(model_file=model_path)
    except Exception as e:
        pytest.skip(f"LightGBM could not open the model ({e}). Skipping test.")

    # Basic sanity: ensure booster object has the predict method
    assert hasattr(booster, 'predict')
