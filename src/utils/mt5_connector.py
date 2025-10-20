#!/usr/bin/env python3
"""
MT5 Connector sécurisé avec fallbacks robustes
Remplace tous les imports directs de MetaTrader5 dans le projet
"""

from typing import Optional, Any, Dict, List
import time
import warnings
import os
from pathlib import Path

# Variable globale pour tracker l'état MT5
MT5_AVAILABLE = False
mt5 = None


def trading_disabled() -> bool:
    """Kill-switch global: retourne True si le trading doit être coupé.

    Sources du kill-switch (priorité aucune, OR logique):
    - Variable d'environnement TRADING_DISABLED=1 ou DISABLE_TRADING=1
    - Fichier control/disable_trading
    - Fichier control/emergency_stop (utilisé par certains moteurs)
    """
    try:
        env_flag = os.getenv("TRADING_DISABLED", "0").strip() in {"1", "true", "TRUE"}
        env_flag2 = os.getenv("DISABLE_TRADING", "0").strip() in {"1", "true", "TRUE"}
        ctrl_dir = Path("control")
        file_flag = (
            (ctrl_dir / "disable_trading").exists()
            or (ctrl_dir / "emergency_stop").exists()
        )
        return bool(env_flag or env_flag2 or file_flag)
    except Exception:
        # En cas d'erreur d'accès FS/env, ne pas bloquer par défaut
        return False


class _MT5Proxy:
    """Proxy autour de l'instance MetaTrader5 pour intercepter order_send.

    - Si le kill-switch est actif, order_send n'envoie rien et retourne un
      résultat simulé avec un retcode d'échec et un commentaire explicite.
    - Tous les autres attributs sont délégués à l'objet sous-jacent.
    """

    def __init__(self, underlying: Any):
        self._u = underlying

    def __getattr__(self, name: str):
        return getattr(self._u, name)

    def order_send(self, request: Dict) -> Dict:
        """Intercepte les envois d'ordres."""
        if trading_disabled():
            # Construire un résultat ressemblant à MT5 pour éviter les crashes
            retcode_reject = getattr(self._u, "TRADE_RETCODE_REJECT", -1)
            Result = type(
                "_MT5OrderResult",
                (),
                {
                    "retcode": retcode_reject,
                    "order": -1,
                    "deal": -1,
                    "volume": request.get("volume", 0.0),
                    "price": request.get("price", 0.0),
                    "comment": "TRADING_DISABLED: kill-switch actif",
                },
            )
            return Result()
        # Sinon, déléguer au MT5 réel
        return self._u.order_send(request)


def safe_mt5_import() -> bool:
    """Import sécurisé de MetaTrader5 avec fallbacks robustes"""
    global MT5_AVAILABLE, mt5

    if MT5_AVAILABLE and mt5 is not None:
        return True

    try:
        import MetaTrader5 as _mt5

        # Envelopper l'instance par un proxy si kill-switch actif
        mt5 = _MT5Proxy(_mt5) if trading_disabled() else _mt5
        MT5_AVAILABLE = True
        return True
    except ImportError as e:
        warnings.warn(f"MetaTrader5 non disponible: {e}")
        mt5 = MockMT5()
        MT5_AVAILABLE = False
        return False
    except Exception as e:
        warnings.warn(f"Erreur import MetaTrader5: {e}")
        mt5 = MockMT5()
        MT5_AVAILABLE = False
        return False


class MockMT5:
    """Mock MT5 pour développement/test sans MetaTrader5"""

    def __init__(self):
        self._initialized = False

    def initialize(self, *args, **kwargs) -> bool:
        """Mock initialize"""
        self._initialized = True
        return True

    def shutdown(self) -> None:
        """Mock shutdown"""
        self._initialized = False

    def login(self, *args, **kwargs) -> bool:
        """Mock login"""
        return True

    def account_info(self) -> Optional[Any]:
        """Mock account info"""
        return type(
            "AccountInfo",
            (),
            {
                "balance": 10000.0,
                "equity": 10000.0,
                "margin": 0.0,
                "server": "Mock-Server",
                "leverage": 100,
            },
        )()

    def symbols_get(self, *args, **kwargs) -> List[Any]:
        """Mock symbols"""
        symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD"]
        return [
            type("Symbol", (), {"name": s, "visible": True})() for s in symbols
        ]

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start: int, count: int
    ) -> Optional[Any]:
        """Mock rates data"""
        import numpy as np
        import pandas as pd

        # Générer données synthétiques réalistes
        dates = pd.date_range(end=pd.Timestamp.now(), periods=count, freq="H")
        np.random.seed(42)

        base_price = 1.1000 if "EUR" in symbol else 1.3000
        if "XAU" in symbol:
            base_price = 2000.0
        elif "BTC" in symbol:
            base_price = 45000.0

        prices = base_price + np.cumsum(
            np.random.normal(0, base_price * 0.0001, count)
        )

        return np.array(
            [
                (
                    int(d.timestamp()),
                    p,
                    p * 1.0002,
                    p * 0.9998,
                    p * 1.0001,
                    100,
                    0,
                    0,
                )
                for d, p in zip(dates, prices)
            ],
            dtype=[
                ("time", "i8"),
                ("open", "f8"),
                ("high", "f8"),
                ("low", "f8"),
                ("close", "f8"),
                ("tick_volume", "i8"),
                ("spread", "i4"),
                ("real_volume", "i8"),
            ],
        )

    def positions_get(self, *args, **kwargs) -> List[Any]:
        """Mock positions"""
        return []

    def orders_get(self, *args, **kwargs) -> List[Any]:
        """Mock orders"""
        return []

    def order_send(self, request: Dict) -> Dict:
        """Mock order send respectant le kill-switch"""
        if trading_disabled():
            return {
                "retcode": -1,
                "order": -1,
                "deal": -1,
                "volume": request.get("volume", 0.1),
                "price": request.get("price", 1.1000),
                "comment": "TRADING_DISABLED: mock no-op",
            }
        return {
            "retcode": 10009,  # TRADE_RETCODE_DONE
            "order": 123456,
            "deal": 123456,
            "volume": request.get("volume", 0.1),
            "price": request.get("price", 1.1000),
            "comment": "Mock order executed",
        }

    def symbol_info(self, symbol: str) -> Optional[Any]:
        """Mock symbol info"""
        return type(
            "SymbolInfo",
            (),
            {
                "name": symbol,
                "visible": True,
                "digits": 5,
                "point": 0.00001,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
            },
        )()


# Constantes MT5 mockées
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_FILLING_IOC = 1


def get_mt5() -> Any:
    """Obtenir instance MT5 (réelle ou mock)"""
    if not safe_mt5_import():
        warnings.warn("Utilisation du mock MT5 - pas de trading réel")
    return mt5


def is_mt5_available() -> bool:
    """Vérifier si MT5 réel est disponible"""
    safe_mt5_import()
    return MT5_AVAILABLE


def mt5_health_check() -> Dict[str, Any]:
    """Check complet de l'état MT5"""
    safe_mt5_import()

    health = {
        "mt5_real_available": MT5_AVAILABLE,
        "mt5_instance": type(mt5).__name__,
        "can_initialize": False,
        "can_get_account": False,
        "status": "unknown",
    }

    try:
        if mt5.initialize():
            health["can_initialize"] = True

            account = mt5.account_info()
            if account:
                health["can_get_account"] = True
                health["account_balance"] = getattr(account, "balance", 0.0)
                health["account_server"] = getattr(
                    account, "server", "Unknown"
                )

                if MT5_AVAILABLE:
                    health["status"] = "operational"
                else:
                    health["status"] = "mock_mode"
            else:
                health["status"] = "no_account"
        else:
            health["status"] = "init_failed"

    except Exception as e:
        health["status"] = "error"
        health["error"] = str(e)

    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass

    return health


# Auto-import à l'importation du module
safe_mt5_import()


def connect_with_retry(login, password, server, max_retries=3, timeout=30):
    """Connexion MT5 avec retry automatique et timeout"""
    for attempt in range(max_retries):
        try:
            if not mt5.initialize(
                login=login, password=password, server=server, timeout=timeout
            ):
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                return False
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise e
    return False

