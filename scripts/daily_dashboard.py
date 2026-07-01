#!/usr/bin/env python3
"""Dashboard quotidien — Rapport de performance complet du robot MOM20x3

Usage:
    python scripts/daily_dashboard.py                # rapport console
    python scripts/daily_dashboard.py --html          # rapport HTML
    python scripts/daily_dashboard.py --watch         # mode monitoring (rafraîchissement)
"""

import csv
import json
import os
import sys
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOG = BASE / "logs" / "simple_robot.log"
TRADES_LOG = BASE / "runtime" / "trades_log.csv"
FTMO_REPORT = BASE / "runtime" / "ftmo_report.json"
PID_FILE = BASE / "runtime" / "robot.pid"
ADAPTIVE_DIR = BASE / "runtime"


def get_ftmo_report():
    try:
        with open(FTMO_REPORT) as f:
            return json.load(f)
    except:
        return {}


def get_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except:
        return None


def get_trades():
    trades = []
    if not TRADES_LOG.exists():
        return trades
    with open(TRADES_LOG) as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 12 and row[11] == "closed":
                try:
                    trades.append(
                        {
                            "ts": row[0],
                            "symbol": row[1],
                            "action": row[2],
                            "lot": float(row[3]),
                            "entry": float(row[4]) if row[4] else 0,
                            "exit": float(row[7]) if row[7] else 0,
                            "pnl": float(row[10]),
                        }
                    )
                except:
                    pass
    return trades


def get_positions_from_log():
    if not LOG.exists():
        return {}
    with open(LOG) as f:
        for line in reversed(f.readlines()):
            if "Par symbole" in line:
                try:
                    import ast

                    dict_str = line.split("Par symbole: ")[1].strip()
                    return ast.literal_eval(dict_str)
                except:
                    return {}
    return {}


def print_report(html=False):
    ftmo = get_ftmo_report()
    trades = get_trades()
    pid = get_pid()
    positions = get_positions_from_log()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] < 0)
    total = len(trades)
    wr = wins / total * 100 if total > 0 else 0

    # Per symbol
    per_sym = defaultdict(list)
    for t in trades:
        per_sym[t["symbol"]].append(t)

    # Today's trades
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t["ts"].startswith(today_str)]
    today_pnl = sum(t["pnl"] for t in today_trades)
    today_wins = sum(1 for t in today_trades if t["pnl"] > 0)
    today_losses = sum(1 for t in today_trades if t["pnl"] < 0)

    # Direction distribution
    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]

    # Last 10 trades
    last_trades = trades[-10:]

    if html:
        print_html(
            ftmo,
            trades,
            per_sym,
            positions,
            pid,
            now,
            total_pnl,
            wins,
            losses,
            wr,
            today_trades,
            today_pnl,
            today_wins,
            today_losses,
            buys,
            sells,
            last_trades,
        )
    else:
        print_console(
            ftmo,
            trades,
            per_sym,
            positions,
            pid,
            now,
            total_pnl,
            wins,
            losses,
            wr,
            today_trades,
            today_pnl,
            today_wins,
            today_losses,
            buys,
            sells,
            last_trades,
        )


def print_console(
    ftmo,
    trades,
    per_sym,
    positions,
    pid,
    now,
    total_pnl,
    wins,
    losses,
    wr,
    today_trades,
    today_pnl,
    today_wins,
    today_losses,
    buys,
    sells,
    last_trades,
):
    total = len(trades)

    print("=" * 62)
    print(f"  MT5 FTMO MOM20x3 — DASHBOARD")
    print(f"  {now}")
    print("=" * 62)

    # Status
    pid_alive = False
    try:
        import psutil

        pid_alive = pid and psutil.pid_exists(pid)
    except ImportError:
        # fallback: check if process with this PID exists
        if pid:
            try:
                import os as _os

                if _os.name == "nt":
                    pid_alive = True  # assume alive on Windows without psutil
                else:
                    pid_alive = _os.path.exists(f"/proc/{pid}")
            except:
                pass

    print(f"\n📡 ROBOT STATUS: {'🟢 ACTIF' if pid_alive else '🔴 ARRETE'} (PID {pid or 'N/A'})")

    # FTMO
    if ftmo:
        print(f"\n🏦 FTMO CHALLENGE $200K")
        print(f"   Balance:     ${ftmo.get('balance', 0):>10,.2f}")
        print(f"   Equity:      ${ftmo.get('equity', 0):>10,.2f}")
        print(f"   PnL:         ${ftmo.get('pnl', 0):>10,.2f}")
        print(f"   Drawdown:    {ftmo.get('dd_from_peak', '0%'):>10}")
        print(f"   Status:      {ftmo.get('status', '?'):>10}")
        print(f"   WR:          {ftmo.get('win_rate', '?'):>10}")
        print(f"   Trading Jrs: {ftmo.get('trading_days', 0):>10}")
        print(f"   Jours rest.: {ftmo.get('days_remaining', 0):>10}")

    # Global stats
    print(f"\n📊 PERFORMANCE GLOBALE")
    print(f"   Trades:      {total:>5}  ({wins}W / {losses}L)")
    print(f"   Win Rate:    {wr:>5.1f}%")
    print(f"   PnL Total:   ${total_pnl:>8,.2f}")
    print(f"   Ratio B/S:   {len(buys)}B / {len(sells)}S ({len(buys) / max(len(buys) + len(sells), 1) * 100:.0f}% BUY)")

    # Today
    print(f"\n📅 AUJOURD'HUI ({datetime.now(timezone.utc).strftime('%d %b')})")
    print(f"   Trades:      {len(today_trades):>3}  ({today_wins}W / {today_losses}L)")
    today_wr = today_wins / max(len(today_trades), 1) * 100
    print(f"   Win Rate:    {today_wr:>5.1f}%")
    print(f"   PnL:         ${today_pnl:>8,.2f}")
    today_buys = sum(1 for t in today_trades if t["action"] == "BUY")
    today_sells = sum(1 for t in today_trades if t["action"] == "SELL")
    print(f"   B/S:         {today_buys}B / {today_sells}S")

    # Positions
    if positions:
        print(f"\n💼 POSITIONS OUVERTES ({sum(positions.values())})")
        for sym, qty in sorted(positions.items(), key=lambda x: -x[1]):
            print(f"   {sym:<12} {qty}")

        # Groups
        groups = {
            "FOREX_MAJORS": ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF", "GBPJPY"],
            "INDICES": ["US500.cash", "US30.cash", "US100.cash", "JP225.cash"],
            "CRYPTO": ["BTCUSD", "ETHUSD"],
            "COMMODITIES": ["XAUUSD", "XAGUSD", "USOIL.cash"],
        }
        print()
        for grp, syms in groups.items():
            grp_pos = {s: positions.get(s, 0) for s in syms if s in positions}
            if grp_pos:
                print(f"   {grp:<16}: {sum(grp_pos.values()):>2} positions")

    # Per symbol PnL
    print(f"\n📈 PnL PAR SYMBOLE")
    print(f"   {'Symbole':<12} {'Trades':>6} {'WR':>6} {'PnL':>10} {'B/S':>8}")
    print(f"   {'-' * 42}")
    for sym in sorted(per_sym.keys()):
        s = per_sym[sym]
        sp = [t["pnl"] for t in s]
        sw = sum(1 for p in sp if p > 0)
        sl = sum(1 for p in sp if p < 0)
        b = sum(1 for t in s if t["action"] == "BUY")
        ss = sum(1 for t in s if t["action"] == "SELL")
        wr_sym = sw / len(s) * 100
        sign = "+" if sum(sp) >= 0 else ""
        print(f"   {sym:<12} {len(s):>6} {wr_sym:>5.1f}% {sign}${sum(sp):>8,.2f} {b}B/{ss}S")

    # Last trades
    if last_trades:
        print(f"\n🔄 DERNIERS TRADES FERMÉS")
        for t in reversed(last_trades[-5:]):
            emoji = "🟢" if t["pnl"] > 0 else "🔴"
            print(f"   {emoji} {t['ts'][11:19]} {t['symbol']:<10} {t['action']:<5} ${t['pnl']:>8,.2f}")

    print(f"\n{'=' * 62}")
    print(f"  Rapport généré le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'=' * 62}")


def print_html(
    ftmo,
    trades,
    per_sym,
    positions,
    pid,
    now,
    total_pnl,
    wins,
    losses,
    wr,
    today_trades,
    today_pnl,
    today_wins,
    today_losses,
    buys,
    sells,
    last_trades,
):
    total = len(trades)

    html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Dashboard MOM20x3 — {now}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; border-bottom: 2px solid #30363d; padding-bottom: 10px; }}
  h2 {{ color: #f0f6fc; margin-top: 30px; }}
  .status {{ padding: 15px; border-radius: 8px; margin: 10px 0; }}
  .ok {{ background: #161b22; border-left: 4px solid #3fb950; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; }}
  .card h3 {{ margin: 0 0 10px 0; color: #8b949e; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}
  .value {{ font-size: 24px; font-weight: bold; }}
  .positive {{ color: #3fb950; }}
  .negative {{ color: #f85149; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #30363d; }}
  th {{ color: #8b949e; font-size: 12px; text-transform: uppercase; }}
  tr:hover {{ background: #1c2128; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; }}
  .badge-buy {{ background: #1a3a1a; color: #3fb950; }}
  .badge-sell {{ background: #3a1a1a; color: #f85149; }}
  .footer {{ margin-top: 30px; color: #484f58; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<h1>📊 Dashboard MOM20x3 — FTMO $200K Challenge</h1>
<p style="color: #8b949e;">{now}</p>

<div class="status ok">
  <strong>🤖 Robot {"ACTIF" if pid else "ARRETÉ"}</strong> (PID {pid or "N/A"})
</div>

<div class="grid">
  <div class="card">
    <h3>Solde</h3>
    <div class="value">${ftmo.get("balance", 0):,.2f}</div>
  </div>
  <div class="card">
    <h3>Equity</h3>
    <div class="value">${ftmo.get("equity", 0):,.2f}</div>
  </div>
  <div class="card">
    <h3>PnL</h3>
    <div class="value {"positive" if ftmo.get("pnl", 0) >= 0 else "negative"}">${ftmo.get("pnl", 0):,.2f}</div>
  </div>
  <div class="card">
    <h3>Drawdown</h3>
    <div class="value">{ftmo.get("dd_from_peak", "0%")}</div>
  </div>
  <div class="card">
    <h3>Win Rate</h3>
    <div class="value">{ftmo.get("win_rate", "?")}</div>
  </div>
  <div class="card">
    <h3>Jours restants</h3>
    <div class="value">{ftmo.get("days_remaining", 0)}</div>
  </div>
</div>

<h2>📈 Performance Globale</h2>
<div class="grid">
  <div class="card">
    <h3>Trades</h3>
    <div class="value">{total}</div>
    <div style="color:#8b949e;font-size:14px;">{wins}W / {losses}L</div>
  </div>
  <div class="card">
    <h3>Win Rate</h3>
    <div class="value">{wr:.1f}%</div>
  </div>
  <div class="card">
    <h3>PnL Total</h3>
    <div class="value {"positive" if total_pnl >= 0 else "negative"}">${total_pnl:,.2f}</div>
  </div>
  <div class="card">
    <h3>Direction</h3>
    <div><span class="badge badge-buy">{len(buys)} BUY</span> <span class="badge badge-sell">{len(sells)} SELL</span></div>
  </div>
</div>

<h2>📅 Aujourd'hui</h2>
<div class="grid">
  <div class="card">
    <h3>Trades</h3>
    <div class="value">{len(today_trades)}</div>
    <div style="color:#8b949e;">{today_wins}W / {today_losses}L</div>
  </div>
  <div class="card">
    <h3>PnL</h3>
    <div class="value {"positive" if today_pnl >= 0 else "negative"}">${today_pnl:,.2f}</div>
  </div>
  <div class="card">
    <h3>B/S</h3>
    <div><span class="badge badge-buy">{sum(1 for t in today_trades if t["action"] == "BUY")} BUY</span> <span class="badge badge-sell">{sum(1 for t in today_trades if t["action"] == "SELL")} SELL</span></div>
  </div>
</div>

<h2>💼 Positions Ouvertes ({sum(positions.values())})</h2>
<table>
<tr><th>Symbole</th><th>Quantité</th></tr>
"""
    for sym, qty in sorted(positions.items(), key=lambda x: -x[1]):
        html_content += f"<tr><td>{sym}</td><td>{qty}</td></tr>\n"

    html_content += """</table>

<h2>📈 PnL par Symbole</h2>
<table>
<tr><th>Symbole</th><th>Trades</th><th>WR</th><th>PnL</th><th>Direction</th></tr>
"""
    for sym in sorted(per_sym.keys()):
        s = per_sym[sym]
        sp = [t["pnl"] for t in s]
        sw = sum(1 for p in sp if p > 0)
        wr_sym = sw / len(s) * 100
        b = sum(1 for t in s if t["action"] == "BUY")
        ss = sum(1 for t in s if t["action"] == "SELL")
        pnl_class = "positive" if sum(sp) >= 0 else "negative"
        html_content += f'<tr><td>{sym}</td><td>{len(s)}</td><td>{wr_sym:.1f}%</td><td class="{pnl_class}">${sum(sp):,.2f}</td><td><span class="badge badge-buy">{b}B</span> <span class="badge badge-sell">{ss}S</span></td></tr>\n'

    html_content += """</table>

<h2>🔄 Derniers Trades</h2>
<table>
<tr><th>Heure</th><th>Symbole</th><th>Action</th><th>PnL</th></tr>
"""
    for t in reversed(last_trades[-10:]):
        emoji = "🟢" if t["pnl"] > 0 else "🔴"
        pnl_class = "positive" if t["pnl"] >= 0 else "negative"
        badge = "badge-buy" if t["action"] == "BUY" else "badge-sell"
        html_content += f'<tr><td>{t["ts"][11:19]}</td><td>{t["symbol"]}</td><td><span class="badge {badge}">{t["action"]}</span></td><td class="{pnl_class}">{emoji} ${t["pnl"]:,.2f}</td></tr>\n'

    html_content += f"""
</table>

<div class="footer">
  Rapport généré le {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC
</div>
</body>
</html>"""

    out_path = BASE / "runtime" / "dashboard.html"
    with open(out_path, "w") as f:
        f.write(html_content)
    print(f"✅ Rapport HTML généré : {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Dashboard MOM20x3")
    parser.add_argument("--html", action="store_true", help="Générer un rapport HTML")
    parser.add_argument(
        "--watch", type=int, nargs="?", const=60, help="Mode monitoring (rafraîchissement toutes les N secondes)"
    )
    args = parser.parse_args()

    if args.watch:
        import time

        try:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                print_report(html=False)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nMonitoring arrêté.")
    else:
        print_report(html=args.html)


if __name__ == "__main__":
    main()
