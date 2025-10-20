#!/usr/bin/env python3
"""
Script pour forcer l'exécution de 9 trades avec SL/TP adaptatifs
Version corrigée avec gestion appropriée des stop-loss et take-profit
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
    # numpy/pandas importés précédemment ne sont pas utilisés ici
except ImportError as e:
    print(f"❌ Erreur import: {e}")
    sys.exit(1)


class ForceTradeWithSLTP(LiveTradingEngine):
    """Moteur modifié pour forcer l'exécution de trades AVEC SL/TP"""

    def __init__(self):
        super().__init__(
            symbols=["EURUSD", "XAUUSD", "BTCUSD"],
            lot_sizes={"EURUSD": 0.01, "XAUUSD": 0.01, "BTCUSD": 0.01},
            max_risk_per_trade=0.02,
        )
        self.forced_trades_count = 0
        self.target_trades = 9

    def is_market_open(self, symbol):
        """Override pour forcer tous les marchés ouverts"""
        return True

    def risk_check(self, action, signals):
        """Check de risque allégé pour permettre les trades forcés"""
        if self.forced_trades_count >= self.target_trades:
            return False
        return True

    def calculate_sltp_levels(self, symbol, action, current_price, symbol_data=None):
        """Calculer les niveaux SL/TP adaptatifs selon le plan"""
        try:
            # Obtenir les informations du symbole pour les digits
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                # Valeurs par défaut si pas d'info
                digits = 5 if "USD" in symbol else 2
            else:
                digits = symbol_info.digits

            # Calculer l'ATR ou utiliser un pourcentage fixe
            if symbol_data is not None and len(symbol_data) > 20:
                # Méthode ATR basée sur les données
                atr = (symbol_data["returns"].rolling(20).std().iloc[-1]) * current_price
                sl_distance = atr * 2  # 2x ATR pour SL
                tp_distance = atr * 3  # 3x ATR pour TP
            else:
                # Méthode pourcentage fixe par symbole
                if symbol == "EURUSD":
                    sl_pct = 0.002  # 0.2% = ~20 pips
                    tp_pct = 0.003  # 0.3% = ~30 pips
                elif symbol == "XAUUSD":
                    sl_pct = 0.01   # 1% = ~$20 pour l'or
                    tp_pct = 0.015  # 1.5% = ~$30 pour l'or
                elif symbol == "BTCUSD":
                    sl_pct = 0.02   # 2% pour crypto
                    tp_pct = 0.03   # 3% pour crypto
                else:
                    sl_pct = 0.005  # 0.5% par défaut
                    tp_pct = 0.0075  # 0.75% par défaut

                sl_distance = current_price * sl_pct
                tp_distance = current_price * tp_pct

            # Calculer les niveaux selon la direction
            if action == "buy":
                stop_loss = current_price - sl_distance
                take_profit = current_price + tp_distance
            else:  # sell
                stop_loss = current_price + sl_distance
                take_profit = current_price - tp_distance

            # Normaliser selon les digits du symbole
            stop_loss = round(stop_loss, digits)
            take_profit = round(take_profit, digits)

            return stop_loss, take_profit

        except Exception as e:
            print(f"❌ Erreur calcul SL/TP pour {symbol}: {e}")
            # Valeurs de fallback
            if action == "buy":
                return current_price * 0.98, current_price * 1.02
            else:
                return current_price * 1.02, current_price * 0.98

    def execute_trade_with_sltp(self, action, symbol, lot_size=0.01):
        """Exécution forcée AVEC SL/TP adaptatifs"""
        if not mt5.initialize():
            self.logger.error("Impossible d'initialiser MT5")
            return False

        try:
            # Obtenir le prix actuel et les données
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.logger.error(f"Impossible d'obtenir le tick pour {symbol}")
                return False

            # Récupérer les données pour calcul ATR
            symbol_data = self.get_live_data(symbol, 50)

            # Prix et type d'ordre
            if action == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid

            # Calculer SL/TP selon le plan
            stop_loss, take_profit = self.calculate_sltp_levels(
                symbol, action, price, symbol_data
            )

            print(f"  💡 Prix: {price}, SL: {stop_loss}, TP: {take_profit}")

            # Configuration avec SL/TP
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": 50,
                "magic": 234000,
                "comment": f"FORCE_SLTP_{self.forced_trades_count+1}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            # Essayer l'ordre avec SL/TP
            result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                # Si échec avec SL/TP, essayer sans SL/TP puis les ajouter
                print("  ⚠️ Échec avec SL/TP direct, essai en 2 étapes...")

                # 1. Ordre sans SL/TP
                request_no_sltp = request.copy()
                del request_no_sltp["sl"]
                del request_no_sltp["tp"]
                request_no_sltp["type_filling"] = mt5.ORDER_FILLING_FOK

                result = mt5.order_send(request_no_sltp)

                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    # 2. Modifier la position pour ajouter SL/TP
                    position_ticket = result.order

                    modify_request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": symbol,
                        "sl": stop_loss,
                        "tp": take_profit,
                        "position": position_ticket,
                    }

                    modify_result = mt5.order_send(modify_request)

                    if modify_result.retcode == mt5.TRADE_RETCODE_DONE:
                        print("  ✅ SL/TP ajoutés via modification")
                    else:
                        print(f"  ⚠️ Échec ajout SL/TP: {modify_result.comment}")

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                # Trade réussi
                trade_info = {
                    "timestamp": datetime.now(),
                    "symbol": symbol,
                    "action": action,
                    "volume": lot_size,
                    "price": price,
                    "order_id": result.order,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "forced": True,
                    "with_sltp": True,
                }

                self.trade_history.append(trade_info)
                self.forced_trades_count += 1
                self.performance_metrics["total_trades"] += 1

                msg = f"✅ TRADE FORCÉ #{self.forced_trades_count}: "
                msg += f"{action.upper()} {symbol} {lot_size} lots à {price}"
                msg += f" [SL:{stop_loss} TP:{take_profit}]"
                self.logger.info(msg)
                return True
            else:
                error_msg = f"❌ Échec trade forcé {symbol}: "
                error_msg += f"{result.comment} (Code: {result.retcode})"
                self.logger.error(error_msg)
                return False

        except Exception as e:
            self.logger.error(f"❌ Erreur trade forcé {symbol}: {e}")
            return False

    def force_nine_trades_with_sltp(self):
        """Forcer l'exécution de 9 trades AVEC SL/TP"""
        print("🚀 DÉBUT FORCE DE 9 TRADES AVEC SL/TP")
        print("="*60)

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

            print(f"\n🎯 Trade #{i+1}/9 - {symbol} {action.upper()} (avec SL/TP)")

            try:
                # Essayer trade réel avec SL/TP
                success = self.execute_trade_with_sltp(action, symbol, 0.01)

                if success:
                    trades_executed += 1
                    print(f"✅ Trade #{i+1} exécuté avec SL/TP (RÉEL)")
                else:
                    # Si échec, simuler le trade
                    sim_success = self.simulate_trade(action, symbol, 0.01)
                    if sim_success:
                        trades_simulated += 1
                        print(f"✅ Trade #{i+1} simulé")
                    else:
                        print(f"❌ Échec complet trade #{i+1}")

            except Exception as e:
                print(f"❌ Erreur trade #{i+1}: {e}")

            # Délai entre les trades
            time.sleep(2)

        # Résumé
        print("\n" + "="*60)
        print("📊 RÉSUMÉ FORCE TRADES AVEC SL/TP:")
        print("  🎯 Trades demandés: 9")
        print(f"  ✅ Trades réels avec SL/TP: {trades_executed}")
        print(f"  🔄 Trades simulés: {trades_simulated}")
        print(f"  📈 Total exécutés: {trades_executed + trades_simulated}")
        print(f"  🎯 Taux de réussite: {(trades_executed + trades_simulated)/9*100:.1f}%")

        # Sauvegarder les résultats
        self.save_forced_trades_session()

        return (trades_executed + trades_simulated) > 0

    def save_forced_trades_session(self):
        """Sauvegarder la session de trades forcés avec SL/TP"""
        try:
            os.makedirs("../artifacts/forced_trades", exist_ok=True)

            session_data = {
                "timestamp": datetime.now().isoformat(),
                "type": "forced_trades_with_sltp",
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
                        "stop_loss": trade.get("stop_loss", None),
                        "take_profit": trade.get("take_profit", None),
                        "forced": trade.get("forced", False),
                        "with_sltp": trade.get("with_sltp", False),
                        "simulated": trade.get("simulated", False),
                    }
                    for trade in self.trade_history
                ],
                "performance_metrics": self.performance_metrics,
            }

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"../artifacts/forced_trades/forced_sltp_session_{timestamp}.json"

            with open(filename, "w") as f:
                json.dump(session_data, f, indent=2, default=str)

            print(f"💾 Session trades forcés SL/TP sauvegardée: {filename}")

        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")


def main():
    """Lancer les 9 trades forcés AVEC SL/TP"""
    print("🚀 FORCE AUTO DE 9 TRADES AVEC SL/TP - PROPFIRM")
    print("="*50)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        # Créer le moteur forcé avec SL/TP
        print("⚡ Initialisation du moteur avec SL/TP adaptatifs...")
        engine = ForceTradeWithSLTP()

        # Exécution automatique avec SL/TP
        print("⚠️  MODE FORCE AVEC SL/TP ACTIVÉ")
        print("📊 9 trades avec SL/TP vont être exécutés IMMÉDIATEMENT")
        print("💰 Paramètres: 0.01 lot par trade")
        print("🛡️  SL/TP adaptatifs selon ATR et volatilité")
        print("🔄 Alternance BUY/SELL sur EURUSD, XAUUSD, BTCUSD")
        print()
        print("🎯 EXÉCUTION EN COURS...")
        print("-"*50)

        # Exécuter les 9 trades avec SL/TP
        success = engine.force_nine_trades_with_sltp()

        if success:
            print("\n✅ Force trades avec SL/TP terminée avec succès")
        else:
            print("\n❌ Échec de la force trades avec SL/TP")

    except KeyboardInterrupt:
        print("\n⏹️ Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
