"""Synchronise certains champs de `config/default.yaml` depuis `engine_simple/strategy.SYMBOL_CONFIG`.
Champs synchronisés: risk_mult, min_score, threshold_trending, threshold_ranging
Usage: python scripts/sync_strategy_to_yaml.py
Fait une sauvegarde: config/default.yaml.bak_sync_{timestamp}
"""
from pathlib import Path
import datetime
import yaml

ROOT = Path(__file__).resolve().parents[1]
strategy_path = ROOT / 'engine_simple' / 'strategy.py'
config_path = ROOT / 'config' / 'default.yaml'

# Import strategy module dynamically
import importlib.util
spec = importlib.util.spec_from_file_location('strategy_mod', str(strategy_path))
strategy_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategy_mod)

SYMBOL_CONFIG = getattr(strategy_mod, 'SYMBOL_CONFIG', {})

if not config_path.exists():
    print('config/default.yaml not found')
    raise SystemExit(1)

with open(config_path, 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

symbol_limits = cfg.get('symbol_limits') or {}

updated = False
changed_symbols = []
for sym, strat in SYMBOL_CONFIG.items():
    if sym not in symbol_limits:
        continue
    y = symbol_limits[sym]
    # keys to sync
    for key in ('risk_mult', 'min_score', 'threshold_trending', 'threshold_ranging'):
        if key in strat:
            strat_val = strat[key]
            # YAML uses snake_case same names (checked)
            y_val = y.get(key)
            # Normalize None vs missing
            if y_val != strat_val:
                y[key] = strat_val
                updated = True
                if sym not in changed_symbols:
                    changed_symbols.append(sym)

if updated:
    ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    backup = config_path.with_name(f"default.yaml.bak_sync_{ts}")
    config_path.rename(backup)
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    print('Updated config/default.yaml; backup written to', backup)
    print('Changed symbols:', changed_symbols)
else:
    print('No changes needed')
