#!/usr/bin/env python3
"""Analyse complète des trades d'une journée."""
import csv
import sys
from datetime import datetime
from collections import defaultdict

def analyze_day(date_str="2026-08-24", log_file="runtime/trades_log.csv"):
    trades = []
    with open(log_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['timestamp'].startswith(date_str):
                trades.append(row)

    if not trades:
        print(f"Aucun trade trouvé pour {date_str}")
        return

    print(f"{'='*60}")
    print(f"  ANALYSE COMPLÈTE — {date_str} — {len(trades)} TRADES")
    print(f"{'='*60}")
    print()

    total_pnl = sum(float(t['pnl']) for t in trades)
    winners = [t for t in trades if float(t['pnl']) > 0]
    losers = [t for t in trades if float(t['pnl']) <= 0]
    avg_win = sum(float(t['pnl']) for t in winners) / len(winners) if winners else 0
    avg_loss = sum(float(t['pnl']) for t in losers) / len(losers) if losers else 0
    pf = abs(sum(float(t['pnl']) for t in winners) / sum(float(t['pnl']) for t in losers)) if losers and sum(float(t['pnl']) for t in losers) != 0 else 0

    print(f"PnL TOTAL:     {total_pnl:+.2f}$")
    print(f"WIN RATE:      {len(winners)}/{len(trades)} = {len(winners)/len(trades)*100:.1f}%")
    print(f"PROFIT FACTOR: {pf:.2f}")
    print(f"MOY GAGNANT:   +{avg_win:.2f}$")
    print(f"MOY PERDANT:   {avg_loss:.2f}$")
    print(f"RR RATIO:      {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "RR: N/A")
    print(f"GAGNANTS:      +{sum(float(t['pnl']) for t in winners):.2f}$ ({len(winners)} trades)")
    print(f"PERDANTS:      {sum(float(t['pnl']) for t in losers):.2f}$ ({len(losers)} trades)")
    print()

    # Par symbole
    print(f"{'─'*60}")
    print(f"  PAR SYMOLE")
    print(f"{'─'*60}")
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t['symbol']].append(t)

    for sym in sorted(by_sym.keys(), key=lambda s: sum(float(t['pnl']) for t in by_sym[s])):
        ts = by_sym[sym]
        pnl = sum(float(t['pnl']) for t in ts)
        wins = [t for t in ts if float(t['pnl']) > 0]
        wr = len(wins)/len(ts)*100
        avg = pnl / len(ts)
        print(f"\n  {sym}: {len(ts)} trades, WR {wr:.0f}%, PnL {pnl:+.2f}$, avg {avg:+.2f}$/trade")
        for t in ts:
            p = float(t['pnl'])
            icon = '+' if p > 0 else ' '
            reason = t['reason'].ljust(12)
            ts_str = t['timestamp'][11:19]
            print(f"    {ts_str} | {reason} | {icon}{p:.2f}$")

    # Par heure
    print(f"\n{'─'*60}")
    print(f"  PAR HEURE (UTC)")
    print(f"{'─'*60}")
    by_hour = defaultdict(list)
    for t in trades:
        h = t['timestamp'][11:13]
        by_hour[h].append(t)

    for h in sorted(by_hour.keys()):
        ts = by_hour[h]
        pnl = sum(float(t['pnl']) for t in ts)
        wins = [t for t in ts if float(t['pnl']) > 0]
        wr = len(wins)/len(ts)*100 if ts else 0
        bar = '#' * len(ts)
        print(f"  {h}h: {len(ts):2d} trades | WR {wr:5.1f}% | PnL {pnl:+8.2f}$ | {bar}")

    # Par raison
    print(f"\n{'─'*60}")
    print(f"  PAR RAISON DE SORTIE")
    print(f"{'─'*60}")
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t['reason']].append(t)

    for r in sorted(by_reason.keys(), key=lambda x: sum(float(t['pnl']) for t in by_reason[x])):
        ts = by_reason[r]
        pnl = sum(float(t['pnl']) for t in ts)
        wins = [t for t in ts if float(t['pnl']) > 0]
        wr = len(wins)/len(ts)*100 if ts else 0
        print(f"  {r:12s}: {len(ts):2d} trades | WR {wr:5.1f}% | PnL {pnl:+8.2f}$")

    # Top trades
    print(f"\n{'─'*60}")
    print(f"  TOP 3 GAGNANTS")
    print(f"{'─'*60}")
    sorted_trades = sorted(trades, key=lambda t: float(t['pnl']), reverse=True)
    for i, t in enumerate(sorted_trades[:3], 1):
        print(f"  {i}. {t['timestamp'][11:19]} {t['symbol']:10s} {t['reason']:12s} +{float(t['pnl']):.2f}$")

    print(f"\n{'─'*60}")
    print(f"  TOP 3 PERDANTS")
    print(f"{'─'*60}")
    for i, t in enumerate(sorted_trades[-3:][::-1], 1):
        print(f"  {i}. {t['timestamp'][11:19]} {t['symbol']:10s} {t['reason']:12s} {float(t['pnl']):.2f}$")

    # Analyse time_stops
    print(f"\n{'─'*60}")
    print(f"  ANALYSE TIME-STOPS (FUIES ?)")
    print(f"{'─'*60}")
    ts_trades = [t for t in trades if t['reason'] == 'time_stop']
    if ts_trades:
        ts_pnl = sum(float(t['pnl']) for t in ts_trades)
        print(f"  Time-stops: {len(ts_trades)} trades, PnL {ts_pnl:+.2f}$")
        print(f"  Moyen: {ts_pnl/len(ts_trades):+.2f}$/trade")
        print(f"  → Les time-stops sont-ils des pertes stoppées ou des trades morts ?")
        for t in ts_trades:
            p = float(t['pnl'])
            print(f"    {t['timestamp'][11:19]} {t['symbol']:10s} {p:+.2f}$")
    else:
        print(f"  Aucun time-stop aujourd'hui")

    # Dash avant/après restart
    print(f"\n{'─'*60}")
    print(f"  AVANT / APRÈS RESTART (21:20 UTC)")
    print(f"{'─'*60}")
    before = [t for t in trades if t['timestamp'] < "2026-08-24 21:20:00"]
    after = [t for t in trades if t['timestamp'] >= "2026-08-24 21:20:00"]

    for label, subset in [("AVANT restart (ancienne config)", before), ("APRÈS restart (nouvelle config)", after)]:
        if subset:
            pnl = sum(float(t['pnl']) for t in subset)
            wins = [t for t in subset if float(t['pnl']) > 0]
            wr = len(wins)/len(subset)*100
            print(f"  {label}: {len(subset)} trades, WR {wr:.0f}%, PnL {pnl:+.2f}$")
        else:
            print(f"  {label}: 0 trades")

    # Analyse consécutives
    print(f"\n{'─'*60}")
    print(f"  SÉQUENCE DE PERTES")
    print(f"{'─'*60}")
    max_consec_loss = 0
    current_consec = 0
    max_consec_win = 0
    current_win = 0
    for t in trades:
        if float(t['pnl']) <= 0:
            current_consec += 1
            current_win = 0
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_win += 1
            current_consec = 0
            max_consec_win = max(max_consec_win, current_win)

    print(f"  Max pertes consécutives: {max_consec_loss}")
    print(f"  Max wins consécutifs:    {max_consec_win}")

    # Circuit breaker check
    if max_consec_loss >= 5:
        print(f"  ⚠️ Circuit breaker déclenché au moins 1 fois (≥5 pertes consécutives)")

    print(f"\n{'='*60}")
    print(f"  DIAGNOSTIC")
    print(f"{'='*60}")

    # Problèmes identifiés
    issues = []
    if len(winners)/len(trades) < 0.5:
        issues.append(f"WR {len(winners)/len(trades)*100:.0f}% < 50% → majorité de trades perdants")
    if avg_loss != 0 and avg_win / abs(avg_loss) < 1.0:
        issues.append(f"RR {avg_win/abs(avg_loss):.2f} < 1.0 → les gains sont plus petits que les pertes")
    if len(ts_trades) > len(trades) * 0.3:
        issues.append(f"{len(ts_trades)} time-stops = {len(ts_trades)/len(trades)*100:.0f}% des trades → trop de trades morts")
    if max_consec_loss >= 5:
        issues.append(f"{max_consec_loss} pertes consécutives → circuit breaker déclenché")

    if issues:
        print("  PROBLÈMES IDENTIFIÉS:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    else:
        print("  Aucun problème majeur identifié")

    # Points positifs
    positives = []
    if pf > 1.0:
        positives.append(f"PF {pf:.2f} > 1.0 → le robot est rentable malgré le WR faible")
    if len(ts_trades) == 0:
        positives.append("Aucun time-stop → tous les trades ont eu une vraie sortie")
    if max_consec_loss < 5:
        positives.append(f"Pertes max consécutives {max_consec_loss} < 5 → circuit breaker épargné")

    if positives:
        print("\n  POINTS POSITIFS:")
        for p in positives:
            print(f"  ✅ {p}")

if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-24"
    analyze_day(date)
