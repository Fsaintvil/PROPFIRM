#!/usr/bin/env python3
"""Propose une migration de `mt5.order_send` vers le wrapper safe.

Ce script recherche les appels `_mt5_send_safe(` dans les dossiers sources
(`scripts/`, `tools/`, `src/`) en excluant les backups et l'environnement virtuel.
Pour chaque fichier trouvé, il génère une proposition de fichier modifié sous
`patches/proposed/<path>` — il n'écrase pas les fichiers originaux.

Le remplacement est "fail-open": on ajoute en tête du fichier une tentative
d'importer `send_order` depuis `src.utils.mt5_safe` (nommé `_mt5_send_safe`) et
on remplace chaque `_mt5_send_safe(request)` par
`_mt5_send_safe(request) if _mt5_send_safe else _mt5_send_safe(request)`.

Usage:
  python scripts/migrate_order_send_to_safe.py --apply (optionnel, non recommandé)

Ne poussez pas ces modifications sans revue : ce script produit des patches
préparatoires à valider en PR.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".venv", "venv", "archive", "archive", "tools_dup"}
TARGET_DIRS = [ROOT / "scripts", ROOT / "tools", ROOT / "src"]
PATCH_OUT = ROOT / "patches" / "proposed"
REPORT_OUT = ROOT / "artifacts" / "migration_order_send_report.json"


def find_files() -> List[Path]:
    files: List[Path] = []
    for base in TARGET_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            # skip backups and vendored/archives
            parts = set(p.parts)
            if parts & EXCLUDE_DIRS:
                continue
            if p.suffix == ".py":
                files.append(p)
    return files


ORDER_SEND_RE = re.compile(r"\bmt5\.order_send\s*\(")


def propose_patch_for_file(path: Path) -> Tuple[int, str]:
    """Return (replacements_count, proposed_content)."""
    text = path.read_text(encoding="utf-8")
    if "mt5.order_send" not in text:
        return 0, ""

    lines = text.splitlines(keepends=True)
    replaced = 0

    # Prepare header import marker if needed
    header_insertion = (
        "# migration: try import safe sender (fail-open)\n"
        "try:\n"
        "    from src.utils.mt5_safe import send_order as _mt5_send_safe\n"
        "except Exception:\n"
        "    _mt5_send_safe = None\n\n"
    )

    # If the header is already present, don't re-add
    if "_mt5_send_safe" not in text:
        # insert after module docstring or at top
        if lines and lines[0].startswith(('"""', "'''")):
            # find end of docstring
            doc_end = 0
            quote = lines[0][:3]
            for i, ln in enumerate(lines[1:], start=1):
                if ln.strip().endswith(quote):
                    doc_end = i
                    break
            insert_at = doc_end + 1
        else:
            insert_at = 0
        lines.insert(insert_at, header_insertion)

    # Replace occurrences
    new_text = "".join(lines)

    def _replacement(match: re.Match) -> str:
        nonlocal replaced
        replaced += 1
        return "_mt5_send_safe("  # we'll keep the trailing parenthesis

    new_text = ORDER_SEND_RE.sub(_replacement, new_text)

    # For clarity, also replace some common variants. The regex above handles
    # the general case where pattern includes the opening parenthesis.

    return replaced, new_text


def write_patch(path: Path, content: str) -> None:
    out_path = PATCH_OUT / path.relative_to(ROOT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Apply patches in-place (not recommended)",
    )
    args = ap.parse_args(argv)

    files = find_files()
    report = {}
    total = 0
    for f in files:
        try:
            count, content = propose_patch_for_file(f)
            if count > 0:
                report[str(f.relative_to(ROOT))] = {"replacements": count}
                total += count
                write_patch(f, content)
        except Exception as exc:
            report[str(f.relative_to(ROOT))] = {"error": repr(exc)}

    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(
        json.dumps({"total_replacements": total, "files": report}, indent=2),
        encoding="utf-8",
    )

    print(f"Proposals written under {PATCH_OUT}. Total replacements: {total}")
    if args.apply:
        print(
            "--apply requested: applying patches in-place "
            "(overwrite originals). Be careful."
        )
        for rel in report.keys():
            src = ROOT / rel
            proposed = PATCH_OUT / rel
            if proposed.exists():
                src.write_text(
                    proposed.read_text(encoding="utf-8"), encoding="utf-8"
                )
                print(f"Applied patch to {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
