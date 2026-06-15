"""Secure credential management for MT5.

Priority order:
1. OS environment variables (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
2. .env file (legacy, with deprecation warning)

Never hardcode credentials. On Windows, prefer system env vars or
Windows Credential Manager for production.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger("robot.credentials")

ENV_FILE = Path(".env")


def _load_dotenv(path: Path = ENV_FILE) -> dict:
    """Minimal .env parser — no dependency on python-dotenv."""
    result: dict = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip("\"'")
    return result


def get_mt5_credentials() -> dict:
    """Return MT5 credentials dict with keys: login, password, server.

    Checks env vars first, then .env file. Warns if .env is used in production.
    """
    login = os.environ.get("MT5_LOGIN")
    password = os.environ.get("MT5_PASSWORD")
    server = os.environ.get("MT5_SERVER")

    if login and password:
        return {"login": int(login), "password": password, "server": server or "FTMO-Demo"}

    dotenv = _load_dotenv()
    login = login or dotenv.get("MT5_LOGIN")
    password = password or dotenv.get("MT5_PASSWORD")
    server = server or dotenv.get("MT5_SERVER")

    if dotenv and login:
        logger.warning(
            "MT5 credentials loaded from .env file — PASSWORD IN PLAINTEXT. "
            "For production, set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER as OS "
            "environment variables or use Windows Credential Manager."
        )
        return {"login": int(login), "password": password, "server": server or "FTMO-Demo"}

    logger.error("MT5 credentials not found. Set MT5_LOGIN and MT5_PASSWORD env vars.")
    return {"login": 0, "password": "", "server": "FTMO-Demo"}
