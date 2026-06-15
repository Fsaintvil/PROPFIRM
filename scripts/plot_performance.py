"""Analyse et diagramme de l'apprentissage du robot — 14 jours glissants (trades_log.csv)."""
import csv
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timedelta

RUNTIME = Path("runtime")

# ── 1. Charger trades_log.csv (source la plus complète) ──────────

csv_path = RUNTIME / "trades_log.csv"
if not csv_path.exists():
    print("❌ trades_log.csv introuvable")
    sys.exit(1)

with open(csv_path) as f:
    rows = list(csv.DictReader(f))

print("=" * 72)
print("  APPRENTISSAGE DU ROBOT — 14 DERNIERS JOURS")
print("  Source : trades_log.csv (%d trades)" % len(rows))
print("=" * 72)

cutoff = datetime.utcnow() - timedelta(days=14)

# ── 2. Agréger par jour ──────────────────────────────────────────

daily = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                              "symbols": defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})})

all_dates = set()
for r in rows:
    ts = r.get("timestamp", "")
    day = ts[:10]
    if not day:
        continue
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        continue
    if dt < cutoff:
        continue
    all_dates.add(day)
    pnl = float(r.get("pnl", 0))
    sym = r.get("symbol", "?")
    daily[day]["trades"] += 1
    daily[day]["pnl"] += pnl
    daily[day]["symbols"][sym]["trades"] += 1
    daily[day]["symbols"][sym]["pnl"] += pnl
    if pnl > 0:
        daily[day]["wins"] += 1
        daily[day]["symbols"][sym]["wins"] += 1
    elif pnl < 0:
        daily[day]["losses"] += 1
        daily[day]["symbols"][sym]["losses"] += 1

dates = sorted(all_dates)
if not dates:
    print("\n⚠️  Aucune donnée dans les 14 derniers jours.")
    sys.exit(0)

# ── 3. Tableau quotidien ─────────────────────────────────────────

print("\n" + "=" * 72)
print("  PERFORMANCE QUOTIDIENNE")
print("=" * 72)
print("%-12s %6s %5s %6s %6s %10s %10s" % ("Date", "Trades", "Wins", "Losses", "WR", "PnL", "Cumul"))
print("-" * 62)

cumul = 0.0
total_trades = total_wins = 0
total_pnl = 0.0
for d in dates:
    dd = daily[d]
    wr = dd["wins"] / max(dd["trades"], 1) * 100
    cumul += dd["pnl"]
    total_trades += dd["trades"]
    total_wins += dd["wins"]
    total_pnl += dd["pnl"]
    print("%-12s %6d %5d %6d %5.1f%% %+9.2f %+9.2f" % (d, dd["trades"], dd["wins"], dd["losses"], wr, dd["pnl"], cumul))

print("-" * 62)
total_wr = total_wins / max(total_trades, 1) * 100
print("%-12s %6d %5d %6d %5.1f%% %+9.2f" % ("TOTAL", total_trades, total_wins, total_trades - total_wins, total_wr, total_pnl))

# ── 4. Performance par symbole ───────────────────────────────────

sym_totals = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
for d in dates:
    for sym, sd in daily[d]["symbols"].items():
        sym_totals[sym]["trades"] += sd["trades"]
        sym_totals[sym]["wins"] += sd.get("wins", 0)
        sym_totals[sym]["pnl"] += sd["pnl"]

print("\n" + "=" * 72)
print("  PERFORMANCE PAR SYMBOLE")
print("=" * 72)
print("%-10s %6s %6s %10s %8s" % ("Symbole", "Trades", "WR", "PnL", "Moy/trade"))
print("-" * 44)
for sym in sorted(sym_totals, key=lambda s: sym_totals[s]["pnl"], reverse=True):
    sd = sym_totals[sym]
    wr = sd["wins"] / max(sd["trades"], 1) * 100
    avg = sd["pnl"] / max(sd["trades"], 1)
    print("%-10s %6d %5.1f%% %+9.2f %+7.2f" % (sym, sd["trades"], wr, sd["pnl"], avg))

# ── 5. Diagramme PnL barres (ASCII) ──────────────────────────────

print("\n" + "=" * 72)
print("  DIAGRAMME PnL QUOTIDIEN")
print("=" * 72)

max_abs = max(abs(daily[d]["pnl"]) for d in dates) or 1
scale = 35 / max_abs

for d in dates:
    pnl = daily[d]["pnl"]
    n = int(abs(pnl) * scale)
    n = min(n, 35)
    if pnl >= 0:
        bar = " " * 18 + "█" * n
    else:
        bar = " " * (18 - n) + "█" * n + " " * (18 - n)
    label = "%+6.0f" % pnl
    print("%-12s %s %s $" % (d, bar, label))

# ── 6. Diagramme Win Rate (ASCII) ────────────────────────────────

print("\n" + "=" * 72)
print("  DIAGRAMME WIN RATE QUOTIDIEN")
print("=" * 72)

for d in dates:
    dd = daily[d]
    wr = dd["wins"] / max(dd["trades"], 1) * 100
    n = int(wr / 100 * 30)
    bar = "█" * n + "░" * (30 - n)
    trades_str = "%d trades" % dd["trades"]
    print("%-12s %s %5.1f%% (%s)" % (d, bar, wr, trades_str))

# ── 7. Diagramme nombre de trades ────────────────────────────────

print("\n" + "=" * 72)
print("  DIAGRAMME VOLUME DE TRADES QUOTIDIEN")
print("=" * 72)

max_trades = max(daily[d]["trades"] for d in dates) or 1
for d in dates:
    n = int(daily[d]["trades"] / max_trades * 30)
    bar = "▓" * n + "░" * (30 - n)
    print("%-12s %s %3d trades" % (d, bar, daily[d]["trades"]))

# ── 8. Indicateur d'apprentissage (1ère vs 2nde moitié) ────────

print("\n" + "=" * 72)
print("  INDICATEUR D'APPRENTISSAGE")
print("=" * 72)

if len(dates) >= 4:
    mid = len(dates) // 2
    first_dates = dates[:mid]
    second_dates = dates[mid:]

    def block_summary(ds, name):
        t = sum(daily[d]["trades"] for d in ds)
        w = sum(daily[d]["wins"] for d in ds)
        p = sum(daily[d]["pnl"] for d in ds)
        wr = w / max(t, 1) * 100
        avg = p / max(t, 1)
        print("   %-30s : %3d trades, WR %5.1f%%, PnL %+8.2f (avg %+.2f/trade)" % (name, t, wr, p, avg))
        return t, w, p, wr

    ft, fw, fp, fwr = block_summary(first_dates, "Première moitié (%s → %s)" % (first_dates[0], first_dates[-1]))
    st, sw, sp, swr = block_summary(second_dates, "Seconde moitié (%s → %s)" % (second_dates[0], second_dates[-1]))

    print()
    if sp > fp and swr >= fwr:
        print("   ✅ APPRENTISSAGE POSITIF — PnL + WR en hausse")
    elif sp > fp:
        print("   ⚠️  PnL en hausse mais WR en baisse — trades plus gros mais moins bons")
    elif swr > fwr:
        print("   ⚠️  WR en hausse mais PnL en baisse — trades plus serrés")
    else:
        print("   🔴 RÉGRESSION — PnL et WR en baisse")
else:
    print("   ⚠️  Pas assez de jours pour établir une tendance (< 4 jours avec données)")

# ── 9. État des modules ML ──────────────────────────────────────

print("\n" + "=" * 72)
print("  ÉTAT DES MODULES D'APPRENTISSAGE")
print("=" * 72)

calib = RUNTIME / "calibration_state.pkl"
if calib.exists():
    size_kb = calib.stat().st_size / 1024
    print("   calibration_state.pkl : %.0f KB (OnlineLearner) — %s" % (size_kb,
        "✅ Données présentes" if size_kb > 1 else "⚠️ Fichier vide (0 KB)"))
else:
    print("   ❌ calibration_state.pkl : inexistant (OnlineLearner jamais calibré)")

# Meta-learner status from last_signals
last_sig = RUNTIME / "last_signals.json"
if last_sig.exists():
    try:
        data = json.loads(last_sig.read_text())
        if isinstance(data, dict) and "meta" in data:
            print("   Meta-Learner : calibré")
        else:
            print("   Meta-Learner : ❌ non calibré (pas de meta analysis)")
    except:
        print("   Meta-Learner : ❌ non chargeable")
else:
    print("   last_signals.json : inexistant")

# trades historiques totaux
print("   Trades_log.csv : %d trades sur %d jours" % (total_trades, len(dates)))
perf = RUNTIME / "performance_history.json"
if perf.exists():
    try:
        pd = json.loads(perf.read_text())
        print("   Performance Monitor : %d jours trackés" % len(pd.get("daily", {})))
    except:
        print("   Performance Monitor : ❌ corrompu")

print()
print("=" * 72)
