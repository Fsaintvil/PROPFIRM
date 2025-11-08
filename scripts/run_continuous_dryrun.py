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
