import os

from src.utils import mt5_connector

print("trading_disabled=", mt5_connector.trading_disabled())
print("mt5_available=", mt5_connector.is_mt5_available())
print("env_MT5_LOGIN=", bool(os.getenv("MT5_LOGIN")))
print("env_MT5_SERVER=", bool(os.getenv("MT5_SERVER")))
