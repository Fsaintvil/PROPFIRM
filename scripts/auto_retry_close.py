#!/usr/bin/env python3
"""
Auto retry loop to close positions:
- Calls `scripts/close_current_positions_verified.py` periodically
- Stops when remaining_positions == 0 or when duration elapsed
- Writes per-iteration logs to artifacts/live_trading/auto_retry_<timestamp>.log
Usage example:
  python scripts/auto_retry_close.py --interval 5 --duration 120
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path('artifacts') / 'live_trading'
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run_once(iter_idx: int, out_prefix: Path) -> dict:
    """Run the verified close script once and capture summary info."""
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    log_file = out_prefix / f'auto_retry_{ts}_{iter_idx}.log'
    cmd = [sys.executable, 'scripts/close_current_positions_verified.py']
    res = subprocess.run(cmd, capture_output=True, text=True)
    # write stdout/stderr for inspection
    log_file.write_text(
        f"# CMD: {' '.join(cmd)}\n# TIMESTAMP: {ts}\n\n--- STDOUT ---\n{res.stdout}\n\n--- STDERR ---\n{res.stderr}\n",
        encoding='utf-8',
    )

    summary = {'timestamp': ts, 'returncode': res.returncode, 'log': str(log_file)}

    # try to read the output JSON produced by the called script
    out_json = out_prefix / 'close_after_diagnostics.json'
    if out_json.exists():
        try:
            data = json.loads(out_json.read_text(encoding='utf-8'))
            summary['remaining_positions'] = data.get('remaining_positions')
            summary['records'] = len(data.get('records', []))
        except Exception as e:
            summary['json_read_error'] = str(e)
    else:
        summary['note'] = 'output json not found'

    # append a concise summary file
    summary_file = out_prefix / 'auto_retry_summary.json'
    all_summaries = []
    if summary_file.exists():
        try:
            all_summaries = json.loads(summary_file.read_text(encoding='utf-8'))
        except Exception:
            all_summaries = []
    all_summaries.append(summary)
    summary_file.write_text(json.dumps(all_summaries, indent=2), encoding='utf-8')

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=float, default=5.0, help='Interval between attempts in minutes')
    ap.add_argument('--duration', type=float, default=120.0, help='Total duration in minutes')
    ap.add_argument('--max-iterations', type=int, default=9999, help='Cap iterations')
    ap.add_argument('--dry-run', action='store_true', help='Run one quick iteration only for testing')
    args = ap.parse_args()

    interval_s = int(args.interval * 60)
    duration_s = int(args.duration * 60)
    end_time = datetime.utcnow() + timedelta(seconds=duration_s)

    out_prefix = OUT_DIR

    iter_idx = 0
    print(f"Starting auto-retry close loop: interval={args.interval}min duration={args.duration}min")
    try:
        while datetime.utcnow() < end_time and iter_idx < args.max_iterations:
            iter_idx += 1
            print(f"Iteration {iter_idx} at {datetime.utcnow().isoformat()}Z")
            summary = run_once(iter_idx, out_prefix)
            print(' ->', summary.get('remaining_positions'), 'remaining_positions, returncode=', summary.get('returncode'))

            if summary.get('remaining_positions') == 0:
                print('All positions closed; exiting loop.')
                break

            if args.dry_run:
                print('Dry-run: stopping after one iteration.')
                break

            # sleep until next iteration
            now = datetime.utcnow()
            if now + timedelta(seconds=interval_s) > end_time:
                # final iteration will be next; adjust sleep to not overshoot
                sleep_s = max(0, int((end_time - now).total_seconds()))
            else:
                sleep_s = interval_s
            print(f"Sleeping {sleep_s} seconds until next attempt...")
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        print('Interrupted by user; exiting.')


if __name__ == '__main__':
    main()
