#!/usr/bin/env python3
"""
Monitoring en temps réel du robot MT5 FTMO
Analyse complète : signaux, positions, performances, régimes
"""
import json
import os
import sys
import time
from datetime import datetime


def load_json_safe(filepath):
    """Charge un JSON de manière sûre"""
    try:
        if not os.path.exists(filepath):
            return {}
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Erreur lecture {filepath}: {e}")
        return {}

def load_csv_safe(filepath):
    """Charge un CSV de manière simple"""
    try:
        if not os.path.exists(filepath):
            return []
        with open(filepath) as f:
            lines = f.readlines()
        return [line.strip() for line in lines if line.strip()]
    except Exception as e:
        print(f"❌ Erreur lecture {filepath}: {e}")
        return []

def format_duration(timestamp_str):
    """Calcule la durée depuis le timestamp"""
    try:
        ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        duration = datetime.now(ts.tzinfo) - ts
        minutes = int(duration.total_seconds() / 60)
        if minutes < 1:
            return "< 1 min"
        elif minutes < 60:
            return f"{minutes}m"
        else:
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours}h {mins}m"
    except Exception:
        return "???"

def analyze_robot_state():
    """Analyse complète de l'état du robot"""
    runtime_dir = "runtime"

    print("\n" + "="*80)
    print("🤖 MONITORING ROBOT MT5 FTMO - EN LIVE 🤖".center(80))
    print("="*80)

    # 1. HEARTBEAT ET PROCESSUS
    print("\n📡 STATUT DU ROBOT:")
    heartbeat_file = os.path.join(runtime_dir, "heartbeat.txt")
    if os.path.exists(heartbeat_file):
        with open(heartbeat_file) as f:
            last_heartbeat = f.read().strip()
        duration = format_duration(last_heartbeat)
        status_icon = "✅" if "5m" not in duration and "10m" not in duration else "⚠️"
        print(f"  {status_icon} Dernier heartbeat: {last_heartbeat} ({duration})")

    pid_file = os.path.join(runtime_dir, "robot.pid")
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            pid = f.read().strip()
        print(f"  🔄 PID processus: {pid}")

    # 2. ÉTAT FTMO
    print("\n💰 RAPPORT FTMO:")
    ftmo_data = load_json_safe(os.path.join(runtime_dir, "ftmo_report.json"))
    if ftmo_data:
        print(f"  💵 Balance: ${ftmo_data.get('balance', 0):,.2f}")
        print(f"  📊 Equity: ${ftmo_data.get('equity', 0):,.2f}")
        print(f"  📈 PnL: ${ftmo_data.get('pnl', 0):+,.2f}")
        print(f"  🎯 Status: {ftmo_data.get('status', '?')}")
        print(f"  📉 DD depuis peak: {ftmo_data.get('dd_from_peak', '?')}")
        print(f"  📊 Win Rate: {ftmo_data.get('win_rate', '0%')}")
        print(f"  🏆 Profit Progress: {ftmo_data.get('profit_progress', '?')}")
        print(f"  ⏰ Trading Days: {ftmo_data.get('trading_days', 0)}/{ftmo_data.get('days_remaining', 10)}")
        print(f"  📊 Total Trades: {ftmo_data.get('total_trades', 0)}")

    # 3. ÉTAT INTERNE
    print("\n🧠 ÉTAT INTERNE DU ROBOT:")
    robot_state = load_json_safe(os.path.join(runtime_dir, "robot_state.json"))
    if robot_state:
        print(f"  💎 Peak Equity: ${robot_state.get('peak_equity', 0):,.2f}")
        print(f"  ❌ Pertes consécutives: {robot_state.get('consecutive_losses', 0)}")
        print(f"  🔄 Restarts: {robot_state.get('restart_count', 0)}")
        print(f"  📍 Initial Balance: ${robot_state.get('challenge_initial_balance', 0):,.2f}")

    # 4. SIGNAUX ACTUELS
    print("\n🎯 SIGNAUX GÉNÉRÉS (derniers):")
    signals_data = load_json_safe(os.path.join(runtime_dir, "last_signals.json"))
    if signals_data:
        print(f"  🔁 Cycle: {signals_data.get('cycle', '?')}")
        signals = signals_data.get('signals', [])
        if signals:
            for sig in signals:
                symbol = sig.get('symbol', '?')
                action = sig.get('action', '?')
                score = sig.get('score', 0)
                confidence = sig.get('confidence', 0)
                adx = sig.get('adx', 0)
                details = sig.get('details', '')
                icon = "🟢" if action == "BUY" else "🔴"
                print(f"    {icon} {symbol}: {action} | Score: {score:.2f} "
                      f"| Conf: {confidence:.2f} | ADX: {adx:.1f} | {details}")
        else:
            print("    ℹ️  Aucun signal en attente")

    # 5. POSITIONS OUVERTES (trading_journal.csv)
    print("\n📍 POSITIONS OUVERTES:")
    tj_lines = load_csv_safe(os.path.join(runtime_dir, "trading_journal.csv"))
    if len(tj_lines) > 1:
        # Parse header
        tj_lines[0].split(',')
        open_positions = []
        for line in tj_lines[1:]:
            cols = line.split(',')
            if len(cols) >= 9:
                # Chercher les positions sans time_close
                time_close = cols[8] if len(cols) > 8 else ''
                if not time_close or time_close == '':
                    open_positions.append({
                        'symbol': cols[0],
                        'direction': cols[1],
                        'entry': cols[2],
                        'sl': cols[3],
                        'tp': cols[4],
                        'lot': cols[5],
                        'reason': cols[9] if len(cols) > 9 else '?'
                    })

        if open_positions:
            print(f"  📌 Total: {len(open_positions)} position(s) ouverte(s)")
            for pos in open_positions[:10]:  # Show first 10
                print(f"    \u2022 {pos['symbol']} {pos['direction']} L={pos['lot']} @ {pos['entry']} "
                      f"| SL={pos['sl']} | TP={pos['tp']} | Raison: {pos['reason']}")
        else:
            print("  ℹ️  Aucune position ouverte")

    # 6. DERNIERS TRADES FERMÉS
    print("\n📊 DERNIERS TRADES FERMÉS:")
    tl_lines = load_csv_safe(os.path.join(runtime_dir, "trades_log.csv"))
    if len(tl_lines) > 1:
        recent_trades = tl_lines[-10:][::-1]  # Last 10, reversed (most recent first)
        for line in recent_trades[:5]:
            try:
                parts = line.split(',')
                if len(parts) >= 11:
                    symbol = parts[0]
                    direction = parts[1]
                    pnl = float(parts[9]) if parts[9] else 0
                    reason = parts[11]
                    duration = parts[12] if len(parts) > 12 else "?"
                    icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "⚪"
                    print(f"    {icon} {symbol} {direction} | PnL: ${pnl:+,.2f} | {reason} | {duration}h")
            except Exception:
                pass

    # 7. STATISTIQUES
    print("\n📈 STATISTIQUES:")
    if len(tl_lines) > 1:
        try:
            total_trades = len(tl_lines) - 1
            wins = sum(1 for line in tl_lines[1:] if 'WIN' in line)
            losses = sum(1 for line in tl_lines[1:] if 'LOSS' in line)
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            total_pnl = 0
            for line in tl_lines[1:]:
                try:
                    pnl = float(line.split(',')[9])
                    total_pnl += pnl
                except Exception:
                    pass

            print(f"  🎯 Total Trades: {total_trades}")
            print(f"  ✅ Wins: {wins} ({win_rate:.1f}%)")
            print(f"  ❌ Losses: {losses}")
            print(f"  💰 Total PnL: ${total_pnl:+,.2f}")
            print(f"  📊 Avg PnL/Trade: ${(total_pnl/total_trades):+,.2f}" if total_trades > 0 else "")
        except Exception as e:
            print(f"  ⚠️  Erreur calcul stats: {e}")

    # 8. NEWS CACHE
    print("\n📰 CACHE NEWS:")
    news_cache = load_json_safe(os.path.join(runtime_dir, "news_cache.json"))
    if news_cache:
        cache_time = news_cache.get('timestamp', '?')
        duration = format_duration(cache_time) if cache_time != '?' else "?"
        count = len(news_cache.get('events', []))
        print(f"  📅 Dernier cache: {cache_time} ({duration})")
        print(f"  📍 Events chargés: {count}")

    # 9. WATCHDOG
    print("\n👀 WATCHDOG SNAPSHOT:")
    watchdog = load_json_safe(os.path.join(runtime_dir, "watchdog_snapshot.json"))
    if watchdog:
        print(f"  ⏰ Timestamp: {watchdog.get('timestamp', '?')}")
        print(f"  🔄 Cycles exécutés: {watchdog.get('cycle_count', '?')}")
        print(f"  ⚠️  Erreurs: {watchdog.get('error_count', 0)}")
        print(f"  📊 Positions: {watchdog.get('position_count', '?')}")

    print("\n" + "="*80)
    print(f"⏰ Analyse mise à jour: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80 + "\n")

def watch_mode():
    """Mode monitoring continu"""
    print("\n🔄 Mode WATCH activé (mise à jour toutes les 30s)")
    print("   Appuyez sur Ctrl+C pour arrêter\n")

    try:
        while True:
            # Clear screen
            os.system('cls' if os.name == 'nt' else 'clear')
            analyze_robot_state()
            print("   ⏳ Prochaine mise à jour dans 30s... (Ctrl+C pour arrêter)")
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n✅ Monitoring arrêté")

if __name__ == "__main__":
    if "--watch" in sys.argv or "-w" in sys.argv:
        watch_mode()
    else:
        analyze_robot_state()
