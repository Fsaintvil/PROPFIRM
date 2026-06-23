#!/usr/bin/env python3
"""Surveillance active du robot — cycles de 30s"""

import psutil, os, json, time, sys
from datetime import datetime

RUNTIME = r"C:\Users\saint\Documents\MT5_FTMO_IA.7"


def check():
    issues = []
    robot = None

    # 1. Robot process
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.cmdline())
            if "main.py" in cmd:
                mem = p.memory_info().rss / 1024 / 1024
                if mem > 50:
                    robot = {"pid": p.pid, "mem": mem}
        except:
            pass

    if not robot:
        return "🔴 ROBOT MORT", True

    # 2. PID lock
    pid_file = os.path.join(RUNTIME, "runtime", "robot.pid")
    lock_ok = False
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            lock_pid = f.read().strip()
        lock_ok = str(robot["pid"]) == lock_pid
    if not lock_ok:
        issues.append("PIDlock")

    # 3. MT5
    try:
        import MetaTrader5 as mt5

        if mt5.initialize(timeout=3000):
            pos = mt5.positions_get() or []
            pnl = sum(p.profit for p in pos)
            bal = mt5.account_info().balance
            eq = mt5.account_info().equity
            dd = max(0, bal - eq)
            dd_pct = dd / bal * 100 if bal > 0 else 0
            if dd_pct > 8:
                issues.append(f"DD={dd_pct:.1f}% 🔴")
            elif dd_pct > 1:
                issues.append(f"DD={dd_pct:.2f}%")
            mt5.shutdown()
        else:
            issues.append("MT5 KO")
            bal, eq, pnl, dd_pct = 0, 0, 0, 0
    except:
        issues.append("MT5 err")
        bal, eq, pnl, dd_pct = 0, 0, 0, 0

    # 4. Log errors
    log_file = os.path.join(RUNTIME, "logs", "simple_robot.log")
    if os.path.exists(log_file):
        with open(log_file) as f:
            lines = f.readlines()
        errors = [l for l in lines[-150:] if "ERROR" in l or "CRITICAL" in l]
        if errors:
            issues.append(f"Err={len(errors)}")

    status = "✅" if not issues else f"⚠️ {', '.join(issues)}"
    return f"{status} PID={robot['pid']} Bal=${bal:.2f} Eq=${eq:.2f} PnL=${pnl:+.2f} Mem={robot['mem']:.0f}MB", False


# Main loop
cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 10
interval = int(sys.argv[2]) if len(sys.argv) > 2 else 30

print(f"Surveillance: {cycles} cycles x {interval}s")
print(f"Demarrage: {datetime.now().strftime('%H:%M:%S')}")
print("-" * 55)

for c in range(1, cycles + 1):
    ts = datetime.now().strftime("%H:%M:%S")
    report, critical = check()
    print(f"[{ts}] C{c:2d}/{cycles} | {report}")
    if critical:
        print(">>> ALERTE CRITIQUE - Arret surveillance")
        sys.exit(1)
    if c < cycles:
        time.sleep(interval)

print("-" * 55)
print(f"Fin: {datetime.now().strftime('%H:%M:%S')} — Robot stable ✅")
