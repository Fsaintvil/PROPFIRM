#!/usr/bin/env python3
"""Lightweight Prometheus exporter for basic production metrics.

Requires `prometheus_client` Python package. If missing, the script writes an
artifact explaining how to install it.

Usage: python tools/metrics_exporter.py --port 9090
"""
import argparse
import json
import os
import time

try:
    from prometheus_client import start_http_server, Gauge
except Exception:
    start_http_server = None
    Gauge = None


def write_missing(out_path):
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'error': 'prometheus_client_missing',
            'install_cmd': 'pip install prometheus_client'
        }, f, indent=2)
    print('prometheus_client not installed; wrote artifact with instructions')


def run_server(port):
    # Basic gauges
    prod_status = Gauge('propfirm_production_process_up', '1 if production process is running (pid file present)')
    last_run = Gauge('propfirm_last_production_run_timestamp', 'Unix timestamp of last production start')

    # set initial values
    pid_files = list(sorted([p for p in os.listdir('artifacts/live_trading') if p.endswith('.pid')])) if os.path.isdir('artifacts/live_trading') else []
    prod_status.set(1.0 if pid_files else 0.0)
    if pid_files:
        # get the newest pid file mtime
        newest = max(pid_files)
        last_run.set(time.time())

    start_http_server(port)
    print(f'Metrics exporter listening on :{port}')

    # update loop
    try:
        while True:
            pid_files = list(sorted([p for p in os.listdir('artifacts/live_trading') if p.endswith('.pid')])) if os.path.isdir('artifacts/live_trading') else []
            prod_status.set(1.0 if pid_files else 0.0)
            time.sleep(5)
    except KeyboardInterrupt:
        print('Metrics exporter stopping')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=int(os.getenv('METRICS_PORT', '9090')))
    parser.add_argument('--artifact', default='artifacts/live_trading/metrics_exporter_status.json')
    args = parser.parse_args()

    if start_http_server is None or Gauge is None:
        write_missing(args.artifact)
        return

    # run exporter (blocking)
    run_server(args.port)


if __name__ == '__main__':
    main()
