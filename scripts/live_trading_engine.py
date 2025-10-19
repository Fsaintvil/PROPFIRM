#!/usr/bin/env python3
"""
Système de Trading Live en Temps Réel.

Ce système implémente :
- Connexion MT5 live avec gestion des erreurs
- Pipeline complet data live → ML → décision → exécution
- Intégration de tous les systèmes (Meta-Learning, RL, Portfolio, Régimes)
- Gestion des ordres automatique avec latence minimale
- Monitoring en temps réel et gestion des risques
"""

import pandas as pd
import numpy as np
import json
import os
import time
import sys
import logging
from datetime import datetime
import warnings

warnings.filterwarnings("ignore")

# Import des utilitaires sécurisés (ajout des path si nécessaire)
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "utils"))

try:
    from safe_io import safe_read_csv, FALLBACK_SAMPLE_DATA
    IO_UTILS_AVAILABLE = True
    print("✅ Utilitaires I/O sécurisées disponibles")
except ImportError:
    IO_UTILS_AVAILABLE = False
    safe_read_csv = None
    FALLBACK_SAMPLE_DATA = None
    print("⚠️  Utilitaires I/O non disponibles - utilisation basique")

# MT5 Integration avec fallback robuste
try:
    from utils.mt5_connector import get_mt5, is_mt5_available

    MT5_AVAILABLE = is_mt5_available()
    mt5 = get_mt5()
    print("✅ MT5 connector loaded successfully")
except ImportError as e:
    # Fallback direct vers MetaTrader5
    try:
        import MetaTrader5 as mt5
        MT5_AVAILABLE = True
        print("✅ MT5 direct import successful")
    except ImportError:
        MT5_AVAILABLE = False
        mt5 = None
        print(f"⚠️  MT5 non disponible: {e}")
        print("🔄 Mode simulation complet activé")
except Exception as e:
    MT5_AVAILABLE = False
    mt5 = None
    get_mt5 = None

    def is_mt5_available():
        return False

    print(f"🔴 Erreur MT5 inattendue: {e}")
    print("🔄 Mode simulation complet activé")

# Nos systèmes développés avec fallback robuste
try:
    from meta_learning_system import MetaLearningTradingSystem
    from reinforcement_learning_agent import ReinforcementLearningTradingSystem
    from multi_asset_portfolio import MultiAssetPortfolioOptimizer
    from market_regime_detection import MarketRegimeDetector

    SYSTEMS_AVAILABLE = True
    print("✅ Systèmes de trading IA chargés avec succès")
except ImportError as e:
    SYSTEMS_AVAILABLE = False
    print(f"⚠️  Systèmes non disponibles: {e}")
    print("🔄 Mode minimal activé - fonctions de base uniquement")
except Exception as e:
    SYSTEMS_AVAILABLE = False
    print(f"🔴 Erreur systèmes inattendue: {e}")
    print("🔄 Mode minimal activé")


class LiveTradingEngine:
    """Moteur de trading live avec tous les systèmes intégrés"""

    def __init__(
        self,
        symbols=None,
        lot_sizes=None,
        max_risk_per_trade=0.02
    ):
        """
        Args:
            symbols: Liste des symboles à trader (utilise config si None)
            lot_sizes: Dict {symbol: lot_size} ou valeur unique
            max_risk_per_trade: Risque maximum par trade (2%)
        """
        # Charger la configuration multi-actifs si disponible
        if symbols is None:
            try:
                config_path = os.path.join(
                    os.path.dirname(__file__), "..", "config"
                )
                sys.path.append(config_path)
                from config.settings import INSTRUMENTS
                self.symbols = INSTRUMENTS
                print(f"✅ Configuration multi-actifs chargée: {INSTRUMENTS}")
            except ImportError:
                # Fallback vers configuration par défaut
                self.symbols = ["EURUSD", "XAUUSD", "BTCUSD"]
                print("⚠️  Config non trouvée - symboles par défaut utilisés")
            except Exception as e:
                # Fallback de sécurité
                self.symbols = ["EURUSD"]
                print(f"⚠️  Erreur config: {e} - symbole par défaut: EURUSD")
        else:
            self.symbols = symbols if isinstance(symbols, list) else [symbols]

        # Configuration des lot sizes
        if lot_sizes is None:
            # Même lot size pour tous les symboles
            self.lot_sizes = {symbol: 0.01 for symbol in self.symbols}
        elif isinstance(lot_sizes, dict):
            self.lot_sizes = lot_sizes
        else:
            # Valeur unique pour tous
            self.lot_sizes = {symbol: lot_sizes for symbol in self.symbols}

        # Validation des paramètres
        for symbol in self.symbols:
            if symbol not in self.lot_sizes:
                self.lot_sizes[symbol] = 0.01  # Valeur par défaut

        self.max_risk_per_trade = max_risk_per_trade

        # État du système
        self.is_connected = False
        self.is_running = False
        self.last_data_update = None

        # Données en temps réel par symbole
        self.live_data = {symbol: pd.DataFrame() for symbol in self.symbols}
        # get_live_data(symbol=None) -> récupère tous ou un symbole spécifique
        self.current_positions = {}

        # Systèmes AI intégrés
        self.meta_learning = None
        self.rl_agent = None
        self.portfolio_optimizer = None
        self.regime_detector = None

        # Performance tracking
        self.trade_history = []
        # Métriques basiques avec support multi-actifs
        self.performance_metrics = {
            "total_trades": 0,
            "winning_trades": 0,
            "current_balance": 10000.0,
            "max_drawdown": 0.0,
            "current_regime": "Unknown",
            "symbols_traded": self.symbols,  # Nouveau
        }

        # Configuration logging
        self.setup_logging()

        print("🚀 Moteur Trading Live Multi-Actifs initialisé:")
        print(f"  📈 Symboles: {', '.join(self.symbols)}")
        print(f"  💰 Lots: {self.lot_sizes}")
        print(f"  🛡️  Risque max: {max_risk_per_trade*100:.1f}%")

    def setup_logging(self):
        """Configuration du système de logging"""
        os.makedirs("logs", exist_ok=True)

        # Logger principal
        self.logger = logging.getLogger("LiveTrading")
        self.logger.setLevel(logging.INFO)

        # Handler fichier avec rotation
        file_handler = logging.FileHandler(
            f"logs/live_trading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        file_handler.setLevel(logging.INFO)

        # Format des logs
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)

        # Handler console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        self.logger.info("Système de logging initialisé")

    def connect_mt5(self):
        """Connexion à MetaTrader 5"""
        if not MT5_AVAILABLE:
            self.logger.warning("MT5 non disponible - Mode simulation activé")
            self.is_connected = True  # Simulation
            return True

        try:
            # Initialiser MT5
            if not mt5.initialize():
                error = mt5.last_error()
                self.logger.error(f"Échec initialisation MT5: {error}")
                return False

            # Vérifier la connexion
            account_info = mt5.account_info()
            if account_info is None:
                self.logger.error("Impossible d'obtenir les infos compte")
                return False

            self.logger.info(f"Connecté au compte: {account_info.login}")
            self.logger.info(f"Balance: {account_info.balance}")
            self.logger.info(f"Equity: {account_info.equity}")

            # Vérifier les symboles
            for symbol in self.symbols:
                symbol_info = mt5.symbol_info(symbol)
                if symbol_info is None:
                    self.logger.warning(
                        f"Symbole {symbol} non trouvé - ignoré"
                    )
                    continue

                # Sélectionner le symbole
                if not mt5.symbol_select(symbol, True):
                    self.logger.warning(
                        f"Échec sélection symbole {symbol}"
                    )
                    continue

                self.logger.info(f"✅ Symbole {symbol} configuré")

            self.is_connected = True
            self.logger.info("Connexion MT5 réussie")
            return True

        except Exception as e:
            self.logger.error(f"Erreur connexion MT5: {e}")
            return False

    def initialize_ai_systems(self):
        """Initialiser tous les systèmes AI"""
        if not SYSTEMS_AVAILABLE:
            self.logger.warning("Systèmes AI non disponibles")
            return False

        try:
            self.logger.info("Initialisation des systèmes AI...")

            # 1. Meta-Learning System
            self.meta_learning = MetaLearningTradingSystem(max_models=3)

            # 2. Reinforcement Learning Agent
            self.rl_agent = ReinforcementLearningTradingSystem(use_dqn=True)

            # 3. Portfolio Optimizer (pour allocation)
            self.portfolio_optimizer = MultiAssetPortfolioOptimizer()

            # 4. Regime Detector
            self.regime_detector = MarketRegimeDetector(n_regimes=3)

            self.logger.info("✅ Tous les systèmes AI initialisés")
            return True

        except Exception as e:
            self.logger.error(f"Erreur initialisation AI: {e}")
            return False

    def get_live_data(self, symbol=None, count=100):
        """Récupérer les données live de MT5 pour un ou tous les symboles"""
        if symbol is None:
            # Récupérer pour tous les symboles
            all_data = {}
            for sym in self.symbols:
                data = self.get_live_data(sym, count)
                if data is not None:
                    all_data[sym] = data
            return all_data

        if not MT5_AVAILABLE:
            # Mode simulation - générer des données
            return self.generate_simulation_data(count)

        try:
            # Récupérer les données OHLCV pour le symbole spécifique
            rates = mt5.copy_rates_from_pos(
                symbol, mt5.TIMEFRAME_M1, 0, count
            )

            if rates is None or len(rates) == 0:
                self.logger.error(f"Aucune donnée reçue pour {symbol}")
                return None

            # Convertir en DataFrame
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.set_index("time", inplace=True)

            # Ajouter des features basiques
            df["returns"] = df["close"].pct_change()
            df["sma_20"] = df["close"].rolling(20).mean()
            df["volatility"] = df["returns"].rolling(20).std()

            self.last_data_update = datetime.now()

            return df

        except Exception as e:
            self.logger.error(f"Erreur récupération données {symbol}: {e}")
            return None

    def generate_simulation_data(self, count):
        """Générer des données de simulation"""
        try:
            # Charger des données historiques si disponibles
            if os.path.exists("data/features_sample.csv"):
                if IO_UTILS_AVAILABLE and safe_read_csv is not None:
                    historical_df = safe_read_csv(
                        "data/features_sample.csv",
                        FALLBACK_SAMPLE_DATA
                    )
                else:
                    historical_df = pd.read_csv("data/features_sample.csv")
                if "Unnamed: 0" in historical_df.columns:
                    historical_df = historical_df.set_index("Unnamed: 0")
                    historical_df.index = pd.to_datetime(historical_df.index)

                # Prendre les dernières données
                return historical_df.tail(count)
            else:
                # Générer des données synthétiques
                dates = pd.date_range(
                    end=datetime.now(), periods=count, freq="1T"
                )

                # Prix avec marche aléatoire
                np.random.seed(int(time.time()))
                returns = np.random.normal(0, 0.001, count)
                prices = [1.0]
                for ret in returns[1:]:
                    prices.append(prices[-1] * (1 + ret))

                df = pd.DataFrame(
                    {
                        "open": prices,
                        "high": [
                            p * (1 + abs(np.random.normal(0, 0.0005)))
                            for p in prices
                        ],
                        "low": [
                            p * (1 - abs(np.random.normal(0, 0.0005)))
                            for p in prices
                        ],
                        "close": prices,
                        "volume": np.random.randint(10, 100, count),
                    },
                    index=dates,
                )

                return df

        except Exception as e:
            self.logger.error(f"Erreur génération données simulation: {e}")
            return None

    def get_ai_signals(self, current_data):
        """Obtenir les signaux de tous les systèmes AI"""
        signals = {
            "meta_learning": None,
            "reinforcement_learning": None,
            "regime_detection": None,
            "portfolio_allocation": None,
            "combined_signal": "hold",
            "confidence": 0.0,
        }

        try:
            # 1. Détecter le régime de marché
            if self.regime_detector:
                regime_result = self.regime_detector.detect_regimes(
                    current_data
                )
                current_regime = regime_result["current_regime"]
                regime_signals = (
                    self.regime_detector.get_regime_strategy_signals(
                        current_regime
                    )
                )

                signals["regime_detection"] = {
                    "regime": self.regime_detector.regime_names[
                        current_regime
                    ],
                    "action": regime_signals["action"],
                    "confidence": regime_signals["confidence"],
                }

                self.performance_metrics["current_regime"] = signals[
                    "regime_detection"
                ]["regime"]

            # 2. Meta-Learning (si initialisé et données suffisantes)
            if self.meta_learning and len(current_data) > 50:
                try:
                    # Utiliser le système auto-optimisé
                    if (hasattr(self.meta_learning, "model_ensemble") and
                            self.meta_learning.model_ensemble):
                        # Préparer les features
                        features = current_data.select_dtypes(
                            include=[np.number]
                        ).fillna(0)

                        if len(features.columns) > 0:
                            # Prédiction avec l'ensemble
                            last_features = features.iloc[-1:].values

                            ensemble_pred = (
                                self.meta_learning.ensemble_predict(
                                    last_features
                                )
                            )

                            signals["meta_learning"] = {
                                "prediction": float(ensemble_pred[0])
                                if len(ensemble_pred) > 0
                                else 0.5,
                                "action": "buy"
                                if ensemble_pred[0] > 0.6
                                else "sell"
                                if ensemble_pred[0] < 0.4
                                else "hold",
                                "confidence": abs(ensemble_pred[0] - 0.5) * 2,
                            }
                except Exception as e:
                    self.logger.warning(f"Erreur Meta-Learning: {e}")

            # 3. Combiner les signaux
            combined_action = "hold"
            combined_confidence = 0.0

            # Logique de combinaison simple
            if signals["regime_detection"]:
                regime_action = signals["regime_detection"]["action"]
                regime_conf = signals["regime_detection"]["confidence"]

                if regime_action == "long_bias":
                    combined_action = "buy"
                    combined_confidence = regime_conf
                elif regime_action == "short_bias":
                    combined_action = "sell"
                    combined_confidence = regime_conf
                else:
                    combined_action = "hold"
                    combined_confidence = regime_conf * 0.5

            # Ajuster avec Meta-Learning si disponible
            if signals["meta_learning"]:
                ml_action = signals["meta_learning"]["action"]
                ml_conf = signals["meta_learning"]["confidence"]

                # Si les signaux concordent, augmenter la confiance
                if ml_action == combined_action:
                    combined_confidence = min(
                        combined_confidence + ml_conf * 0.3, 1.0
                    )
                # Si les signaux divergent, réduire la confiance
                elif ml_action != "hold" and combined_action != "hold":
                    combined_confidence *= 0.5
                    combined_action = (
                        "hold"  # Neutraliser en cas de divergence
                    )

            signals["combined_signal"] = combined_action
            signals["confidence"] = combined_confidence

            return signals

        except Exception as e:
            self.logger.error(f"Erreur calcul signaux AI: {e}")
            return signals

    def execute_trade(
        self,
        action,
        symbol=None,
        lot_size=None,
        stop_loss=None,
        take_profit=None
    ):
        """Exécuter un trade sur MT5 pour un symbole spécifique"""
        # Utiliser le premier symbole par défaut si non spécifié
        if symbol is None:
            symbol = self.symbols[0]

        if lot_size is None:
            lot_size = self.lot_sizes.get(symbol, 0.01)

        if not MT5_AVAILABLE:
            # Simulation
            return self.simulate_trade(
                action, symbol, lot_size, stop_loss, take_profit
            )

        try:
            # Initialiser MT5 si pas encore fait
            if not mt5.initialize():
                self.logger.error("Impossible d'initialiser MT5")
                return False

            # Obtenir le prix actuel pour le symbole
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self.logger.error(
                    f"Impossible d'obtenir le tick pour {symbol}"
                )
                return False

            # Déterminer le type d'ordre
            if action == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            elif action == "sell":
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            else:
                return False  # Action non reconnue

            # Préparer la requête
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "deviation": 20,
                "magic": 234000,
                "comment": "Live AI Trading",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            # Ajouter stop loss et take profit si spécifiés
            if stop_loss:
                request["sl"] = stop_loss
            if take_profit:
                request["tp"] = take_profit

            # Envoyer l'ordre
            result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.error(
                    f"Échec exécution ordre {symbol}: {result.comment}"
                )
                return False

            # Enregistrer le trade
            trade_info = {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "action": action,
                "volume": lot_size,
                "price": price,
                "order_id": result.order,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

            self.trade_history.append(trade_info)
            self.performance_metrics["total_trades"] += 1

            self.logger.info(
                f"✅ Ordre exécuté: {action} {symbol} {lot_size} lots à {price}"
            )

            return True

        except Exception as e:
            self.logger.error(f"Erreur exécution trade {symbol}: {e}")
            return False

    def simulate_trade(
        self, action, symbol, lot_size, stop_loss=None, take_profit=None
    ):
        """Simuler un trade (mode sans MT5)"""
        try:
            # Prix de simulation
            symbol_has_data = (symbol in self.live_data and
                               self.live_data[symbol] is not None and
                               len(self.live_data[symbol]) > 0)

            if symbol_has_data:
                current_price = self.live_data[symbol]["close"].iloc[-1]
            else:
                current_price = 1.0  # Prix par défaut

            # Simuler l'exécution
            trade_info = {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "action": action,
                "volume": lot_size,
                "price": current_price,
                "order_id": f"SIM_{len(self.trade_history)+1}",
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "simulated": True,
            }

            self.trade_history.append(trade_info)
            self.performance_metrics["total_trades"] += 1

            self.logger.info(
                f"✅ Trade simulé: {action} {symbol} {lot_size} lots"
                f" à {current_price}"
            )

            return True

        except Exception as e:
            self.logger.error(f"Erreur simulation trade {symbol}: {e}")
            return False

    # LIGNE 547-589 : Checks de risque simplistes
    def risk_check(self, action, signals):
        """Vérifier les contraintes de risque avant exécution"""
        try:
            # 1. Vérifier la confiance minimum
            if signals["confidence"] < 0.6:
                self.logger.info(
                    f"Confiance trop faible: {signals['confidence']:.2f}"
                )
                return False

            # 2. Vérifier le nombre de positions ouvertes
            if len(self.current_positions) >= 3:
                self.logger.info("Nombre maximum de positions atteint")
                return False

            # 3. Vérifier le drawdown
            if self.performance_metrics["max_drawdown"] < -0.10:  # -10%
                self.logger.warning(
                    "Drawdown maximum atteint - Trading suspendu"
                )
                return False

            # 4. Vérifier la cohérence du signal
            if action not in ["buy", "sell"]:
                return False

            # 5. Vérifier la volatilité (éviter conditions extrêmes)
            if self.live_data is not None and len(self.live_data) > 20:
                recent_volatility = (
                    self.live_data["returns"].rolling(20).std().iloc[-1]
                )
                if recent_volatility > 0.01:  # Volatilité > 1%
                    self.logger.warning(
                        f"Volatilité élevée: {recent_volatility:.4f}"
                    )
                    return False

            return True

        except Exception as e:
            self.logger.error(f"Erreur vérification risque: {e}")
            return False

    # LIGNE 590-676 : Une seule grosse fonction
    def main_trading_loop(self):
        """Boucle principale de trading"""
        self.logger.info("🚀 Démarrage de la boucle de trading")

        cycle_count = 0

        while self.is_running:
            try:
                cycle_count += 1
                cycle_start = time.time()

                # 1. Récupérer les données live pour tous les symboles
                current_data = self.get_live_data(count=200)

                if not current_data or len(current_data) == 0:
                    self.logger.warning("Aucune donnée reçue")
                    time.sleep(10)
                    continue

                # Vérifier qu'on a des données suffisantes
                # pour au moins un symbole
                has_sufficient_data = False
                for symbol, data in current_data.items():
                    if data is not None and len(data) >= 50:
                        has_sufficient_data = True
                        break

                if not has_sufficient_data:
                    self.logger.warning(
                        "Données insuffisantes pour tous les symboles"
                    )
                    time.sleep(10)
                    continue

                self.live_data = current_data

                # 2. Obtenir les signaux AI
                signals = self.get_ai_signals(current_data)

                # 3. Décision de trading
                action = signals["combined_signal"]
                confidence = signals["confidence"]

                regime = self.performance_metrics.get('current_regime', 'N/A')
                self.logger.info(
                    f"Cycle {cycle_count}: Action={action}, "
                    f"Confiance={confidence:.2f}, Régime={regime}"
                )

                # 4. Vérification des risques
                if action in ["buy", "sell"] and self.risk_check(
                    action, signals
                ):
                    # Calculer stop loss et take profit adaptatifs
                    if self.live_data is not None:
                        current_price = self.live_data["close"].iloc[-1]
                        atr = (self.live_data["returns"]
                               .rolling(20)
                               .std()
                               .iloc[-1]) * current_price

                        if action == "buy":
                            stop_loss = current_price - (atr * 2)
                            take_profit = current_price + (atr * 3)
                        else:  # sell
                            stop_loss = current_price + (atr * 2)
                            take_profit = current_price - (atr * 3)

                        # Exécuter le trade
                        success = self.execute_trade(
                            action, self.lot_size, stop_loss, take_profit
                        )

                        if success:
                            self.logger.info("💰 Trade exécuté avec succès")
                        else:
                            self.logger.error("❌ Échec exécution trade")

                # 5. Mise à jour des métriques de performance
                self.update_performance_metrics()

                # 6. Log périodique des stats
                if cycle_count % 10 == 0:  # Log tous les 10 cycles
                    self.log_performance_summary()

                # 7. Attendre avant le prochain cycle
                cycle_duration = time.time() - cycle_start
                sleep_time = max(
                    30 - cycle_duration, 5
                )  # Min 5 sec entre cycles
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                self.logger.info("Arrêt demandé par l'utilisateur")
                break
            except Exception as e:
                self.logger.error(f"Erreur dans la boucle principale: {e}")
                time.sleep(30)  # Pause plus longue en cas d'erreur

    def update_performance_metrics(self):
        """Mettre à jour les métriques de performance"""
        try:
            if len(self.trade_history) > 0:
                # Calculer les trades gagnants (simulation simple)
                winning_trades = 0
                for trade in self.trade_history[-10:]:  # Derniers 10 trades
                    # Simulation simpliste
                    # Simulation: 60% de chance de trade gagnant
                    if hash(str(trade["timestamp"])) % 100 < 60:
                        winning_trades += 1

                self.performance_metrics["winning_trades"] = winning_trades

                # Simuler l'évolution de la balance
                if len(self.trade_history) > 1:
                    recent_trades = len(
                        self.trade_history
                    ) - self.performance_metrics.get("last_balance_update", 0)
                    balance_change = (
                        recent_trades * 50
                        if winning_trades > len(self.trade_history[-10:]) * 0.5
                        else -25
                    )

                    self.performance_metrics[
                        "current_balance"
                    ] += balance_change
                    self.performance_metrics["last_balance_update"] = len(
                        self.trade_history
                    )

                # Calculer le drawdown
                peak_balance = max(
                    self.performance_metrics.get("peak_balance", 10000),
                    self.performance_metrics["current_balance"],
                )
                self.performance_metrics["peak_balance"] = peak_balance

                current_drawdown = (
                    self.performance_metrics["current_balance"] - peak_balance
                ) / peak_balance
                self.performance_metrics["max_drawdown"] = min(
                    self.performance_metrics.get("max_drawdown", 0),
                    current_drawdown,
                )

        except Exception as e:
            self.logger.error(f"Erreur mise à jour métriques: {e}")

    def log_performance_summary(self):
        """Logger un résumé des performances"""
        try:
            metrics = self.performance_metrics

            win_rate = 0
            if metrics["total_trades"] > 0:
                win_rate = (
                    metrics["winning_trades"] / metrics["total_trades"] * 100
                )

            self.logger.info("📊 RÉSUMÉ PERFORMANCES:")
            self.logger.info(f"  💰 Balance: {metrics['current_balance']:.2f}")
            self.logger.info(f"  📈 Trades totaux: {metrics['total_trades']}")
            self.logger.info(f"  🎯 Win Rate: {win_rate:.1f}%")
            self.logger.info(
                f"  📉 Max Drawdown: {metrics['max_drawdown']*100:.1f}%"
            )
            self.logger.info(f"  🎭 Régime actuel: {metrics['current_regime']}")

        except Exception as e:
            self.logger.error(f"Erreur log résumé: {e}")

    def start_live_trading(self):
        """Démarrer le trading live"""
        try:
            self.logger.info("🚀 DÉMARRAGE TRADING LIVE")

            # 1. Connexion MT5
            if not self.connect_mt5():
                self.logger.error("❌ Impossible de se connecter à MT5")
                return False

            # 2. Initialiser les systèmes AI
            if not self.initialize_ai_systems():
                self.logger.error("❌ Impossible d'initialiser les systèmes AI")
                return False

            # 3. Démarrer la boucle de trading
            self.is_running = True
            self.main_trading_loop()

        except Exception as e:
            self.logger.error(f"Erreur démarrage trading: {e}")
            return False
        finally:
            self.stop_live_trading()

    def stop_live_trading(self):
        """Arrêter le trading live"""
        self.logger.info("🛑 ARRÊT TRADING LIVE")

        self.is_running = False

        # Fermer MT5
        if MT5_AVAILABLE:
            mt5.shutdown()

        # Sauvegarder les résultats
        self.save_trading_session()

        self.logger.info("✅ Trading live arrêté")

    # LIGNE 790-826 : Sauvegarde synchrone bloquante
    def save_trading_session(self):
        """Sauvegarder la session de trading"""
        try:
            os.makedirs("artifacts/live_trading", exist_ok=True)

            session_data = {
                "timestamp": datetime.now().isoformat(),
                "symbols": self.symbols,
                "performance_metrics": self.performance_metrics,
                "trade_history": [
                    {
                        "timestamp": trade["timestamp"].isoformat(),
                        "symbol": trade.get("symbol", "N/A"),
                        "action": trade["action"],
                        "volume": trade["volume"],
                        "price": trade["price"],
                        "order_id": trade["order_id"],
                    }
                    for trade in self.trade_history
                ],
                "total_runtime": str(
                    datetime.now() - datetime.now()
                ),  # Approximation
            }

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"artifacts/live_trading/session_{timestamp}.json"

            with open(filename, "w") as f:
                json.dump(session_data, f, indent=2, default=str)

            self.logger.info(f"💾 Session sauvegardée: {filename}")

        except Exception as e:
            self.logger.error(f"Erreur sauvegarde session: {e}")


def main():
    """Test du système de trading live"""
    print("🚀 TEST SYSTÈME TRADING LIVE")
    print("=" * 30)

    try:
        # Créer le moteur de trading multi-actifs
        engine = LiveTradingEngine(
            symbol="EURUSD",
            lot_size=0.01
        )

        print("\n🎯 Démarrage en mode test (30 secondes)...")

        # Note: Threading serait nécessaire pour thread séparé
        # Simulation directe pour le test

        # Démarrage direct pour le test
        print("Démarrage direct du trading pour test...")

        # Simulation: test rapide au lieu de 30 secondes
        print("Test de connexion et initialisation...")
        engine.connect_mt5()
        engine.initialize_ai_systems()

        # Test de l'exécution d'un trade
        print("Test exécution d'un trade...")
        engine.execute_trade("buy", "EURUSD")

        # Arrêter
        engine.stop_live_trading()

        print("\n✅ Test du système de trading live terminé")

    except KeyboardInterrupt:
        print("\n🛑 Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
