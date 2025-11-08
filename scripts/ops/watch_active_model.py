#!/usr/bin/env python3
"""Surveillance non-invasive du fichier `control/active_model.txt`.

Usage:
  python scripts/ops/watch_active_model.py --once

Ce script lit `control/active_model.txt` et, si absent, extrait des
indices depuis les logs (modèle chargé / connexions MT5) pour aider au
diagnostic sans toucher à la production.
"""
from pathlib import Path
import argparse
import re


def read_active_model():
    p = Path('control') / 'active_model.txt'
    if not p.exists():
        return None
    try:
        return p.read_text(encoding='utf-8')
    except Exception:
        return None


def recent_model_loads(log_dir: Path, limit=10):
    pattern = re.compile(r"Modèle LightGBM chargé|Model loaded|Modèle chargé", re.IGNORECASE)
    found = []
    for p in sorted(log_dir.glob('*.log'), reverse=True):
        try:
            for ln in p.read_text(encoding='utf-8', errors='ignore').splitlines():
                if pattern.search(ln):
                    found.append((p.name, ln.strip()))
                    if len(found) >= limit:
                        return found
        except Exception:
            continue
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true', help='Run one check and exit')
    _ = ap.parse_args()

    active = read_active_model()
    if active:
        print('active_model.txt content:\n')
        print(active)
        return

    print('control/active_model.txt absent. Collecting recent model-load log lines:')
    for fn, ln in recent_model_loads(Path('logs')):
        print(f'  {fn}: {ln}')


if __name__ == '__main__':
    main()
