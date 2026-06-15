"""Check all v2 models."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import logging
logging.basicConfig(level=logging.WARNING)

from engine_simple.anticipation import AnticipationEngine

ae = AnticipationEngine()
ae.initialize(retrain=False)
s = ae.get_summary()

print("=== ANTICIPATION V2 RESULTS ===")
for sym in s["symbols"]:
    acc = s["accuracies"].get(sym, 0)
    thr = s["thresholds"].get(sym, 0.5)
    trained = s["trained"].get(sym, False)
    print(f"  {sym}: acc={acc:.2%} | threshold={thr:.2f} | {'TRAINED' if trained else 'PENDING'}")
