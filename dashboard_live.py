#!/usr/bin/env python3
"""
DASHBOARD LIVE - Monitoring en temps réel du robot MT5 FTMO
Met à jour toutes les 30s avec l'état complet du robot
"""
import contextlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime


def clear_screen():
    """Efface l'écran"""
    os.system('cls' if os.name == 'nt' else 'clear')

def format_time_ago(timestamp_str):
    """Formate la durée depuis le timestamp"""
    try:
        ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        duration = datetime.now(ts.tzinfo) - ts
        minutes = int(duration.total_seconds() / 60)
        seconds = int(duration.total_seconds() % 60)
        if minutes < 1:
            return f"{seconds}s"
        elif minutes < 60:
            return f"{minutes}m {seconds}s"
        else:
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}h {mins}m"
    except Exception:
        return "???"

def load_json_safe(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def load_csv_lines(path):
    try:
        with open(path) as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    except Exception:
        return []

def get_symbol_performance():
    """Analyse performances par symbole"""
    runtime_dir = "runtime"
    trades_log = load_csv_lines(os.path.join(runtime_dir, "trades_log.csv"))

    symbol_stats = defaultdict(lambda: {
        'wins': 0, 'losses': 0, 'pnl': 0, 'latest': None
    })

    for line in trades_log[1:]:  # Skip header
        parts = line.split(',')
        if len(parts) >= 12:
            try:
                symbol = parts[0].strip()
                pnl = float(parts[9].strip())
                reason = parts[11].strip()
                timestamp = parts[0]

                if 'WIN' in reason:
                    symbol_stats[symbol]['wins'] += 1
                elif 'LOSS' in reason:
                    symbol_stats[symbol]['losses'] += 1

                symbol_stats[symbol]['pnl'] += pnl
                symbol_stats[symbol]['latest'] = timestamp
            except Exception:
                pass

    return sorted(symbol_stats.items(),
                  key=lambda x: x[1]['pnl'],
                  reverse=True)

def get_open_positions():
    """Récupère les positions ouvertes"""
    runtime_dir = "runtime"
    trading_journal = load_csv_lines(os.path.join(runtime_dir, "trading_journal.csv"))

    open_pos = []
    for line in trading_journal[1:]:  # Skip header
        parts = line.split(',')
        if len(parts) >= 10:
            time_close = parts[8].strip() if len(parts) > 8 else ''
            if not time_close:
                with contextlib.suppress(Exception):
                    open_pos.append({
                        'symbol': parts[0].strip(),
                        'direction': parts[1].strip(),
                        'entry': float(parts[2].strip()),
                        'sl': float(parts[3].strip()) if parts[3].strip() else 0,
                        'tp': float(parts[4].strip()) if parts[4].strip() else 0,
                        'lot': float(parts[5].strip()),
                        'reason': parts[9].strip() if len(parts) > 9 else '?',
                        'time': parts[7].strip() if len(parts) > 7 else '?'
                    })

    return open_pos

def dashboard():
    """Affiche le dashboard principal"""
    runtime_dir = "runtime"

    # Load all data
    robot_state = load_json_safe(os.path.join(runtime_dir, "robot_state.json"))
    ftmo_report = load_json_safe(os.path.join(runtime_dir, "ftmo_report.json"))
    last_signals = load_json_safe(os.path.join(runtime_dir, "last_signals.json"))

    with open(os.path.join(runtime_dir, "heartbeat.txt")) as f:
        heartbeat = f.read().strip()
    with open(os.path.join(runtime_dir, "robot.pid")) as f:
        pid = f.read().strip()

    trades_log = load_csv_lines(os.path.join(runtime_dir, "trades_log.csv"))
    total_trades = len(trades_log) - 1
    wins = sum(1 for line in trades_log[1:] if 'WIN' in line)

    symbol_perf = get_symbol_performance()
    open_positions = get_open_positions()

    # AFFICHAGE
    clear_screen()

    # === HEADER ===
    print("┌" + "─" * 118 + "┐")
    ts = datetime.now().strftime('%d/%m %H:%M:%S')
    print("│" + f"  ROBOT MT5 FTMO - LIVE DASHBOARD  │  {ts}" .ljust(118) + "│")
    print("└" + "─" * 118 + "┘")

    # === STATUS BAR ===
    hb_age = format_time_ago(heartbeat)
    status_icon = "🟢" if "s" in hb_age or ("1m" in hb_age) else "🟡" if "2m" in hb_age or "3m" in hb_age else "🔴"
    status_text = "ACTIF" if "🟢" in status_icon else "LENT" if "🟡" in status_icon else "INACTIF"

    print(f"\n  {status_icon} STATUS: {status_text:8} │ PID: {pid:6} "
          f"│ Heartbeat: {hb_age:10} │ Cycle: {last_signals.get('cycle', '?'):3}")

    # === BALANCE ===
    balance = ftmo_report.get('balance', 0)
    equity = ftmo_report.get('equity', 0)
    pnl = ftmo_report.get('pnl', 0)
    pnl_color = "🟢" if pnl >= 0 else "🔴"

    initial = robot_state.get('challenge_initial_balance', 0)
    pnl_pct = (pnl / initial * 100) if initial > 0 else 0

    print("\n  💰 FINANCES:")
    print(f"     Balance: ${balance:,.2f} │ Equity: ${equity:,.2f} │ {pnl_color} PnL: {pnl:+,.2f} ({pnl_pct:+.2f}%)")

    # === PERFORMANCE ===
    print("\n  📊 PERFORMANCE:")
    dd = ftmo_report.get('dd_from_peak', '?')
    profit_prog = ftmo_report.get('profit_progress', '?')
    trading_days = ftmo_report.get('trading_days', 0)
    wr_val = wins / total_trades * 100 if total_trades > 0 else 0
    print(f"     Trades: {total_trades:3} │ WR: {wr_val:5.1f}% ({wins:2} W) "
          f"│ DD: {dd:6} │ Progress: {profit_prog:8} │ Days: {trading_days}/10")

    # === POSITIONS OUVERTES ===
    print(f"\n  📍 POSITIONS OUVERTES: {len(open_positions)} / 14")
    if len(open_positions) > 0:
        print(f"     {'Symbol':<10} {'Dir':<6} {'Entry':<12} {'SL':<12} {'TP':<12} {'Lot':<6} {'Raison':<15}")
        print(f"     {'-'*97}")
        for pos in open_positions[:8]:  # Top 8
            entry_str = f"{pos['entry']:.5g}"[:12]
            sl_str = f"{pos['sl']:.5g}"[:12]
            tp_str = f"{pos['tp']:.5g}"[:12]
            print(f"     {pos['symbol']:<10} {pos['direction']:<6} "
                  f"{entry_str:<12} {sl_str:<12} {tp_str:<12} "
                  f"{pos['lot']:<6.2f} {pos['reason']:<15}")
        if len(open_positions) > 8:
            print(f"     ... et {len(open_positions) - 8} autres positions")
    else:
        print("     ✅ Aucune position ouverte")

    # === TOP 5 SYMBOLES ===
    print("\n  🏆 TOP SYMBOLES (derniers 2 jours):")
    print(f"     {'Symbol':<10} {'Trades':<8} {'WR':<8} {'PnL':<12}")
    print(f"     {'-'*38}")
    for symbol, stats in symbol_perf[:5]:
        total = stats['wins'] + stats['losses']
        wr = (stats['wins'] / total * 100) if total > 0 else 0
        wr_icon = "🟢" if wr > 60 else "🟡" if wr > 50 else "🔴"
        pnl_icon = "📈" if stats['pnl'] > 0 else "📉"
        print(f"     {symbol:<10} {total:<8} {wr_icon} {wr:5.1f}% {pnl_icon} ${stats['pnl']:+8.2f}")

    # === SIGNAUX ACTUELS ===
    signals = last_signals.get('signals', [])
    print(f"\n  🎯 SIGNAUX (Cycle {last_signals.get('cycle', '?')}):")
    if signals:
        for sig in signals:
            icon = "🟢" if sig['action'] == 'BUY' else "🔴"
            print(f"     {icon} {sig['symbol']:<10} {sig['action']:<6} "
                  f"│ Score: {sig['score']:.2f} │ Conf: {sig['confidence']:.2f} "
                  f"│ ADX: {sig['adx']:.1f}")
    else:
        print("     ⏳ Aucun signal en attente")

    # === FOOTER ===
    print("\n  ⚠️  ALERTES:")
    alerts = []
    if len(open_positions) > 14:
        alerts.append(f"  🔴 POSITIONS ZOMBIES: {len(open_positions)} > 14 limit")
    if ftmo_report.get('total_trades', 0) == 0 and total_trades > 0:
        alerts.append(f"  🔴 SYNCHRO MT5: 0 trades reportés (mais {total_trades} en cache)")
    if pnl < -0.02 * initial:  # Moins de -2%
        alerts.append(f"  🟠 DRAWDOWN ÉLEVÉ: {pnl_pct:.2f}%")
    if hb_age not in ["s", "1m"] and "1m" not in hb_age:
        alerts.append(f"  🟡 ROBOT LENT: Heartbeat {hb_age}")

    if alerts:
        for alert in alerts:
            print(f"  {alert}")
    else:
        print("  ✅ Aucune alerte")

    print("\n  💡 LOGS: À chaque cycle (15s), le robot exécute:")
    print("     1) Scan positions 2) Calcul signaux 3) Détection régime 4) Prédiction ML")
    print("     5) Meta-Learner 6) Filtres FTMO 7) Exécution trades")

    print("\n  📖 Lisez RAPPORT_ANALYSE_EN_LIVE_27MAI.md pour l'analyse complète")
    print("\n┌" + "─" * 118 + "┐")
    print(f"│ {'Appuyez sur Ctrl+C pour arrêter. Mise à jour automatique toutes les 30s...'.center(118)} │")
    print("└" + "─" * 118 + "┘\n")

def watch_loop():
    """Boucle de monitoring"""
    try:
        while True:
            dashboard()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n✅ Dashboard arrêté.\n")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Erreur: {e}")
        time.sleep(5)
        watch_loop()

if __name__ == "__main__":
    watch_loop()
