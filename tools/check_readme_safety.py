#!/usr/bin/env python3
"""
Outil simple pour vérifier que les README du dépôt ne suppriment pas les garde-fous
ou n'encouragent pas des pratiques dangereuses (commit de secrets, suppression de preflight, etc.).

Usage:
    python tools/check_readme_safety.py

Exit code: 0 si OK, 1 si trouvées des issues critiques.
"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERNS_CRITICAL = [
    re.compile(r"remove.*preflight", re.I),
    re.compile(r"dry-?run removed", re.I),
    re.compile(r"100% live", re.I),
    re.compile(r"la seule règle", re.I),
    re.compile(r"supprim.*lockfile", re.I),
    re.compile(r"setx\s+", re.I),
    re.compile(r"commit.*secret", re.I),
]

PATTERNS_WARNING = [
    re.compile(r"YES GO", re.I),
    re.compile(r"AUTO_APPLY", re.I),
    re.compile(r"CONFIRM_RUN_PHRASE", re.I),
    re.compile(r"ALLOW_MT5_SEND", re.I),
]

exclusions = [".venv", "node_modules"]

issues = []

for p in ROOT.rglob("README*.md"):
    if any(part in p.parts for part in exclusions):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    for pat in PATTERNS_CRITICAL:
        if pat.search(text):
            issues.append({"file": str(p.relative_to(ROOT)), "type": "CRITICAL", "pattern": pat.pattern})
    # check for statements that claim single-literal authorization
    if re.search(r"La seule règle obligatoire|La seule règle", text):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "CRITICAL", "pattern": "La seule règle"})
    # detect direct instructions to commit secrets or use setx persistently
    if re.search(r"setx\s+|commit .*secret|commit .*\.env", text, re.I):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "CRITICAL", "pattern": "setx/commit secrets"})

# Warnings: missing mention of preflight/lockfile
for p in ROOT.rglob("README*.md"):
    if any(part in p.parts for part in exclusions):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    if not re.search(r"preflight|pre-?flight|tools/preflight_live_check.py", text, re.I):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "WARNING", "pattern": "no preflight mention"})
    if not re.search(r"control/production.lock|production.lock|lockfile", text, re.I):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "WARNING", "pattern": "no lockfile mention"})

if not issues:
    print("OK: aucune issue critique detectee dans les README projet (excl. .venv).")
    sys.exit(0)

# Print report
crit = [i for i in issues if i["type"] == "CRITICAL"]
print("README safety scan report\n")
for i in issues:
    print(f"[{i['type']}] {i['file']} => pattern: {i['pattern']}")

if crit:
    print("\nACTION REQUISE: Des issues critiques ont été detectees. Corrigez-les avant d'autoriser un run live.")
    sys.exit(1)
else:
    print("\nWarnings seulement. Veuillez examiner les fichiers listés.")
    sys.exit(0)
