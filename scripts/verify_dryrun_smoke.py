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
