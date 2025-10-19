"""Application settings loaded from environment with sane defaults.

This module centralizes configuration so other modules import from here.
"""

import os
from pathlib import Path
from typing import Optional

# Try to load a .env file if python-dotenv is available
try:
    from dotenv import load_dotenv

    env_path = Path(".") / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print("✅ Variables d'environnement chargées depuis .env")
except ImportError:
    # dotenv is optional; continue silently
    print("⚠️  python-dotenv non installé - variables OS uniquement")
except Exception as e:
    # dotenv is optional; continue silently
    print(f"⚠️  Erreur chargement .env: {e}")


def getenv(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable with improved error reporting."""
    value = os.getenv(key, default)
    if value is None and default is None:
        print(f"⚠️  Variable d'environnement manquante: {key}")
    return value


# MT5 / Broker settings
MT5_TERMINAL = getenv("MT5_TERMINAL", None)
MT5_LOGIN = getenv("MT5_LOGIN", None)
MT5_PWD = getenv("MT5_PWD", None)
MT5_SERVER = getenv("MT5_SERVER", None)

# Execution
AUTO_EXECUTION = getenv("AUTO_EXECUTION", "false").lower() in (
    "1",
    "true",
    "yes",
)
INSTRUMENTS = getenv("INSTRUMENTS", "EURUSD,XAUUSD,BTCUSD").split(",")

# Logging
LOG_DIR = Path(getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Risk defaults avec validation
try:
    RISK_PER_POSITION = float(getenv("RISK_PER_POSITION", "0.02"))
    if not 0.001 <= RISK_PER_POSITION <= 0.1:
        print(f"⚠️  RISK_PER_POSITION invalide ({RISK_PER_POSITION}), "
              "utilisation par défaut: 0.02")
        RISK_PER_POSITION = 0.02
except ValueError:
    print("⚠️  RISK_PER_POSITION invalide, utilisation par défaut: 0.02")
    RISK_PER_POSITION = 0.02

try:
    DAILY_DRAWDOWN_LIMIT = float(getenv("DAILY_DRAWDOWN_LIMIT", "4.5"))
    if not 1.0 <= DAILY_DRAWDOWN_LIMIT <= 10.0:
        print(f"⚠️  DAILY_DRAWDOWN_LIMIT invalide ({DAILY_DRAWDOWN_LIMIT}), "
              "utilisation par défaut: 4.5")
        DAILY_DRAWDOWN_LIMIT = 4.5
except ValueError:
    print("⚠️  DAILY_DRAWDOWN_LIMIT invalide, utilisation par défaut: 4.5")
    DAILY_DRAWDOWN_LIMIT = 4.5


__all__ = [
    "MT5_TERMINAL",
    "MT5_LOGIN",
    "MT5_PWD",
    "MT5_SERVER",
    "AUTO_EXECUTION",
    "INSTRUMENTS",
    "LOG_DIR",
    "RISK_PER_POSITION",
    "DAILY_DRAWDOWN_LIMIT",
]
# -*- coding: utf-8 -*-
# placeholder python module
