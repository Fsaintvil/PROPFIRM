"""Restore the kill_switch from the most recent backup.

Usage: python tools/rollback_activate.py
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "control"


def find_latest_bak():
    baks = sorted(
        CONTROL.glob("kill_switch.bak_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return baks[0] if baks else None


def main():
    bak = find_latest_bak()
    if not bak:
        print("No kill_switch backup found")
        return 2
    dest = CONTROL / "kill_switch"
    if dest.exists():
        print("kill_switch already exists at", dest)
        return 3
    shutil.copy2(bak, dest)
    print("Restored", bak, "to", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
