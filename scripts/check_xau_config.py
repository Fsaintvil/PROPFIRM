#!/usr/bin/env python3
"""Vérifier config XAUUSD et time-stop."""
import sys
sys.path.insert(0, '.')
import engine_simple.strategy as strat
from engine_simple.trailer import TIME_STOP_MAX_HOURS, TIME_STOP_MAX_HOURS_PROFIT

print("=== CONFIG TIME-STOP ===")
print(f"TIME_STOP_MAX_HOURS = {TIME_STOP_MAX_HOURS}")
print(f"TIME_STOP_MAX_HOURS_PROFIT (défaut) = {TIME_STOP_MAX_HOURS_PROFIT}")
print()

for sym in ['XAUUSD', 'BTCUSD', 'USDCAD', 'US30.cash']:
    cfg = strat.SYMBOL_CONFIG.get(sym, {})
    tsh = cfg.get('time_stop_max_hours', TIME_STOP_MAX_HOURS)
    tsph = cfg.get('time_stop_max_hours_profit', TIME_STOP_MAX_HOURS_PROFIT)
    print(f"{sym}:")
    print(f"  time_stop_max_hours = {tsh}")
    print(f"  time_stop_max_hours_profit = {tsph}")
    print()

print("=== CONFIG XAUUSD ===")
xau = strat.SYMBOL_CONFIG.get('XAUUSD', {})
for key in ['sl_atr_trending', 'tp_atr_trending', 'sl_atr_ranging', 'tp_atr_ranging',
            'threshold_trending', 'threshold_ranging', 'partial_tp_progress',
            'risk_mult', 'max_lot', 'cooldown_minutes']:
    val = xau.get(key, 'DÉFAUT')
    print(f"  {key} = {val}")
