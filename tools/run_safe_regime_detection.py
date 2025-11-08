#!/usr/bin/env python3
"""Runner to execute the safe regime detector and save a small report."""
import os
import json
import sys

# ensure repo root in path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.market_regime_detection_safe import MarketRegimeDetectorSafe
import pandas as pd
from datetime import datetime

OUT_DIR = "artifacts/diagnostics"
os.makedirs(OUT_DIR, exist_ok=True)
IN = "data/features_sample.csv"
OUT = os.path.join(OUT_DIR, f"regime_features_safe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

try:
    df = pd.read_csv(IN)
    if "Unnamed: 0" in df.columns:
        df = df.set_index("Unnamed: 0")
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            pass

    det = MarketRegimeDetectorSafe(n_regimes=3)
    res = det.detect_regimes(df)

    report = {
        "timestamp": datetime.now().isoformat(),
        "current_regime": int(res["current_regime"]),
        "n_observations": int(len(res["regimes"]))
    }
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)

    print("✅ Safe run complete. Report:", OUT)
except Exception as e:
    print("❌ Safe run failed:", e)
    raise
