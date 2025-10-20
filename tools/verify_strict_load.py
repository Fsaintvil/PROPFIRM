"""Forcibly load each pickle under MT5_FTMO_IA/models with ALLOW_MODEL_LOAD=1
and print any warnings (notably InconsistentVersionWarning) per file.

Usage: run this script to force-load pickles with ALLOW_MODEL_LOAD=1
"""
from pathlib import Path
import sys
import warnings
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Ensure model loading is allowed for this verification run
os.environ["ALLOW_MODEL_LOAD"] = "1"

MODELS_DIR = ROOT / "MT5_FTMO_IA" / "models"


def check_one(p: Path):
    print("--", p)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            from MT5_FTMO_IA.scripts import model_io

            _ = model_io.load_model(str(p), require_trust=True)
        except Exception as e:
            print("  Exception during load:", repr(e))
            return False
        if not w:
            print("  OK: no warnings")
            return True
        else:
            print("  WARNINGS:")
            for ww in w:
                print("   -", type(ww.message).__name__ + ":", ww.message)
            return False


def main():
    if not MODELS_DIR.exists():
        print("Models dir not found:", MODELS_DIR)
        return
    pkls = sorted(MODELS_DIR.glob("*.pkl"))
    if not pkls:
        print("No pkl files found in", MODELS_DIR)
        return
    total = 0
    bad = []
    for p in pkls:
        total += 1
        ok = check_one(p)
        if not ok:
            bad.append(p)
    print("\nSummary: {} checked, {} problematic".format(total, len(bad)))
    if bad:
        print("Problematic files:")
        for p in bad:
            print(" -", p)


if __name__ == "__main__":
    main()
