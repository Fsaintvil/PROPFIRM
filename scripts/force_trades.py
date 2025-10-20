#!/usr/bin/env python3
"""
Script pour forcer l'exécution de 9 trades immédiatement
Contourne les limitations de marché et force les trades avec paramètres ajustés
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

    def get_ai_signals_forced(self, symbol_data, trade_num):
        """Générer des signaux forcés alternant BUY/SELL"""
        actions = ["buy", "sell"]
        action = actions[trade_num % 2]  # Alterner buy/sell

        return {
            "meta_learning": {"action": action, "confidence": 0.75},
            "regime_detection": {
                "action": "long_bias" if action == "buy" else "short_bias",
                "confidence": 0.8,
            },
            "combined_signal": action,
            "confidence": 0.85,  # Confiance forcée élevée
        }

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

            # Essayer d'abord sans SL/TP
            result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                # Si échec, essayer avec type de remplissage différent
                request["type_filling"] = mt5.ORDER_FILLING_FOK
                result = mt5.order_send(request)

                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    # Dernière tentative avec paramètres minimaux
                    request["type_filling"] = mt5.ORDER_FILLING_RETURN
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

                msg = (
                    f"✅ TRADE FORCÉ #{self.forced_trades_count}: {action.upper()} "
                    f"{symbol} {lot_size} lots à {price}"
                )
                self.logger.info(msg)
                return True
            else:
                err = (
                    f"❌ Échec trade forcé {symbol}: {result.comment} "
                    f"(Code: {result.retcode})"
                )
                self.logger.error(err)
                return False

        except Exception as e:
            self.logger.error(f"❌ Erreur trade forcé {symbol}: {e}")
            return False

    def force_nine_trades(self):
        """Forcer l'exécution de 9 trades"""
        self.logger.info("🚀 DÉBUT FORCE DE 9 TRADES")
        self.logger.info("="*50)

        # Connexion MT5
        if not self.connect_mt5():
            self.logger.error("❌ Impossible de se connecter à MT5")
            return False

        trades_executed = 0
        symbols_cycle = ["EURUSD", "XAUUSD", "BTCUSD"]

        for i in range(9):
            symbol = symbols_cycle[i % len(symbols_cycle)]
            action = "buy" if i % 2 == 0 else "sell"

            self.logger.info(f"\n🎯 Trade #{i+1}/9 - {symbol} {action.upper()}")

            # Récupérer les données (même simulées)
            try:
                current_data = self.get_live_data(symbol, 50)
                if current_data is None or len(current_data) == 0:
                    self.logger.warning(f"Pas de données pour {symbol} - Trade simulé")
                    # Simuler le trade
                    success = self.simulate_trade(action, symbol, 0.01)
                else:
                    # Essayer trade réel
                    success = self.execute_trade_force(action, symbol, 0.01)

                if success:
                    trades_executed += 1
                    self.logger.info(f"✅ Trade #{i+1} exécuté avec succès")
                else:
                    self.logger.error(f"❌ Échec trade #{i+1}")

            except Exception as e:
                self.logger.error(f"❌ Erreur trade #{i+1}: {e}")

            # Petit délai entre les trades
            time.sleep(2)

        # Résumé
        self.logger.info("\n" + "=" * 50)
        self.logger.info("📊 RÉSUMÉ FORCE TRADES:")
        self.logger.info("  🎯 Trades demandés: 9")
        self.logger.info(f"  ✅ Trades exécutés: {trades_executed}")
        self.logger.info(f"  📈 Taux de réussite: {trades_executed/9*100:.1f}%")

        # Sauvegarder les résultats
        self.save_forced_trades_session()

        return trades_executed > 0

    def save_forced_trades_session(self):
        """Sauvegarder la session de trades forcés"""
        try:
            os.makedirs("artifacts/forced_trades", exist_ok=True)

            session_data = {
                "timestamp": datetime.now().isoformat(),
                "type": "forced_trades",
                "target_trades": 9,
                "executed_trades": self.forced_trades_count,
                "trade_history": [
                    {
                        "timestamp": trade["timestamp"].isoformat(),
                        "symbol": trade.get("symbol", "N/A"),
                        "action": trade["action"],
                        "volume": trade["volume"],
                        "price": trade.get("price", 0),
                        "order_id": trade["order_id"],
                        "forced": trade.get("forced", False),
                    }
                    for trade in self.trade_history if trade.get("forced", False)
                ],
                "performance_metrics": self.performance_metrics,
            }

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"artifacts/forced_trades/forced_session_{timestamp}.json"

            with open(filename, "w") as f:
                json.dump(session_data, f, indent=2, default=str)

            self.logger.info(f"💾 Session trades forcés sauvegardée: {filename}")

        except Exception as e:
            self.logger.error(f"❌ Erreur sauvegarde: {e}")


def main():
    """Lancer les 9 trades forcés"""
    print("🚀 FORCE DE 9 TRADES - PROPFIRM")
    print("="*40)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        # Créer le moteur forcé
        engine = ForceTradeEngine()

        # Confirmation
        print("⚠️  MODE FORCE ACTIVÉ")
        print("📊 9 trades vont être exécutés immédiatement")
        print("💰 Paramètres: 0.01 lot par trade")
        print("🔄 Alternance BUY/SELL sur EURUSD, XAUUSD, BTCUSD")
        print()

        confirm = input("▶️ Confirmer l'exécution forcée ? (oui/non): ")

        if confirm.lower() in ['oui', 'o', 'yes', 'y']:
            print("\n🎯 EXÉCUTION FORCÉE CONFIRMÉE")
            print("-"*40)

            # Exécuter les 9 trades
            success = engine.force_nine_trades()

            if success:
                print("\n✅ Force trades terminée avec succès")
            else:
                print("\n❌ Échec de la force trades")

        else:
            print("\n⏹️ Force trades annulée")

    except KeyboardInterrupt:
        print("\n⏹️ Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
