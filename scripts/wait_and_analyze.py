#!/usr/bin/env python3
"""Watcher: attend la fin des N cycles du moteur (par défaut 12) puis lance l'analyse JSONL.
Usage: python scripts/wait_and_analyze.py [cycles]
"""
import time
from pathlib import Path
import datetime
import subprocess
import sys

def log_path_for_today():
    today = datetime.datetime.now().strftime('%Y%m%d')
    return Path('logs') / f'live_trading_{today}.log'

def wait_for_cycle(target_cycle=12, poll_interval=5, timeout_hours=3):
    logpath = log_path_for_today()
    deadline = time.time() + timeout_hours * 3600
    print(f'Waiting for cycle {target_cycle} completion in {logpath} (timeout {timeout_hours}h)')

    last_size = 0
    while time.time() < deadline:
        if logpath.exists():
            try:
                text = logpath.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                text = ''

            if f'Cycle {target_cycle} terminé' in text or f'Cycle {target_cycle} termin' in text:
                print(f'Found completion marker for cycle {target_cycle}')
                return True

            # If file hasn't changed for a while, still continue waiting
            curr_size = logpath.stat().st_size
            if curr_size != last_size:
                last_size = curr_size
            # print progress occasionally
            print(f'... waiting (log size {curr_size} bytes)')
        else:
            print('Log file not yet present, waiting...')

        time.sleep(poll_interval)

    print('Timeout reached while waiting for cycles')
    return False

def run_analyzer():
    script = Path('scripts') / 'analyze_decision_dumps.py'
    jsonl = Path('logs') / 'decision_dumps.jsonl'
    if not script.exists():
        print('Analyzer script missing:', script)
        return 1
    if not jsonl.exists():
        print('No decision_dumps.jsonl found at', jsonl)
        return 1

    print('Running analyzer on', jsonl)
    # Call the analyzer script and stream output
    proc = subprocess.run([sys.executable, str(script), str(jsonl)], capture_output=True, text=True)
    print(proc.stdout)
    if proc.stderr:
        print('Analyzer stderr:', proc.stderr)
    return proc.returncode

def main():
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    ok = wait_for_cycle(cycles)
    if not ok:
        print('Watcher timed out; running analyzer anyway on current data')
    rc = run_analyzer()
    print('Analyzer exit code:', rc)

if __name__ == '__main__':
    main()
