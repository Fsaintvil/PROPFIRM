"""
monitor.py — Tableau de bord live du robot FTMO
Affiche : positions, PnL, DD, progression challenge, trades du jour, logs
Usage (2e terminal) : python monitor.py
"""

import json
import os
import re
import sqlite3
import time
from datetime import date, datetime

LOG_FILE = "logs/simple_robot.log"
DB_PATH = "runtime/trading_journal.db"
STATE_FILE = "runtime/robot_state.json"
HEARTBEAT = "runtime/heartbeat.txt"

def read_last_lines(n=50):
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        return lines[-n:]
    except Exception:
        return []

def get_db_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE reason='history_calibrate'")
        hist = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE time_close!='' AND reason!='history_calibrate'")
        closed = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE time_close=''")
        opened = c.fetchone()[0]
        c.execute("SELECT COALESCE(SUM(profit),0) FROM trades WHERE time_close!='' AND reason!='history_calibrate'")
        pnl_closed = c.fetchone()[0]
        c.execute("SELECT profit FROM trades WHERE reason!='history_calibrate' AND time_close!=''")
        profits = [r[0] for r in c.fetchall()]
        conn.close()
        wr = sum(1 for p in profits if p > 0) / max(len(profits), 1)
        return hist, closed, opened, pnl_closed, wr, len(profits)
    except Exception:
        return 0,0,0,0,0,0

def parse_cycle(lines):
    """Extract latest cycle info from log"""
    data = {}
    for line in reversed(lines):
        # Balance line
        m = re.search(r'\[Cycle (\d+)\].*Balance: ([\d.]+).*Equity: ([\d.]+).*Floating: ([-+\d.]+).*DD: ([\d.]+)', line)
        if m and 'balance' not in data:
            data.update(cycle=int(m.group(1)), balance=float(m.group(2)),
                       equity=float(m.group(3)), floating=float(m.group(4)),
                       dd=float(m.group(5)))
        # FTMO report
        m = re.search(r'profit_progress: ([\d.]+%)', line)
        if m and 'progress' not in data:
            data['progress'] = m.group(1)
        # Closed trades
        m = re.search(r'\[CLOSED\] (\w+) profit=([-+\d.]+).*R=([-+\d.]+)', line)
        if m and 'closed_trades' not in data:
            data.setdefault('closed_trades', []).append(f"{m.group(1)} {m.group(2)} (R={m.group(3)})")
        # Regime
        m = re.search(r'\[REGIME\] (\w+): (\w+)', line)
        if m and 'regime' not in data:
            data['regime'] = f"{m.group(1)}={m.group(2)}"
        # ML
        m = re.search(r'\[ML\] (\w+): (\w+) \(score=([\d.]+), agree=(\w+)', line)
        if m and 'ml' not in data:
            data['ml'] = f"{m.group(1)} {m.group(2)} sc={m.group(3)} agree={m.group(4)}"
        # DL
        m = re.search(r'\[DL\] (\w+): (\w+) \(score=([\d.]+), agree=(\w+)', line)
        if m and 'dl' not in data:
            data['dl'] = f"{m.group(1)} {m.group(2)} sc={m.group(3)} agree={m.group(4)}"
        # Alert lines
        if 'FATAL' in line or 'CRITICAL' in line or 'ERROR' in line:
            data['last_error'] = line.strip()
        # FTMO summary
        m = re.search(r'RAPPORT FTMO CHALLENGE', line)
        if m:
            data['ftmo_report'] = True
    return data

def get_ftmo_progress():
    """Extract FTMO report from log"""
    lines = read_last_lines(200)
    report = {}
    capture = False
    for line in reversed(lines):
        if 'RAPPORT FTMO CHALLENGE' in line:
            capture = True
            continue
        if capture and '=' * 50 in line:
            break
        if capture and ': ' in line:
            parts = line.strip().split(': ', 1)
            if len(parts) == 2:
                report[parts[0].strip()] = parts[1].strip()
    return report

def count_today_trades():
    lines = read_last_lines(2000)
    today = date.today().isoformat()
    opened = sum(1 for line in lines if '[TRADE]' in line and today in line)
    closed = sum(1 for line in lines if '[CLOSED]' in line and today in line)
    return opened, closed

def get_recent_closed(n=5):
    lines = read_last_lines(500)
    closed = []
    for line in reversed(lines):
        m = re.search(r'\[CLOSED\] (\w+) profit=([-+\d.]+).*R=([-+\d.]+).*regime=(\w+)', line)
        if m:
            closed.append(f"{m.group(1)} {m.group(2)} R={m.group(3)} [{m.group(4)}]")
            if len(closed) >= n:
                break
    return closed

def get_positions_from_log():
    lines = read_last_lines(500)
    _positions, _pending = [], []
    for line in reversed(lines):
        m = re.search(r'Positions: (\d+), Pending: (\d+)', line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 0, 0

def main():
    print("=" * 62)
    print("  MONITEUR FTMO CHALLENGE — Ctrl+C pour quitter")
    print("=" * 62)

    while True:
        os.system("cls" if os.name == "nt" else "clear")

        # ─── State ───
        lines = read_last_lines(100)
        d = parse_cycle(lines)
        report = get_ftmo_progress()
        hist, closed, opened, pnl_closed, wr, total_live = get_db_stats()
        today_op, today_cl = count_today_trades()
        pos_count, pend_count = get_positions_from_log()
        recent_closed = get_recent_closed(5)

        # ─── Header ───
        bal = d.get('balance', 0)
        eq = d.get('equity', 0)
        fl = d.get('floating', 0)
        dd = d.get('dd', 0)
        progress = report.get('profit_progress', d.get('progress', '?'))

        print(f"  Balance: ${bal:>8.2f}  |  Equity: ${eq:>8.2f}  |  Floating: {fl:>+8.2f}")
        print(f"  Drawdown: ${dd:>7.2f}  |  Peak: {report.get('dd_from_peak', '?'):>6}  |  Progress: {progress:>6}")
        print(f"  PnL clôturé: ${pnl_closed:>+8.2f}  |  Live WR: {wr:.0%} ({total_live}t)")
        print(f"  Positions: {pos_count}  Pending: {pend_count}  "
              f"|  Aujourd'hui: {today_op} ouvert(s) {today_cl} fermé(s)")
        print(f"  Cycle: {d.get('cycle', '?')}  |  Consec. pertes: {report.get('consecutive_losses', '?')}")
        print(f"  Régime: {d.get('regime', '?')}")
        print(f"  ML: {d.get('ml', '—')}")
        print(f"  DL: {d.get('dl', '—')}")

        # ─── FTMO Report ───
        if report:
            print("\n  ┌─ FTMO CHALLENGE ─────────────────────────────┐")
            for k, v in list(report.items())[:8]:
                k = k.replace('_', ' ').title()
                print(f"  │ {k:25s}: {v:>30s} │")
            print("  └────────────────────────────────────────────────┘")

        # ─── Recent closed ───
        if recent_closed:
            print("\n  ── Dernières fermetures ──")
            for c in recent_closed:
                print(f"    {c}")

        # ─── Positions details ───
        for line in reversed(lines):
            m = re.search(r'(\w+): ([-+\d.]+) USD', line)
            if m:
                sym = m.group(1)
                pnl = float(m.group(2))
                if sym in ["TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL"]:
                    continue
                print(f"  {sym+':':>12s} {pnl:>+8.2f} USD")

        # ─── Last error ───
        if d.get('last_error'):
            err = d['last_error'][:100]
            print(f"\n  ⚠ ERREUR: {err}")

        # ─── Calibration ───
        try:
            state_path = os.path.join("runtime", "robot_state.json")
            cal_data = json.load(open(state_path)) if os.path.exists(state_path) else {}
            if cal_data:
                print("\n  ── État sauvegardé ──")
        except Exception:
            pass

        # ─── DB stats ───
        print(f"\n  Journal: {hist} historique + {total_live} live | Ouvert: {opened}")

        # ─── Heartbeat ───
        if os.path.exists(HEARTBEAT):
            with open(HEARTBEAT) as f:
                hb = f.read().strip()[:19]
            age = (time.time() - datetime.fromisoformat(hb).timestamp()) if hb else 999
            if age > 120:
                print(f"\n  ❌ HEARTBEAT: {age:.0f}s (mort ?!)")
            else:
                print(f"  ✅ Heartbeat: {age:.0f}s ago")
        else:
            print("\n  ⚫ Heartbeat: N/A")

        time.sleep(4)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
