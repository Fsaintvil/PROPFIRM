"""Quick check: MT5 positions"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import config_simple as cfg
import MetaTrader5 as mt5

mt5.initialize()
logged = mt5.login(cfg.MT5_LOGIN, password=cfg.MT5_PASSWORD, server=cfg.MT5_SERVER)
print("Logged in:", logged)

all_pos = mt5.positions_get()
print(f"Total positions: {len(all_pos) if all_pos is not None else 0}")
if all_pos:
    for p in all_pos:
        print(f"  {p.symbol} #{p.ticket} type={p.type} vol={p.volume:.2f} profit={p.profit:.2f} magic={p.magic} comment='{p.comment}'")

our_pos = [p for p in (all_pos or []) if p.magic == cfg.ROBOT_MAGIC]
print(f"Our positions (magic={cfg.ROBOT_MAGIC}): {len(our_pos)}")

mt5.shutdown()
