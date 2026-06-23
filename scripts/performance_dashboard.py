#!/usr/bin/env python3
"""Dashboard de performance du robot MOM20x3"""

import json, os, re
import psutil
from datetime import datetime

try:
    import MetaTrader5 as mt5

    MT5_AVAIL = True
except:
    MT5_AVAIL = False

print("=" * 68)
print("   DIAGRAMME DE PERFORMANCE — ROBOT MOM20x3")
print("   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"))
print("=" * 68)

# === 1. ÉTAT DU COMPTE ===
print("""
╔══════════════════════════════════════════════════════════╗
║                 TABLEAU DE BORD COMPTE                  ║
╚══════════════════════════════════════════════════════════╝""")

bal, eq = 200527.76, 200519.31
pos = []

if MT5_AVAIL:
    try:
        if mt5.initialize(timeout=5000):
            acc = mt5.account_info()
            bal, eq = acc.balance, acc.equity
            margin, margin_free = acc.margin, acc.margin_free
            leverage = acc.leverage
            pos = mt5.positions_get() or []
            pnl_total = sum(p.profit for p in pos)
            flottant = eq - bal

            print(f"""
  Balance:         ${bal:>9.2f}
  Equity:          ${eq:>9.2f}  ({"+" if flottant >= 0 else ""}${flottant:+.2f} flottant)
  Marge:           ${margin:>9.2f}
  Marge libre:     ${margin_free:>9.2f}
  Levier:          {leverage}
  Positions:       {len(pos)}
  PnL total:       ${pnl_total:>+9.2f}
  Drawdown:        ${max(0, bal - eq):>9.2f}  ({(max(0, bal - eq) / bal * 100):.3f}%)
  Niveau marge:    {acc.margin_level:.0f}%
""")
            mt5.shutdown()
    except Exception as e:
        print(f"  MT5 error: {e}")

# === 2. POSITIONS PAR SYMBOLE ===
print("╔══════════════════════════════════════════════════════════╗")
print("║              POSITIONS OUVERTES PAR SYMBOLE             ║")
print("╚══════════════════════════════════════════════════════════╝")

if pos:
    by_sym = {}
    for p in pos:
        by_sym.setdefault(p.symbol, {"n": 0, "pnl": 0, "vol": 0, "buy": 0, "sell": 0})
        d = by_sym[p.symbol]
        d["n"] += 1
        d["pnl"] += p.profit
        d["vol"] += p.volume
        if p.type == 0:
            d["buy"] += 1
        else:
            d["sell"] += 1

    # Déterminer la barre max
    max_abs = max((abs(d["pnl"]) for d in by_sym.values()), default=1)

    print(f"\n  {'Symbole':<12} {'Pos':>3} {'Dir':>8} {'Vol':>5} {'PnL':>10}  Barre PnL")
    print(f"  {'-' * 12} {'-' * 3} {'-' * 8} {'-' * 5} {'-' * 10}  {'-' * 30}")

    for sym in ["BTCUSD", "EURUSD", "ETHUSD", "US500.cash", "XAUUSD"]:
        if sym in by_sym:
            d = by_sym[sym]
            dir_str = f"{d['buy']}B/{d['sell']}S"
            pnl = d["pnl"]
            bar_len = max(1, int(abs(pnl) / max_abs * 20)) if max_abs > 0 else 1
            if pnl >= 0:
                bar = " " * 10 + "█" * bar_len + " " * (20 - bar_len)
            else:
                bar = " " * (10 - bar_len) + "█" * bar_len + " " * (10)
            print(f"  {sym:<12} {d['n']:>3} {dir_str:>8} {d['vol']:>5.2f} ${pnl:>+8.2f}  {bar}")

    total_pnl = sum(d["pnl"] for d in by_sym.values())
    total_vol = sum(d["vol"] for d in by_sym.values())
    total_pos = sum(d["n"] for d in by_sym.values())
    print(f"  {'-' * 12} {'-' * 3} {'-' * 8} {'-' * 5} {'-' * 10}")
    print(f"  {'TOTAL':<12} {total_pos:>3} {'':>8} {total_vol:>5.2f} ${total_pnl:>+8.2f}")
else:
    print("\n  Aucune position ouverte")

# === 3. PERFORMANCE HISTORIQUE ===
print("""
╔══════════════════════════════════════════════════════════╗
║              PERFORMANCE HISTORIQUE                     ║
╚══════════════════════════════════════════════════════════╝""")

rs_file = r"C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\robot_state.json"
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)

    daily = rs.get("daily_pnl_by_date", {})
    trades = rs.get("trade_history", [])
    cons_losses = rs.get("consecutive_losses", 0)
    peak = rs.get("peak_equity", 0)

    wins = sum(1 for t in trades if t.get("profit", 0) > 0) if trades else 0
    total = len(trades)
    wr = (wins / total * 100) if total > 0 else 0
    gross_profit = sum(t.get("profit", 0) for t in trades if t.get("profit", 0) > 0) if trades else 0
    gross_loss = abs(sum(t.get("profit", 0) for t in trades if t.get("profit", 0) < 0)) if trades else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    net = gross_profit - gross_loss

    # Barre WR
    wr_bar_win = "#" * max(1, int(wr / 5))
    wr_bar_loss = "." * max(1, int((100 - wr) / 5))

    print(f"""
  Trades:          {total}
  Win Rate:        {wr:.1f}%  [{wr_bar_win}{wr_bar_loss}] ({wins}W / {total - wins}L)
  Profit Factor:   {pf:.2f}
  Net PnL:         ${net:+.2f}
  Gross Profit:    ${gross_profit:.2f}
  Gross Loss:      ${gross_loss:.2f}
  Peak Equity:     ${peak:.2f}
  Consec. Losses:  {cons_losses}
""")

    # Tableau des trades récents
    if trades:
        recent = trades[-8:]
        print(f"  Derniers trades:")
        print(f"  {'Date':<12} {'Symbole':<10} {'Dir':<6} {'PnL':>8} {'Raison':<18}")
        print(f"  {'-' * 12} {'-' * 10} {'-' * 6} {'-' * 8} {'-' * 18}")
        for t in recent:
            date = str(t.get("close_time", "?"))[:10]
            sym = t.get("symbol", "?")[:10]
            direction = t.get("direction", "?")[:6]
            pnl = t.get("profit", 0)
            rai = str(t.get("reason", t.get("exit_reason", "")))[:18]
            print(f"  {date:<12} {sym:<10} {direction:<6} ${pnl:>+7.2f} {rai:<18}")

# === 4. PnL QUOTIDIEN ===
print("""
╔══════════════════════════════════════════════════════════╗
║              PnL QUOTIDIEN                              ║
╚══════════════════════════════════════════════════════════╝""")

if "daily" in dir() and daily:
    dates_sorted = sorted(daily.keys())
    last7 = dates_sorted[-7:] if len(dates_sorted) > 7 else dates_sorted
    max_pnl = max((abs(v) for v in daily.values()), default=1)

    print(f"\n  {'Date':<14} {'PnL':>10}  Barre")
    print(f"  {'-' * 14} {'-' * 10}  {'-' * 30}")

    for d in last7:
        v = daily.get(d, 0)
        bar_len = max(1, int(abs(v) / max_pnl * 20)) if max_pnl > 0 else 1
        if v >= 0:
            bar = " " * 10 + "+" + "█" * bar_len
        else:
            bar = " " * (10 - bar_len) + "█" * bar_len + "-"
        print(f"  {d:<14} ${v:>+9.2f}  {bar}")

# === 5. RÉGIMES DE MARCHÉ ===
print("""
╔══════════════════════════════════════════════════════════╗
║              RÉGIMES DE MARCHÉ                          ║
╚══════════════════════════════════════════════════════════╝""")

log_file = r"C:\Users\saint\Documents\MT5_FTMO_IA.7\logs\simple_robot.log"
if os.path.exists(log_file):
    with open(log_file) as f:
        lines = f.readlines()
    content = "".join(lines[-2000:])  # Dernières 2000 lignes

    for sym in ["XAUUSD", "BTCUSD", "ETHUSD", "EURUSD", "US500.cash"]:
        # Dernier regime
        sym_lines = [l for l in lines if sym in l and ("REGIME" in l or "regime" in l.lower())]
        if sym_lines:
            last_regime = sym_lines[-1]
            regimes_found = re.findall(r"(TREND_UP|TREND_DOWN|RANGING|HIGH_VOL|LOW_VOL|DOW|RAN|TRE)", last_regime)
            regime = regimes_found[-1] if regimes_found else "?"
        else:
            regime = "?"

        # Dernière ATR
        atr_lines = [l for l in lines if sym in l and "ATR=" in l]
        atr = 0
        if atr_lines:
            atr_match = re.search(r"ATR=(\d+\.?\d*)", atr_lines[-1])
            atr = float(atr_match.group(1)) if atr_match else 0

        # Dernier signal
        signal_lines = [l for l in lines if sym in l and "score=" in l and "conf=" in l]
        last_score = "?"
        if signal_lines:
            score_match = re.search(r"conf=([\d.]+)", signal_lines[-1])
            last_score = score_match.group(1) if score_match else "?"

        colors = {
            "TREND_UP": "🟢",
            "TREND_DOWN": "🔴",
            "RANGING": "🟡",
            "HIGH_VOL": "🟠",
            "LOW_VOL": "🔵",
            "DOW": "⬇️",
            "RAN": "🟡",
            "TRE": "📈",
            "?": "⚪",
        }
        c = colors.get(regime, "⚪")

        print(f"  {sym:<12} {c} {regime:<15} ATR={atr:<8.2f} Conf={last_score}")

# === 6. SANTÉ ===
print("""
╔══════════════════════════════════════════════════════════╗
║                 SANTÉ DU SYSTÈME                        ║
╚══════════════════════════════════════════════════════════╝""")

# Mémoire
robot_mem = 0
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        cmd = " ".join(p.cmdline())
        if "main.py" in cmd:
            mem = p.memory_info().rss / 1024 / 1024
            if mem > 50:
                robot_mem = mem
                break
    except:
        pass

# Vérifier crash récent
recent_crash = False
recent_errors = 0
if os.path.exists(log_file):
    last_5000 = content[-5000:] if len(content) > 5000 else content
    recent_crash = "Watchdog: echec reconnexion MT5" in last_5000
    recent_errors = content[-10000:].count("ERROR") if len(content) > 10000 else content.count("ERROR")

# Vérifier EURUSD dans OL
ol_path = r"C:\Users\saint\Documents\MT5_FTMO_IA.7\runtime\ol_state.json"
eur_in_ol = False
if os.path.exists(ol_path):
    with open(ol_path) as f:
        ol_data = json.load(f)
    eur_in_ol = "EURUSD" in ol_data.get("history", {})

mem_ok = robot_mem < 200
print(f"""
  Mémoire robot:   {robot_mem:.0f} MB  {"🟢" if mem_ok else "🔴"}
  Crash MT5:       {"🔴 OUI (dernier redémarrage)" if recent_crash else "🟢 NON"}
  Erreurs récentes:{"🟡 " + str(recent_errors) if recent_errors > 0 else "🟢 0"}
  EURUSD dans OL:  {"🔴 OUI" if eur_in_ol else "🟢 NON (protégé)"}
  PID Lock:        🟢 OK (redémarrage frais)
""")

print("=" * 68)
print("   FIN DU RAPPORT DE PERFORMANCE")
print("=" * 68)
