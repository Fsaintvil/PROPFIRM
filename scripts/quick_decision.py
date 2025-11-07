#!/usr/bin/env python3
"""
⚡ QUICK DECISION HELPER
Assistant rapide pour prendre des décisions de trading éclairées

UTILISATION:
python scripts/quick_decision.py [SYMBOLE]

EXEMPLES:
python scripts/quick_decision.py EURUSD
python scripts/quick_decision.py --all
python scripts/quick_decision.py --monitor
    """

import sys
import json
from pathlib import Path
from datetime import datetime
import warnings

warnings.filterwarnings("ignore")

# Import avec fallback robuste
try:
    sys.path.append(str(Path(__file__).parent))
    from smart_trading_signals import SmartTradingSignals
    SIGNALS_OK = True
except ImportError:
    try:
        from scripts.smart_trading_signals import SmartTradingSignals
        SIGNALS_OK = True
    except ImportError:
        SIGNALS_OK = False


class QuickDecisionHelper:
    """Assistant rapide pour décisions de trading"""

    def __init__(self):
        self.signals_engine = None

        if SIGNALS_OK:
            try:
                self.signals_engine = SmartTradingSignals()
                print("✅ Moteur de signaux connecté")
            except Exception as e:
                print(f"⚠️ Moteur signaux: {e}")

        # Charger configuration si disponible
        self.config = self.load_decision_config()

        print("⚡ Quick Decision Helper prêt")

    def load_decision_config(self):
        """Charger configuration de décision"""
        config_file = Path("config/trading_decision.json")

        if config_file.exists():
            with open(config_file) as f:
                return json.load(f)
        else:
            # Configuration par défaut
            return {
                "confidence_thresholds": {
                    "execute_min": 0.50,
                        "consider_min": 0.60,
                            "warning_max": 0.50
                },
                    "risk_reward": {"minimum": 1.5}
            }

    def analyze_symbol(self, symbol: str):
        """Analyser un symbole spécifique rapidement"""
        print(f"\n⚡ ANALYSE RAPIDE - {symbol}")
        print("=" * 40)

        if not self.signals_engine:
            print("❌ Moteur de signaux non disponible")
            return None

        try:
            # Obtenir données
            data = self.signals_engine.get_market_data(symbol)
            if data is None:
                print("❌ Données non disponibles")
                return None

            # Analyser signal
            signal = self.signals_engine.analyze_signal(symbol, data)

            # Afficher résultat
            self.display_quick_analysis(signal)

            return signal

        except Exception as e:
            print(f"❌ Erreur analyse: {e}")
            return None

    def display_quick_analysis(self, signal):
        """Afficher analyse rapide et claire"""
        # Header avec symbole
        print(f"📊 {signal['symbol']} - {datetime.now().strftime('%H:%M:%S')}")
        print("-" * 40)

        # Action principale avec icône
        action_icons = {
            "buy": "📈",
                "sell": "📉",
                    "hold": "⏸️"
        }

        action_icon = action_icons.get(signal['action'], "❓")
        print(f"{action_icon} ACTION: {signal['action'].upper()}")

        # Score et confiance
        confidence = signal['confidence']
        if confidence >= 0.7:
            conf_color = "🟢"
        elif confidence >= 0.5:
            conf_color = "🟡"
        else:
            conf_color = "🔴"
        print(f"{conf_color} CONFIANCE: {confidence:.2f}")

        # Risk/Reward
        rr = signal['risk_reward']
        rr_color = "🟢" if rr >= 2.0 else "🟡" if rr >= 1.5 else "🔴"
        print(f"{rr_color} RISK/REWARD: {rr:.1f}")

        # Prix et niveaux
        print(f"💰 PRIX: {signal['entry_price']:.5f}")
        if signal['action'] != 'hold':
            print(f"🛡️ STOP LOSS: {signal['stop_loss']:.5f}")
            print(f"🎯 TAKE PROFIT: {signal['take_profit']:.5f}")

        # Recommandation finale
        rec = signal['recommendation']
        print(f"\n{rec}")

        # Conseils personnalisés
        self.give_personalized_advice(signal)

    def give_personalized_advice(self, signal):
        """Donner conseils personnalisés"""
        print("\n💡 CONSEIL:")

        conf = signal['confidence']
        rr = signal['risk_reward']
        action = signal['action']

        if action == "hold":
            print("⏳ Attendre une meilleure opportunité")
        elif conf >= 0.75 and rr >= 2.0:
            print("🚀 EXCELLENT signal - Considérer l'exécution")
        elif conf >= 0.50 and rr >= 1.5:
            print("✅ BON signal - Exécution recommandée")
        elif conf >= 0.60:
            print("🤔 Signal modéré - Surveiller de près")
        else:
            print("⚠️ Signal faible - Éviter ou attendre")

        # Conseils sur le timing
        if action in ["buy", "sell"]:
            print("\n⏰ TIMING:")
            if conf > 0.7:
                print("   🟢 Entrée immédiate possible")
            else:
                print("   🟡 Attendre confirmation supplémentaire")

    def quick_scan_all(self):
        """Scan rapide de tous les symboles"""
        print("\n⚡ SCAN RAPIDE MULTI-ACTIFS")
        print("=" * 50)

        if not self.signals_engine:
            print("❌ Moteur de signaux non disponible")
            return

        symbols = self.signals_engine.config["symbols"]
        results = []

        for symbol in symbols:
            print(f"\n📊 {symbol}...")
            signal = self.analyze_symbol(symbol)
            if signal:
                results.append(signal)

        # Résumé des opportunités
        self.display_opportunities_summary(results)

    def display_opportunities_summary(self, signals):
        """Afficher résumé des opportunités"""
        if not signals:
            print("\n❌ Aucun signal disponible")
            return

        print("\n🎯 RÉSUMÉ DES OPPORTUNITÉS")
        print("=" * 50)

        # Trier par score final
        sorted_signals = sorted(signals,
                                key=lambda x: x['final_score'],
                                reverse=True)

        execute_signals = []
        consider_signals = []

        for signal in sorted_signals:
            if "EXECUTE" in signal['recommendation']:
                execute_signals.append(signal)
            elif "CONSIDER" in signal['recommendation']:
                consider_signals.append(signal)

        # Afficher par priorité
        if execute_signals:
            print(f"\n🚀 À EXÉCUTER ({len(execute_signals)}):")
            for signal in execute_signals:
                conf = signal['confidence']
                rr = signal['risk_reward']
                print(f"  📈 {signal['symbol']}: {signal['action'].upper()} "
                      f"(conf={conf:.2f}, RR={rr:.1f})")

        if consider_signals:
            print(f"\n🤔 À CONSIDÉRER ({len(consider_signals)}):")
            for signal in consider_signals:
                conf = signal['confidence']
                rr = signal['risk_reward']
                print(f"  📊 {signal['symbol']}: {signal['action'].upper()} "
                      f"(conf={conf:.2f}, RR={rr:.1f})")

        # Meilleure opportunité
        if sorted_signals:
            best = sorted_signals[0]
            print("\n🏆 MEILLEURE OPPORTUNITÉ:")
            print(f"   💎 {best['symbol']} - {best['action'].upper()}")
            print(f"   📊 Score: {best['final_score']:.3f}")
            print(f"   💰 Prix: {best['entry_price']:.5f}")

    def monitor_mode(self):
        """Mode monitoring simple"""
        print("\n📡 MODE MONITORING ACTIF")
        print("🔄 Refresh toutes les 2 minutes")
        print("⏹️ Ctrl+C pour arrêter\n")

        import time

        try:
            while True:
                # Clear screen
                print("\033[2J\033[H")

                # Header avec timing
                timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"📡 MONITORING ACTIF - {timestamp}")
                print("=" * 60)

                # Scan rapide
                self.quick_scan_all()

                # Attendre
                print("\n⏳ Prochain scan dans 120 secondes...")
                time.sleep(120)

        except KeyboardInterrupt:
            print("\n👋 Monitoring arrêté")

    def show_help(self):
        """Afficher aide"""
        print("\n⚡ QUICK DECISION HELPER - AIDE")
        print("=" * 40)
        print("\n📋 COMMANDES DISPONIBLES:")
        print("  python scripts/quick_decision.py EURUSD     "
              "# Analyser EURUSD")
        print("  python scripts/quick_decision.py --all      # Scanner tous")
        print("  python scripts/quick_decision.py --monitor  "
              "# Mode monitoring")
        print("  python scripts/quick_decision.py --help     # Cette aide")

        print("\n🎯 INTERPRÉTATION DES SIGNAUX:")
        print("  🟢 Confiance ≥ 0.7  : Signal fort")
        print("  🟡 Confiance ≥ 0.5  : Signal moyen")
        print("  🔴 Confiance < 0.5  : Signal faible")

        print("\n⚖️ RISK/REWARD:")
        print("  🟢 R/R ≥ 2.0  : Excellent")
        print("  🟡 R/R ≥ 1.5  : Acceptable")
        print("  🔴 R/R < 1.5  : Risqué")

        print("\n📊 RECOMMANDATIONS:")
        print("  🟢 EXECUTE   : Exécuter le trade")
        print("  🟡 CONSIDER  : Considérer avec prudence")
        print("  🟠 WAIT      : Attendre meilleure opportunité")
        print("  🔴 AVOID     : Éviter le trade")


def main():
    """Point d'entrée principal"""
    helper = QuickDecisionHelper()

    if len(sys.argv) == 1:
        # Pas d'arguments - montrer aide
        helper.show_help()
        return

    arg = sys.argv[1].upper()

    if arg == "--HELP":
        helper.show_help()
    elif arg == "--ALL":
        helper.quick_scan_all()
    elif arg == "--MONITOR":
        helper.monitor_mode()
    elif arg in ["EURUSD", "XAUUSD", "BTCUSD"]:
        helper.analyze_symbol(arg)
    else:
        print(f"❌ Symbole non reconnu: {arg}")
        print("💡 Symboles supportés: EURUSD, XAUUSD, BTCUSD")
        print("💡 Utilisez --help pour voir toutes les options")


if __name__ == "__main__":
    main()
