"""Vérifie que les modèles re-picklés ne déclenchent plus
InconsistentVersionWarning lors du chargement.

Usage: python tools/verify_repickled.py
"""
from pathlib import Path
import sys
import warnings

# Ensure project root is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODELS_DIR = ROOT / "MT5_FTMO_IA" / "models"


def check_one(p: Path):
    print("Checking", p)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        # Import locally so module import ordering remains clean
        from scripts import model_io

        _ = model_io.load_model(str(p), require_trust=True)
        if not w:
            print("  OK: no warnings")
            return True
        else:
            print("  WARNINGS:")
            for ww in w:
                print("   -", ww.message)
            return False


def main():
    if not MODELS_DIR.exists():
        print("Models dir not found:", MODELS_DIR)
        return
    pkls = list(MODELS_DIR.glob("*.pkl"))
    if not pkls:
        print("No pkl files found in", MODELS_DIR)
        return
    bad = []
    for p in pkls:
        ok = check_one(p)
        if not ok:
            bad.append(p)
    print("\nSummary: {} checked, {} problematic".format(len(pkls), len(bad)))
    if bad:
        print("Problematic files:")
        for p in bad:
            print(" -", p)


if __name__ == "__main__":
    main()
