#!/usr/bin/env python3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import mt5_connector

mt5 = mt5_connector.get_mt5()
print('MT5_impl=', type(mt5).__name__)
try:
    ok = mt5.initialize(login=1512027373, password='W21!vc*ul@', server='FTMO-Demo', timeout=15)
except Exception as e:
    ok = False
    print('initialize exception:', e)
print('initialize returned:', ok)
try:
    le = mt5.last_error()
    print('last_error=', le)
except Exception as e:
    print('no last_error or exception:', e)
try:
    mt5.shutdown()
except Exception:
    pass
