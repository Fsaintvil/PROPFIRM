"""Tests pour credentials.py — chargement sécurisé des credentials MT5"""
import os
from pathlib import Path

from engine_simple.credentials import _load_dotenv, get_mt5_credentials


class TestLoadDotenv:
    def test_parse_simple_key_value(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("KEY=value\nFOO=bar")
        result = _load_dotenv(f)
        assert result == {"KEY": "value", "FOO": "bar"}

    def test_skip_empty_and_comments(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("\n# comment\n\nKEY=val\n")
        result = _load_dotenv(f)
        assert result == {"KEY": "val"}

    def test_strip_quotes(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('KEY="quoted"\nOTHER=\'single\'')
        result = _load_dotenv(f)
        assert result == {"KEY": "quoted", "OTHER": "single"}

    def test_handles_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.env"
        result = _load_dotenv(f)
        assert result == {}

    def test_handles_no_equals(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("MALFORMED_LINE\nKEY=ok")
        result = _load_dotenv(f)
        assert result == {"KEY": "ok"}


class TestGetCredentials:
    def test_env_var_priority(self, monkeypatch):
        monkeypatch.setenv("MT5_LOGIN", "12345")
        monkeypatch.setenv("MT5_PASSWORD", "secret")
        monkeypatch.setenv("MT5_SERVER", "server1")
        result = get_mt5_credentials()
        assert result == {"login": 12345, "password": "secret", "server": "server1"}

    def test_env_var_without_server(self, monkeypatch):
        monkeypatch.setenv("MT5_LOGIN", "12345")
        monkeypatch.setenv("MT5_PASSWORD", "secret")
        if "MT5_SERVER" in os.environ:
            monkeypatch.delenv("MT5_SERVER")
        result = get_mt5_credentials()
        assert result["login"] == 12345
        assert result["server"] == "FTMO-Demo"

    def test_dotenv_fallback(self, monkeypatch, tmp_path):
        for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"):
            if k in os.environ:
                monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(
            "engine_simple.credentials._load_dotenv",
            lambda p=None: {"MT5_LOGIN": "67890", "MT5_PASSWORD": "dotenv_pass", "MT5_SERVER": "dotenv_srv"}
        )
        result = get_mt5_credentials()
        assert result == {"login": 67890, "password": "dotenv_pass", "server": "dotenv_srv"}

    def test_returns_defaults_when_nothing_set(self, monkeypatch):
        for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"):
            if k in os.environ:
                monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr("engine_simple.credentials._load_dotenv", lambda p=None: {})
        result = get_mt5_credentials()
        assert result == {"login": 0, "password": "", "server": "FTMO-Demo"}
