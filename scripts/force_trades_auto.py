#!/usr/bin/env python3
"""
Script pour forcer l'exécution automatique de 9 trades immédiatement
SANS CONFIRMATION - Exécution directe
"""

import sys
import os
import time
from datetime import datetime
import json

# Ajouter le répertoire parent au path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
sys.path.append('scripts')

try:
    from live_trading_engine import LiveTradingEngine
    import MetaTrader5 as mt5
except ImportError as e:
    print(f"❌ Erreur import: {e}")
    sys.exit(1)


class ForceTradeEngine(LiveTradingEngine):
    """Moteur modifié pour forcer l'exécution de trades"""

    def __init__(self):
        super().__init__(
            symbols=["EURUSD", "XAUUSD", "BTCUSD"],
            lot_sizes={"EURUSD": 0.01, "XAUUSD": 0.01, "BTCUSD": 0.01},
            max_risk_per_trade=0.02,
        )
        self.forced_trades_count = 0
        self.target_trades = 9

    def force_market_open(self, symbol):
        """Force tous les marchés à être considérés comme ouverts"""
        return True

    def is_market_open(self, symbol):
        """Override pour forcer tous les marchés ouverts"""
        return True

    def risk_check(self, action, signals):
        """Check de risque allégé pour permettre les trades forcés"""
        if self.forced_trades_count >= self.target_trades:
            return False
        return True

    def execute_trade_force(self, action, symbol, lot_size=0.01):
        """Exécution forcée avec gestion d'erreurs améliorée"""
        if not mt5.initialize():
            self.logger.error("Impossible d'initialiser MT5")
            return False

        try:
            # Obtenir le prix actuel
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.logger.error(f"Impossible d'obtenir le tick pour {symbol}")
                return False

            # Prix et type d'ordre
            if action == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid

            # Configuration simplifiée sans SL/TP pour éviter "Invalid stops"
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "deviation": 50,  # Déviation plus large
                "magic": 234000,
                "comment": f"FORCE_TRADE_{self.forced_trades_count+1}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            # Essayer l'ordre
            result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                # Si échec, essayer avec type de remplissage différent
                request["type_filling"] = mt5.ORDER_FILLING_FOK
                result = mt5.order_send(request)

                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    # Dernière tentative - retour aux paramètres basiques
                    request["type_filling"] = mt5.ORDER_FILLING_RETURN
                    request["deviation"] = 100
                    result = mt5.order_send(request)

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                # Trade réussi
                trade_info = {
                    "timestamp": datetime.now(),
                    "symbol": symbol,
                    "action": action,
                    "volume": lot_size,
                    "price": price,
                    "order_id": result.order,
                    "forced": True,
                }

                self.trade_history.append(trade_info)
                self.forced_trades_count += 1
                self.performance_metrics["total_trades"] += 1

                msg = f"✅ TRADE FORCÉ #{self.forced_trades_count}: "
                msg += f"{action.upper()} {symbol} {lot_size} lots à {price}"
                self.logger.info(msg)
                return True
            else:
                error_msg = (
                    f"❌ Échec trade forcé {symbol}: "
                    f"{result.comment} (Code: {result.retcode})"
                )
                self.logger.error(error_msg)
                return False

        except Exception as e:
            self.logger.error(f"❌ Erreur trade forcé {symbol}: {e}")
            return False

    def force_nine_trades(self):
        """Forcer l'exécution de 9 trades"""
        print("🚀 DÉBUT FORCE DE 9 TRADES")
        print("="*50)

        # Connexion MT5
        if not self.connect_mt5():
            print("❌ Impossible de se connecter à MT5")
            return False

        trades_executed = 0
        trades_simulated = 0
        symbols_cycle = ["EURUSD", "XAUUSD", "BTCUSD"]

        for i in range(9):
            symbol = symbols_cycle[i % len(symbols_cycle)]
            action = "buy" if i % 2 == 0 else "sell"

            print(f"\n🎯 Trade #{i+1}/9 - {symbol} {action.upper()}")

            # Récupérer les données (même simulées)
            try:
                _ = self.get_live_data(symbol, 50)

                # Essayer trade réel d'abord
                success = self.execute_trade_force(action, symbol, 0.01)

                if success:
                    trades_executed += 1
                    print(f"✅ Trade #{i+1} exécuté avec succès (RÉEL)")
                else:
                    # Si échec, simuler le trade
                    sim_success = self.simulate_trade(action, symbol, 0.01)
                    if sim_success:
                        trades_simulated += 1
                        print(f"✅ Trade #{i+1} simulé avec succès")
                    else:
                        print(f"❌ Échec trade #{i+1}")

            except Exception as e:
                print(f"❌ Erreur trade #{i+1}: {e}")

            # Petit délai entre les trades
            time.sleep(1)

        # Résumé
            print("\n" + "="*50)
            print("📊 RÉSUMÉ FORCE TRADES:")
            print("  🎯 Trades demandés: 9")
            print(f"  ✅ Trades réels: {trades_executed}")
            print(f"  🔄 Trades simulés: {trades_simulated}")
            print(f"  📈 Total exécutés: {trades_executed + trades_simulated}")
            print(f"  🎯 Taux de réussite: {(trades_executed + trades_simulated)/9*100:.1f}%")

        # Sauvegarder les résultats
        self.save_forced_trades_session()

        return (trades_executed + trades_simulated) > 0

    def save_forced_trades_session(self):
        """Sauvegarder la session de trades forcés"""
        try:
            os.makedirs("../artifacts/forced_trades", exist_ok=True)

            session_data = {
                "timestamp": datetime.now().isoformat(),
                "type": "forced_trades",
                "target_trades": 9,
                "executed_trades": self.forced_trades_count,
                "total_trades_in_history": len(self.trade_history),
                "trade_history": [
                    {
                        "timestamp": trade["timestamp"].isoformat(),
                        "symbol": trade.get("symbol", "N/A"),
                        "action": trade["action"],
                        "volume": trade["volume"],
                        "price": trade.get("price", 0),
                        "order_id": trade["order_id"],
                        "forced": trade.get("forced", False),
                        "simulated": trade.get("simulated", False),
                    }
                    for trade in self.trade_history
                ],
                "performance_metrics": self.performance_metrics,
            }

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"../artifacts/forced_trades/forced_session_{timestamp}.json"

            with open(filename, "w") as f:
                json.dump(session_data, f, indent=2, default=str)

            print(f"💾 Session trades forcés sauvegardée: {filename}")

        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")


def main():
    """Lancer les 9 trades forcés AUTOMATIQUEMENT"""
    print("🚀 FORCE AUTO DE 9 TRADES - PROPFIRM")
    print("="*40)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        # Créer le moteur forcé
        print("⚡ Initialisation du moteur de force trades...")
        engine = ForceTradeEngine()

        # Exécution automatique sans confirmation
        print("⚠️  MODE FORCE AUTOMATIQUE ACTIVÉ")
        print("📊 9 trades vont être exécutés IMMÉDIATEMENT")
        print("💰 Paramètres: 0.01 lot par trade")
        print("🔄 Alternance BUY/SELL sur EURUSD, XAUUSD, BTCUSD")
        print()
        print("🎯 EXÉCUTION EN COURS...")
        print("-"*40)

        # Exécuter les 9 trades automatiquement
        success = engine.force_nine_trades()

        if success:
            print("\n✅ Force trades terminée avec succès")
        else:
            print("\n❌ Échec de la force trades")

    except KeyboardInterrupt:
        print("\n⏹️ Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
