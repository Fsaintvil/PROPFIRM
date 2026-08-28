#!/usr/bin/env python3
"""Analyse des axes d'amélioration."""
import csv
import sys
from collections import defaultdict

trades = []
with open('runtime/trades_log.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        row['_pnl'] = float(row['pnl'])
        row['_date'] = row['timestamp'][:10]
        trades.append(row)

# === AVANT vs APRÈS FIXES ===
post_fix = [t for t in trades if t['_date'] >= '2026-08-20']
pre_fix = [t for t in trades if t['_date'] < '2026-08-20']

print("=== AVANT vs APRÈS FIXES (20/08) ===")
print()
for label, subset in [('TOUTES PERIODES (71j)', trades), ('AVANT fixes (14/06-19/08)', pre_fix), ('APRES fixes (20/08-24/08)', post_fix)]:
    pnl = sum(t['_pnl'] for t in subset)
    wins = [t for t in subset if t['_pnl'] > 0]
    losers = [t for t in subset if t['_pnl'] <= 0]
    wr = len(wins)/len(subset)*100 if subset else 0
    w_sum = sum(t['_pnl'] for t in wins)
    l_sum = sum(t['_pnl'] for t in losers)
    pf = abs(w_sum/l_sum) if l_sum != 0 else 999
    avg = pnl/len(subset) if subset else 0
    days = len(set(t['_date'] for t in subset))
    tpd = len(subset)/days if days else 0
    print(f"{label}:")
    print(f"  Trades: {len(subset)} ({tpd:.1f}/jour)")
    print(f"  PnL: {pnl:+.2f}$")
    print(f"  WR: {wr:.1f}%")
    print(f"  PF: {pf:.2f}")
    print(f"  Avg/trade: {avg:+.2f}$")
    print()

# === SANS XAUUSD + XAGUSD ===
print("=== SANS XAUUSD + XAGUSD ===")
clean = [t for t in trades if t['symbol'] not in ('XAUUSD', 'XAGUSD')]
pnl = sum(t['_pnl'] for t in clean)
wins = [t for t in clean if t['_pnl'] > 0]
wr = len(wins)/len(clean)*100 if clean else 0
w_sum = sum(t['_pnl'] for t in wins)
l_sum = sum(t['_pnl'] for t in [t for t in clean if t['_pnl'] <= 0])
pf = abs(w_sum/l_sum) if l_sum != 0 else 999
avg = pnl/len(clean) if clean else 0
days = len(set(t['_date'] for t in clean))
print(f"Trades: {len(clean)}, PnL: {pnl:+.2f}$, WR: {wr:.1f}%, PF: {pf:.2f}, Avg: {avg:+.2f}$/trade, {days} jours")

# === PAR SYMOLE APRÈS 20/08 ===
print()
print("=== PAR SYMOLE APRÈS 20/08 ===")
by_sym = defaultdict(lambda: {'pnl':0, 'trades':0, 'wins':0})
for t in post_fix:
    by_sym[t['symbol']]['pnl'] += t['_pnl']
    by_sym[t['symbol']]['trades'] += 1
    if t['_pnl'] > 0:
        by_sym[t['symbol']]['wins'] += 1

for sym, data in sorted(by_sym.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = data['wins']/data['trades']*100 if data['trades'] else 0
    print(f"  {sym:12}: {data['trades']:2d} trades, WR {wr:.0f}%, PnL {data['pnl']:+.2f}$")

# === RISK/REWARD PAR SYMOLE ===
print()
print("=== RISK/REWARD PAR SYMOLE ===")
by_sym2 = defaultdict(lambda: {'wins': [], 'losses': []})
for t in trades:
    if t['_pnl'] > 0:
        by_sym2[t['symbol']]['wins'].append(t['_pnl'])
    elif t['_pnl'] < 0:
        by_sym2[t['symbol']]['losses'].append(t['_pnl'])

for sym in sorted(by_sym2.keys()):
    data = by_sym2[sym]
    avg_w = sum(data['wins'])/len(data['wins']) if data['wins'] else 0
    avg_l = sum(data['losses'])/len(data['losses']) if data['losses'] else 0
    rr = abs(avg_w/avg_l) if avg_l != 0 else 999
    print(f"  {sym:12}: avg_win +{avg_w:.2f}$, avg_loss {avg_l:.2f}$, RR {rr:.2f}")

# === ANALYSE DES GROSSES PERTES ===
print()
print("=== GROSSES PERTES (> 100$) ===")
big_losses = [t for t in trades if t['_pnl'] < -100]
print(f"Nombre: {len(big_losses)}")
total = sum(t['_pnl'] for t in big_losses)
print(f"Total: {total:+.2f}$")
by_sym_bl = defaultdict(lambda: {'count': 0, 'pnl': 0})
for t in big_losses:
    by_sym_bl[t['symbol']]['count'] += 1
    by_sym_bl[t['symbol']]['pnl'] += t['_pnl']
for sym, data in sorted(by_sym_bl.items(), key=lambda x: x[1]['pnl']):
    print(f"  {sym:12}: {data['count']} trades, {data['pnl']:+.2f}$")

# === TIME-STOP ANALYSIS ===
print()
print("=== TIME-STOP ANALYSIS ===")
ts = [t for t in trades if t['reason'] == 'time_stop']
ts_pnl = sum(t['_pnl'] for t in ts)
ts_wins = [t for t in ts if t['_pnl'] > 0]
print(f"Total: {len(ts)} trades, PnL {ts_pnl:+.2f}$")
print(f"WR: {len(ts_wins)/len(ts)*100:.1f}%")
print(f"Avg: {ts_pnl/len(ts):+.2f}$/trade")
print()
print("Time-stops PAR SYMOLE:")
ts_by_sym = defaultdict(lambda: {'trades': [], 'pnl': 0})
for t in ts:
    ts_by_sym[t['symbol']]['trades'].append(t)
    ts_by_sym[t['symbol']]['pnl'] += t['_pnl']
for sym, data in sorted(ts_by_sym.items(), key=lambda x: x[1]['pnl']):
    print(f"  {sym:12}: {len(data['trades'])} trades, PnL {data['pnl']:+.2f}$")

# === OPENING vs CLOSING SESSION ===
print()
print("=== ANALYSE SESSION ===")
sessions = {
    'Asia (21-05 UTC)': lambda h: h >= 21 or h < 5,
    'London (07-12 UTC)': lambda h: 7 <= h < 12,
    'Overlap LDN-NY (12-15 UTC)': lambda h: 12 <= h < 15,
    'NY (15-20 UTC)': lambda h: 15 <= h < 20,
    'Late NY (20-21 UTC)': lambda h: 20 <= h < 21,
}

for session_name, session_fn in sessions.items():
    session_trades = [t for t in trades if session_fn(int(t['timestamp'][11:13]))]
    if session_trades:
        pnl = sum(t['_pnl'] for t in session_trades)
        wins = [t for t in session_trades if t['_pnl'] > 0]
        wr = len(wins)/len(session_trades)*100
        avg = pnl/len(session_trades)
        print(f"  {session_name:28}: {len(session_trades):3d} trades, WR {wr:5.1f}%, PnL {pnl:>+8.2f}$, avg {avg:+.2f}$/trade")
