#!/usr/bin/env python3
"""Monitor auto-retry summary and print a short notification at each iteration.

This script polls `artifacts/live_trading/auto_retry_summary.json` and prints any
new entries found.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

OUT = Path("artifacts") / "live_trading" / "auto_retry_summary.json"
DIAG = Path("artifacts") / "live_trading" / "close_after_diagnostics.json"


def read_summary() -> list:
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return []


def short_msg(entry: dict) -> str:
    ts = entry.get("timestamp")
    rp = entry.get("remaining_positions")
    rc = entry.get("returncode")
    log = entry.get("log")
    return f"[{ts}] remaining={rp} returncode={rc} log={log}"


def read_diags() -> list:
    """Read the close_after_diagnostics file if present.
    Return list of records.
    """
    if not DIAG.exists():
        return []
    try:
        return json.loads(DIAG.read_text(encoding="utf-8"))
    except Exception:
        return []


def check_executions(diags: list) -> int:
    """Return count of records where order_send.retcode == 100.

    A return > 0 indicates at least one execution accepted by the broker.
    """
    count = 0
    for rec in diags:
        try:
            # record may contain nested dicts for order_send
            osend = rec.get("order_send") if isinstance(rec, dict) else None
            if not osend:
                # in older schema there may be requests -> result
                osend = rec.get("order_send") or rec.get("send_result")
            if isinstance(osend, dict) and osend.get("retcode") == 100:
                count += 1
        except Exception:
            continue
    return count


def main():
    print("Starting monitor for auto-retry summary. Polling every 60s.")
    seen = 0
    try:
        while True:
            items = read_summary()
            if len(items) > seen:
                for item in items[seen:]:
                    print(short_msg(item))
                seen = len(items)

            # check diagnostics for any successful execution
            diags = read_diags()
            exec_count = check_executions(diags)
            if exec_count > 0:
                msg = (
                    f"ALERT: detected {exec_count} executed deal(s) "
                    "(order_send.retcode == 100)"
                )
                print(msg)

            # exit early if closed
            if items and items[-1].get("remaining_positions") == 0:
                print("All positions closed — monitor exiting.")
                break

            time.sleep(60)
    except KeyboardInterrupt:
        print("Monitor interrupted by user.")


if __name__ == "__main__":
    main()
