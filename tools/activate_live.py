"""Activate live mode by validating a pending token and (optionally)
removing the control/kill_switch. Use with caution. Do NOT run unless you
intend to enable live trading.

Usage:
  python tools/activate_live.py <token> [--execute]
"""
from pathlib import Path
import json
import shutil
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "control"
LOGS = ROOT / "logs"


def main(token: str, execute: bool = False):
    pending = CONTROL / f"pending_live_activation_{token}.json"
    if not pending.exists():
        print("Pending token not found:", pending)
        return 2
    data = json.loads(pending.read_text())
    print(
        "Found pending activation:",
        data.get("token"),
        "user:",
        data.get("user"),
    )
    # append audit
    LOGS.mkdir(exist_ok=True)
    log = LOGS / "live_enable_audit.csv"
    line = (
        f"{datetime.utcnow().isoformat()},activated_attempt,"
        f"{data.get('token')},{data.get('user')},{data.get('note', '')}"
    )
    with open(log, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print("Audit appended to", log)
    if execute:
        ks = CONTROL / "kill_switch"
        if ks.exists():
            bak = CONTROL / (
                "kill_switch.bak_"
                + datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            )
            shutil.copy2(ks, bak)
            ks.unlink()
            print("kill_switch moved to", bak, "-> live enabled")
        else:
            print("kill_switch not present; live may already be enabled")
    else:
        print(
            "Dry run (no destructive actions). Use --execute to modify "
            "kill_switch"
        )
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/activate_live.py <token> [--execute]")
        sys.exit(2)
    token = sys.argv[1]
    execute = "--execute" in sys.argv[2:]
    sys.exit(main(token, execute))
