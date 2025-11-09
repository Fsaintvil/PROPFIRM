# Merged preview for prefix: verify
# Generated from 3 files

################################################################################
# FROM: scripts\verify_dryrun_smoke.py
################################################################################
"""
Script de vérification smoke: s'assure que l'engine n'appelle pas mt5.order_send
lorsque le kill-switch est actif (control/disable_trading).

Usage: python scripts/verify_dryrun_smoke.py
"""
import os
import importlib.util
from pathlib import Path

# Créer le fichier de contrôle
ctrl_dir = Path("control")
ctrl_dir.mkdir(exist_ok=True)
disable_file = ctrl_dir / "disable_trading"
disable_file.write_text("1")

# Importer le module de l'engine par chemin (évite les problèmes de package)
engine_path = Path(__file__).resolve().parent / "live_trading_engine.py"
spec = importlib.util.spec_from_file_location("live_trading_engine", str(engine_path))
engine_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine_mod)

# Stub MT5 pour détecter appels
class StubResult:
    def __init__(self):
        self.retcode = 0
        self.order = 12345
        self.comment = "stub"

class StubMT5:
    def __init__(self):
        self.order_calls = 0
    def order_send(self, request):
        print("STUB MT5.order_send appelé avec:", request)
        self.order_calls += 1
        return StubResult()

stub = StubMT5()

# Forcer la disponibilité MT5 et injecter le stub
try:
    engine_mod.MT5_AVAILABLE = True
    engine_mod.mt5 = stub
except Exception as e:
    print("Impossible d'injecter le stub MT5:", e)

# Créer une instance de l'engine
try:
    EngineClass = getattr(engine_mod, 'LiveTradingEngine')
except Exception as e:
    print("Impossible d'importer LiveTradingEngine:", e)
    raise

engine = EngineClass(symbols=['EURUSD'], lot_sizes={'EURUSD': 0.01})

print("=== Début smoke test: kill-switch via control/disable_trading présent ===")

# Essayer d'exécuter un trade
success = engine.execute_trade('buy', 'EURUSD', lot_size=0.01, stop_loss=1.0, take_profit=2.0, price=1.1)

print("Résultat execute_trade:", success)
print("Nombre d'appels mt5.order_send détectés par le stub:", stub.order_calls)

# Nettoyer
try:
    disable_file.unlink()
    print("Fichier de contrôle supprimé")
except Exception:
    pass

# Assertion minimale pour CI-style output
if stub.order_calls == 0 and success is False:
    print("VERIFICATION OK: Aucun ordre envoyé pendant kill-switch (dry-run).")
    exit(0)
else:
    print("VERIFICATION FAIL: Appel(s) mt5.order_send detecté ou execute_trade a renvoyé True.")
    exit(2)


################################################################################
# FROM: tools\verify_repickled.py
################################################################################
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
        from MT5_FTMO_IA.scripts import model_io
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


if __name__ == '__main__':
    main()


################################################################################
# FROM: tools\verify_strict_load.py
################################################################################
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
        warnings.simplefilter('always')
        try:
            from MT5_FTMO_IA.scripts import model_io
            _ = model_io.load_model(str(p), require_trust=True)
        except Exception as e:
            print('  Exception during load:', repr(e))
            return False
        if not w:
            print('  OK: no warnings')
            return True
        else:
            print('  WARNINGS:')
            for ww in w:
                print('   -', type(ww.message).__name__ + ':', ww.message)
            return False


def main():
    if not MODELS_DIR.exists():
        print('Models dir not found:', MODELS_DIR)
        return
    pkls = sorted(MODELS_DIR.glob('*.pkl'))
    if not pkls:
        print('No pkl files found in', MODELS_DIR)
        return
    total = 0
    bad = []
    for p in pkls:
        total += 1
        ok = check_one(p)
        if not ok:
            bad.append(p)
    print('\nSummary: {} checked, {} problematic'.format(total, len(bad)))
    if bad:
        print('Problematic files:')
        for p in bad:
            print(' -', p)


if __name__ == '__main__':
    main()


# End of merged preview
