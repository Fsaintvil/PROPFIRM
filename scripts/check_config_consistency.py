"""
Script de vérification rapide des constantes critiques entre modules.
Usage: python scripts/check_config_consistency.py
Retourne 0 si OK, 1 si divergences trouvées.
"""
from pprint import pprint
import sys

issues = []

try:
    from engine_simple import backtest_utils
    from engine_simple import strategy
except Exception as e:
    print(f"Erreur d'import: {e}")
    sys.exit(1)

# Comparaisons simples
checks = [
    ("THRESHOLD_MAX", getattr(backtest_utils, "THRESHOLD_MAX", None), getattr(strategy, "THRESHOLD_MAX", None)),
    ("THRESHOLD_TRENDING", getattr(backtest_utils, "THRESHOLD_TRENDING", None), None),
    ("THRESHOLD_RANGING", getattr(backtest_utils, "THRESHOLD_RANGING", None), None),
]

for name, val_bt, val_str in checks:
    if val_str is None:
        # essayer de récupérer depuis strategy module-level constants or SYMBOL_CONFIG defaults
        val_str = getattr(strategy, name, None)
    if val_bt is None and val_str is None:
        continue
    if val_bt != val_str:
        issues.append((name, val_bt, val_str))

if issues:
    print("Incohérences trouvées :")
    pprint(issues)
    sys.exit(1)
else:
    print("Aucune incohérence détectée pour les constantes vérifiées.")
    sys.exit(0)
