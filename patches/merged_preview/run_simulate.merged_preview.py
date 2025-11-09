# Merged preview for prefix: run
# Generated from 7 files

################################################################################
# FROM: scripts\run_all_instruments.py
################################################################################
"""Runner to train+backtest across all instruments found in data/.

It looks for CSV files under data/ (top-level). For each CSV it:
- copies it to data/features_sample.csv (the existing scripts expect
    that path),
- runs the training script, then the backtest script,
- collects the produced `artifacts/backtest_report.json` and stores it
    under `artifacts/reports/{instrument}.json`.

Finally it writes `artifacts/reports/aggregate.json` with simple
aggregates.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


def find_csvs(base: Path):
    d = base / "data"
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.suffix.lower() == ".csv"])


def run_for_csv(csv_path: Path, base: Path) -> dict:
    # copy to expected path
    target = base / "data" / "features_sample.csv"
    try:
        # avoid copying if it's the same file
        if csv_path.resolve() != target.resolve():
            shutil.copy(csv_path, target)
    except Exception:
        # best-effort: if resolving fails or any issue, attempt direct copy
        if csv_path.exists() and csv_path != target:
            shutil.copy(csv_path, target)
    # run training
    subprocess.run(["python", "scripts/train_lightgbm.py"], check=True)
    # run backtest
    subprocess.run(["python", "scripts/backtest_poc.py"], check=True)
    # read report
    rpt = base / "artifacts" / "backtest_report.json"
    if not rpt.exists():
        raise FileNotFoundError(rpt)
    return json.loads(rpt.read_text(encoding="utf-8"))


def main():
    base = Path.cwd()
    csvs = find_csvs(base)
    if not csvs:
        # fallback to the single sample file
        csvs = [base / "data" / "features_sample.csv"]

    out_dir = base / "artifacts" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_reports = {}
    for csv in csvs:
        name = csv.stem
        print(f"Processing {csv} -> instrument {name}")
        try:
            report = run_for_csv(csv, base)
        except Exception as e:
            report = {"error": str(e)}
        (out_dir / f"{name}.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        all_reports[name] = report

    # write aggregate
    (out_dir / "aggregate.json").write_text(
        json.dumps(all_reports, indent=2), encoding="utf-8"
    )
    print(f"Wrote reports to {out_dir}")


if __name__ == "__main__":
    main()


################################################################################
# FROM: scripts\run_continuous_dryrun.py
################################################################################
"""
Run a safe dry-run for a given duration (seconds).
- Creates control/disable_trading to enable kill-switch
- Runs LiveTradingEngine.main_trading_loop in a background thread
- MT5 is disabled to avoid external calls (engine_mod.MT5_AVAILABLE=False)
- After duration completes, stops engine and copies/prints a summary of logs/decision_dumps.jsonl

Usage: python scripts/run_continuous_dryrun.py --minutes 15
"""
import argparse
import time
import shutil
from pathlib import Path
import importlib.util
import threading
import json

parser = argparse.ArgumentParser()
parser.add_argument("--minutes", type=int, default=15, help="Run duration in minutes")
args = parser.parse_args()

DURATION = args.minutes * 60
WORKDIR = Path(__file__).resolve().parent.parent
LOGS = WORKDIR / "logs"
DUMPS = LOGS / "decision_dumps.jsonl"
ARTIFACTS = WORKDIR / "artifacts" / "dryrun"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

# create kill-switch
ctrl_dir = WORKDIR / "control"
ctrl_dir.mkdir(exist_ok=True)
disable_file = ctrl_dir / "disable_trading"
disable_file.write_text("1")
print(f"Kill-switch enabled: {disable_file}")

# import engine module by path
engine_path = WORKDIR / "scripts" / "live_trading_engine.py"
spec = importlib.util.spec_from_file_location("live_trading_engine", str(engine_path))
engine_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine_mod)

# Ensure no MT5 network calls
try:
    engine_mod.MT5_AVAILABLE = False
    print("MT5_AVAILABLE set to False (dry-run safe)")
except Exception:
    pass

# instantiate engine
EngineClass = getattr(engine_mod, 'LiveTradingEngine')
engine = EngineClass(symbols=["EURUSD", "XAUUSD", "BTCUSD"], lot_sizes={"EURUSD":0.001, "XAUUSD":0.001, "BTCUSD":0.001})
# tune for smoke / fast cycles
engine.trading_interval = 1  # 1s between cycles to collect many samples
engine.min_sleep_seconds = 0.2
engine.smoke_sleep = 0.1

# run in thread
def _runner():
    try:
        engine.is_running = True
        engine.main_trading_loop()
    except Exception as e:
        print("Runner exception:", e)

thread = threading.Thread(target=_runner, daemon=True)
thread.start()

print(f"Engine started in dry-run mode for {args.minutes} minute(s). Waiting...")

start = time.time()
try:
    while time.time() - start < DURATION:
        time.sleep(5)
except KeyboardInterrupt:
    print("Interrupted by user")

# stop engine
engine.is_running = False
thread.join(timeout=10)
print("Engine stopped")

# collect dumps summary
if DUMPS.exists():
    lines = DUMPS.read_text(encoding='utf-8').strip().splitlines()
    count = len(lines)
    print(f"Decision dumps lines: {count}")
    # copy artifact
    ts = time.strftime('%Y%m%d_%H%M%S')
    dest = ARTIFACTS / f"decision_dumps_{ts}.jsonl"
    shutil.copyfile(DUMPS, dest)
    print(f"Copied dump to: {dest}")
    # quick stats sample
    try:
        accepted = 0
        for ln in lines[-500:]:
            j = json.loads(ln)
            if j.get('decision', {}).get('action') in ('buy','sell'):
                accepted += 1
        print(f"Accepted (last up to 500 entries): {accepted}")
    except Exception as e:
        print("Quick stats failed:", e)
else:
    print("No decision_dumps.jsonl found")

# cleanup
try:
    disable_file.unlink()
    print("Kill-switch removed")
except Exception:
    pass

print("Dry-run complete")


################################################################################
# FROM: scripts\run_simulated_cycles.py
################################################################################
#!/usr/bin/env python3
"""Run simulated validation cycles across all engine symbols.
Usage: python scripts/run_simulated_cycles.py --cycles 150

This script instantiates the `LiveTradingEngine` in local simulation/fallback
mode and runs N cycles per symbol, calling the AI pipeline so that
`logs/decision_dumps.jsonl` is populated for analysis.
"""
import argparse
import time
import logging
import sys
import os

# Ensure project and scripts paths are importable when executed from project root
proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)
scripts_path = os.path.abspath(os.path.dirname(__file__))
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

from live_trading_engine import LiveTradingEngine


def main():
    parser = argparse.ArgumentParser(
        description="Run simulated cycles per symbol"
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=150,
        help="Number of cycles to run per symbol (default: 150)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="Sleep (seconds) between cycles to avoid tight loop",
    )

    args = parser.parse_args()

    engine = LiveTradingEngine()
    # Debug level to see diagnostics minimally
    engine.logger.setLevel(logging.INFO)

    print(f"Running {args.cycles} cycles per symbol for: {engine.symbols}")

    # Initialize AI systems (will write active_model.txt when applicable)
    engine.initialize_ai_systems()

    total_cycles = args.cycles * len(engine.symbols)
    seen = 0

    try:
        for i in range(args.cycles):
            for sym in engine.symbols:
                # get data (simulation if MT5 unavailable)
                data = engine.get_live_data(sym, count=200)
                if data is None:
                    # generate fallback
                    data = engine.generate_simulation_data(200)
                # trigger AI pipeline and advanced decision
                _ = engine.get_ai_signals(data, symbol=sym)

                seen += 1
                if seen % max(1, (len(engine.symbols) * 10)) == 0:
                    print(f"Progress: {seen}/{total_cycles} cycles completed")

                # small sleep to avoid flooding the system
                time.sleep(args.sleep)

        print("All cycles completed")

    except KeyboardInterrupt:
        print("Interrupted by user - stopping early")


if __name__ == '__main__':
    main()


################################################################################
# FROM: tools\run_dryrun_symbols.py
################################################################################
#!/usr/bin/env python3
"""Run dry-run (light mode) for a list of symbols non-invasively.

This script sets LIVE_ENGINE_LIGHT_MODE=1 and iterates over provided symbols,
running a single-cycle dry-run for each and printing a compact summary.
"""
import os
import json
import sys
from pathlib import Path
import importlib.util


SYMBOLS = [
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "USDCAD",
    "AUDNZD",
    "EURJPY",
    "GBPCHF",
    "NZDJPY",
    "EURUSD",
    "EURAUD",
    "US500.cash",
    "JP225.cash",
]


def load_engine_module():
    ROOT = Path(__file__).resolve().parents[1]
    MODULE_PATH = ROOT / "scripts" / "live_trading_engine.py"
    spec = importlib.util.spec_from_file_location("live_trading_engine", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_trading_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_for_symbol(engine_cls, symbol):
    try:
        eng = engine_cls(symbols=[symbol])
        # Ensure light mode and single cycle
        eng.max_cycles = 1
        # Try to fetch live data
        df = eng.get_live_data(symbol, count=100)
        eng.live_data[symbol] = df
        signals = eng.get_ai_signals(df, symbol)
        summary = {
            "symbol": symbol,
            "combined_signal": signals.get("combined_signal") if isinstance(signals, dict) else str(signals),
            "confidence": signals.get("confidence") if isinstance(signals, dict) else None,
        }
        print(json.dumps(summary, default=str))
        return True, summary
    except Exception as e:
        print(f"[error] symbol={symbol} -> {e}")
        return False, str(e)


def main():
    os.environ["LIVE_ENGINE_LIGHT_MODE"] = "1"

    mod = load_engine_module()
    LiveTradingEngine = getattr(mod, "LiveTradingEngine")

    results = {}
    for sym in SYMBOLS:
        print(f"--- Running dry-run for {sym} ---")
        ok, res = run_for_symbol(LiveTradingEngine, sym)
        results[sym] = {"ok": ok, "result": res}

    print("=== Summary ===")
    print(json.dumps(results, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())


################################################################################
# FROM: tools\run_preflight_and_dryrun.py
################################################################################
#!/usr/bin/env python3
"""Runner non-invasif : preflight + dry-run léger (1 cycle).

Ce script :
- force LIVE_ENGINE_LIGHT_MODE=1 pour éviter imports ML lourds
- instancie `LiveTradingEngine`
- appelle `production_health_check()` et affiche le résultat
- récupère des données live simulées ou via MT5 (mode simulation possible)
- appelle `get_ai_signals` pour 1 symbole et affiche un résumé du résultat

Ne réalise aucun envoi d'ordres ni modification d'état externe.
"""
import os
import json
from pathlib import Path
import importlib.util
import sys


def load_engine_module():
    ROOT = Path(__file__).resolve().parents[1]
    MODULE_PATH = ROOT / "scripts" / "live_trading_engine.py"
    spec = importlib.util.spec_from_file_location("live_trading_engine", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_trading_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    # Environment: light mode, single cycle
    os.environ["LIVE_ENGINE_LIGHT_MODE"] = "1"
    os.environ["ENGINE_MAX_CYCLES"] = "1"

    print("[runner] LIVE_ENGINE_LIGHT_MODE=1, ENGINE_MAX_CYCLES=1")

    mod = load_engine_module()
    LiveTradingEngine = getattr(mod, "LiveTradingEngine")

    # Instantiate engine with default symbols
    eng = LiveTradingEngine()

    print("[runner] Running production_health_check()...")
    try:
        ok = eng.production_health_check()
        print(f"[runner] production_health_check -> {ok}")
    except Exception as e:
        print("[runner] production_health_check raised:", e)

    # Dry-run: get live data for first symbol and compute signals
    symbol = eng.symbols[0] if eng.symbols else None
    if not symbol:
        print("[runner] No symbols configured, exiting")
        return 1

    print(f"[runner] Performing dry-run for symbol: {symbol}")

    try:
        # Use engine's get_live_data (it may generate simulation data)
        df = eng.get_live_data(symbol, count=100)
        if df is None:
            print("[runner] get_live_data returned None")
            return 2
        # Ensure engine.live_data updated
        eng.live_data[symbol] = df

        # Call get_ai_signals (non-invasive) to compute signals
        signals = eng.get_ai_signals(df, symbol)
        # Print a compact summary
        summary = {
            "symbol": symbol,
            "combined_signal": signals.get("combined_signal") if isinstance(signals, dict) else str(signals),
            "confidence": signals.get("confidence") if isinstance(signals, dict) else None,
        }
        print("[runner] signals summary:", json.dumps(summary, default=str))
    except Exception as e:
        print("[runner] Dry-run failed:", e)
        return 3

    print("[runner] Dry-run completed successfully")
    return 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)


################################################################################
# FROM: tools\run_safe_regime_detection.py
################################################################################
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


################################################################################
# FROM: tools\run_simulate.py
################################################################################
import sys
import os
from importlib import import_module

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run():
    m = import_module('MT5_FTMO_IA.scripts._execute_recommendations_live')
    import sys as _sys
    _sys.argv = ['', '--auth-token', 'DRY-TOKEN', '--simulate']
    m.main()


if __name__ == '__main__':
    run()


# End of merged preview
