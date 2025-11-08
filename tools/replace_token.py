#!/usr/bin/env python3
"""Remplacer strictement le token 0.68 par 0.50 dans les fichiers du dépôt.
Usage: python tools/replace_token.py [--batch-size N] [--dry-run]

Comportement:
- Parcourt le dépôt en ignorant les dossiers listés (venv, .venv, site-packages, .git, node_modules).
- Remplace uniquement les occurrences regex '\\b0\\.68\\b'.
- Écrit les fichiers modifiés (sauf en --dry-run).
- Affiche la liste des fichiers modifiés et le nombre de remplacements.
"""
import os
import re
import argparse

SKIP_DIRS = {'.venv', 'venv', 'env', 'site-packages', '.git', 'node_modules', '__pycache__'}
PATTERN = re.compile(r"\b0\.68\b")


def is_text_file(path):
    try:
        with open(path, 'rb') as f:
            chunk = f.read(4096)
            if b"\0" in chunk:
                return False
    except Exception:
        return False
    return True


def iter_files(root='.'):
    for dirpath, dirnames, filenames in os.walk(root):
        # normalize and skip
        lower = dirpath.replace('\\', '/').lower()
        if any(skip in lower for skip in SKIP_DIRS):
            continue
        for fname in filenames:
            yield os.path.join(dirpath, fname)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-size', type=int, default=200)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    changed = []
    total_replacements = 0
    processed_files = 0

    for path in iter_files('.'):
        if processed_files >= args.batch_size:
            break
        # readable text files only
        if not is_text_file(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception:
            try:
                with open(path, 'r', encoding='latin-1') as f:
                    text = f.read()
            except Exception:
                continue

        if PATTERN.search(text):
            new_text, n = PATTERN.subn('0.50', text)
            if n > 0:
                changed.append((path, n))
                total_replacements += n
                if not args.dry_run:
                    try:
                        with open(path, 'w', encoding='utf-8') as f:
                            f.write(new_text)
                    except Exception:
                        with open(path, 'w', encoding='latin-1') as f:
                            f.write(new_text)
        processed_files += 1

    print(f"Files modified: {len(changed)}")
    for p, n in changed:
        print(f"  {p}: {n} replacements")
    print(f"Total replacements: {total_replacements}")

    if len(changed) == 0:
        print("No files changed in this batch.")
    else:
        print("Batch complete.")
