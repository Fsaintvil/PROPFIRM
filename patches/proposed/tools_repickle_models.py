"""Re-pickle models with the running environment to avoid
sklearn version mismatch warnings.

This script will:
 - search MT5_FTMO_IA/models for .pkl files
 - for each file: load (with ALLOW_MODEL_LOAD=1), write a backup
   <name>.bak, then save_model() to rewrite the pickle using the
   current environment
 - log results

Run: python tools/repickle_models.py
"""

from pathlib import Path
import sys
import os
import shutil
import logging

# Project root (parent of tools/)
ROOT = Path(__file__).resolve().parents[1]
# Ensure project root is on sys.path so local package imports work
sys.path.insert(0, str(ROOT))

MODELS_DIR = ROOT / "MT5_FTMO_IA" / "models"
LOG = logging.getLogger("repickle")
if not LOG.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(h)
    LOG.setLevel(logging.INFO)


def repickle_one(p: Path):
    LOG.info("Processing %s", p)
    os.environ["ALLOW_MODEL_LOAD"] = "1"
    # Import model_io here after sys.path has been adjusted above
    from scripts import model_io

    m = model_io.load_model(str(p), require_trust=True)
    if m is None:
        LOG.warning("Skipping: could not load %s", p)
        return False
    bak = p.with_suffix(p.suffix + ".bak")
    try:
        shutil.copy2(p, bak)
        LOG.info("Backup created: %s", bak)
    except Exception:
        LOG.exception("Backup failed for %s", p)
        return False
    ok = model_io.save_model(str(p), m)
    if ok:
        LOG.info("Re-saved model: %s", p)
    return ok


def main():
    if not MODELS_DIR.exists():
        LOG.error("Models dir not found: %s", MODELS_DIR)
        return
    pkls = list(MODELS_DIR.glob("*.pkl"))
    if not pkls:
        LOG.info("No pkl files found in %s", MODELS_DIR)
        return
    for p in pkls:
        repickle_one(p)


if __name__ == "__main__":
    main()
