"""
Rapport quotidien — Affiche le bilan complet du robot.
Usage: python scripts/daily_report.py
       opencode "bilan"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine_simple.performance_monitor import PerformanceMonitor, HISTORY_FILE, REPORT_FILE
import json

pm = PerformanceMonitor()

# Vérifier si le fichier de rapport existe déjà (généré récemment)
import time

now = time.time()
use_cached = False
if REPORT_FILE.exists() and (now - REPORT_FILE.stat().st_mtime) < 60:
    try:
        with open(REPORT_FILE) as f:
            report = json.load(f)
        if report.get("generated_at"):
            use_cached = True
    except Exception:
        pass

if not use_cached:
    report = pm.generate_report()

# Afficher le rapport textuel
print(pm.summary_text(detailed=True))

# Afficher les alertes non lues
alerts = report.get("alerts", [])
if alerts:
    critical = [a for a in alerts if a["level"] == "CRITICAL"]
    warnings = [a for a in alerts if a["level"] == "WARNING"]
    if critical:
        print(f"\n🔴 {len(critical)} ALERTE(S) CRITIQUE(S) — Intervention requise")
        for a in critical:
            print(f"  • {a['message']}")
    if warnings:
        print(f"\n🟡 {len(warnings)} ALERTE(S) — Surveillance")
        for a in warnings:
            print(f"  • {a['message']}")

# Challenge status bar
c = report.get("challenge", {})
pp = c.get("profit_progress_pct", 0)
bar_len = 40
filled = max(0, min(bar_len, int((pp + 100) / 200 * bar_len)))
print(f"\n🎯 Progression: [{'█' * filled}{'░' * (bar_len - filled)}] {pp:+.1f}%")

# Temps restant
dr = c.get("days_remaining", 0)
td = c.get("trading_days", 0)
print(f"   Jours: {td} tradés / {dr} restants")
print(f"   Rythme: ${c.get('avg_daily_pnl', 0):.0f}/jour → ~{c.get('estimated_days_to_target', '?')} jours")
