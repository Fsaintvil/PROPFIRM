"""Small monitor that triggers the Forex FORCE LIVE plan.

Usage:
  - Create file `control/trigger_forex_open.now` to trigger immediate run
  - Or run with --poll-interval and --timeout to wait for the trigger file

This script is intentionally simple and conservative: it logs actions and
requires the presence of the trigger file before executing the forced run.
"""
import argparse
import time
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--trigger-file', default='control/trigger_forex_open.now')
    p.add_argument('--poll-interval', type=float, default=30.0, help='seconds')
    p.add_argument('--timeout', type=float, default=24*3600, help='seconds to wait')
    args = p.parse_args()

    trigger = Path(args.trigger_file)
    start = time.time()
    print(f"Waiting for trigger file: {trigger} (poll {args.poll_interval}s, timeout {args.timeout}s)")
    try:
        while True:
            if trigger.exists():
                print(f"Trigger found: {trigger}. Running Forex FORCE LIVE plan.")
                # read the plan to construct command if present
                plan = Path('control/production_forex_plan.json')
                if plan.exists():
                    try:
                        import json
                        with plan.open() as f:
                            cfg = json.load(f)
                        cmd = cfg.get('command')
                    except Exception as e:
                        print('Error reading plan:', e)
                        cmd = None
                else:
                    cmd = None

                if not cmd:
                    # fallback command
                    cmd = 'python .\\start_production.py --symbols EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,EURJPY,EURGBP,GBPJPY --force --yes'

                print('Executing:', cmd)
                # run via shell so environment from caller can be used
                rc = subprocess.call(cmd, shell=True)
                print('Process finished with rc=', rc)
                # do not auto-delete the trigger file; leave it for operator
                return rc

            if time.time() - start > args.timeout:
                print('Timeout reached waiting for trigger file')
                return 2
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print('Interrupted')
        return 130


if __name__ == '__main__':
    sys.exit(main())
