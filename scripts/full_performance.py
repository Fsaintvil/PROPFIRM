#!/usr/bin/env python3
"""Analyse complète de performance du robot — tous les jours."""
import csv
import sys
from datetime import datetime
from collections import defaultdict

def full_analysis():
    trades = []
    with open('runtime/trades_log.csv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row['_pnl'] = float(row['pnl'])
                row['_date'] = row['timestamp'][:10]
                row['_hour'] = row['timestamp'][11:13]
                trades.append(row)
            except:
                pass

    print(f"{'='*70}")
    print(f"  ANALYSE COMPLÈTE DU ROBOT — {len(trades)} TRADES")
    print(f"  Période: {trades[0]['timestamp'][:10]} → {trades[-1]['timestamp'][:10]}")
    print(f"{'='*70}")

    # ====== GLOBAL ======
    total_pnl = sum(t['_pnl'] for t in trades)
    winners = [t for t in trades if t['_pnl'] > 0]
    losers = [t for t in trades if t['_pnl'] <= 0]
    avg_win = sum(t['_pnl'] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t['_pnl'] for t in losers) / len(losers) if losers else 0
    pf = abs(sum(t['_pnl'] for t in winners) / sum(t['_pnl'] for t in losers)) if losers and sum(t['_pnl'] for t in losers) != 0 else 0

    print(f"\n{'─'*70}")
    print(f"  BILAN GLOBAL")
    print(f"{'─'*70}")
    print(f"  Trades totaux:   {len(trades)}")
    print(f"  PnL total:       {total_pnl:+.2f}$")
    print(f"  Win Rate:        {len(winners)}/{len(trades)} = {len(winners)/len(trades)*100:.1f}%")
    print(f"  Profit Factor:   {pf:.2f}")
    print(f"  Avg Winner:      +{avg_win:.2f}$")
    print(f"  Avg Loser:       {avg_loss:.2f}$")
    print(f"  RR Ratio:        {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "  RR: N/A")

    # ====== PAR JOUR ======
    print(f"\n{'='*70}")
    print(f"  CLASSEMENT PAR JOUR (meilleurs → pires)")
    print(f"{'='*70}")

    by_day = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0})
    for t in trades:
        d = t['_date']
        by_day[d]["trades"].append(t)
        by_day[d]["pnl"] += t['_pnl']
        if t['_pnl'] > 0:
            by_day[d]["wins"] += 1

    # Trier par PnL décroissant
    sorted_days = sorted(by_day.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print(f"\n  {'Rang':>4} | {'Date':10} | {'Trades':>6} | {'WR':>6} | {'PnL':>10} | {'PF':>6} | {'Statut'}")
    print(f"  {'─'*4}─┼─{'─'*10}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*10}─┼─{'─'*6}─┼─{'─'*20}")

    for rank, (date, data) in enumerate(sorted_days, 1):
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        w_trades = [t for t in data["trades"] if t["_pnl"] > 0]
        l_trades = [t for t in data["trades"] if t["_pnl"] <= 0]
        w_sum = sum(t["_pnl"] for t in w_trades)
        l_sum = sum(t["_pnl"] for t in l_trades)
        pf_day = abs(w_sum / l_sum) if l_sum != 0 else 999

        if pnl > 50:
            statut = "🟢 EXCELLENT"
        elif pnl > 10:
            statut = "🟢 BON"
        elif pnl > 0:
            statut = "🟡 POSITIF"
        elif pnl > -10:
            statut = "🟡 NEUTRE"
        elif pnl > -50:
            statut = "🟠 MAUVAIS"
        else:
            statut = "🔴 CATASTROPHIQUE"

        print(f"  {rank:>4} | {date:10} | {n:>6} | {wr:>5.1f}% | {pnl:>+10.2f}$ | {pf_day:>6.2f} | {statut}")

    # ====== TOP 5 MEILLEURS ======
    print(f"\n{'='*70}")
    print(f"  🏆 TOP 5 MEILLEURS JOURS")
    print(f"{'='*70}")

    for rank, (date, data) in enumerate(sorted_days[:5], 1):
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        print(f"\n  #{rank} — {date} — PnL {pnl:+.2f}$ — {n} trades — WR {wr:.0f}%")
        for t in data["trades"]:
            p = t["_pnl"]
            icon = '+' if p > 0 else ' '
            print(f"    {t['timestamp'][11:19]} {t['symbol']:10s} {t['reason']:12s} {icon}{p:.2f}$")

    # ====== TOP 5 PIRES ======
    print(f"\n{'='*70}")
    print(f"  💀 TOP 5 PIRES JOURS")
    print(f"{'='*70}")

    for rank, (date, data) in enumerate(sorted_days[-5:][::-1], 1):
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        print(f"\n  #{rank} — {date} — PnL {pnl:+.2f}$ — {n} trades — WR {wr:.0f}%")
        for t in data["trades"]:
            p = t["_pnl"]
            icon = '+' if p > 0 else ' '
            print(f"    {t['timestamp'][11:19]} {t['symbol']:10s} {t['reason']:12s} {icon}{p:.2f}$")

    # ====== PAR SYMOLE ======
    print(f"\n{'='*70}")
    print(f"  PERFORMANCE PAR SYMOLE (TOUTES PÉRIODES)")
    print(f"{'='*70}")

    by_sym = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0})
    for t in trades:
        s = t['symbol']
        by_sym[s]["trades"].append(t)
        by_sym[s]["pnl"] += t['_pnl']
        if t['_pnl'] > 0:
            by_sym[s]["wins"] += 1

    sorted_syms = sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print(f"\n  {'Symbole':12} | {'Trades':>6} | {'WR':>6} | {'PnL':>10} | {'Avg':>8} | {'PF':>6}")
    print(f"  {'─'*12}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*10}─┼─{'─'*8}─┼─{'─'*6}")

    for sym, data in sorted_syms:
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        avg = pnl / n if n else 0
        w_sum = sum(t["_pnl"] for t in data["trades"] if t["_pnl"] > 0)
        l_sum = sum(t["_pnl"] for t in data["trades"] if t["_pnl"] <= 0)
        pf_s = abs(w_sum / l_sum) if l_sum != 0 else 999
        print(f"  {sym:12} | {n:>6} | {wr:>5.1f}% | {pnl:>+10.2f}$ | {avg:>+7.2f}$ | {pf_s:>6.2f}")

    # ====== PAR HEURE ======
    print(f"\n{'='*70}")
    print(f"  PERFORMANCE PAR HEURE (UTC)")
    print(f"{'='*70}")

    by_hour = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0})
    for t in trades:
        h = t['_hour']
        by_hour[h]["trades"].append(t)
        by_hour[h]["pnl"] += t['_pnl']
        if t['_pnl'] > 0:
            by_hour[h]["wins"] += 1

    print(f"\n  {'Heure':6} | {'Trades':>6} | {'WR':>6} | {'PnL':>10} | {'Bar'}")
    print(f"  {'─'*6}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*10}─┼─{'─'*30}")

    for h in sorted(by_hour.keys()):
        data = by_hour[h]
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        bar_len = int(abs(pnl) / 10)
        bar = ('+' * bar_len) if pnl > 0 else ('-' * bar_len)
        color = '🟢' if pnl > 0 else '🔴'
        print(f"  {h:>4}h  | {n:>6} | {wr:>5.1f}% | {pnl:>+10.2f}$ | {color} {bar}")

    # ====== PAR RAISON ======
    print(f"\n{'='*70}")
    print(f"  PERFORMANCE PAR RAISON DE SORTIE")
    print(f"{'='*70}")

    by_reason = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0})
    for t in trades:
        r = t['reason']
        by_reason[r]["trades"].append(t)
        by_reason[r]["pnl"] += t['_pnl']
        if t['_pnl'] > 0:
            by_reason[r]["wins"] += 1

    for reason, data in sorted(by_reason.items(), key=lambda x: x[1]["pnl"]):
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        print(f"  {reason:12}: {n:>4} trades | WR {wr:>5.1f}% | PnL {pnl:>+10.2f}$")

    # ====== TENDANCE HEBDOMADAIRE ======
    print(f"\n{'='*70}")
    print(f"  TENDANCE HEBDOMADAIRE")
    print(f"{'='*70}")

    by_week = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0, "days": set()})
    for t in trades:
        dt = datetime.strptime(t['timestamp'], '%Y-%m-%d %H:%M:%S')
        week_start = dt - __import__('datetime').timedelta(days=dt.weekday())
        wk = week_start.strftime('%Y-%m-%d')
        by_week[wk]["trades"].append(t)
        by_week[wk]["pnl"] += t['_pnl']
        by_week[wk]["days"].add(t['_date'])
        if t['_pnl'] > 0:
            by_week[wk]["wins"] += 1

    print(f"\n  {'Semaine':12} | {'Jours':>4} | {'Trades':>6} | {'WR':>6} | {'PnL':>10}")
    print(f"  {'─'*12}─┼─{'─'*4}─┼─{'─'*6}─┼─{'─'*6}─┼─{'─'*10}")

    for wk in sorted(by_week.keys()):
        data = by_week[wk]
        n = len(data["trades"])
        nd = len(data["days"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        print(f"  {wk:12} | {nd:>4} | {n:>6} | {wr:>5.1f}% | {pnl:>+10.2f}$")

    # ====== ÉVOLUTION DU WR ======
    print(f"\n{'='*70}")
    print(f"  ÉVOLUTION DU WIN RATE (fenêtre glissante 20 trades)")
    print(f"{'='*70}")

    window = 20
    for i in range(window, len(trades), window):
        chunk = trades[i-window:i]
        n = len(chunk)
        w = sum(1 for t in chunk if t['_pnl'] > 0)
        pnl = sum(t['_pnl'] for t in chunk)
        wr = w/n*100
        date_range = f"{chunk[0]['_date']}→{chunk[-1]['_date']}"
        bar = '#' * int(wr / 2)
        print(f"  {date_range:23} | WR {wr:5.1f}% | PnL {pnl:>+8.2f}$ | {bar}")

    # ====== DRAWDOWN ======
    print(f"\n{'='*70}")
    print(f"  DRAWDOWN ANALYSIS")
    print(f"{'='*70}")

    peak = 0
    equity = 0
    max_dd = 0
    max_dd_date = ""
    dd_start = ""
    in_dd = False

    for t in trades:
        equity += t['_pnl']
        if equity > peak:
            peak = equity
            in_dd = False
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_date = t['timestamp']
            if not in_dd:
                dd_start = t['_date']
                in_dd = True

    print(f"  Peak equity:      {peak:+.2f}$")
    print(f"  Current equity:   {equity:+.2f}$")
    print(f"  Max drawdown:     {max_dd:.2f}$")
    print(f"  Max DD date:      {max_dd_date}")
    print(f"  DD start:         {dd_start}")

    # ====== CIRCUIT BREAKER ======
    print(f"\n{'='*70}")
    print(f"  CIRCUIT BREAKER ANALYSIS")
    print(f"{'='*70}")

    max_consec = 0
    current_consec = 0
    trips = 0
    for t in trades:
        if t['_pnl'] <= 0:
            current_consec += 1
            if current_consec >= 5:
                trips += 1
        else:
            max_consec = max(max_consec, current_consec)
            current_consec = 0

    print(f"  Max pertes consécutives: {max_consec}")
    print(f"  Circuit breaker trips:   {trips}")

    # ====== JOURS DE SEMAINE ======
    print(f"\n{'='*70}")
    print(f"  PERFORMANCE PAR JOUR DE SEMAINE")
    print(f"{'='*70}")

    day_names = {0: 'Lundi', 1: 'Mardi', 2: 'Mercredi', 3: 'Jeudi', 4: 'Vendredi', 5: 'Samedi', 6: 'Dimanche'}
    by_dow = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0})
    for t in trades:
        dt = datetime.strptime(t['timestamp'], '%Y-%m-%d %H:%M:%S')
        dow = dt.weekday()
        by_dow[dow]["trades"].append(t)
        by_dow[dow]["pnl"] += t['_pnl']
        if t['_pnl'] > 0:
            by_dow[dow]["wins"] += 1

    for dow in sorted(by_dow.keys()):
        data = by_dow[dow]
        n = len(data["trades"])
        wr = data["wins"]/n*100 if n else 0
        pnl = data["pnl"]
        avg = pnl/n if n else 0
        bar = '█' * max(1, int(abs(pnl)/20))
        sign = '+' if pnl > 0 else ' '
        print(f"  {day_names[dow]:12} | {n:>4} trades | WR {wr:>5.1f}% | PnL {sign}{pnl:.2f}$ | avg {avg:+.2f}$ | {bar}")


if __name__ == "__main__":
    full_analysis()
