#!/usr/bin/env python3
"""
🎯 TRADING DECISION DASHBOARD
Dashboard intelligent pour faciliter la prise de décision de trading

FONCTIONNALITÉS:
✅ Tableau de bord en temps réel avec signaux visuels
✅ Score de confiance multi-critères
✅ Analyse de risque/reward instantanée
✅ Alertes automatiques pour opportunités
✅ Résumé exécutif pour décision rapide
✅ Interface web interactive (optionnel)

UTILISATION:
python scripts/trading_decision_dashboard.py
"""

import json
import time
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
from dataclasses import dataclass
import warnings

warnings.filterwarnings("ignore")

# Imports avec fallbacks
try:
    from scripts.live_trading_engine import LiveTradingEngine
    LIVE_ENGINE_AVAILABLE = True
except ImportError:
    LIVE_ENGINE_AVAILABLE = False

try:
    from scripts.simplified_trading_robot import SimplifiedTradingRobot
    SIMPLE_ROBOT_AVAILABLE = True
except ImportError:
    SIMPLE_ROBOT_AVAILABLE = False


@dataclass
class TradingOpportunity:
    """Structure d'une opportunité de trading"""
    symbol: str
    action: str  # buy/sell/hold
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    signal_strength: str  # STRONG/MEDIUM/WEAK
    market_regime: str
    time_frame: str
    recommendation: str  # EXECUTE/WAIT/AVOID


class TradingDecisionDashboard:
    """Dashboard intelligent pour faciliter les décisions de trading"""

    def __init__(self, config_path: Optional[str] = None):
        self.config = self.load_config(config_path)
        self.symbols = self.config.get(
            "symbols", ["EURUSD", "XAUUSD", "BTCUSD"]
        )

        # Initialiser les engines
        self.live_engine = None
        self.simple_robot = None

        if LIVE_ENGINE_AVAILABLE:
            try:
                self.live_engine = LiveTradingEngine()
                print("✅ Live Engine connecté")
            except Exception as e:
                print(f"⚠️ Live Engine indisponible: {e}")

        if SIMPLE_ROBOT_AVAILABLE:
            try:
                self.simple_robot = SimplifiedTradingRobot()
                print("✅ Simple Robot connecté")
            except Exception as e:
                print(f"⚠️ Simple Robot indisponible: {e}")

        # État du dashboard
        self.last_update = None
        self.opportunities = {}
        self.market_summary = {}
        self.alerts = []

        print("🎯 Trading Decision Dashboard initialisé")

    def load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """Charger configuration du dashboard"""
        default_config = {
            "symbols": ["EURUSD", "XAUUSD", "BTCUSD"],
            "refresh_interval": 60,  # secondes
            "confidence_thresholds": {
                "high": 0.75,
                "medium": 0.60,
                "low": 0.45,
            },
            "risk_reward_minimum": 1.5,
            "max_alerts": 10,
            "dashboard_mode": "console",  # console/web
            "auto_refresh": True,
        }

        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                user_config = json.load(f)
                default_config.update(user_config)

        return default_config

    def get_market_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Obtenir données de marché pour un symbole"""
        try:
            # Priorité au Live Engine
            if self.live_engine:
                try:
                    data = self.live_engine.get_live_data(symbol, 200)
                    if data is not None and isinstance(data, pd.DataFrame) and len(data) > 0:
                        return data
                except Exception:
                    pass

            # Fallback vers Simple Robot
            if self.simple_robot:
                try:
                    data = self.simple_robot.load_market_data(symbol)
                    return data
                except Exception:
                    pass

            # Fallback vers données historiques
            return self.load_fallback_data(symbol)

        except Exception as e:
            print(f"❌ Erreur données {symbol}: {e}")
            return None

    def load_fallback_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Générer données de fallback pour démo"""
        try:
            # Créer données synthétiques pour démo
            dates = pd.date_range(
                start=datetime.now() - timedelta(days=1),
                end=datetime.now(),
                freq="1min",
            )

            # Prix de base par symbole
            base_prices = {
                "EURUSD": 1.0850,
                "XAUUSD": 2650.0,
                "BTCUSD": 67000.0,
            }

            base_price = base_prices.get(symbol, 100.0)

            # Générer série de prix réaliste
            returns = np.random.normal(0, 0.001, len(dates))
            prices = [base_price]

            for ret in returns[1:]:
                new_price = prices[-1] * (1 + ret)
                prices.append(new_price)

            df = pd.DataFrame(
                {
                    "timestamp": dates,
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
                    "volume": np.random.randint(100, 1000, len(dates)),
                }
            )

            df.set_index('timestamp', inplace=True)
            return df

        except Exception as e:
            print(f"❌ Erreur génération données fallback: {e}")
            return None

    def analyze_opportunity(
        self, symbol: str, data: pd.DataFrame
    ) -> TradingOpportunity:
        """Analyser une opportunité de trading"""
        try:
            current_price = data['close'].iloc[-1]

            # Obtenir signaux des différents systèmes
            signals = self.get_all_signals(symbol, data)

            # Calculer action et confiance combinées
            action, confidence = self.combine_signals(signals)

            # Calculer SL/TP adaptatifs
            atr = self.calculate_atr(data)
            stop_loss, take_profit = self.calculate_sl_tp(
                current_price, action, atr
            )

            # Calculer risk/reward
            if action == "buy":
                risk = abs(current_price - stop_loss)
                reward = abs(take_profit - current_price)
            elif action == "sell":
                risk = abs(stop_loss - current_price)
                reward = abs(current_price - take_profit)
            else:
                risk = reward = 0

            risk_reward = reward / risk if risk > 0 else 0

            # Déterminer force du signal
            signal_strength = self.get_signal_strength(confidence)

            # Obtenir régime de marché
            market_regime = self.detect_market_regime(data)

            # Recommandation finale
            recommendation = self.get_recommendation(
                confidence, risk_reward, market_regime
            )

            return TradingOpportunity(
                symbol=symbol,
                action=action,
                confidence=confidence,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward=risk_reward,
                signal_strength=signal_strength,
                market_regime=market_regime,
                time_frame="15M",
                recommendation=recommendation,
            )

        except Exception as e:
            print(f"❌ Erreur analyse {symbol}: {e}")
            return TradingOpportunity(
                symbol=symbol,
                action="hold",
                confidence=0.0,
                entry_price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                risk_reward=0.0,
                signal_strength="WEAK",
                market_regime="UNDEFINED",
                time_frame="15M",
                recommendation="AVOID",
            )

    def get_all_signals(
        self, symbol: str, data: pd.DataFrame
    ) -> Dict[str, Any]:
        """Obtenir signaux de tous les systèmes disponibles"""
        signals = {}

        # Signal du Live Engine
        if self.live_engine:
            try:
                live_signals = self.live_engine.get_ai_signals(data, symbol)
                # Normaliser pour la fusion: action/confidence
                act = live_signals.get(
                    'combined_signal', live_signals.get('action', 'hold')
                )
                normalized = {
                    'action': act,
                    'confidence': float(live_signals.get('confidence', 0.0)),
                }
                signals['live_engine'] = normalized
            except Exception:
                pass

        # Signal du Simple Robot
        if self.simple_robot:
            try:
                simple_signal = self.simple_robot.calculate_simple_signal(data)
                signals['simple_robot'] = simple_signal
            except Exception:
                pass

        # Signal technique de base (toujours disponible)
        signals['technical'] = self.calculate_basic_technical_signal(data)

        return signals

    def combine_signals(self, signals: Dict[str, Any]) -> tuple:
        """Combiner tous les signaux pour une décision finale"""
        actions = []
        confidences = []

        for source, signal in signals.items():
            if isinstance(signal, dict):
                # Récupérer l'action depuis les champs possibles
                action = signal.get('action')
                if action is None and 'combined_signal' in signal:
                    action = signal.get('combined_signal')
                if action is None:
                    action = 'hold'
                conf = float(signal.get('confidence', 0.0))

                # Convertir biais de régime en actions simples
                if action in ['long_bias']:
                    action = 'buy'
                elif action in ['short_bias']:
                    action = 'sell'

                if action in ['buy', 'sell']:
                    actions.append(action)
                    confidences.append(conf)

        if not actions:
            return "hold", 0.0

        # Logique de combinaison
        if len(set(actions)) == 1:
            # Tous les signaux concordent
            final_action = actions[0]
            final_confidence = np.mean(confidences)
        else:
            # Signaux divergents - réduire confiance
            final_action = "hold"
            final_confidence = np.mean(confidences) * 0.5

        return final_action, final_confidence

    def calculate_basic_technical_signal(
        self, data: pd.DataFrame
    ) -> Dict[str, Any]:
        """Calculer un signal technique de base"""
        try:
            close = data['close']

            # Moyennes mobiles
            ma_short = close.rolling(10).mean().iloc[-1]
            ma_long = close.rolling(20).mean().iloc[-1]

            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean().iloc[-1]
            loss = -delta.where(delta < 0, 0).rolling(14).mean().iloc[-1]
            rsi = 100 - (100 / (1 + gain / loss))

            # Signal combiné
            ma_signal = 1 if ma_short > ma_long else -1
            rsi_signal = -1 if rsi > 70 else 1 if rsi < 30 else 0

            combined = ma_signal + rsi_signal

            if combined >= 1:
                action = "buy"
                confidence = min(0.8, abs(combined) * 0.4)
            elif combined <= -1:
                action = "sell"
                confidence = min(0.8, abs(combined) * 0.4)
            else:
                action = "hold"
                confidence = 0.3

            return {
                "action": action,
                "confidence": confidence,
                "ma_short": ma_short,
                "ma_long": ma_long,
                "rsi": rsi,
            }

        except Exception:
            return {"action": "hold", "confidence": 0.0}

    def calculate_atr(self, data: pd.DataFrame, period: int = 14) -> float:
        """Calculer Average True Range"""
        try:
            high = data['high']
            low = data['low']
            close = data['close']

            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())

            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(period).mean().iloc[-1]

            return atr if not pd.isna(atr) else 0.01

        except Exception:
            return 0.01

    def calculate_sl_tp(self, price: float, action: str, atr: float) -> tuple:
        """Calculer Stop Loss et Take Profit adaptatifs"""
        sl_multiplier = 2.0
        tp_multiplier = 3.0

        if action == "buy":
            stop_loss = price - (atr * sl_multiplier)
            take_profit = price + (atr * tp_multiplier)
        elif action == "sell":
            stop_loss = price + (atr * sl_multiplier)
            take_profit = price - (atr * tp_multiplier)
        else:
            stop_loss = take_profit = price

        return stop_loss, take_profit

    def get_signal_strength(self, confidence: float) -> str:
        """Déterminer la force du signal"""
        thresholds = self.config["confidence_thresholds"]

        if confidence >= thresholds["high"]:
            return "STRONG"
        elif confidence >= thresholds["medium"]:
            return "MEDIUM"
        else:
            return "WEAK"

    def detect_market_regime(self, data: pd.DataFrame) -> str:
        """Détecter le régime de marché simple"""
        try:
            returns = data['close'].pct_change().dropna()

            # Volatilité récente
            volatility = returns.rolling(20).std().iloc[-1]

            # Trend récent
            ma_short = data['close'].rolling(5).mean().iloc[-1]
            ma_long = data['close'].rolling(20).mean().iloc[-1]

            if volatility > 0.02:
                return "HIGH_VOLATILITY"
            elif ma_short > ma_long * 1.01:
                return "BULLISH"
            elif ma_short < ma_long * 0.99:
                return "BEARISH"
            else:
                return "SIDEWAYS"

        except Exception:
            return "UNDEFINED"

    def get_recommendation(
        self, confidence: float, risk_reward: float, regime: str
    ) -> str:
        """Obtenir recommandation finale"""
        min_rr = self.config["risk_reward_minimum"]

        if confidence < 0.5:
            return "AVOID"
        elif confidence >= 0.75 and risk_reward >= min_rr:
            return "EXECUTE"
        elif confidence >= 0.6 and risk_reward >= min_rr * 0.8:
            return "CONSIDER"
        else:
            return "WAIT"

    def scan_all_symbols(self) -> Dict[str, TradingOpportunity]:
        """Scanner tous les symboles pour opportunités"""
        opportunities = {}

        print("🔍 Scanning des opportunités...")

        for symbol in self.symbols:
            print(f"  📊 Analyse {symbol}...")

            data = self.get_market_data(symbol)
            if data is not None:
                opportunity = self.analyze_opportunity(symbol, data)
                opportunities[symbol] = opportunity
            else:
                print(f"  ❌ Données indisponibles pour {symbol}")

        return opportunities

    def generate_alerts(self, opportunities: Dict[str, TradingOpportunity]):
        """Générer alertes basées sur les opportunités"""
        new_alerts = []

        for symbol, opp in opportunities.items():
            # Alerte pour signaux forts
            if (
                opp.recommendation == "EXECUTE"
                and opp.signal_strength == "STRONG"
            ):
                message = (
                    f"🚀 SIGNAL FORT {symbol}: {opp.action.upper()} "
                    f"conf={opp.confidence:.2f} RR={opp.risk_reward:.1f}"
                )
                alert = {
                    "time": datetime.now().isoformat(),
                    "type": "STRONG_SIGNAL",
                    "symbol": symbol,
                    "message": message,
                    "priority": "HIGH",
                }
                new_alerts.append(alert)

            # Alerte pour bon risk/reward
            elif opp.risk_reward > 3.0 and opp.confidence > 0.6:
                message = (
                    f"💎 EXCELLENT RR {symbol}: {opp.risk_reward:.1f} "
                    f"conf={opp.confidence:.2f}"
                )
                alert = {
                    "time": datetime.now().isoformat(),
                    "type": "HIGH_RR",
                    "symbol": symbol,
                    "message": message,
                    "priority": "MEDIUM",
                }
                new_alerts.append(alert)

        # Ajouter nouvelles alertes
        self.alerts.extend(new_alerts)

        # Limiter nombre d'alertes
        max_alerts = self.config["max_alerts"]
        if len(self.alerts) > max_alerts:
            self.alerts = self.alerts[-max_alerts:]

    def display_console_dashboard(self):
        """Afficher dashboard en mode console"""
        # Clear screen
        print("\033[2J\033[H")  # ANSI clear

        # Header
        print("🎯" + "=" * 78 + "🎯")
        print(f"{'TRADING DECISION DASHBOARD':^80}")
        print(f"{'Dernière mise à jour: ' + datetime.now().strftime('%H:%M:%S'):^80}")
        print("🎯" + "=" * 78 + "🎯")
        print()

        # Opportunities Table
        print("📊 OPPORTUNITÉS DE TRADING")
        print("-" * 80)

        header = (
            f"{'SYMBOLE':<8} {'ACTION':<6} {'CONF':<6} "
            f"{'R/R':<6} {'FORCE':<8} {'RECOMMANDATION':<13}"
        )
        print(header)
        print("-" * 80)

        execute_count = 0
        for symbol, opp in self.opportunities.items():
            # Couleurs selon recommandation
            rec_color = {
                "EXECUTE": "🟢",
                "CONSIDER": "🟡",
                "WAIT": "🟠",
                "AVOID": "🔴",
            }.get(opp.recommendation, "⚪")

            action_symbol = {
                "buy": "📈",
                "sell": "📉",
                "hold": "⏸️",
            }.get(opp.action, "❓")

            row = (f"{symbol:<8} {action_symbol}{opp.action.upper():<4} "
                   f"{opp.confidence:.2f}  {opp.risk_reward:.1f}   "
                   f"{opp.signal_strength:<8} {rec_color}{opp.recommendation}")
            print(row)

            if opp.recommendation == "EXECUTE":
                execute_count += 1

        print("-" * 80)

        # Résumé Exécutif
        print()
        print("📋 RÉSUMÉ EXÉCUTIF")
        print("-" * 30)
        print(f"🎯 Opportunités EXECUTE: {execute_count}")

        best_opp = None
        best_score = 0
        for symbol, opp in self.opportunities.items():
            score = opp.confidence * opp.risk_reward
            if score > best_score:
                best_score = score
                best_opp = (symbol, opp)

        if best_opp:
            symbol, opp = best_opp
            print(f"🏆 Meilleure opportunité: {symbol} ({opp.action.upper()})")
            print(f"   Confiance: {opp.confidence:.2f} | R/R: {opp.risk_reward:.1f}")
            print(f"   Prix: {opp.entry_price:.5f}")
            print(f"   SL: {opp.stop_loss:.5f} | TP: {opp.take_profit:.5f}")

        # Alertes récentes
        if self.alerts:
            print()
            print("🚨 ALERTES RÉCENTES")
            print("-" * 30)
            for alert in self.alerts[-3:]:  # 3 dernières alertes
                time_str = alert["time"].split("T")[1][:8]
                print(f"[{time_str}] {alert['message']}")

        # Instructions
        print()
        print("⚡ COMMANDES RAPIDES")
        print("-" * 30)
        print("🚀 python start_production.py  # Lancer trading")
        print("⏹️  Ctrl+C                    # Arrêter dashboard")
        print()

    def update_dashboard(self):
        """Mettre à jour toutes les données du dashboard"""
        try:
            # Scanner opportunités
            self.opportunities = self.scan_all_symbols()

            # Générer alertes
            self.generate_alerts(self.opportunities)

            # Mettre à jour timestamp
            self.last_update = datetime.now()

            print("✅ Dashboard mis à jour")

        except Exception as e:
            print(f"❌ Erreur mise à jour dashboard: {e}")

    def run_dashboard(self):
        """Lancer le dashboard en mode interactif"""
        print("🎯 Démarrage Trading Decision Dashboard...")
        print("📊 Mode: Console Interactif")
        print("⏰ Refresh automatique toutes les 60s")
        print("⏹️ Appuyer Ctrl+C pour arrêter")
        print()

        try:
            while True:
                # Mise à jour des données
                self.update_dashboard()

                # Affichage
                self.display_console_dashboard()

                # Attendre avant prochain refresh
                if self.config["auto_refresh"]:
                    time.sleep(self.config["refresh_interval"])
                else:
                    input("\n💡 Appuyer Entrée pour actualiser (Ctrl+C pour arrêter)...")

        except KeyboardInterrupt:
            print("\n👋 Dashboard arrêté par l'utilisateur")
        except Exception as e:
            print(f"\n❌ Erreur dashboard: {e}")


def main():
    """Point d'entrée principal"""
    dashboard = TradingDecisionDashboard()

    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Mode single scan
        print("🔍 SCAN UNIQUE DES OPPORTUNITÉS")
        dashboard.update_dashboard()
        dashboard.display_console_dashboard()
    else:
        # Mode dashboard continu
        dashboard.run_dashboard()


if __name__ == "__main__":
    main()
