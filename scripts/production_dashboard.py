#!/usr/bin/env python3
"""
📊 TABLEAU DE BORD PRODUCTION - TRADING IA
Surveillance continue et statistiques en temps réel
"""

import time
import os
from datetime import datetime
import psutil


class ProductionDashboard:
    """Tableau de bord de production pour le trading IA"""

    def __init__(self):
        self.running = False
        self.stats = {
            'start_time': None,
            'total_trades': 0,
            'successful_trades': 0,
            'failed_trades': 0,
            'total_profit': 0.0,
            'last_update': None
        }

    def is_trading_active(self):
        """Vérifier si le processus de trading est actif"""
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info['cmdline'] or [])
                    engine_in_cmd = 'live_trading_engine.py' in cmdline
                    python_in_name = 'python' in proc.info['name']
                    if engine_in_cmd and python_in_name:
                        return True, proc.info['pid']
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False, None
        except Exception:
            return False, None

    def get_balance_info(self):
        """Récupérer les informations de balance depuis les logs"""
        try:
            logs_dir = r"c:\Users\saint\Documents\PROPFIRM\logs"
            log_files = [
                f for f in os.listdir(logs_dir)
                if f.startswith('live_trading_20251020')
            ]

            if not log_files:
                return None

            latest_log = os.path.join(logs_dir, sorted(log_files)[-1])

            with open(latest_log, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Chercher la dernière ligne avec Balance/Equity
            for line in reversed(lines):
                if 'Balance:' in line and 'Equity:' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == 'Balance:':
                            balance = parts[i + 1]
                        elif part == 'Equity:':
                            equity = parts[i + 1]
                    return {'balance': balance, 'equity': equity}

            return None

        except Exception:
            return None

    def count_todays_trades(self):
        """Compter les trades d'aujourd'hui"""
        try:
            trades_file = r"c:\Users\saint\Documents\PROPFIRM\logs\trades.json"
            if not os.path.exists(trades_file):
                return 0

            today = datetime.now().strftime('%Y-%m-%d')

            with open(trades_file, 'r') as f:
                lines = f.readlines()

            count = 0
            for line in lines:
                if line.strip() and today in line:
                    count += 1

            return count

        except Exception:
            return 0

    def get_recent_activity(self):
        """Obtenir l'activité récente"""
        try:
            logs_dir = r"c:\Users\saint\Documents\PROPFIRM\logs"
            log_files = [
                f for f in os.listdir(logs_dir)
                if f.startswith('live_trading_20251020')
            ]

            if not log_files:
                return []

            latest_log = os.path.join(logs_dir, sorted(log_files)[-1])

            with open(latest_log, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Filtrer les lignes importantes
            important_lines = []
            keywords = ['Trade', 'TRADE', 'Ordre', 'Cycle', 'ERROR', 'Échec']

            for line in reversed(lines[-50:]):  # 50 dernières lignes
                if any(keyword in line for keyword in keywords):
                    timestamp = line.split(' - ')[0] if ' - ' in line else ''
                    if ' - ' in line:
                        content = line.split(' - ')[-1].strip()
                    else:
                        content = line.strip()

                    if len(content) > 60:
                        content = content[:57] + "..."

                    important_lines.append({
                        'timestamp': timestamp,
                        'content': content
                    })

                if len(important_lines) >= 10:
                    break

            return important_lines

        except Exception:
            return []

    def display_dashboard(self):
        """Afficher le tableau de bord"""
        os.system('cls' if os.name == 'nt' else 'clear')

        current_time = datetime.now()

        print("🚀 " + "=" * 70)
        print("           TABLEAU DE BORD PRODUCTION - TRADING IA")
        print("=" * 74)
        print("🕐 {} | Session Continue".format(
            current_time.strftime('%Y-%m-%d %H:%M:%S')
        ))
        print()

        # Statut système
        is_active, pid = self.is_trading_active()
        status_icon = "🟢" if is_active else "🔴"
        status_text = "PRODUCTION ACTIVE" if is_active else "SYSTÈME ARRÊTÉ"

        print(f"📊 STATUT: {status_icon} {status_text}")
        if is_active:
            print(f"🔧 PID Process: {pid}")
        print()

        # Configuration
        if is_active:
            print("⚙️  CONFIGURATION PRODUCTION:")
            print("   📈 Instruments: EURUSD, XAUUSD, BTCUSD")
            # Utiliser TradingConfig si disponible
            try:
                from config.trading_config import TradingConfig as _TC
                _interval = getattr(_TC, 'TRADING_INTERVAL_SECONDS', 600)
            except Exception:
                _interval = 600
            print(f"   ⏱️  Cycle: {_interval} secondes")
            print("   🎯 Seuil: 68% de confiance minimum")
            print("   💰 Taille: 0.01 lot par trade")
            print("   🛡️  Risque: Maximum 2% par position")
            print()

        # Informations financières
        balance_info = self.get_balance_info()
        if balance_info:
            print("💰 FINANCES:")
            print(f"   💵 Balance: ${balance_info['balance']}")
            print(f"   💎 Equity: ${balance_info['equity']}")
            print()

        # Statistiques du jour
        todays_trades = self.count_todays_trades()
        print("📈 STATISTIQUES AUJOURD'HUI:")
        print(f"   🎯 Trades exécutés: {todays_trades}")

        # Calcul temps écoulé depuis démarrage
        if self.stats['start_time']:
            elapsed = current_time - self.stats['start_time']
            hours = int(elapsed.total_seconds() // 3600)
            minutes = int((elapsed.total_seconds() % 3600) // 60)
            print("   ⏱️  Temps actif: {}h {}m".format(hours, minutes))
        print()

        # Activité récente
        print("📋 ACTIVITÉ RÉCENTE:")
        recent_activity = self.get_recent_activity()

        if recent_activity:
            for activity in recent_activity[: 8]:  # 8 dernières activités
                timestamp = activity['timestamp']
                if len(timestamp) > 8:
                    time_part = timestamp[-8:]
                else:
                    time_part = timestamp
                content = activity['content']

                # Icône selon le type d'activité
                if 'Trade' in content or 'Ordre' in content:
                    icon = "💰"
                elif 'ERROR' in content or 'Échec' in content:
                    icon = "❌"
                elif 'Cycle' in content:
                    icon = "🔄"
                else:
                    icon = "📝"

                print(f"   {icon} {time_part} | {content}")
        else:
            print("   ⏸️ Aucune activité récente détectée")

        print()
        print("=" * 74)
        if is_active:
            print("✅ Système en marche | 🔄 Actualisation: 15s | "
                  "Ctrl+C: Arrêter")
        else:
            print("⚠️ Système arrêté | 🔄 Surveillance: 15s | Ctrl+C: Quitter")

    def run(self):
        """Démarrer la surveillance"""
        self.running = True
        self.stats['start_time'] = datetime.now()

        print("🚀 Démarrage du tableau de bord production...")
        print("📊 Surveillance continue du système de trading IA")
        time.sleep(2)

        try:
            while self.running:
                self.display_dashboard()
                time.sleep(15)  # Actualisation toutes les 15 secondes

        except KeyboardInterrupt:
            print("\n\n🛑 Tableau de bord arrêté")
            print("✅ Surveillance terminée")
            self.running = False

    def stop(self):
        """Arrêter la surveillance"""
        self.running = False


def main():
    """Point d'entrée principal"""
    dashboard = ProductionDashboard()

    try:
        dashboard.run()
    except Exception as e:
        print(f"\n❌ Erreur dashboard: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
