import importlib


def test_mt5_shim_is_importable():
    """Basic smoke test: the compatibility shim imports and exposes diagnostics."""
    m = importlib.import_module("MT5_FTMO_IA")
    assert hasattr(m, "__shim_info__"), "__shim_info__ missing"
    si = getattr(m, "__shim_info__")
    assert isinstance(si, dict)
    # resolved_path should be a list (possibly empty in some CI setups)
    assert "resolved_path" in si
    assert isinstance(si["resolved_path"], list)
