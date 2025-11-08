#!/usr/bin/env python3
"""
Check artifact integrity against a manifest CSV (path,size,sha256).

Usage:
    python scripts/check_artifact_integrity.py \
        [--manifest tmp/file_hashes.csv] \
        [--prefix artifacts\\auto_improve]

Exits with code 0 if all checked files match the manifest, 1 otherwise.
"""
import argparse
import csv
import hashlib
import sys
from pathlib import Path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def load_manifest(manifest_path: Path, prefix: str):
    entries = {}
    with manifest_path.open('r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            p = row[0].strip('"')
            # Normalize separators
            if prefix.replace('/', '\\') in p.replace('/', '\\'):
                expected_hash = row[2].strip() if len(row) > 2 else ''
                entries[Path(p)] = expected_hash.upper()
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', default='tmp/file_hashes.csv')
    ap.add_argument('--prefix', default='artifacts/auto_improve')
    args = ap.parse_args()

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"Manifest not found: {manifest}")
        sys.exit(2)

    entries = load_manifest(manifest, args.prefix)
    if not entries:
        print(f"No entries found in manifest for prefix '{args.prefix}'")
        sys.exit(0)

    failures = []
    for p, expected in entries.items():
        # Convert manifest absolute path into workspace-relative when possible
        target = Path(str(p))
        if not target.exists():
            print(f"MISSING: {target} (expected hash {expected})")
            failures.append((target, 'MISSING', expected, ''))
            continue
        actual = sha256_of(target)
        if actual != expected:
            print(f"MISMATCH: {target}\n  expected: {expected}\n  actual:   {actual}")
            failures.append((target, 'MISMATCH', expected, actual))
        else:
            print(f"OK: {target} {actual}")

    if failures:
        print(f"\nIntegrity check failed: {len(failures)} problems")
        sys.exit(1)
    print("\nAll checked artifacts match manifest.")
    sys.exit(0)


if __name__ == '__main__':
    main()
