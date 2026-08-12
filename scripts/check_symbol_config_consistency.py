"""Compare per-symbol keys between config/default.yaml:symbol_limits and strategy.SYMBOL_CONFIG.

Checks: threshold_trending, threshold_ranging, risk_mult, min_score
"""
import sys
import yaml

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine_simple import strategy

CONFIG_PATH = ROOT / "config" / "default.yaml"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

symbol_limits = cfg.get("symbol_limits", {}) or {}

mismatches = []

for sym, defaults in symbol_limits.items():
    strat = strategy.SYMBOL_CONFIG.get(sym)
    if strat is None:
        mismatches.append((sym, "missing_in_strategy", None, None))
        continue
    # keys to compare and mapping from yaml->strategy
    checks = [
        ("threshold_trending", "threshold_trending"),
        ("threshold_ranging", "threshold_ranging"),
        ("risk_mult", "risk_mult"),
        ("min_score", "min_score"),
    ]
    for ykey, skey in checks:
        yval = defaults.get(ykey)
        sval = strat.get(skey)
        if yval is None and sval is None:
            continue
        # normalize numeric-ish
        try:
            yn = float(yval) if yval is not None else None
        except Exception:
            yn = yval
        try:
            sn = float(sval) if sval is not None else None
        except Exception:
            sn = sval
        if yn != sn:
            mismatches.append((sym, ykey, yn, sn))

if not mismatches:
    print("Aucune incohérence par-symbole détectée pour les clés vérifiées.")
    raise SystemExit(0)

print("Incohérences par-symbole trouvées :")
for m in mismatches:
    sym, key, yval, sval = m
    if key == "missing_in_strategy":
        print(f" - {sym}: présent dans default.yaml mais absent dans strategy.SYMBOL_CONFIG")
    else:
        print(f" - {sym}: {key} -> default.yaml={yval}  strategy={sval}")

# exit non-zero to signal attention
raise SystemExit(2)
