#!/usr/bin/env python3
"""Live Monitor — surveillance continue du robot MT5 FTMO.
Usage: python scripts/live_monitor.py
"""
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def get_pid():
    try:
        with open("runtime/robot.pid") as f:
            return int(f.read().strip())
    except Exception:
        return None


def is_process_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            import signal
            os.kill(pid, 0)
            return True
        except (OSError, ImportError):
            return False


def read_tail(path, n=6):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 4096)
            f.seek(max(0, size - chunk))
            data = f.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return lines[-n:]
    except Exception:
        return []


def print_status():
    ftmo = read_json("runtime/ftmo_report.json")
    pid = get_pid()
    alive = is_process_alive(pid) if pid else False
    now = datetime.now().strftime("%H:%M:%S")

    lines = [
        f"\033[36m=== LIVE MONITOR — {now} ===\033[0m",
    ]

    if pid and alive:
        lines.append(f"  \033[32m✅ Robot actif\033[0m — PID {pid}")
    else:
        lines.append(f"  \033[31m❌ Robot MORT\033[0m — PID {pid or 'N/A'}")

    if ftmo:
        dd = ftmo.get("dd_from_peak", "?")
        wr = ftmo.get("win_rate", "?")
        trades = ftmo.get("total_trades", "?")
        profit = ftmo.get("profit_progress", "?")
        balance = ftmo.get("balance", "?")
        equity = ftmo.get("equity", "?")
        cons = ftmo.get("consecutive_losses", "?")
        days = ftmo.get("trading_days", "?")
        remaining = ftmo.get("days_remaining", "?")
        status = ftmo.get("status", "?")

        lines.append(f"  \033[1mChallenge\033[0m : {status}")
        lines.append(f"  Balance   : ${balance:,.2f}  Equity: ${equity:,.2f}")
        lines.append(f"  Profit    : {profit}  DD: {dd}")
        lines.append(f"  Trades    : {trades}  WR: {wr}  Cons: {cons}")
        lines.append(f"  Jours     : {days}/{remaining}")

    hb = read_json("runtime/performance_history.json")
    if hb:
        daily = hb.get("daily", {})
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if today in daily:
            td = daily[today]
            lines.append(f"  Aujourd'hui: {td.get('trades', 0)} trades, "
                         f"PnL=${td.get('pnl', 0):+.2f}, "
                         f"Wins={td.get('wins', 0)} Losses={td.get('losses', 0)}")

    logs = read_tail("logs/simple_robot.log", 3)
    if logs:
        lines.append(f"  \033[90mDerniers logs:\033[0m")
        for l in logs:
            lines.append(f"    {l[:120]}")

    # Check for circuit breaker
    try:
        with open("logs/simple_robot.log", "r", encoding="utf-8", errors="replace") as f:
            last_cb = None
            for line in f:
                if "CIRCUIT BREAKER" in line and "suspendu" in line:
                    last_cb = line
            if last_cb:
                lines.append(f"  \033[33m⚠️ Circuit breaker actif\033[0m")
    except Exception:
        pass

    return "\n".join(lines)


def main():
    print("\033[2J\033[H")  # clear screen
    count = 0
    while True:
        output = print_status()
        print(output, end="\n\n")
        count += 1
        try:
            time.sleep(30)
            print("\033[2J\033[H")  # clear screen
        except KeyboardInterrupt:
            print("\n\033[32mMonitoring arrêté\033[0m")
            break


if __name__ == "__main__":
    main()
