#!/usr/bin/env python3
"""Clean ol_state.json — remove duplicate EURUSD trades and HIST regimes.

Usage:
    python scripts/clean_ol_state.py
"""

import json
from pathlib import Path
from collections import defaultdict

OL_PATH = Path("runtime/ol_state.json")
BACKUP_PATH = Path("runtime/ol_state_backup.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def deduplicate_entries(entries):
    """Deduplicate entries by (rounded_second, profit, win, r) combo.

    EURUSD had ~1400 entries where the same trade was recorded ~100 times
    with timestamps differing only in milliseconds.
    """
    seen = set()
    unique = []
    for e in entries:
        # Truncate timestamp to second granularity
        ts = e.get("time", "")
        if ts:
            ts_rounded = ts[:19]  # "2026-07-21T18:19:20" — truncate milliseconds
        else:
            ts_rounded = ""

        profit = e.get("profit", None)
        win = e.get("win", None)
        r = e.get("r", 0)
        regime = e.get("regime", "")

        # Round r to 2 decimals for comparison
        r_rounded = round(r, 2)

        key = (ts_rounded, profit, win, r_rounded, regime)
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


def main():
    if not OL_PATH.exists():
        print(f"[CLEAN] {OL_PATH} not found, skipping")
        return

    # Backup
    data = load_json(OL_PATH)
    save_json(BACKUP_PATH, data)
    print(f"[CLEAN] Backup saved to {BACKUP_PATH}")

    history = data.get("history", {})
    total_before = sum(len(v) for v in history.values())

    cleaned = {}
    for symbol, entries in history.items():
        # Step 1: Remove HIST regime entries
        valid_entries = [e for e in entries if e.get("regime", "") != "HIST"]

        # Step 2: Deduplicate by timestamp + profit + win + r
        deduped = deduplicate_entries(valid_entries)

        removed = len(entries) - len(deduped)
        if removed > 0:
            print(f"[CLEAN] {symbol}: {len(entries)} → {len(deduped)} (removed {removed})")

        cleaned[symbol] = deduped

    data["history"] = cleaned

    # Remove adapted_params for symbols with no valid trades
    params = data.get("adapted_params", {})
    params_removed = 0
    for sym in list(params.keys()):
        if sym not in cleaned or len(cleaned[sym]) < 3:
            # Keep only if symbol has at least 3 valid trades
            if sym in params:
                del params[sym]
                params_removed += 1
                print(f"[CLEAN] Removed adapted_params for {sym} (< 3 valid trades)")

    data["adapted_params"] = params
    save_json(OL_PATH, data)

    total_after = sum(len(v) for v in cleaned.values())
    print(f"[CLEAN] Done: {total_before} → {total_after} total entries")
    print(f"[CLEAN] Adapted params removed: {params_removed}")

    # Recalculate adapted_params for remaining symbols
    print("\n[CLEAN] The OL will recalculate params on next trade. Ready.")


if __name__ == "__main__":
    main()
