#!/usr/bin/env python3
"""Simple health check: read `control/active_model.txt` and verify recent MT5 connection in logs.

Usage:
  python scripts/ops/check_active_model_and_mt5.py

This script is read-only and non-intrusive.
"""
from pathlib import Path
from datetime import datetime
import re


def read_active_model(path: Path):
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def find_recent_mt5_success(log_dir: Path, within_minutes: int = 60):
    pattern = re.compile(
        r"Connexion MT5 réussie|Connexion MT5 établie|MT5 connected", re.IGNORECASE
    )
    # We intentionally keep this simple: search logs for MT5-success lines.
    results = []
    for p in sorted(log_dir.glob("*.log"), reverse=True):
        try:
            for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if pattern.search(ln):
                    results.append((p.name, ln.strip()))
                    if len(results) > 10:
                        return results
        except Exception:
            continue
    return results


def main():
    active = read_active_model(Path("control/active_model.txt"))
    print("control/active_model.txt:")
    print(active or "<absent>")
    print()
    print("MT5 recent success lines (searching logs):")
    res = find_recent_mt5_success(Path("logs"), within_minutes=240)
    if not res:
        print("  (no matching lines found in logs)")
    else:
        for fn, line in res[:20]:
            print(f"  {fn}: {line}")


if __name__ == "__main__":
    main()
