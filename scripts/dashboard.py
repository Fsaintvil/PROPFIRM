#!/usr/bin/env python3
"""Dashboard temps réel — lit MT5 + runtime/ pour suivi FTMO"""
import json
import os
import sqlite3
import time
from collections import deque
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(__file__))
RUNTIME = os.path.join(BASE, "runtime")
REPORT = os.path.join(RUNTIME, "ftmo_report.json")
STATE = os.path.join(RUNTIME, "robot_state.json")
HEARTBEAT = os.path.join(RUNTIME, "heartbeat.txt")
DB = os.path.join(RUNTIME, "trading_journal.db")

C = {"RESET":"\033[0m","RED":"\033[91m","GREEN":"\033[92m","YELLOW":"\033[93m",
     "CYAN":"\033[96m","BOLD":"\033[1m","DIM":"\033[2m","REV":"\033[7m"}

def color_pnl(v):
    s = f"{v:+.2f}"
    if v > 0:
        return f"{C['GREEN']}{s}{C['RESET']}"
    if v < 0:
        return f"{C['RED']}{s}{C['RESET']}"
    return f"{C['DIM']}{s}{C['RESET']}"

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def get_heartbeat():
    try:
        with open(HEARTBEAT) as f:
            dt = datetime.fromisoformat(f.read().strip())
            return dt, (datetime.now() - dt).total_seconds()
    except Exception:
        return None, None

def get_recent_trades(n=10):
    try:
        conn = sqlite3.connect(DB)
        rows = conn.execute(
            "SELECT symbol, direction, profit, time_close FROM trades "
            "WHERE time_close != '' ORDER BY time_close DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_symbol_stats():
    try:
        conn = sqlite3.connect(DB)
        rows = conn.execute(
            "SELECT symbol, COUNT(*), SUM(profit), "
            "SUM(CASE WHEN profit>0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN profit>0 THEN profit ELSE 0 END), "
            "SUM(CASE WHEN profit<0 THEN profit ELSE 0 END) "
            "FROM trades GROUP BY symbol ORDER BY SUM(profit) DESC"
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

TRADING_DAYS = set()

def dashboard():
    try:
        import MetaTrader5 as mt5
        mt5.initialize()
    except Exception as e:
        print(f"{C['RED']}MT5 init failed: {e}{C['RESET']}")
        return

    hist = deque(maxlen=20)
    last_refresh = 0

    while True:
        try:
            now = time.time()
            os.system("cls" if os.name == "nt" else "clear")

            acc = mt5.account_info()
            if acc is None:
                print(f"{C['RED']}MT5 non connecte{C['RESET']}")
                time.sleep(10)
                continue

            balance, equity = acc.balance, acc.equity
            fl_pnl = equity - balance

            positions = mt5.positions_get() or []
            our_pos = [p for p in positions]
            total_profit = sum(p.profit for p in our_pos)
            margin_used = 0
            for p in our_pos:
                try:
                    m = mt5.order_calc_margin(
                        mt5.ORDER_TYPE_BUY if p.type == 0 else mt5.ORDER_TYPE_SELL,
                        p.symbol, p.volume, p.price_open
                    )
                    if m:
                        margin_used += m
                except Exception:
                    pass
            pending_orders = mt5.orders_get() or []

            report = read_json(REPORT)
            state = read_json(STATE)
            hb_dt, hb_age = get_heartbeat()
            init_bal = (state or {}).get("challenge_initial_balance", balance)
            peak_eq = max(init_bal, (state or {}).get("peak_equity", equity))

            dd_peak = (peak_eq - equity) / max(peak_eq, 1)

            if now - last_refresh > 30:
                recent = get_recent_trades(10)
                for t in recent:
                    hist.append(t)
                last_refresh = now

            sym_stats = get_symbol_stats()

            # ── Display ──
            print(f"{C['BOLD']}{C['CYAN']}{'='*80}{C['RESET']}")
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"{C['BOLD']}{C['CYAN']}  FTMO DASHBOARD — {ts}{C['RESET']}")
            print(f"{C['BOLD']}{C['CYAN']}{'='*80}{C['RESET']}")

            # Compte
            print(f"\n{C['BOLD']}COMPTE{C['RESET']}")
            print(f"  Balance={C['BOLD']}${balance:,.2f}{C['RESET']}  "
                  f"Equity={C['BOLD']}${equity:,.2f}{C['RESET']}  "
                  f"Flottant={color_pnl(fl_pnl)}")
            print(f"  Peak=${peak_eq:,.0f}  DD={C['RED'] if dd_peak>0.03 else ''}{dd_peak:.2%}{C['RESET']}  "
                  f"Init=${init_bal:,.0f}  Margin=${margin_used:.0f}")

            # FTMO
            print(f"\n{C['BOLD']}CHALLENGE{C['RESET']}")
            if report:
                cons_ok = not report.get("consistency_violated", False)
                cons_s = f"{C['GREEN']}OK{C['RESET']}" if cons_ok else f"{C['RED']}VIOLATED{C['RESET']}"
                print(f"  Statut={report['status']}  Consistance={cons_s}")
                prog = report.get("profit_progress", "0%")
                prog_color = C["GREEN"] if prog.startswith("+") else (C["RED"] if prog.startswith("-") else "")
                print(f"  Progress={prog_color}{prog}{C['RESET']}  "
                      f"Target={report.get('profit_remaining','?')}  "
                      f"Jours={report.get('trading_days',0)}")
                print(f"  DD Init={report.get('dd_from_initial','0%')}  "
                      f"DD Peak={report.get('dd_from_peak','0%')}  "
                      f"Daily={report.get('daily_pnl','$0')}  "
                      f"Pertes cons={report.get('consecutive_losses',0)}")

            if hb_dt:
                hb_c = C["RED"] if (hb_age or 999) > 180 else C["DIM"]
                print(f"  Heartbeat: {hb_c}{hb_dt.strftime('%H:%M:%S')} (age={hb_age:.0f}s){C['RESET']}")

            # Pending orders
            pending_str = ""
            if pending_orders:
                sym_pending = {}
                for o in pending_orders:
                    sym_pending[o.symbol] = sym_pending.get(o.symbol, 0) + 1
                pending_parts = [f"{s}x{c}" for s, c in sorted(sym_pending.items())]
                pending_str = f" + {sum(sym_pending.values())} pending: {' '.join(pending_parts)}"

            # Positions ouvertes
            print(f"\n{C['BOLD']}POSITIONS ({len(our_pos)}){pending_str}{C['RESET']}")
            if our_pos:
                print(f"  {'Ticket':>8} {'Symbole':>10} {'Dir':>4} {'Vol':>5} {'Profit':>10} "
                      f"{'SL':>12} {'TP':>12} {'Entry':>11}")
                print(f"  {'-'*72}")
                for p in sorted(our_pos, key=lambda x: abs(x.profit), reverse=True):
                    dire = f"{C['GREEN']}BUY{C['RESET']}" if p.type == 0 else f"{C['RED']}SELL{C['RESET']}"
                    # Check if SL is trailed (SL distance < 25% of TP distance)
                    has_trail = (
                        " T" if p.sl and p.tp
                        and abs(p.sl - p.price_open) < abs(p.tp - p.price_open) * 0.25
                        else "  "
                    )
                    # SL distance in pips/points
                    abs(p.sl - p.price_open) if p.sl else 0
                    print(f"  {p.ticket:>8} {p.symbol:>10} {dire:>4} {p.volume:>5.2f} "
                          f"{color_pnl(p.profit):>10} "
                          f"{p.sl or 0:>12.5f} {p.tp or 0:>12.5f} {p.price_open:>11.5f}{has_trail}")
                print(f"  {'-'*72}")
                print(f"  {' ':>33}Total={color_pnl(total_profit)}  Margin=${margin_used:.0f}")
            else:
                print(f"  {C['DIM']}Aucune position{C['RESET']}")

            # Per-symbol stats
            if sym_stats:
                print(f"\n{C['BOLD']}STATS PAR SYMBOLE (journal){C['RESET']}")
                print(f"  {'Symbole':>10} {'Trades':>7} {'PnL':>10} {'WR':>5} {'PF':>6}")
                print(f"  {'-'*40}")
                for s in sym_stats:
                    sym, n, pnl, w, wp, lp = s
                    wr = w/max(n,1)*100
                    pf = wp/max(abs(lp),0.01)
                    pnl_c = color_pnl(pnl)
                    wr_c = C["GREEN"] if wr >= 50 else (C["RED"] if wr < 30 else "")
                    pf_c = C["GREEN"] if pf >= 1.3 else (C["RED"] if pf < 0.8 else "")
                    print(f"  {sym:>10} {n:>7} {pnl_c:>10} {wr_c}{wr:>4.0f}%{C['RESET']} {pf_c}{pf:>5.1f}x{C['RESET']}")

            # Derniers trades fermes
            recent_list = list(hist)[-10:]
            if recent_list:
                print(f"\n{C['BOLD']}DERNIERS TRADES FERMES{C['RESET']}")
                for t in recent_list:
                    sym, dire, prof, ct = t if len(t)==4 else (*t, "")
                    ct_s = str(ct)[-8:] if ct else "..."
                    print(f"  {ct_s} {sym:>8} {dire:>4} {color_pnl(prof)}")

            # PID
            try:
                with open(os.path.join(RUNTIME, "robot.pid")) as f:
                    print(f"\n{C['DIM']}PID: {f.read().strip()}{C['RESET']}")
            except Exception:
                pass

            print(f"{C['REV']} Ctrl+C pour quitter  (rafraichit toutes les 10s) {C['RESET']}")
            time.sleep(10)

        except KeyboardInterrupt:
            mt5.shutdown()
            print(f"\n{C['YELLOW']}Dashboard arrete{C['RESET']}")
            break
        except Exception as e:
            print(f"\n{C['RED']}Erreur: {e}{C['RESET']}")
            time.sleep(10)

if __name__ == "__main__":
    dashboard()
