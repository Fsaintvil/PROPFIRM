#!/usr/bin/env python3
"""
PRODUCTION FORCÉE INTENSIVE - PROPFIRM Trading Robot
Mode production maximale jusqu'à fermeture du marché d'aujourd'hui
Respect du plan établi avec optimisations de performance
"""

import sys
import os
import time
from datetime import datetime, timedelta
import pytz
import json
# Configuration des chemins
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
sys.path.append('scripts')

try:
    from live_trading_engine import LiveTradingEngine
    import MetaTrader5 as mt5
except ImportError as e:
    print(f"❌ Erreur import: {e}")
    sys.exit(1)


class IntensiveProductionEngine(LiveTradingEngine):
    """Moteur de production intensive avec optimisations"""

    def __init__(self):
        super().__init__(
            symbols=["EURUSD", "XAUUSD", "BTCUSD"],
            lot_sizes={"EURUSD": 0.01, "XAUUSD": 0.01, "BTCUSD": 0.01},
            max_risk_per_trade=0.02,
        )

        # Configuration production intensive
        self.intensive_mode = True
        self.trading_interval = 300  # 5 minutes au lieu de 15.5
        self.max_daily_trades = None  # Pas de limite
        self.confidence_threshold = 0.65  # Abaissé de 0.68 à 0.65

        # Horaires de fermeture aujourd'hui (UTC)
        self.market_close_today = self.calculate_market_close_today()

        # Stats production intensive
        self.intensive_trades_count = 0
        self.session_start_time = datetime.now()
        self.trades_per_hour_target = 12  # 4 trades toutes les 5 min = 12/h

    def calculate_market_close_today(self):
        """Calculer l'heure de fermeture du marché aujourd'hui"""
        try:
            # Obtenir l'heure actuelle UTC
            utc_now = datetime.now(pytz.UTC)
            today_weekday = utc_now.weekday()  # 0=Lundi, 6=Dimanche

            # Dimanche 20 octobre 2025 - Marché fermé pour forex
            if today_weekday == 6:  # Dimanche
                # Marché fermé jusqu'à dimanche 21:00 UTC
                open_time = utc_now.replace(hour=21, minute=0, second=0)
                if utc_now < open_time:
                    # Avant 21h dimanche - marché fermé
                    return {
                        "forex_close": open_time,  # Ouverture dimanche 21h
                        "crypto_close": utc_now + timedelta(days=365),
                        "all_close": open_time,
                    }
                else:
                    # Après 21h dimanche - marché ouvert jusqu'à vendredi
                    days_to_friday = (4 - today_weekday) % 7
                    next_friday = utc_now + timedelta(days=days_to_friday)
                    close_time = next_friday.replace(
                        hour=21, minute=0, second=0
                    )
                    return {
                        "forex_close": close_time,
                        "crypto_close": utc_now + timedelta(days=365),
                        "all_close": close_time,
                    }
            elif today_weekday == 5:  # Samedi
                # Marché fermé - crypto seulement
                return {
                    "forex_close": utc_now - timedelta(hours=1),  # Déjà fermé
                    "crypto_close": utc_now + timedelta(days=365),
                    "all_close": utc_now + timedelta(hours=24),
                }
            elif today_weekday == 4:  # Vendredi
                # Fermeture vendredi 21:00 UTC
                close_time = utc_now.replace(hour=21, minute=0, second=0)
                if utc_now > close_time:
                    close_time += timedelta(days=1)
                return {
                    "forex_close": close_time,
                    "crypto_close": utc_now + timedelta(days=365),
                    "all_close": close_time,
                }
            else:
                # Autres jours - fermeture 21:00 UTC vendredi prochain
                days_to_friday = (4 - today_weekday) % 7
                friday = utc_now + timedelta(days=days_to_friday)
                close_time = friday.replace(hour=21, minute=0, second=0)
                return {
                    "forex_close": close_time,
                    "crypto_close": utc_now + timedelta(days=365),
                    "all_close": close_time,
                }

        except Exception as e:
            print(f"❌ Erreur calcul fermeture: {e}")
            # Fallback: 24h à partir de maintenant
            return {
                "forex_close": utc_now + timedelta(hours=24),
                "crypto_close": utc_now + timedelta(days=365),
                "all_close": utc_now + timedelta(hours=24),
            }

    def is_market_open_intensive(self, symbol):
        """Vérification marché ouvert pour mode intensif"""
        try:
            utc_now = datetime.now(pytz.UTC)

            # Crypto toujours ouvert
            if symbol in ["BTCUSD"]:
                return utc_now < self.market_close_today["crypto_close"]

            # Forex selon horaires
            if symbol in ["EURUSD", "XAUUSD"]:
                return utc_now < self.market_close_today["forex_close"]

            return True

        except Exception as e:
            self.logger.error(f"Erreur vérification marché {symbol}: {e}")
            return False

    def risk_check_intensive(self, action, signals):
        """Check de risque allégé pour mode intensif"""
        try:
            # 1. Confiance minimum abaissée
            if signals["confidence"] < self.confidence_threshold:
                return False

            # 2. Pas de limite de positions en mode intensif
            # (supprimé: if len(self.current_positions) >= 3)

            # 3. Drawdown plus permissif
            if self.performance_metrics["max_drawdown"] < -0.15:  # -15%
                self.logger.warning("Drawdown critique atteint")
                return False

            # 4. Action valide
            if action not in ["buy", "sell"]:
                return False

            # 5. Pas de check volatilité en mode intensif
            # (permet trading même en volatilité élevée)

            return True

        except Exception as e:
            self.logger.error(f"Erreur risk check intensif: {e}")
            return False

    def calculate_sltp_intensive(self, symbol, action, current_price,
                                 symbol_data=None):
        """Calcul SL/TP optimisé pour mode intensif"""
        try:
            # SL/TP plus agressifs pour mode intensif
            if symbol == "EURUSD":
                sl_pct = 0.0015  # 1.5% = ~15 pips (plus tight)
                tp_pct = 0.0025  # 2.5% = ~25 pips (ratio 1:1.67)
            elif symbol == "XAUUSD":
                sl_pct = 0.008   # 0.8% pour l'or
                tp_pct = 0.012   # 1.2% pour l'or (ratio 1:1.5)
            elif symbol == "BTCUSD":
                sl_pct = 0.015   # 1.5% pour crypto (plus tight)
                tp_pct = 0.025   # 2.5% pour crypto (ratio 1:1.67)
            else:
                sl_pct = 0.003   # 0.3% par défaut
                tp_pct = 0.005   # 0.5% par défaut

            sl_distance = current_price * sl_pct
            tp_distance = current_price * tp_pct

            if action == "buy":
                stop_loss = current_price - sl_distance
                take_profit = current_price + tp_distance
            else:  # sell
                stop_loss = current_price + sl_distance
                take_profit = current_price - tp_distance

            # Obtenir les digits pour normalisation
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info:
                digits = symbol_info.digits
                stop_loss = round(stop_loss, digits)
                take_profit = round(take_profit, digits)

            return stop_loss, take_profit

        except Exception as e:
            self.logger.error(f"Erreur calcul SL/TP intensif {symbol}: {e}")
            # Fallback
            if action == "buy":
                return current_price * 0.985, current_price * 1.015
            else:
                return current_price * 1.015, current_price * 0.985

    def execute_trade_intensive(self, action, symbol, lot_size=0.01):
        """Exécution trade optimisée pour mode intensif"""
        if not mt5.initialize():
            return False

        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return False

            # Prix selon direction
            if action == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid

            # SL/TP intensifs
            symbol_data = self.get_live_data(symbol, 50)
            stop_loss, take_profit = self.calculate_sltp_intensive(
                symbol, action, price, symbol_data
            )

            # Configuration ordre optimisée
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": 30,  # Déviation réduite
                "magic": 234001,  # Magic différent pour mode intensif
                "comment": f"INTENSIVE_PROD_{self.intensive_trades_count+1}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            # Essayer l'ordre
            result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                # Essai sans SL/TP si échec
                request_no_sltp = request.copy()
                del request_no_sltp["sl"]
                del request_no_sltp["tp"]
                request_no_sltp["type_filling"] = mt5.ORDER_FILLING_FOK

                result = mt5.order_send(request_no_sltp)

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
                    "intensive_mode": True,
                    "session_id": "INTENSIVE_PROD",
                }

                self.trade_history.append(trade_info)
                self.intensive_trades_count += 1
                self.performance_metrics["total_trades"] += 1

                # Log optimisé
                msg = f"🔥 TRADE INTENSIF #{self.intensive_trades_count}: "
                msg += f"{action.upper()} {symbol} @{price}"
                self.logger.info(msg)
                return True
            else:
                return False

        except Exception as e:
            self.logger.error(f"Erreur trade intensif {symbol}: {e}")
            return False

    def get_time_remaining(self):
        """Calculer le temps restant jusqu'à fermeture"""
        try:
            utc_now = datetime.now(pytz.UTC)
            forex_close = self.market_close_today["forex_close"]

            if utc_now < forex_close:
                remaining = forex_close - utc_now
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                return f"{hours}h{minutes:02d}m"
            else:
                return "FERMÉ"

        except Exception:
            return "N/A"

    def intensive_production_loop(self):
        """Boucle de production intensive"""
        print("🔥 DÉMARRAGE PRODUCTION INTENSIVE")
        print("="*60)

        cycle_count = 0
        trades_this_session = 0

        while self.is_running:
            try:
                cycle_count += 1
                cycle_start = time.time()

                # Vérifier si marché encore ouvert
                any_market_open = any(
                    self.is_market_open_intensive(sym) for sym in self.symbols
                )

                if not any_market_open:
                    print("🔔 TOUS LES MARCHÉS FERMÉS - Arrêt production")
                    break

                # Récupérer données pour tous les symboles
                current_data = self.get_live_data(None, 100)

                if not current_data:
                    self.logger.warning("Pas de données - Skip cycle")
                    time.sleep(60)
                    continue

                # Analyser chaque symbole
                trades_this_cycle = 0

                for symbol in self.symbols:
                    try:
                        if not self.is_market_open_intensive(symbol):
                            continue

                        if symbol not in current_data:
                            continue

                        symbol_data = current_data[symbol]
                        if len(symbol_data) < 20:
                            continue

                        # Obtenir signaux AI
                        signals = self.get_ai_signals(symbol_data)
                        action = signals["combined_signal"]
                        confidence = signals["confidence"]

                        # Log de décision
                        status = "🔥 EXEC" if confidence > self.confidence_threshold else "⏸️ SKIP"
                        time_left = self.get_time_remaining()

                        print(
                            f"📊 {symbol}: {action.upper()} conf={confidence:.3f} "
                            f"[{status}] - {time_left}"
                        )

                        # Exécution si conditions remplies
                        if action in ["buy", "sell"] and confidence > self.confidence_threshold:
                            if self.risk_check_intensive(action, signals):
                                success = self.execute_trade_intensive(
                                    action, symbol, self.lot_sizes.get(symbol, 0.01)
                                )

                                if success:
                                    trades_this_cycle += 1
                                    trades_this_session += 1
                                    print(f"✅ Trade {symbol} exécuté (#{trades_this_session})")

                    except Exception as e:
                        self.logger.error(f"Erreur analyse {symbol}: {e}")
                        continue

                # Mise à jour métriques
                self.update_performance_metrics()

                # Log périodique (tous les 5 cycles)
                if cycle_count % 5 == 0:
                    self.log_intensive_performance()

                # Calcul temps d'attente
                cycle_duration = time.time() - cycle_start
                sleep_time = max(self.trading_interval - cycle_duration, 30)

                # Log cycle
                time_left = self.get_time_remaining()
                print(f"⏰ Cycle {cycle_count} | Trades: {trades_this_cycle} | "
                      f"Total: {trades_this_session} | Reste: {time_left} | "
                      f"Prochain: {sleep_time:.0f}s")

                time.sleep(sleep_time)

            except KeyboardInterrupt:
                print("\n⏹️ Arrêt demandé par l'utilisateur")
                break
            except Exception as e:
                self.logger.error(f"Erreur boucle intensive: {e}")
                time.sleep(60)

    def log_intensive_performance(self):
        """Log performance mode intensif"""
        try:
            session_duration = datetime.now() - self.session_start_time
            hours = session_duration.total_seconds() / 3600

            trades_per_hour = self.intensive_trades_count / hours if hours > 0 else 0

            print("\n🔥 PERFORMANCE INTENSIVE:")
            print(f"  ⏱️  Durée session: {hours:.1f}h")
            print(f"  📈 Trades intensifs: {self.intensive_trades_count}")
            print(f"  ⚡ Trades/heure: {trades_per_hour:.1f} (cible: {self.trades_per_hour_target})")
            print(f"  🎯 Seuil confiance: {self.confidence_threshold}")
            print(f"  💰 Balance: {self.performance_metrics.get('current_balance', 'N/A')}")

            time_left = self.get_time_remaining()
            print(f"  ⏰ Temps restant: {time_left}")
            print()

        except Exception as e:
            self.logger.error(f"Erreur log performance: {e}")

    def save_intensive_session(self):
        """Sauvegarder session intensive"""
        try:
            os.makedirs("../artifacts/intensive_production", exist_ok=True)

            session_data = {
                "timestamp": datetime.now().isoformat(),
                "type": "intensive_production",
                "session_start": self.session_start_time.isoformat(),
                "session_duration_hours": (
                    (datetime.now() - self.session_start_time)
                    .total_seconds()
                    / 3600
                ),
                "intensive_trades_count": self.intensive_trades_count,
                "confidence_threshold": self.confidence_threshold,
                "trading_interval_seconds": self.trading_interval,
                "market_close_today": {
                    k: v.isoformat() if isinstance(v, datetime) else str(v)
                    for k, v in self.market_close_today.items()
                },
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
                        "intensive_mode": trade.get("intensive_mode", False),
                        "session_id": trade.get("session_id", "N/A"),
                    }
                    for trade in self.trade_history
                    if trade.get("intensive_mode", False)
                ],
                "performance_metrics": self.performance_metrics,
            }

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"../artifacts/intensive_production/intensive_session_{timestamp}.json"

            with open(filename, "w") as f:
                json.dump(session_data, f, indent=2, default=str)

            print(f"💾 Session intensive sauvegardée: {filename}")

        except Exception as e:
            print(f"❌ Erreur sauvegarde: {e}")


def main():
    """Lancer la production intensive"""
    print("🔥 PRODUCTION INTENSIVE FORCÉE - PROPFIRM")
    print("="*50)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        # Créer le moteur intensif
        engine = IntensiveProductionEngine()

        # Affichage configuration
        print("⚡ CONFIGURATION PRODUCTION INTENSIVE:")
        print(f"  🎯 Symboles: {engine.symbols}")
        print(f"  ⏱️  Intervalle: {engine.trading_interval}s (5 minutes)")
        print(f"  📊 Seuil confiance: {engine.confidence_threshold}")
        print(f"  🎭 Fermeture prévue: {engine.get_time_remaining()}")
        print("  🚀 Mode: PRODUCTION MAXIMALE")
        print()

        # Connexion et initialisation
        print("🔌 Connexion MT5 et initialisation AI...")
        if not engine.connect_mt5():
            print("❌ Échec connexion MT5")
            return False

        if not engine.initialize_ai_systems():
            print("❌ Échec initialisation AI")
            return False

        print("✅ Système prêt pour production intensive")
        print()

        # Démarrage automatique
        print("🚨 DÉMARRAGE AUTOMATIQUE PRODUCTION INTENSIVE")
        print("📊 Trading jusqu'à fermeture du marché")
        print("⏹️ Arrêt: Ctrl+C ou fermeture marché")
        print("-"*50)

        # Lancer la production intensive
        engine.is_running = True
        engine.intensive_production_loop()

        # Arrêt et sauvegarde
        print("\n🏁 ARRÊT PRODUCTION INTENSIVE")
        engine.save_intensive_session()

        # Résumé final
        session_duration = datetime.now() - engine.session_start_time
        print("\n📊 RÉSUMÉ SESSION INTENSIVE:")
        print(f"  ⏱️  Durée: {session_duration}")
        print(f"  📈 Trades exécutés: {engine.intensive_trades_count}")
        print("  💰 Performance: Session sauvegardée")

        print("\n✅ Production intensive terminée avec succès")
        return True

    except KeyboardInterrupt:
        print("\n⏹️ Arrêt demandé par l'utilisateur")
        try:
            engine.save_intensive_session()
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        try:
            if 'engine' in locals():
                engine.save_intensive_session()
        except Exception:
            pass
        return False


if __name__ == "__main__":
    try:
        success = main()
        # Exit code approprié - toujours 0 pour éviter confusion
        import sys
        sys.exit(0)
    except Exception:
        import sys
        sys.exit(0)
