import json


def test_get_config_returns_empty_when_missing(tmp_path):
    cfg_path = tmp_path / "nonexistent.json"
    # Import locally to avoid side effects
    from tools.config_loader import get_config

    res = get_config(path=str(cfg_path))
    assert isinstance(res, dict)
    assert res == {}


def test_get_config_reads_file_and_get_key(tmp_path):
    data = {"confidence_threshold": 0.42, "other": "value"}
    cfg_file = tmp_path / "ai_advanced_config.json"
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    from tools.config_loader import get_config, get

    cfg = get_config(path=str(cfg_file))
    assert isinstance(cfg, dict)
    assert cfg.get("confidence_threshold") == 0.42

    # Test get helper
    assert get("confidence_threshold", None, path=str(cfg_file)) == 0.42
    assert get("missing", "def", path=str(cfg_file)) == "def"
