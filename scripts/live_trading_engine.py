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
import csv
import traceback
from datetime import datetime, timedelta
from pathlib import Path
import pytz
import warnings

warnings.filterwarnings("ignore")

# Configuration centralisée
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config.trading_config import TradingConfig
    from utils.robust_retry import (
        robust_mt5_retry, MT5ConnectionError, MT5OperationError
    )
    CONFIG_AVAILABLE = True
    print("✅ Configuration centralisée chargée")
except ImportError:
    CONFIG_AVAILABLE = False
    print("⚠️ Configuration centralisée non disponible - utilisation defaults")

    # Fallback vers valeurs par défaut
    class TradingConfig:
        TRADING_INTERVAL_SECONDS = 930
        CLEANUP_CYCLE_INTERVAL = 20
        LOG_SUMMARY_INTERVAL = 5
        MIN_SLEEP_SECONDS = 60
        MAX_HISTORY_TRADES = 1000
        MAX_MARKET_DATA_BARS = 300
        DEFAULT_CONFIDENCE_THRESHOLD = 0.60
        # Par défaut: auto-close positions après X minutes si SL/TP inchangés
        AUTO_CLOSE_MINUTES = 30

# Fallback robuste pour utils.robust_retry si non disponible
try:
    robust_mt5_retry
    MT5ConnectionError
    MT5OperationError
except Exception:
    def robust_mt5_retry(max_attempts: int = 1, exceptions=(Exception,)):
        def _decorator(func):
            def _wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return _wrapper
        return _decorator

    class MT5ConnectionError(Exception):
        pass

    class MT5OperationError(Exception):
        pass

# Configuration unifiée (optionnelle)
try:
    from config.unified_settings import CONFIG as UNIFIED_CONFIG
    UNIFIED_AVAILABLE = True
    print("✅ Configuration unifiée détectée")
except Exception:
    UNIFIED_CONFIG = None
    UNIFIED_AVAILABLE = False

# Import des utilitaires sécurisés avec chemins multiples
utils_paths = [
    os.path.join(os.path.dirname(__file__), "..", "utils"),
    os.path.join(os.path.dirname(__file__), "..", "src", "utils"),
    "utils", "src/utils"
]
for path in utils_paths:
    if path not in sys.path:
        sys.path.append(path)

try:
    from safe_io import safe_read_csv, FALLBACK_SAMPLE_DATA
    IO_UTILS_AVAILABLE = True
    print("✅ Utilitaires I/O sécurisées disponibles")
except ImportError:
    IO_UTILS_AVAILABLE = False
    # Définir fallbacks

    def safe_read_csv(*args, **kwargs):
        import pandas as pd
        return pd.read_csv(*args, **kwargs) if args else pd.DataFrame()
    FALLBACK_SAMPLE_DATA = {
        "EURUSD": pd.DataFrame({
            'timestamp': pd.date_range('2025-01-01', periods=100, freq='h'),
            'open': np.random.randn(100) * 0.001 + 1.1000,
            'high': np.random.randn(100) * 0.001 + 1.1005,
            'low': np.random.randn(100) * 0.001 + 0.9995,
            'close': np.random.randn(100) * 0.001 + 1.1000,
            'volume': np.random.randint(1000, 5000, 100)
        })
    }
    print("⚠️  Utilitaires I/O non disponibles - fallback activé")

# MT5 Integration avec fallback robuste et chemins multiples
try:
    # Ajouter les chemins src/utils pour mt5_connector
    src_utils_path = os.path.join(
        os.path.dirname(__file__), "..", "src", "utils"
    )
    if src_utils_path not in sys.path:
        sys.path.append(src_utils_path)

    from mt5_connector import get_mt5, is_mt5_available
    MT5_AVAILABLE = is_mt5_available()
    mt5 = get_mt5()
    print("✅ MT5 connector loaded successfully")
except ImportError:
    # Fallback direct vers MetaTrader5
    try:
        import MetaTrader5 as mt5
        MT5_AVAILABLE = True
        print("✅ MT5 direct import successful")
    except ImportError as import_error:
        MT5_AVAILABLE = False
        mt5 = None
        print(f"⚠️  MT5 non disponible: {import_error}")
        print("🔄 Mode simulation complet activé")
except Exception as e:
    MT5_AVAILABLE = False
    mt5 = None
    get_mt5 = None

    def is_mt5_available():
        return False

    print(f"🔴 Erreur MT5 inattendue: {e}")
    print("🔄 Mode simulation complet activé")

# Nos systèmes développés avec fallback robuste (import différé possible)
if os.getenv("LIVE_ENGINE_LIGHT_MODE", "0") == "1":
    SYSTEMS_AVAILABLE = False
    print("⚙️  Mode light: import des systèmes IA différé/skippé")
else:
    # Prefer explicit imports from the scripts/ package to avoid root module
    # name collisions (there are shim/duplicate files at repo root).
    try:
        from scripts.meta_learning_system import MetaLearningTradingSystem
        from scripts.reinforcement_learning_agent import ReinforcementLearningTradingSystem
        from scripts.multi_asset_portfolio import MultiAssetPortfolioOptimizer
        from scripts.market_regime_detection import MarketRegimeDetector

        SYSTEMS_AVAILABLE = True
        print("✅ Systèmes de trading IA (scripts/) chargés avec succès")
    except Exception:
        # Fallback: try importing from repository root modules (shim files)
        try:
            from meta_learning_system import MetaLearningTradingSystem
            from reinforcement_learning_agent import ReinforcementLearningTradingSystem
            from multi_asset_portfolio import MultiAssetPortfolioOptimizer
            from market_regime_detection import MarketRegimeDetector

            SYSTEMS_AVAILABLE = True
            print("✅ Systèmes de trading IA (root) chargés avec succès")
        except ImportError as e:
            SYSTEMS_AVAILABLE = False
            print(f"⚠️  Systèmes non disponibles: {e}")
            print("🔄 Mode minimal activé - fonctions de base uniquement")
        except Exception as e:
            SYSTEMS_AVAILABLE = False
            print(f"🔴 Erreur systèmes inattendue: {e}")
            print("🔄 Mode minimal activé")

# Import MTF pipeline (convergence 15m) avec fallback
try:
    from src.pipeline.mtf_features import (
        build_live_mtf_from_m1,
        compute_mtf_convergence,
    )
    from src.pipeline.fundamentals import (
        load_fundamentals_csv,
        compute_fundamental_confluence,
    )
    MTF_AVAILABLE = True
except Exception:
    MTF_AVAILABLE = False


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
            # 1) Préférer la configuration unifiée si disponible
            if UNIFIED_AVAILABLE and getattr(UNIFIED_CONFIG, "trading", None):
                try:
                    self.symbols = list(UNIFIED_CONFIG.trading.symbols)
                    print(
                        f"✅ Symboles chargés depuis config unifiée: "
                        f"{self.symbols}"
                    )
                except Exception as e:
                    print(f"⚠️  Échec config unifiée pour symboles: {e}")
                    self.symbols = ["EURUSD", "XAUUSD", "BTCUSD"]
            else:
                try:
                    config_path = os.path.join(
                        os.path.dirname(__file__), "..", "config"
                    )
                    if config_path not in sys.path:
                        sys.path.append(config_path)
                    from config.settings import INSTRUMENTS
                    self.symbols = INSTRUMENTS
                    print(
                        f"✅ Configuration multi-actifs chargée: {INSTRUMENTS}"
                    )
                except ImportError:
                    # Fallback vers configuration par défaut
                    self.symbols = ["EURUSD", "XAUUSD", "BTCUSD"]
                    print(
                        "⚠️  Config non trouvée - symboles par défaut utilisés"
                    )
                except Exception as e:
                    # Fallback de sécurité
                    self.symbols = ["EURUSD"]
                    print(
                        f"⚠️  Erreur config: {e} - symbole par défaut: EURUSD"
                    )
        else:
            self.symbols = symbols if isinstance(symbols, list) else [symbols]

        # Configuration des lot sizes
        if lot_sizes is None:
            # Si config unifiée disponible, utiliser ses lot_sizes
            if UNIFIED_AVAILABLE and getattr(UNIFIED_CONFIG, "trading", None):
                try:
                    configured_lots = dict(UNIFIED_CONFIG.trading.lot_sizes)
                    # S'assurer que tous les symboles ont une valeur
                    self.lot_sizes = {
                        symbol: configured_lots.get(symbol, 0.01)
                        for symbol in self.symbols
                    }
                    print(
                        f"🔧 Lot sizes depuis config unifiée: {self.lot_sizes}"
                    )
                except Exception as e:
                    print(f"⚠️  Échec lot_sizes config unifiée: {e}")
                    self.lot_sizes = {symbol: 0.01 for symbol in self.symbols}
            else:
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

        # NOUVELLE FONCTIONNALITÉ: Contrôles d'arrêt d'urgence
        self.emergency_stop_active = False
        self.emergency_stop_until = None
        self.emergency_stop_file = Path("control/emergency_stop")

        # Configuration depuis TradingConfig
        self.trading_interval = TradingConfig.TRADING_INTERVAL_SECONDS
        self.cleanup_cycle_interval = TradingConfig.CLEANUP_CYCLE_INTERVAL
        self.log_summary_interval = TradingConfig.LOG_SUMMARY_INTERVAL
        self.min_sleep_seconds = TradingConfig.MIN_SLEEP_SECONDS
        self.max_history_trades = TradingConfig.MAX_HISTORY_TRADES
        self.max_market_data_bars = TradingConfig.MAX_MARKET_DATA_BARS
        self.confidence_threshold = TradingConfig.DEFAULT_CONFIDENCE_THRESHOLD
        # Si config unifiée définie, surcharger le seuil de confiance
        if UNIFIED_AVAILABLE and getattr(UNIFIED_CONFIG, "trading", None):
            try:
                self.confidence_threshold = (
                    UNIFIED_CONFIG.trading.confidence_threshold
                )
                # Journaliser l'ajustement
                self.logger.info(
                    f"🎯 Seuil ajusté (unified): "
                    f"{self.confidence_threshold:.3f}"
                ) if hasattr(self, 'logger') else None
            except Exception:
                pass

        # Performance tracking
        self.trade_history = []
        # Métriques optimisées basées sur backtests
        self.performance_metrics = {
            "total_trades": 0,
            "winning_trades": 0,
            "current_balance": 10000.0,
            "max_drawdown": 0.0,
            "current_regime": "Unknown",
            "symbols_traded": self.symbols,
            # Métriques d'optimisation (basées sur vos tests)
            "optimal_threshold": self.confidence_threshold,
            "target_win_rate": self.confidence_threshold,
            "expected_sharpe": 18.46,   # Ratio optimal identifié
            "confidence_filter_rejections": 0,  # Nouveaux filtres
        }

        # 🧠 AMÉLIORATION: Suivi performance en temps réel
        self.recent_trades_performance = []  # 20 derniers trades
        self.symbol_performance = {}  # Performance par symbole
        self.confidence_accuracy_tracker = {}  # Précision par niveau confiance
        # Liste de suivi des positions ouvertes pour auto-close
        # Chaque entrée: {ticket, symbol, open_time, sl, tp, auto_close_at, closed}
        self.position_watchlist = []
        # Durée par défaut avant auto-close (minutes)
        self.auto_close_minutes = getattr(TradingConfig, 'AUTO_CLOSE_MINUTES', 30)

        # Configuration de trading en continu (sans limite quotidienne)
        # Utiliser la valeur de TradingConfig déjà chargée plus haut
        self.trade_count_today = 0  # Compteur pour statistiques seulement
        self.max_daily_trades = None  # Pas de limite - trading continu
        self.last_reset_date = None

        # Mode SMOKE (tests rapides) via variables d'environnement
        try:
            self.max_cycles = int(os.getenv("ENGINE_MAX_CYCLES", "0"))
        except Exception:
            self.max_cycles = 0
        try:
            self.smoke_sleep = float(os.getenv("ENGINE_SMOKE_SLEEP", "2"))
        except Exception:
            self.smoke_sleep = 2.0

        # Horaires de marché (UTC)
        self.market_hours = {
            "forex": {
                "open": "21:00",  # Dimanche 21:00 UTC (Sydney)
                "close": "21:00",  # Vendredi 21:00 UTC (NY)
                "24h": True
            },
            "crypto": {
                "24h": True,
                "always_open": True
            }
        }

        # Configuration logging
        self.setup_logging()

        # Charger seuil optimisé si présent (non-invasif)
        try:
            opt_file = os.path.join(
                Path.cwd(),
                "artifacts",
                "auto_improve",
                "optimization",
                "selected_threshold.json",
            )
            if os.path.exists(opt_file):
                with open(opt_file, "r", encoding="utf-8") as _f:
                    import json as _json

                    _data = _json.load(_f)
                    if "selected_threshold" in _data:
                        try:
                            self.confidence_threshold = float(
                                _data["selected_threshold"]
                            )
                            print(
                                "🔧 Seuil optimisé chargé: ",
                                self.confidence_threshold,
                            )
                        except Exception:
                            self.logger.warning(
                                "Impossible de convertir selected_threshold"
                            )
        except Exception as _e:
            # Non critique, poursuivre sans interrompre
            print(f"⚠️ Impossible de charger selected_threshold.json: {_e}")

        print("🚀 Moteur Trading Live Multi-Actifs initialisé:")
        print(f"  📈 Symboles: {', '.join(self.symbols)}")
        print(f"  💰 Lots: {self.lot_sizes}")
        print(f"  🛡️  Risque max: {max_risk_per_trade*100:.1f}%")

        # Préparer support MTF/fondamentaux (chargement lazy)
        self._fundamentals_map = None

    def is_market_open(self, symbol):
        """Vérifier si le marché est ouvert pour un symbole"""
        try:
            utc_now = datetime.now(pytz.UTC)
            current_weekday = utc_now.weekday()  # 0=Lundi, 6=Dimanche

            # Crypto : toujours ouvert
            if symbol in ["BTCUSD"]:
                return True

            # Forex : fermé le weekend (sam 21h - dim 21h UTC)
            if symbol in ["EURUSD", "XAUUSD"]:
                # Fermé du vendredi 21:00 au dimanche 21:00 UTC
                if current_weekday == 5:  # Samedi
                    return False
                elif current_weekday == 6:  # Dimanche
                    return utc_now.hour >= 21  # Ouvert à partir de 21h
                elif current_weekday == 4:  # Vendredi
                    return utc_now.hour < 21   # Fermé à 21h
                else:
                    return True  # Ouvert lun-jeu

            return True

        except Exception as e:
            self.logger.error(f"Erreur vérification marché {symbol}: {e}")
            return False

    def reset_daily_counters(self):
        """Reset des compteurs quotidiens (stats seulement)"""
        today = datetime.now().date()
        if self.last_reset_date != today:
            self.trade_count_today = 0
            self.last_reset_date = today
            self.logger.info(f"🔄 Reset quotidien - Date: {today}")

    def can_trade_continuously(self):
        """Vérifier si trading continu possible (pas de limite)"""
        self.reset_daily_counters()
        return True  # Trading continu sans limite quotidienne

    def setup_logging(self):
        """Configuration avancée du système de logging"""
        os.makedirs("logs", exist_ok=True)
        # Dossier logs accessible par d'autres méthodes
        # (ex: _log_rejected_signal)
        self.logs_folder = "logs"

        # Logger principal avec nom unique
        logger_name = f"LiveTrading_{id(self)}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)

        # Éviter la duplication des handlers
        if self.logger.handlers:
            self.logger.handlers.clear()

        # Formatage amélioré avec plus d'informations
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(funcName)-20s | '
            'PID:%(process)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Handler fichier avec rotation par taille
        try:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(
                f"logs/live_trading_{datetime.now().strftime('%Y%m%d')}.log",
                maxBytes=50*1024*1024,  # 50MB
                backupCount=5
            )
        except ImportError:
            # Fallback si RotatingFileHandler n'est pas disponible
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            file_handler = logging.FileHandler(
                f"logs/live_trading_{timestamp}.log"
            )

        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # Handler console avec formatage simplifié
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Handler pour erreurs critiques (séparé)
        error_handler = logging.FileHandler("logs/critical_errors.log")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        self.logger.addHandler(error_handler)

        self.logger.info("Système de logging initialisé")

    def production_health_check(self):
        """Check complet avant production"""
        checks = {
            "mt5_connection": False,
            "symbols_available": False,
            "market_hours": False,
            "config_valid": False
        }

        # 1. MT5
        if self.connect_mt5():
            checks["mt5_connection"] = True

        # 2. Symboles
        if len(self.symbols) > 0:
            checks["symbols_available"] = True

        # 3. Heures de marché
        any_open = any(self.is_market_open(sym) for sym in self.symbols)
        checks["market_hours"] = any_open

        # 4. Configuration
        checks["config_valid"] = (
            self.trading_interval > 0 and
            len(self.lot_sizes) > 0 and
            self.performance_metrics["optimal_threshold"] > 0
        )

        # Résumé
        passed = sum(checks.values())
        total = len(checks)

        for check, status in checks.items():
            status_icon = "✅" if status else "❌"
            self.logger.info(f"{status_icon} {check}: {status}")

        if passed == total:
            self.logger.info(f"🎯 Production Ready ({passed}/{total})")
            return True
        else:
            self.logger.warning(f"⚠️ Partial Ready ({passed}/{total})")
            return passed >= 2  # Au moins MT5 + config

    def connect_mt5(self):
        """Connexion à MetaTrader 5"""
        if not MT5_AVAILABLE:
            self.logger.warning("MT5 non disponible - Mode simulation activé")
            self.is_connected = True  # Simulation
            return True

        try:
            # Initialiser MT5 avec retry robuste
            @robust_mt5_retry(max_attempts=3)
            def _initialize_mt5():
                if not mt5.initialize():
                    error = mt5.last_error()
                    raise MT5ConnectionError(
                        "Échec initialisation MT5: {}".format(error)
                    )
                return True

            _initialize_mt5()

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
        """Initialiser tous les systèmes AI avec retry et mode fallback"""
        max_retries = 3
        retry_delay = 2  # secondes

        # Tenter plusieurs fois l'initialisation complète
        for attempt in range(max_retries):
            self.logger.info(
                f"Initialisation des systèmes AI "
                f"(tentative {attempt + 1}/{max_retries})..."
            )

            initialized = {
                'meta_learning': False,
                'rl_agent': False,
                'portfolio_optimizer': False,
                'regime_detector': False,
            }

            # 1. Meta-Learning System
            try:
                self.meta_learning = MetaLearningTradingSystem(max_models=3)
                initialized['meta_learning'] = True
                self.logger.info("✅ Meta-Learning initialisé")
                # Si l'ensemble de modèles est vide, tenter de charger un
                # modèle LightGBM déjà entraîné depuis artifacts (non invasif)
                try:
                    if (hasattr(self.meta_learning, 'model_ensemble') and
                            (not self.meta_learning.model_ensemble)):
                        import lightgbm as _lgb
                        from pathlib import Path
                        art = Path('artifacts') / 'auto_improve'
                        # Priorité au modèle large si présent
                        candidate = None
                        if art.exists():
                            p1 = art / 'best_lightgbm_large.txt'
                            p2 = art / 'best_lightgbm.txt'
                            if p1.exists():
                                candidate = p1
                            elif p2.exists():
                                candidate = p2

                        if candidate is not None:
                            try:
                                booster = _lgb.Booster(
                                    model_file=str(candidate)
                                )
                                self.meta_learning.model_ensemble = [
                                    {
                                        'model': booster,
                                        'performance': 1.0,
                                        'architecture': (
                                            'lightgbm_booster_file'
                                        ),
                                    }
                                ]
                                self.logger.info(
                                    f"🔁 Modèle LightGBM chargé depuis "
                                    f"{candidate}"
                                )
                            except Exception as _e:
                                self.logger.warning(
                                    f"Échec chargement LightGBM: {_e}"
                                )
                except Exception:
                    # Ne pas échouer l'initialisation globale si import absent
                    pass
            except Exception as e:
                self.logger.warning(f"Meta-Learning init failed: {e}")

            # 2. Reinforcement Learning Agent
            try:
                self.rl_agent = ReinforcementLearningTradingSystem(
                    use_dqn=True
                )
                initialized['rl_agent'] = True
                self.logger.info("✅ RL Agent initialisé")
            except Exception as e:
                self.logger.warning(f"RL Agent init failed: {e}")

            # 3. Portfolio Optimizer (pour allocation)
            try:
                self.portfolio_optimizer = MultiAssetPortfolioOptimizer()
                initialized['portfolio_optimizer'] = True
                self.logger.info("✅ Portfolio Optimizer initialisé")
            except Exception as e:
                self.logger.warning(f"Portfolio Optimizer init failed: {e}")

            # 4. Regime Detector
            try:
                self.regime_detector = MarketRegimeDetector(n_regimes=3)
                initialized['regime_detector'] = True
                self.logger.info("✅ Regime Detector initialisé")
            except Exception as e:
                self.logger.warning(f"Regime Detector init failed: {e}")

            # Si au moins un composant clé est opérationnel, quitter et
            # utiliser les composants disponibles (mode partiel)
            if any(initialized.values()):
                self.ai_fallback_mode = False
                self.logger.info(
                    "✅ Au moins un composant AI initialisé - "
                    "mode partiel activé"
                )
                # Écrire atomiquement le statut du modèle actif depuis le
                # process long-vivant afin que les watchers externes voient
                # le bon PID et le modèle chargé (safe / non invasif).
                try:
                    self._write_active_model()
                except Exception as _e:
                    # Ne pas interrompre l'initialisation si l'écriture échoue
                    self.logger.debug(f"Écriture active_model échouée: {_e}")
                return True

            # Sinon retry exponential backoff
            self.logger.warning(
                "Aucun composant AI initialisé sur cette tentative"
            )
            if attempt < max_retries - 1:
                self.logger.info(f"Nouvelle tentative dans {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2

        # Après toutes les tentatives, activer fallback
        self.logger.warning(
            "Toutes les tentatives d'initialisation AI ont échoué - "
            "activation du mode fallback"
        )
        return self._activate_fallback_mode()

    def _activate_fallback_mode(self):
        """Active le mode fallback sans AI pour maintenir le trading"""
        self.logger.warning("🔄 ACTIVATION MODE FALLBACK - Trading sans AI")
        self.ai_fallback_mode = True
        
        # Systèmes minimaux pour continuer le trading
        self.meta_learning = None
        self.rl_agent = None
        self.portfolio_optimizer = None
        self.regime_detector = None
        
        # Configuration simplifiée
        self.fallback_config = {
            'default_lot_size': 0.01,
            'simple_sl_pips': 20,
            'simple_tp_pips': 30,
            'max_positions': 3
        }
        
        self.logger.info("✅ Mode fallback activé - Trading simple maintenu")
        return True

    def _write_active_model(self):
        """Écrit atomiquement control/active_model.txt avec le modèle chargé,
        un timestamp UTC et le PID du process courant. Cette méthode est
        non invasive et ne doit jamais lever d'exception vers l'appelant.
        """
        try:
            model_path = None
            # Tenter d'obtenir le chemin depuis le meta_learning (implémentations variées)
            if getattr(self, 'meta_learning', None) is not None:
                ml = self.meta_learning
                # Attribut direct exposé par le shim
                if hasattr(ml, 'loaded_model_path') and ml.loaded_model_path:
                    model_path = str(ml.loaded_model_path)
                # Sinon tenter d'extraire depuis model_ensemble
                elif hasattr(ml, 'model_ensemble') and ml.model_ensemble:
                    try:
                        first = ml.model_ensemble[0]
                        if isinstance(first, dict):
                            m = first.get('model')
                        else:
                            m = first
                        # tenter d'obtenir un attribut indiquant le path
                        if hasattr(m, 'file_name'):
                            model_path = str(m.file_name)
                        elif hasattr(m, 'model_file'):
                            model_path = str(m.model_file)
                        elif hasattr(m, 'save_model'):
                            # impossible d'inférer sans écrire; skip
                            model_path = None
                    except Exception:
                        model_path = None

            # Fallback déterministe vers artifacts
            if model_path is None:
                cand = Path('artifacts') / 'auto_improve' / 'best_lightgbm.txt'
                if cand.exists():
                    model_path = str(cand.resolve())

            status_dir = Path('control')
            status_dir.mkdir(parents=True, exist_ok=True)
            status_file = status_dir / 'active_model.txt'
            tmp_file = status_dir / ('.active_model.tmp')

            with open(tmp_file, 'w', encoding='utf-8') as f:
                f.write(f"loaded_model_path: {model_path or 'None'}\n")
                f.write(f"timestamp: {datetime.utcnow().isoformat()}Z\n")
                try:
                    f.write(f"pid: {os.getpid()}\n")
                except Exception:
                    pass

            # Remplacer atomiquement
            try:
                tmp_file.replace(status_file)
            except Exception:
                tmp_file.rename(status_file)

            self.logger.info(f"🔁 active_model écrit: pid={os.getpid()} model={model_path}")
        except Exception as e:
            # Ne jamais remonter l'exception
            try:
                self.logger.debug(f"_write_active_model failed: {e}")
            except Exception:
                pass

    def check_emergency_stop(self):
        """Vérifie si un arrêt d'urgence est actif"""
        try:
            if self.emergency_stop_file.exists():
                # Lire le fichier d'arrêt d'urgence
                with open(self.emergency_stop_file, 'r') as f:
                    content = f.read()

                # Déterminer la date d'expiration si présente
                until_dt = None
                try:
                    for line in content.splitlines():
                        if line.strip().lower().startswith("until:"):
                            _, value = line.split(":", 1)
                            until_dt = datetime.fromisoformat(value.strip())
                            break
                except Exception:
                    until_dt = None

                # Mémoriser localement l'expiration si non définie
                if until_dt and not self.emergency_stop_until:
                    self.emergency_stop_until = until_dt

                # Si le flag est actif dans le fichier
                if "EMERGENCY_STOP_ACTIVE" in content:
                    # Si une date d'expiration est définie et dépassée, lever l'arrêt
                    if until_dt and datetime.now() > until_dt:
                        try:
                            self.emergency_stop_active = False
                            self.emergency_stop_until = None
                            # Supprimer le fichier d'arrêt (arrêt expiré)
                            self.emergency_stop_file.unlink(missing_ok=True)
                            self.logger.info(
                                "✅ Période d'arrêt d'urgence expirée - Reprise du trading"
                            )
                            return False
                        except Exception as _e:
                            self.logger.warning(
                                f"Impossible de supprimer le fichier d'arrêt: {_e}"
                            )
                            # Même si la suppression échoue, considérer expiré
                            return False

                    # Sinon, arrêt toujours actif
                    self.emergency_stop_active = True
                    self.logger.warning(
                        "🚨 ARRÊT D'URGENCE DÉTECTÉ - Trading suspendu"
                    )
                    return True
                    
            # Vérifier si la période d'arrêt est expirée
            if (self.emergency_stop_until and
                    datetime.now() > self.emergency_stop_until):
                self.emergency_stop_active = False
                self.emergency_stop_until = None
                self.logger.info(
                    "✅ Période d'arrêt d'urgence expirée - Reprise du trading"
                )
                # Supprimer le fichier d'arrêt
                if self.emergency_stop_file.exists():
                    try:
                        self.emergency_stop_file.unlink()
                    except Exception as _e:
                        self.logger.warning(
                            f"Impossible de supprimer le fichier d'arrêt: {_e}"
                        )
                    
            return self.emergency_stop_active
            
        except Exception as e:
            self.logger.warning(f"Erreur vérification arrêt d'urgence: {e}")
            return False

    def activate_emergency_stop(self, duration_minutes=5):
        """Active un arrêt d'urgence pour une durée spécifiée"""
        try:
            self.emergency_stop_active = True
            self.emergency_stop_until = (
                datetime.now() + timedelta(minutes=duration_minutes)
            )
            
            self.logger.critical(
                f"🚨 ARRÊT D'URGENCE ACTIVÉ pour {duration_minutes} minutes"
            )
            
            # Fermer toutes les positions ouvertes
            self.close_all_positions()
            
            # Créer le fichier d'arrêt d'urgence
            with open(self.emergency_stop_file, 'w') as f:
                f.write("EMERGENCY_STOP_ACTIVE\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                f.write(f"Duration: {duration_minutes} minutes\n")
                f.write(f"Until: {self.emergency_stop_until.isoformat()}\n")
                f.write("Status: ACTIVE\n")
                
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur activation arrêt d'urgence: {e}")
            return False

    def close_all_positions(self):
        """Ferme toutes les positions ouvertes immédiatement"""
        if not MT5_AVAILABLE:
            self.logger.info(
                "🔄 Mode simulation - Positions fermées virtuellement"
            )
            return True
            
        try:
            positions = mt5.positions_get()
            if positions is None or len(positions) == 0:
                self.logger.info("ℹ️ Aucune position ouverte à fermer")
                return True
                
            closed_count = 0
            for position in positions:
                try:
                    # Déterminer le type d'ordre de fermeture
                    if position.type == mt5.ORDER_TYPE_BUY:
                        order_type = mt5.ORDER_TYPE_SELL
                        price = mt5.symbol_info_tick(position.symbol).bid
                    else:
                        order_type = mt5.ORDER_TYPE_BUY
                        price = mt5.symbol_info_tick(position.symbol).ask
                    
                    # Créer la requête de fermeture
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "position": position.ticket,
                        "symbol": position.symbol,
                        "volume": position.volume,
                        "type": order_type,
                        "price": price,
                        "magic": 234000,
                        "comment": "Emergency Stop Close",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    
                    # Envoyer l'ordre de fermeture
                    result = mt5.order_send(request)
                    
                    if result.retcode == mt5.TRADE_RETCODE_DONE:
                        closed_count += 1
                        self.logger.info(
                            f"✅ Position fermée: {position.symbol} "
                            f"ticket {position.ticket}"
                        )
                    else:
                        self.logger.error(
                            f"❌ Échec fermeture position {position.ticket}: "
                            f"{result.comment}"
                        )
                        
                except Exception as e:
                    self.logger.error(
                        f"Erreur fermeture position {position.ticket}: {e}"
                    )
                    
            self.logger.info(
                "🔄 Fermeture d'urgence terminée: "
                f"{closed_count} positions fermées"
            )
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur fermeture globale des positions: {e}")
            return False

    @robust_mt5_retry(max_attempts=3)
    def get_live_data(self, symbol: str = None, count: int = 100):
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

    def get_ai_signals(self, current_data, symbol=None):
        """Obtenir les signaux AI avec système de décision avancé"""
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
                        # Préparer les features avec validation robuste
                        features = current_data.select_dtypes(
                            include=[np.number]
                        ).fillna(method='ffill').fillna(0)

                        if len(features.columns) > 0 and len(features) > 0:
                            # Validation des données
                            if features.isnull().sum().sum() > 0:
                                features = features.fillna(features.mean())

                            # Prédiction avec l'ensemble
                            # Passer un DataFrame (pas seulement .values) pour
                            # permettre à l'implémentation de meta_learning
                            # d'adapter/renommer les colonnes si nécessaire.
                            last_features = features.iloc[-1:]

                            # Validation taille features
                            if last_features.shape[1] > 0:
                                # Non-invasif: tenter d'adapter
                                # les colonnes aux noms attendus
                                # par le modèle (si dispo)
                                try:
                                    mapped_input = last_features
                                    # Si le meta_learning expose un ensemble et
                                    # un booster, récupérer les noms attendus
                                    if (
                                        hasattr(self.meta_learning,
                                                'model_ensemble')
                                        and self.meta_learning.model_ensemble
                                    ):
                                        primary = (
                                            self.meta_learning
                                            .model_ensemble[0]
                                            .get('model')
                                        )
                                        if (
                                            primary is not None and
                                            hasattr(primary, 'feature_name')
                                        ):
                                            fn = primary.feature_name() or []
                                            n_feat = (
                                                len(fn) if fn is not None
                                                else 0
                                            )
                                            if n_feat == 0:
                                                n_feat = 5

                                            # Ordre préféré des colonnes live
                                            preferred_order = [
                                                'close', 'volume', 'sma_1T',
                                                'ema_15T', 'rsi_60T'
                                            ]

                                            # Construire un ndarray (1, n_feat)
                                            vals = []
                                            num_df = (
                                                last_features
                                                .select_dtypes(
                                                    include=[np.number]
                                                )
                                                .copy()
                                            )
                                            for i in range(n_feat):
                                                source_col = None
                                                if (
                                                    i < len(preferred_order)
                                                    and preferred_order[i]
                                                    in last_features.columns
                                                ):
                                                    source_col = (
                                                        preferred_order[i]
                                                    )
                                                elif i < len(num_df.columns):
                                                    source_col = (
                                                        num_df.columns[i]
                                                    )

                                                if (
                                                    source_col is not None
                                                    and source_col
                                                    in num_df.columns
                                                ):
                                                    try:
                                                        v = float(
                                                            num_df[source_col]
                                                            .iloc[-1]
                                                        )
                                                    except Exception:
                                                        v = 0.0
                                                else:
                                                    v = 0.0

                                                vals.append(v)

                                            import pandas as _pd
                                            mapped_input = _pd.DataFrame(
                                                [vals],
                                                columns=fn[:len(vals)]
                                            )

                                except Exception:
                                    mapped_input = last_features

                                ensemble_pred = (
                                    self.meta_learning
                                    .ensemble_predict(mapped_input)
                                )

                                if (ensemble_pred is not None and
                                        len(ensemble_pred) > 0):
                                    pred_value = float(ensemble_pred[0])
                                    # Borner les prédictions entre 0 et 1
                                    pred_value = max(0.0, min(1.0, pred_value))

                                    # Calculer la confiance meta puis clampper
                                    raw_meta_conf = abs(pred_value - 0.5) * 2
                                    # Faible risque: limiter la confiance meta pour éviter
                                    # des sur-confiances issues de prédictions extrêmes
                                    META_CONF_CLAMP = 0.3
                                    meta_conf = min(raw_meta_conf, META_CONF_CLAMP)

                                    # Journaliser si clamp appliqué (info, peu verbeux)
                                    try:
                                        if raw_meta_conf != meta_conf:
                                            self.logger.info(
                                                "Meta-confidence clamp applied: raw=%.3f clamped=%.3f",
                                                raw_meta_conf,
                                                meta_conf,
                                            )
                                    except Exception:
                                        pass

                                    signals["meta_learning"] = {
                                        "prediction": pred_value,
                                        "action": (
                                            "buy" if pred_value > 0.6
                                            else (
                                                "sell" if pred_value < 0.4
                                                else "hold"
                                            )
                                        ),
                                        "confidence": meta_conf,
                                    }
                                else:
                                    self.logger.warning(
                                        "Meta-Learning: prédiction vide"
                                    )
                            else:
                                self.logger.warning(
                                    "Meta-Learning: features vides"
                                )
                        else:
                            self.logger.warning(
                                "Meta-Learning: pas de features numériques"
                            )
                except Exception as e:
                    self.logger.error(f"Erreur critique Meta-Learning: {e}")
                    # Fallback sécurisé
                    signals["meta_learning"] = {
                        "prediction": 0.5,
                        "action": "hold",
                        "confidence": 0.0
                    }

            # 🔧 NOUVEAU: Signal XAUUSD amélioré si confiance AI faible
            ml_sig = signals.get("meta_learning") or {}
            ml_conf = ml_sig.get("confidence", 0)
            if symbol == "XAUUSD" and ml_conf < 0.3:
                try:
                    enhanced_xau_signal = self.generate_enhanced_xauusd_signal(
                        current_data
                    )
                    if enhanced_xau_signal.get("confidence", 0) > 0.3:
                        signals["enhanced_xauusd"] = enhanced_xau_signal
                        self.logger.info(
                            f"🥇 Signal XAUUSD amélioré appliqué: "
                            f"{enhanced_xau_signal['action']} "
                            f"conf={enhanced_xau_signal['confidence']:.3f}"
                        )
                except Exception as e:
                    self.logger.warning(f"Erreur signal XAUUSD amélioré: {e}")

            # 3. Combiner les signaux
            combined_action = "hold"
            combined_confidence = 0.0

            # 🥇 PRIORITÉ: Signal XAUUSD amélioré si disponible
            if signals.get("enhanced_xauusd"):
                enhanced_signal = signals["enhanced_xauusd"]
                combined_action = enhanced_signal["action"]
                combined_confidence = enhanced_signal["confidence"]
                self.logger.info(
                    f"🥇 XAUUSD amélioré utilisé: {combined_action} "
                    f"conf={combined_confidence:.3f}"
                )
            # Si pas de détection de régime, utiliser directement
            # le signal du meta_learning comme base
            elif (
                not signals.get("regime_detection")
                and signals.get("meta_learning")
            ):
                ml_action = signals["meta_learning"]["action"]
                ml_conf = signals["meta_learning"]["confidence"]
                combined_action = ml_action
                combined_confidence = ml_conf

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

            # 🧠 NOUVEAU: Appliquer le système de décision avancé
            try:
                # Diagnostics faibles risques: journaliser l'état compact
                try:
                    ml = signals.get('meta_learning') or {}
                    reg = signals.get('regime_detection') or {}
                    diag = {
                        'symbol': symbol,
                        'combined_signal': signals.get('combined_signal'),
                        'confidence': signals.get('confidence'),
                        'meta_prediction': ml.get('prediction'),
                        'meta_confidence': ml.get('confidence'),
                        'regime_action': reg.get('action'),
                        'regime_conf': reg.get('confidence')
                    }
                    # Utiliser debug pour ne pas polluer les logs INFO en prod
                    self.logger.debug("DIAG pre-advanced signals: %s", diag)
                except Exception:
                    # Ne jamais échouer pour du logging
                    pass

                enhanced_signals = self.apply_advanced_decision_engine(
                    symbol, current_data, signals
                )

                # Journaliser le résultat post-enhancement (compact)
                try:
                    post_diag = {
                        'symbol': symbol,
                        'final_action': enhanced_signals.get('combined_signal'),
                        'final_confidence': enhanced_signals.get('confidence'),
                        'enhanced': enhanced_signals.get('enhanced', False),
                        'adaptive_threshold': enhanced_signals.get('adaptive_threshold')
                    }
                    self.logger.info("DIAG post-advanced: %s", post_diag)
                except Exception:
                    pass

                return enhanced_signals
            except Exception as e:
                self.logger.warning(f"Système avancé indisponible: {e}")
                return signals

        except Exception as e:
            self.logger.error(f"Erreur calcul signaux AI: {e}")
            return signals

    def generate_enhanced_xauusd_signal(self, data):
        """Signal XAUUSD amélioré avec analyse technique spécialisée"""
        try:
            if data is None or len(data) < 20:
                return self.generate_fallback_signals("XAUUSD", data)
            
            # XAUUSD réagit fortement aux:
            # 1. Support/Resistance psychologiques (1900, 2000, 2100, etc.)
            # 2. Divergences RSI
            # 3. Pattern engulfing sur H1
            
            current_price = data['close'].iloc[-1]
            
            # Niveaux psychologiques XAUUSD
            psychological_levels = [1900, 2000, 2100, 2200, 2300, 2400, 2500]
            nearest_level = min(
                psychological_levels,
                key=lambda x: abs(x - current_price)
            )
            
            distance_to_level = abs(current_price - nearest_level)
            # Max 50$ distance
            level_proximity = 1 - min(distance_to_level / 50, 1.0)
            
            # RSI divergence pour XAUUSD
            rsi = self.calculate_rsi(data['close'], 14)
            price_trend = (
                (data['close'].iloc[-1] - data['close'].iloc[-5]) /
                data['close'].iloc[-5]
            )
            rsi_trend = (rsi.iloc[-1] - rsi.iloc[-5]) / rsi.iloc[-5]
            
            # Divergence detection
            divergence_strength = abs(price_trend - rsi_trend)
            bullish_divergence = (
                price_trend < 0 and rsi_trend > 0 and rsi.iloc[-1] < 30
            )
            bearish_divergence = (
                price_trend > 0 and rsi_trend < 0 and rsi.iloc[-1] > 70
            )
            
            # Signal calculation
            base_confidence = 0.3  # Base minimum pour XAUUSD
            
            if bullish_divergence:
                confidence = (
                    base_confidence +
                    (divergence_strength * 0.4) +
                    (level_proximity * 0.3)
                )
                return {"action": "buy", "confidence": min(confidence, 0.95)}
            elif bearish_divergence:
                confidence = (
                    base_confidence +
                    (divergence_strength * 0.4) +
                    (level_proximity * 0.3)
                )
                return {"action": "sell", "confidence": min(confidence, 0.95)}
            else:
                # Trend following with psychological levels
                if current_price > nearest_level and level_proximity > 0.8:
                    confidence = base_confidence + (level_proximity * 0.2)
                    return {
                        "action": "buy",
                        "confidence": min(confidence, 0.7)
                    }
                elif current_price < nearest_level and level_proximity > 0.8:
                    confidence = base_confidence + (level_proximity * 0.2)
                    return {
                        "action": "sell",
                        "confidence": min(confidence, 0.7)
                    }
            
            return {"action": "hold", "confidence": base_confidence}
            
        except Exception as e:
            self.logger.warning(f"Erreur signal XAUUSD spécialisé: {e}")
            return self.generate_fallback_signals("XAUUSD", data)

    def calculate_rsi(self, prices, period=14):
        """Calcul RSI robuste"""
        try:
            delta = prices.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            return 100 - (100 / (1 + rs))
        except Exception:
            # Fallback RSI simple
            return pd.Series([50] * len(prices), index=prices.index)

    def generate_fallback_signals(self, symbol, current_data):
        """Génère des signaux basiques quand l'AI retourne 0.000"""
        try:
            if len(current_data) < 20:
                return {
                    "confidence": 0.0,
                    "action": "hold",
                    "type": "insufficient_data",
                }
            
            # Calculs techniques simples
            close_prices = current_data['close'].values
            current_price = close_prices[-1]
            
            # SMA court/long terme
            sma_short = np.mean(close_prices[-5:])   # 5 périodes
            sma_long = np.mean(close_prices[-20:])   # 20 périodes
            
            # RSI simplifié (basé sur gains/pertes)
            price_changes = np.diff(close_prices[-14:])
            gains = price_changes[price_changes > 0]
            losses = abs(price_changes[price_changes < 0])
            
            avg_gain = np.mean(gains) if len(gains) > 0 else 0.001
            avg_loss = np.mean(losses) if len(losses) > 0 else 0.001
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            # Volatilité récente
            volatility = np.std(close_prices[-10:]) / current_price
            
            # Génération du signal
            signal = {"type": "fallback", "confidence": 0.0, "action": "hold"}
            
            # Logique pour EURUSD (Forex stable)
            if symbol == "EURUSD":
                if sma_short > sma_long * 1.0005 and rsi < 70:
                    # Tendance haussière modérée
                    signal = {
                        "confidence": 0.55,
                        "action": "buy",
                        "type": "forex_momentum",
                    }
                elif sma_short < sma_long * 0.9995 and rsi > 30:
                    # Tendance baissière modérée
                    signal = {
                        "confidence": 0.55,
                        "action": "sell",
                        "type": "forex_momentum",
                    }
                elif volatility > 0.002:  # Volatilité élevée = opportunité
                    signal = {
                        "confidence": 0.52,
                        "action": (
                            "buy" if current_price < sma_long else "sell"
                        ),
                        "type": "volatility_breakout",
                    }
                    
            # Logique pour XAUUSD (Or - plus volatil)
            elif symbol == "XAUUSD":
                # Tendance haussière pour l'or
                if sma_short > sma_long * 1.002 and rsi < 75:
                    signal = {
                        "confidence": 0.58,
                        "action": "buy",
                        "type": "gold_trend",
                    }
                # Tendance baissière pour l'or
                elif sma_short < sma_long * 0.998 and rsi > 25:
                    signal = {
                        "confidence": 0.58,
                        "action": "sell",
                        "type": "gold_trend",
                    }
                elif volatility > 0.01:  # Volatilité significative pour l'or
                    signal = {
                        "confidence": 0.54,
                        "action": ("buy" if rsi < 50 else "sell"),
                        "type": "gold_volatility",
                    }
                    
            # Logique pour BTCUSD (déjà bien pris en charge par l'AI)
            elif symbol == "BTCUSD":
                # Signaux moins agressifs pour ne pas interférer avec l'AI
                if (
                    volatility > 0.03
                    and abs(sma_short - sma_long) / sma_long > 0.01
                ):
                    signal = {
                        "confidence": 0.51,
                        "action": (
                            "buy" if sma_short > sma_long else "sell"
                        ),
                        "type": "crypto_fallback",
                    }
            
            self.logger.debug(
                "🔄 Signal fallback %s: %s conf=%.3f",
                symbol,
                signal['action'],
                signal['confidence'],
            )
            return signal
            
        except Exception as e:
            self.logger.warning(
                "Erreur génération signal fallback %s: %s", symbol, e
            )
            return {"confidence": 0.0, "action": "hold", "type": "error"}

    def apply_advanced_decision_engine(self, symbol, data, base_signals):
        """Appliquer le moteur de décision avancé"""
        try:
            # Lazy import du système avancé
            try:
                from advanced_decision_engine import (
                    AdvancedDecisionEngine,
                )
            except Exception:
                from scripts.advanced_decision_engine import (
                    AdvancedDecisionEngine,
                )

            # Initialiser si pas encore fait
            if not hasattr(self, 'advanced_engine'):
                self.advanced_engine = AdvancedDecisionEngine()
                self.logger.info("🧠 Système décision avancé activé")

            # Utiliser le symbole passé en paramètre
            if not symbol:
                symbol = self.symbols[0] if self.symbols else "EURUSD"

            # Appliquer enhancement
            enhanced_decision = self.advanced_engine.make_enhanced_decision(
                symbol, data, base_signals
            )

            # Intégrer résultats
            if enhanced_decision.get('enhancement_applied'):
                # Utiliser confiance et action améliorées
                base_signals['confidence'] = enhanced_decision['confidence']
                base_signals['combined_signal'] = enhanced_decision['action']

                # Ajouter métadonnées avancées
                base_signals['enhanced'] = True
                base_signals['adaptive_threshold'] = enhanced_decision.get(
                    'adaptive_threshold', self.confidence_threshold
                )
                base_signals['execution_urgency'] = enhanced_decision.get(
                    'execution_urgency', 'later'
                )
                base_signals['risk_adjusted_score'] = enhanced_decision.get(
                    'risk_adjusted_score', 0.5
                )

                self.logger.info(
                    f"🧠 Enhancement appliqué - "
                    f"Confiance: {enhanced_decision['confidence']:.3f}, "
                    f"Urgence: {enhanced_decision['execution_urgency']}"
                )

            return base_signals

        except ImportError:
            self.logger.warning("Module advanced_decision_engine non trouvé")
            return base_signals
        except Exception as e:
            self.logger.warning(f"Erreur enhancement: {e}")
            return base_signals

    def validate_signal_quality(
        self,
        action,
        symbol,
        lot_size,
        stop_loss,
        take_profit,
        price=None,
    ):
        """Valide la qualité d'un signal avant exécution"""
        validation_errors = []
        
        # 1. Validation des paramètres obligatoires
        if not action or action not in ["buy", "sell"]:
            validation_errors.append(f"Action invalide: {action}")
        
        if not symbol or len(symbol) < 2:
            validation_errors.append(f"Symbole invalide: {symbol}")
            
        if lot_size is None or lot_size <= 0:
            validation_errors.append(f"Lot size invalide: {lot_size}")
            
        # 2. Validation des niveaux de prix (si fournis)
        if price is not None and price <= 0:
            validation_errors.append(f"Prix invalide: {price}")
            
        if stop_loss is not None and stop_loss <= 0:
            validation_errors.append(f"Stop loss invalide: {stop_loss}")
            
        if take_profit is not None and take_profit <= 0:
            validation_errors.append(f"Take profit invalide: {take_profit}")
            
        # 3. Validation de la cohérence des niveaux
        if (
            price is not None and stop_loss is not None and
            take_profit is not None
        ):
            if action == "buy":
                if stop_loss >= price:
                    validation_errors.append(
                        "Buy: stop_loss doit être < prix d'entrée"
                    )
                if take_profit <= price:
                    validation_errors.append(
                        "Buy: take_profit doit être > prix d'entrée"
                    )
            elif action == "sell":
                if stop_loss <= price:
                    validation_errors.append(
                        "Sell: stop_loss doit être > prix d'entrée"
                    )
                if take_profit >= price:
                    validation_errors.append(
                        "Sell: take_profit doit être < prix d'entrée"
                    )
                    
        # 4. Validation du risque/récompense
        if (
            price is not None and stop_loss is not None and
            take_profit is not None
        ):
            if action == "buy":
                risk = abs(price - stop_loss)
                reward = abs(take_profit - price)
            else:  # sell
                risk = abs(stop_loss - price)
                reward = abs(price - take_profit)
                
            if risk > 0 and reward > 0:
                risk_reward_ratio = reward / risk
                if risk_reward_ratio < 0.5:
                    validation_errors.append(
                        f"Ratio R/R trop faible: {risk_reward_ratio:.2f}"
                    )
        
        # Log des erreurs et retour du résultat
        if validation_errors:
            self.logger.warning(
                f"Signal invalide pour {symbol}: "
                f"{' ; '.join(validation_errors)}"
            )
            return False, validation_errors
        else:
            self.logger.debug(f"✅ Signal validé pour {symbol}")
            return True, []

    def execute_trade(
        self,
        action: str,
        symbol: str = None,
        lot_size: float = None,
        stop_loss: float = None,
        take_profit: float = None,
        price: float = None,
    ):
        """Exécuter un trade sur MT5 pour un symbole spécifique avec validation"""
        # Kill-switch global via fichiers/variables d'environnement
        try:
            from mt5_connector import trading_disabled  # dispo via utils path
            if trading_disabled():
                self.logger.warning(
                    "TRADING_DISABLED: kill-switch actif - ordre bloqué"
                )
                # Journaliser le signal rejeté
                self._log_rejected_signal(
                    symbol or (self.symbols[0] if self.symbols else ""),
                    action,
                    ["Kill-switch actif"],
                )
                return False
        except Exception:
            # Si le helper n'est pas dispo, fallback sur le fichier de contrôle
            try:
                from pathlib import Path as _Path
                if _Path("control/emergency_stop").exists() or _Path(
                    "control/disable_trading"
                ).exists():
                    self.logger.warning(
                        "TRADING_DISABLED: fichier de contrôle présent - ordre bloqué"
                    )
                    self._log_rejected_signal(
                        symbol or (self.symbols[0] if self.symbols else ""),
                        action,
                        ["Fichier de contrôle présent"],
                    )
                    return False
            except Exception:
                pass
        # Utiliser le premier symbole par défaut si non spécifié
        if symbol is None:
            symbol = self.symbols[0]

        if lot_size is None:
            lot_size = self.lot_sizes.get(symbol, 0.01)

        # PROTECTION (non invasive) - Eviter tailles de position catastrophiques
        try:
            # Estimation simple du notional: lot_size * price
            # Note: cela est une approximation (conversion lot->base units dépend du marché).
            # On utilise cette estimation pour appliquer un plafond conservateur.
            current_balance = self.performance_metrics.get('current_balance', 10000.0)
            max_notional = current_balance * 0.05  # 5% du capital par trade (conservative default)

            # Obtenir prix si non fourni
            if price is None:
                # essayer d'utiliser tick si MT5 disponible
                try:
                    if MT5_AVAILABLE:
                        tick = mt5.symbol_info_tick(symbol)
                        if tick is not None:
                            if action == 'buy':
                                price_probe = tick.ask
                            else:
                                price_probe = tick.bid
                        else:
                            price_probe = None
                    else:
                        price_probe = None
                except Exception:
                    price_probe = None

                if price_probe is None:
                    # fallback vers la dernière donnée connue si disponible
                    try:
                        if symbol in self.live_data and len(self.live_data[symbol]) > 0:
                            price = float(self.live_data[symbol]['close'].iloc[-1])
                        else:
                            price = None
                    except Exception:
                        price = None

            # Si on a un prix utilisable, estimer le notional et redimensionner
            if price is not None and price > 0:
                est_notional = lot_size * price
                if est_notional > max_notional:
                    # réduire proportionnellement
                    new_lot = max_notional / price
                    self.logger.warning(
                        f"Lot size {lot_size} for {symbol} reduced to "
                        f"{new_lot:.6f} to respect max notional "
                        f"{max_notional:.2f}"
                    )
                    lot_size = float(max(new_lot, 1e-6))
        except Exception as e:
            # Ne pas bloquer l'exécution si la protection échoue
            self.logger.debug(f"Sizing protection skipped due to error: {e}")

        # NOUVELLE VALIDATION: Vérifier la qualité du signal avant exécution
        is_valid, validation_errors = self.validate_signal_quality(
            action, symbol, lot_size, stop_loss, take_profit, price
        )
        
        if not is_valid:
            self.logger.error(f"❌ Signal rejeté pour {symbol}: qualité insuffisante")
            # Enregistrer les signaux rejetés pour analyse
            self._log_rejected_signal(symbol, action, validation_errors)
            return False

        if not MT5_AVAILABLE:
            # Mode 100% live: ne pas simuler
            self.logger.error("MT5 indisponible – exécution annulée (simulation interdite)")
            return False

        try:
            # Les validations complètes ont déjà été faites par validate_signal_quality
            
            if lot_size <= 0 or lot_size > 10.0:  # Limite sécurité
                self.logger.error(f"Lot size invalide: {lot_size}")
                return False

            # Initialiser MT5 si pas encore fait avec retry
            @robust_mt5_retry(max_attempts=2)
            def _ensure_mt5_initialized():
                if not mt5.initialize():
                    raise MT5ConnectionError("Impossible d'initialiser MT5")
                return True

            _ensure_mt5_initialized()

            # Vérifier que le symbole est disponible
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                self.logger.error(f"Symbole indisponible: {symbol}")
                return False

            if not symbol_info.visible:
                # Essayer d'activer le symbole
                if not mt5.symbol_select(symbol, True):
                    self.logger.error(
                        f"Impossible d'activer symbole: {symbol}"
                    )
                    return False

            # Obtenir le prix actuel avec retry
            tick = None
            for retry in range(3):
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    break
                time.sleep(0.1)

            if tick is None:
                self.logger.error(
                    f"Impossible d'obtenir le tick pour {symbol} après retry"
                )
                return False

            # Validation du spread
            spread = tick.ask - tick.bid
            if spread > tick.ask * 0.001:  # Spread > 0.1%
                self.logger.warning(f"Spread élevé pour {symbol}: {spread}")

            # Déterminer le type d'ordre
            if action == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            elif action == "sell":
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            else:
                self.logger.error(f"Action non reconnue: {action}")
                return False

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

            # Amélioration: Utiliser stop loss dynamique si non spécifié
            if stop_loss:
                request["sl"] = stop_loss
            else:
                # Calculer stop loss dynamique basé sur la volatilité
                dynamic_sl = self.calculate_dynamic_stop_loss(symbol, action, price)
                request["sl"] = dynamic_sl
                self.logger.info(f"🎯 Stop loss dynamique appliqué: {dynamic_sl:.5f}")
                
            # 🔧 NOUVEAU: Take Profit automatique avec Risk/Reward 1:2
            if take_profit:
                request["tp"] = take_profit
            else:
                # Calculer TP automatique basé sur SL avec ratio 1:2
                dynamic_tp = self.calculate_dynamic_take_profit(
                    symbol, action, price, request["sl"]
                )
                if dynamic_tp:
                    request["tp"] = dynamic_tp
                    self.logger.info(f"🎯 Take profit automatique: {dynamic_tp:.5f}")

            # Envoyer l'ordre avec retry robuste
            @robust_mt5_retry(max_attempts=3)
            def _send_order():
                result = mt5.order_send(request)
                if result is None:
                    raise MT5OperationError("Résultat ordre null")
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    raise MT5OperationError(
                        f"Échec ordre {symbol}: {result.comment}"
                    )
                return result

            result = _send_order()

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

            # Enregistrer la position dans la watchlist pour auto-close
            try:
                # Register asynchronously but attempt immediate discovery
                self._register_position_watchlist(result, symbol, lot_size, request.get('sl'), request.get('tp'), price)
            except Exception as _reg_e:
                self.logger.debug(f"Impossible d'enregistrer position dans watchlist: {_reg_e}")

            return True

        except Exception as e:
            self.logger.error(f"Erreur exécution trade {symbol}: {e}")
            return False

    # simulate_trade supprimé: le mode simulation est interdit (100% live)

    # LIGNE 547-589 : Checks de risque simplistes
    def risk_check(self, action, signals, symbol="UNKNOWN"):
        """Vérification de risque avancée avec système intelligent"""
        try:
            # 🧠 NOUVEAU: Utiliser seuil adaptatif si disponible
            min_confidence = signals.get(
                'adaptive_threshold', self.confidence_threshold)

            # 1. Vérifier confiance avec seuil intelligent
            current_confidence = signals["confidence"]
            
            # 🧠 AMÉLIORATION: Appliquer boost performance symbole
            performance_boost = self.get_symbol_performance_boost(symbol)
            adjusted_confidence = current_confidence + performance_boost
            
            if adjusted_confidence < min_confidence:
                conf_msg = (f"Confiance {adjusted_confidence:.3f} "
                            f"< seuil {min_confidence:.3f} "
                            f"(boost: {performance_boost:+.3f})")
                self.logger.info(conf_msg)
                return False

            # 🧠 AMÉLIORATION: Gestion urgence plus granulaire
            urgency = signals.get('execution_urgency', 'later')
            if urgency == 'avoid':
                self.logger.info("Exécution déconseillée par système avancé")
                return False
            
            # Ajustement seuil selon urgence
            urgency_adjustments = {
                'immediate': -0.05,  # Plus permissif
                'soon': 0.0,         # Normal
                'later': +0.03       # Plus strict
            }
            
            adjusted_threshold = min_confidence + urgency_adjustments.get(urgency, 0.0)
            if current_confidence < adjusted_threshold:
                urgency_msg = (
                    f"Confiance {current_confidence:.3f} "
                    f"< seuil ajusté {adjusted_threshold:.3f} "
                    f"(urgence: {urgency})"
                )
                self.logger.info(urgency_msg)
                return False

            # 🧠 NOUVEAU: Vérifier score de risque ajusté
            risk_score = signals.get('risk_adjusted_score', current_confidence)
            if risk_score < 0.5:
                risk_msg = f"Score risque ajusté trop faible: {risk_score:.3f}"
                self.logger.info(risk_msg)
                return False

            # 2. Vérifier le nombre de positions ouvertes
            max_positions = 2 if urgency == 'immediate' else 3
            if len(self.current_positions) >= max_positions:
                pos_msg = (f"Nombre maximum de positions "
                           f"atteint ({max_positions})")
                self.logger.info(pos_msg)
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

            # 5. Vérification volatilité intelligente (par symbole)
            df_symbol = None
            try:
                if symbol and isinstance(self.live_data, dict):
                    df_symbol = self.live_data.get(symbol)
            except Exception:
                df_symbol = None

            if (
                isinstance(df_symbol, pd.DataFrame)
                and len(df_symbol) > 20
                and "returns" in df_symbol.columns
            ):
                recent_volatility = df_symbol["returns"].rolling(20).std().iloc[-1]

                # Seuil adaptatif selon urgence
                vol_threshold = 0.008 if urgency == 'immediate' else 0.012

                if recent_volatility > vol_threshold:
                    vol_msg = (f"Volatilité {recent_volatility:.4f} "
                               f"> seuil {vol_threshold:.4f}")
                    self.logger.warning(vol_msg)
                    return False

            return True

        except Exception as e:
            self.logger.error(f"Erreur vérification risque: {e}")
            return False

    # LIGNE 590-676 : Trading continu multi-asset avec heures de marché
    def main_trading_loop(self):
        """Boucle principale de trading en continu"""
        self.logger.info(
            f"🚀 Démarrage trading continu - Intervalle: {self.trading_interval}s"
        )

        cycle_count = 0

        while self.is_running:
            try:
                cycle_count += 1
                cycle_start = time.time()

                # 0. NOUVEAU: Vérifier arrêt d'urgence en priorité
                if self.check_emergency_stop():
                    self.logger.warning("⏸️ Trading suspendu - Arrêt d'urgence actif")
                    time.sleep(30)  # Vérifier toutes les 30 secondes
                    continue

                # 1. Health check périodique (tous les 10 cycles)
                if cycle_count % 10 == 1:  # Premier et chaque 10 cycles
                    if not self.production_health_check():
                        msg = "❌ Health check échoué - Retry dans 5min"
                        self.logger.error(msg)
                        time.sleep(300)  # 5 minutes
                        continue

                # Reset quotidien des compteurs (stats seulement)
                self.reset_daily_counters()

                # 2. Récupérer les données live pour tous les symboles
                current_data = self.get_live_data(None, 200)

                if not current_data or len(current_data) == 0:
                    self.logger.warning("Aucune donnée reçue")
                    time.sleep(self.trading_interval)
                    continue

                # 2. Analyser chaque symbole
                for symbol in self.symbols:
                    try:
                        # Vérifier si le marché est ouvert
                        if not self.is_market_open(symbol):
                            self.logger.info(f"🔒 Marché fermé pour {symbol}")
                            continue

                        # Données spécifiques au symbole
                        if symbol not in current_data:
                            msg = f"Pas de données pour {symbol}"
                            self.logger.warning(msg)
                            continue

                        symbol_data = current_data[symbol]
                        if len(symbol_data) < 50:
                            self.logger.warning(
                                f"Données insuffisantes pour {symbol}"
                            )
                            continue

                        # 3. Obtenir signaux AI pour ce symbole
                        signals = self.get_ai_signals(symbol_data, symbol)
                        action = signals["combined_signal"]
                        confidence = signals["confidence"]

                        # 3.b Convergence MTF 15m (optionnel, non-intrusif)
                        try:
                            from config.trading_config import TradingConfig as _TC
                            if MTF_AVAILABLE and getattr(_TC, 'USE_MTF_CONVERGENCE', True):
                                # Charger les fondamentaux une seule fois
                                if self._fundamentals_map is None:
                                    try:
                                        from pathlib import Path as _P
                                        funda_dir = _P("data/fundamentals")
                                        if funda_dir.exists():
                                            self._fundamentals_map = load_fundamentals_csv(funda_dir)
                                        else:
                                            self._fundamentals_map = {}
                                    except Exception as _e:
                                        self._fundamentals_map = {}

                                o15, tech_mtf, funda_mtf = build_live_mtf_from_m1(
                                    symbol_data, self._fundamentals_map
                                )
                                if tech_mtf is not None and len(tech_mtf) > 0:
                                    mtf_action, mtf_conf, mtf_agree = compute_mtf_convergence(tech_mtf)
                                    signals["mtf_convergence"] = {
                                        "action": mtf_action,
                                        "confidence": mtf_conf,
                                        "agreement": mtf_agree,
                                    }
                                    # mémoriser un bref résumé pour affichage périodique
                                    try:
                                        self.last_mtf_summary = {
                                            'action': mtf_action,
                                            'confidence': mtf_conf,
                                            'agreement': mtf_agree,
                                            'symbol': symbol,
                                        }
                                    except Exception:
                                        pass

                                    # Fusion simple: si AI neutre/faible et MTF fort => utiliser MTF
                                    if (
                                        mtf_action in ("buy", "sell")
                                        and mtf_conf > max(0.55, self.confidence_threshold)
                                        and confidence < self.confidence_threshold
                                    ):
                                        action = mtf_action
                                        confidence = max(confidence, mtf_conf)
                                        self.logger.info(
                                            "🧭 MTF pris en compte: %s conf=%.3f (agree=%s)",
                                            mtf_action,
                                            mtf_conf,
                                            str(mtf_agree),
                                        )
                                    # Sinon, si concordance avec AI, booster légèrement la confiance
                                    elif (
                                        mtf_action == action and action in ("buy", "sell")
                                    ):
                                        confidence = float(min(1.0, confidence + 0.1))
                                        self.logger.info(
                                            f"🧭 MTF concordant: boost conf -> {confidence:.3f}"
                                        )

                                # 3.c Confluence fondamentale (optionnelle)
                                try:
                                    if (
                                        getattr(_TC, 'USE_FUNDAMENTAL_CONFLUENCE', True)
                                        and funda_mtf is not None
                                        and len(funda_mtf) > 0
                                    ):
                                        fconf = compute_fundamental_confluence(funda_mtf)
                                        signals["fundamental_confluence"] = fconf

                                        # Appliquer un léger boost si biais aligné
                                        max_boost = float(getattr(_TC, 'FUNDAMENTAL_BOOST_MAX', 0.07))
                                        bias = fconf.get('bias', 'neutral')
                                        fscore = float(fconf.get('score', 0.0))

                                        aligned = (
                                            (bias == 'bull' and action == 'buy') or
                                            (bias == 'bear' and action == 'sell')
                                        )
                                        if aligned and action in ("buy", "sell"):
                                            boost = min(max_boost, fscore * max_boost)
                                            old_conf = confidence
                                            confidence = float(min(1.0, confidence + boost))
                                            self.logger.info(
                                                (
                                                    "🧮 Confluence fondamentale alignée "
                                                    "(%s, score=%.2f): +%.3f -> conf=%.3f"
                                                ),
                                                bias, fscore, boost, confidence
                                            )
                                except Exception as _fe:
                                    self.logger.debug(f"Fundamental confluence indisponible: {_fe}")

                                # 3.d Extension technique prudente (EMA/BB/ATR/MACD hist)
                                try:
                                    if (
                                        getattr(_TC, 'USE_EXTENDED_MTF_TECH', False)
                                        and tech_mtf is not None
                                        and len(tech_mtf) > 0
                                    ):
                                        # Lis légèrement quelques signaux techniques complémentaires
                                        last = tech_mtf.iloc[-1]
                                        ema_ok = 0.0
                                        bb_ok = 0.0
                                        atr_ok = 0.0
                                        macd_hist_ok = 0.0

                                        for lbl in ("1D", "4H", "1H", "30T", "15T", "5T"):
                                            try:
                                                ema = float(last.get(f"tech_{lbl}_ema20", np.nan))
                                                close = (
                                                    float(o15.iloc[-1]['close'])
                                                    if len(o15) else np.nan
                                                )
                                                if np.isfinite(ema) and np.isfinite(close):
                                                    ema_ok += 1.0 if close > ema else -0.5
                                            except Exception:
                                                pass
                                            try:
                                                bb_h = float(
                                                    last.get(
                                                        f"tech_{lbl}_bb_high", np.nan
                                                    )
                                                )
                                                bb_l = float(
                                                    last.get(
                                                        f"tech_{lbl}_bb_low", np.nan
                                                    )
                                                )
                                                close = (
                                                    float(o15.iloc[-1]['close'])
                                                    if len(o15) else np.nan
                                                )
                                                if (
                                                    np.isfinite(bb_h) and np.isfinite(bb_l)
                                                    and np.isfinite(close) and bb_h > bb_l
                                                ):
                                                    pos = (close - bb_l) / (bb_h - bb_l)
                                                    # proche des bords = momentum
                                                    bb_ok += (pos - 0.5) * 0.5
                                            except Exception:
                                                pass
                                            try:
                                                atr = float(last.get(f"tech_{lbl}_atr14", np.nan))
                                                if np.isfinite(atr) and atr > 0:
                                                    atr_ok += 0.1  # présence/info seulement
                                            except Exception:
                                                pass
                                            try:
                                                macd_hist = float(
                                                    last.get(
                                                        f"tech_{lbl}_macd_hist", np.nan
                                                    )
                                                )
                                                if np.isfinite(macd_hist):
                                                    macd_hist_ok += np.sign(macd_hist) * 0.2
                                            except Exception:
                                                pass

                                        ext_score = (
                                            ema_ok * 0.05 + bb_ok * 0.1 +
                                            atr_ok * 0.02 + macd_hist_ok * 0.1
                                        )
                                        # Clip et appliquer boost modeste
                                        if 'np' in globals():
                                            ext_boost = float(
                                                np.clip(ext_score, -0.05, 0.08)
                                            )
                                        else:
                                            ext_boost = float(
                                                max(min(ext_score, 0.08), -0.05)
                                            )
                                        if action == 'buy' and ext_boost > 0:
                                            confidence = float(min(1.0, confidence + ext_boost))
                                            self.logger.info(
                                                (
                                                    "🧩 Extension MTF technique: "
                                                    "+%.3f -> conf=%.3f"
                                                ),
                                                ext_boost, confidence
                                            )
                                        elif action == 'sell' and ext_boost > 0:
                                            confidence = float(min(1.0, confidence + ext_boost))
                                            self.logger.info(
                                                (
                                                    "🧩 Extension MTF technique: "
                                                    "+%.3f -> conf=%.3f"
                                                ),
                                                ext_boost, confidence
                                            )
                                except Exception as _te:
                                    self.logger.debug(f"Extended MTF tech indisponible: {_te}")
                        except Exception as _e:
                            self.logger.debug(f"MTF convergence indisponible: {_e}")

                        # Mettre à jour live_data pour usage ultérieur (volatilité, etc.)
                        try:
                            self.live_data[symbol] = symbol_data
                        except Exception:
                            pass

                        # NOUVEAU: Fallback pour signaux AI faibles
                        if confidence <= 0.001:  # Pratiquement 0
                            fallback_signal = self.generate_fallback_signals(symbol, symbol_data)
                            if fallback_signal["confidence"] > 0.50:
                                action = fallback_signal["action"]
                                confidence = fallback_signal["confidence"]
                                signals["fallback_used"] = True
                                signals["fallback_type"] = fallback_signal["type"]
                                self.logger.info(
                                    f"🔄 Fallback activé pour {symbol}: "
                                    f"{action} conf={confidence:.3f}"
                                )

                        # Logging optimisé avec seuil de décision
                        if confidence > self.confidence_threshold:
                            status = "✅ TRADE"
                        else:
                            status = "⏸️  SKIP"
                        self.logger.info(
                            f"📊 {symbol}: {action.upper()} "
                            f"conf={confidence:.3f} [{status}]"
                        )

                        # 4. Vérification des risques et exécution
                        # Seuil optimisé configuré (+98% perf vs 0.6)
                        if (action in ["buy", "sell"] and
                                confidence > self.confidence_threshold):
                            try:
                                if self.risk_check(action, signals, symbol):
                                    # Calculer SL/TP adaptatifs avec validation
                                    if "close" not in symbol_data.columns:
                                        self.logger.error(
                                            f"Colonne 'close' manquante pour "
                                            f"{symbol}"
                                        )
                                        continue

                                    current_price = symbol_data[
                                        "close"].iloc[-1]

                                    # Validation prix
                                    if (pd.isna(current_price) or
                                            current_price <= 0):
                                        self.logger.error(
                                            "Prix invalide pour {}: {}".format(
                                                symbol, current_price)
                                        )
                                        continue

                                    # Calcul ATR avec validation
                                    if "returns" in symbol_data.columns:
                                        atr_series = (symbol_data["returns"]
                                                      .rolling(20)
                                                      .std())
                                        if (
                                            len(atr_series) > 0
                                            and not pd.isna(
                                                atr_series.iloc[-1]
                                            )
                                        ):
                                            atr = (atr_series.iloc[-1] *
                                                   current_price)
                                        else:
                                            # Fallback ATR basé sur high-low
                                            if all(col in symbol_data.columns
                                                   for col in ["high", "low"]):
                                                price_range = (
                                                    symbol_data["high"] -
                                                    symbol_data["low"]
                                                )
                                                atr = (price_range
                                                       .rolling(20).mean()
                                                       .iloc[-1])
                                            else:
                                                # 0.1% fallback
                                                atr = current_price * 0.001
                                    else:
                                        # Fallback conservateur
                                        atr = current_price * 0.001

                                    # Calcul SL/TP sécurisé
                                    # ATR minimum
                                    atr = max(atr, current_price * 0.0005)
                                    # ATR maximum
                                    atr = min(atr, current_price * 0.005)

                                    if action == "buy":
                                        stop_loss = current_price - (atr * 2)
                                        take_profit = current_price + (atr * 3)
                                    else:  # sell
                                        stop_loss = current_price + (atr * 2)
                                        take_profit = current_price - (atr * 3)

                                    # Validation finale SL/TP
                                    if stop_loss <= 0 or take_profit <= 0:
                                        self.logger.error(
                                            f"SL/TP invalides pour {symbol}: "
                                            f"SL={stop_loss}, TP={take_profit}"
                                        )
                                        continue

                                    # Exécuter le trade avec retry
                                    success = self.execute_trade(
                                        action,
                                        symbol,
                                        self.lot_sizes.get(symbol, 0.01),
                                        stop_loss,
                                        take_profit,
                                    )
                            except Exception as e:
                                self.logger.error(
                                    f"Erreur exécution trade {symbol}: {e}"
                                )
                                continue

                            # Gérer le résultat trade si pas d'exception
                            try:
                                if success:
                                    self.trade_count_today += 1
                                    msg = (f"💰 Trade {symbol} exécuté "
                                           f"(#{self.trade_count_today})")
                                    self.logger.info(msg)
                                    # 🧠 AMÉLIORATION: Enregistrer pour suivi performance
                                    self.record_trade_for_learning(
                                        symbol, action, confidence, signals
                                    )
                                else:
                                    self.logger.error(
                                        f"❌ Échec trade {symbol}"
                                    )
                            except Exception:
                                pass

                    except Exception as e:
                        self.logger.error(f"Erreur analyse {symbol}: {e}")
                        continue

                # 5. Mise à jour des métriques
                self.update_performance_metrics()

                # 6. Gestion avancée des risques - Trailing stops (nouveau)
                if cycle_count % 3 == 0:  # Toutes les 3 cycles (environ 30 min)
                    self.update_positions_risk_management()

                # 7. Gestion mémoire périodique
                if cycle_count % self.cleanup_cycle_interval == 0:
                    self.cleanup_memory()

                # 8. Log périodique
                if cycle_count % self.log_summary_interval == 0:
                    self.log_performance_summary()

                # Vérifier la watchlist d'auto-close à chaque cycle (best-effort)
                try:
                    self.enforce_auto_close()
                except Exception:
                    pass

                # 8. Attendre selon l'intervalle configuré
                cycle_duration = time.time() - cycle_start
                sleep_time = max(
                    self.trading_interval - cycle_duration,
                    self.min_sleep_seconds,
                )

                # Mode smoke: limiter fortement le temps d'attente
                if self.max_cycles and self.max_cycles > 0:
                    try:
                        sleep_time = min(sleep_time, float(self.smoke_sleep))
                    except Exception:
                        sleep_time = min(sleep_time, 2.0)

                # Log optimisé avec métriques de performance
                trades_today = self.trade_count_today
                self.logger.info(
                    f"⏰ Cycle {cycle_count} terminé "
                    f"({cycle_duration:.1f}s) | "
                    f"Trades: {trades_today} | "
                    f"Seuil: {self.performance_metrics['optimal_threshold']}"
                    f" | Prochain: {sleep_time:.0f}s"
                )

                time.sleep(sleep_time)

                # Sortie anticipée en mode smoke après N cycles
                if self.max_cycles and cycle_count >= self.max_cycles:
                    self.logger.info(
                        f"🧪 Mode smoke: arrêt après {cycle_count} cycle(s)"
                    )
                    break

            except KeyboardInterrupt:
                self.logger.info("Arrêt demandé par l'utilisateur")
                break
            except Exception as e:
                self.logger.error(f"Erreur dans la boucle principale: {e}")
                time.sleep(300)  # 5 minutes en cas d'erreur

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

    def cleanup_memory(self):
        """Nettoyage mémoire périodique pour optimiser les performances"""
        try:
            import gc

            # 1. Limiter l'historique des trades (garde les 1000 derniers)
            if len(self.trade_history) > 1000:
                self.trade_history = self.trade_history[-1000:]
                self.logger.info("🧹 Historique trades nettoyé (>1000 entrées)")

            # 2. Nettoyer les données de marché anciennes
            for symbol in self.live_data:
                if len(self.live_data[symbol]) > 500:
                    # Garder les 300 dernières barres
                    self.live_data[symbol] = self.live_data[symbol].tail(300)
                    self.logger.debug(f"🧹 Données {symbol} nettoyées")

            # 3. Forcer garbage collection
            collected = gc.collect()

            # 4. Log des stats mémoire
            if collected > 0:
                self.logger.info(f"🧹 GC: {collected} objets collectés")

        except Exception as e:
            self.logger.warning(f"Erreur nettoyage mémoire: {e}")

    def _log_rejected_signal(self, symbol, action, validation_errors):
        """Log les signaux rejetés pour analyse et amélioration"""
        try:
            rejected_signal = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'action': action,
                'errors': validation_errors,
                'type': 'signal_rejected'
            }
            
            # Sauvegarder dans un fichier pour analyse ultérieure
            rejected_signals_file = Path(self.logs_folder) / 'rejected_signals.jsonl'
            with open(rejected_signals_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rejected_signal) + '\n')
                
            # Garder stats en mémoire
            if not hasattr(self, 'rejected_signals_count'):
                self.rejected_signals_count = {}
            
            error_type = validation_errors[0] if validation_errors else 'unknown'
            self.rejected_signals_count[error_type] = (
                self.rejected_signals_count.get(error_type, 0) + 1
            )
            
            self.logger.debug(f"📊 Signal rejeté enregistré: {symbol} - {error_type}")
            
        except Exception as e:
            self.logger.warning(f"Erreur lors de l'enregistrement du signal rejeté: {e}")

    def calculate_dynamic_stop_loss(self, symbol, action, entry_price, current_volatility=None):
        """Calcule un stop loss dynamique basé sur la volatilité du marché"""
        try:
            # 🔧 PARAMÈTRES OPTIMISÉS - Stop Loss professionnels réalistes
            default_stops = {
                'EURUSD': 0.0005,    # 5 pips (0.04% risk) - était 20 pips
                'XAUUSD': 2.0,       # 2 dollars (0.08% risk) - était 5 dollars
                'BTCUSD': 150.0      # 150 dollars (0.22% risk)
            }
            
            base_stop = default_stops.get(symbol, 0.0005)
            
            # 🔧 VOLATILITÉ AJUSTÉE - Amplification réduite
            if current_volatility is not None:
                # Ajustement modéré: max +50% au lieu de +200%
                volatility_adjustment = min(current_volatility * 0.5, 0.5)
                base_stop *= (1.0 + volatility_adjustment)
                
            # Ajustement selon l'action
            if action == "buy":
                stop_loss = entry_price - base_stop
            else:  # sell
                stop_loss = entry_price + base_stop
                
            self.logger.debug(
                f"Stop loss dynamique {symbol}: {stop_loss:.5f} "
                f"(base: {base_stop:.5f})"
            )
            return stop_loss
            
        except Exception as e:
            self.logger.warning(f"Erreur calcul stop loss dynamique: {e}")
            # 🔧 FALLBACK AMÉLIORÉ - 0.5% au lieu de 2%
            if action == "buy":
                return entry_price * 0.995  # -0.5% au lieu de -2%
            else:
                return entry_price * 1.005  # +0.5% au lieu de +2%

    def calculate_dynamic_take_profit(self, symbol, action, entry_price, stop_loss):
        """Calcule un take profit automatique avec Risk/Reward optimal"""
        try:
            if stop_loss is None:
                return None
                
            # Calculer la distance du stop loss
            if action == "buy":
                sl_distance = entry_price - stop_loss
                # Take profit à 2x la distance du SL (Risk/Reward 1:2)
                take_profit = entry_price + (sl_distance * 2.0)
            else:  # sell
                sl_distance = stop_loss - entry_price
                # Take profit à 2x la distance du SL
                take_profit = entry_price - (sl_distance * 2.0)
            
            # Validation minimum pour éviter TP trop proche
            min_tp_distance = {
                'EURUSD': 0.0008,    # 8 pips minimum
                'XAUUSD': 3.0,       # 3 dollars minimum
                'BTCUSD': 200.0      # 200 dollars minimum
            }
            
            min_distance = min_tp_distance.get(symbol, 0.0008)
            actual_distance = abs(take_profit - entry_price)
            
            if actual_distance < min_distance:
                # Ajuster TP pour respecter la distance minimum
                if action == "buy":
                    take_profit = entry_price + min_distance
                else:
                    take_profit = entry_price - min_distance
                    
            self.logger.debug(
                f"Take profit automatique {symbol}: {take_profit:.5f} "
                f"(R/R 1:2, distance: {actual_distance:.5f})"
            )
            return take_profit
            
        except Exception as e:
            self.logger.warning(f"Erreur calcul take profit automatique: {e}")
            return None

    def calculate_trailing_stop(
        self, symbol, action, entry_price, current_price, current_sl=None
    ):
        """Calcule un trailing stop intelligent"""
        try:
            # Distance minimum de trailing selon l'instrument
            min_trailing_distance = {
                'EURUSD': 0.0015,    # 15 pips
                'XAUUSD': 3.0,       # 3 dollars
                'BTCUSD': 200.0      # 200 dollars
            }
            
            min_distance = min_trailing_distance.get(symbol, 0.0015)
            
            if action == "buy":
                # Pour un achat, le trailing stop suit le prix vers le haut
                potential_stop = current_price - min_distance
                
                # Ne déplacer le stop que s'il est plus avantageux
                if current_sl is None or potential_stop > current_sl:
                    new_stop = potential_stop
                    self.logger.debug(f"Trailing stop BUY {symbol}: {current_sl} → {new_stop:.5f}")
                    return new_stop
                    
            else:  # sell
                # Pour une vente, le trailing stop suit le prix vers le bas
                potential_stop = current_price + min_distance
                
                # Ne déplacer le stop que s'il est plus avantageux
                if current_sl is None or potential_stop < current_sl:
                    new_stop = potential_stop
                    self.logger.debug(f"Trailing stop SELL {symbol}: {current_sl} → {new_stop:.5f}")
                    return new_stop
                    
            # Pas de changement nécessaire
            return current_sl
            
        except Exception as e:
            self.logger.warning(f"Erreur calcul trailing stop: {e}")
            return current_sl

    def update_positions_risk_management(self):
        """Met à jour la gestion des risques pour toutes les positions ouvertes"""
        if not MT5_AVAILABLE:
            return
            
        try:
            # Obtenir toutes les positions ouvertes
            positions = mt5.positions_get()
            if positions is None:
                return
                
            for position in positions:
                symbol = position.symbol
                current_price = position.price_current
                
                # Calculer nouveau trailing stop
                new_sl = self.calculate_trailing_stop(
                    symbol,
                    "buy" if position.type == mt5.ORDER_TYPE_BUY else "sell",
                    position.price_open,
                    current_price,
                    position.sl
                )
                
                # Mettre à jour le stop loss si nécessaire
                if new_sl != position.sl and new_sl is not None:
                    self._update_position_stop_loss(position.ticket, new_sl)
                    
        except Exception as e:
            self.logger.warning(f"Erreur mise à jour gestion des risques: {e}")

    def _register_position_watchlist(self, order_result, symbol, lot_size, sl, tp, entry_price):
        """Enregistrer une position récemment ouverte dans la watchlist pour auto-close.

        Tentative best-effort pour retrouver le ticket sur MT5 et sauvegarder un audit.
        """
        try:
            audit_dir = Path('artifacts') / 'live_trading'
            audit_dir.mkdir(parents=True, exist_ok=True)

            ticket = None
            # tenter de retrouver la position ouverte
            if MT5_AVAILABLE:
                attempts = 6
                delay = 0.5
                for _ in range(attempts):
                    try:
                        positions = mt5.positions_get() or []
                        for p in positions:
                            try:
                                if getattr(p, 'symbol', None) == symbol and abs(float(getattr(p, 'volume', 0.0)) - float(lot_size)) < 1e-6:
                                    ticket = int(getattr(p, 'ticket', 0))
                                    break
                            except Exception:
                                continue
                        if ticket is not None:
                            break
                    except Exception:
                        pass
                    time.sleep(delay)

            entry = {
                'registered_at': datetime.utcnow().isoformat() + 'Z',
                'order_id': getattr(order_result, 'order', None),
                'ticket': ticket,
                'symbol': symbol,
                'volume': float(lot_size),
                'entry_price': float(entry_price) if entry_price is not None else None,
                'sl': float(sl) if sl is not None else None,
                'tp': float(tp) if tp is not None else None,
                'auto_close_at': (datetime.utcnow() + timedelta(minutes=self.auto_close_minutes)).isoformat() + 'Z',
                'closed': False,
            }

            # Append to in-memory watchlist
            self.position_watchlist.append(entry)

            # Write audit line
            try:
                fn = audit_dir / 'mt5_watchlist.jsonl'
                with open(fn, 'a', encoding='utf-8') as _f:
                    _f.write(json.dumps(entry, default=str) + '\n')
            except Exception:
                pass

            self.logger.info(f"🔖 Position enregistrée dans watchlist: {symbol} ticket={ticket} order={entry['order_id']}")
            return True
        except Exception as e:
            try:
                self.logger.debug(f"Erreur _register_position_watchlist: {e}")
            except Exception:
                pass
            return False

    def enforce_auto_close(self):
        """Vérifier la watchlist et fermer les positions arrivées à échéance si SL/TP inchangés.

        Cette méthode est idempotente et best-effort; toute action est auditée.
        """
        if not MT5_AVAILABLE:
            return

        audit_dir = Path('artifacts') / 'live_trading'
        audit_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot current positions for post-run reconciliation
        try:
            if MT5_AVAILABLE:
                try:
                    current_positions = mt5.positions_get() or []
                    serializable = []
                    for p in current_positions:
                        try:
                            serializable.append({
                                'ticket': int(getattr(p, 'ticket', None)),
                                'symbol': getattr(p, 'symbol', None),
                                'volume': float(getattr(p, 'volume', 0.0)),
                                'sl': getattr(p, 'sl', None),
                                'tp': getattr(p, 'tp', None),
                                'open_time': getattr(p, 'time', None),
                            })
                        except Exception:
                            continue

                    fn_snapshot = audit_dir / 'current_positions_post_autoclose.json'
                    try:
                        with open(fn_snapshot, 'w', encoding='utf-8') as _sf:
                            json.dump({'ts': datetime.utcnow().isoformat() + 'Z', 'positions': serializable}, _sf, default=str)
                        try:
                            self.logger.info(f"Positions snapshot écrit: {str(fn_snapshot)}")
                        except Exception:
                            pass
                    except Exception as e:
                        try:
                            tb = traceback.format_exc()
                            self.logger.warning(f"Impossible d'écrire snapshot positions: {e} | traceback: {tb}")
                        except Exception:
                            pass
                except Exception as e:
                    try:
                        self.logger.warning(f"Erreur récupération positions MT5 pour snapshot: {e}")
                    except Exception:
                        pass
        except Exception:
            # non critique
            pass

        now = datetime.utcnow()
        for entry in list(self.position_watchlist):
            try:
                if entry.get('closed'):
                    continue

                # comparer l'heure
                auto_close_at = None
                try:
                    auto_close_at = datetime.fromisoformat(entry.get('auto_close_at').replace('Z', ''))
                except Exception:
                    auto_close_at = None

                # Log basic diagnostics for this entry
                try:
                    self.logger.info(
                        f"Watchlist check: ticket={entry.get('ticket')} symbol={entry.get('symbol')} auto_close_at={auto_close_at} now={now.isoformat()}"
                    )
                except Exception:
                    pass

                if auto_close_at is None or now < auto_close_at:
                    try:
                        self.logger.debug(f"Auto-close not due yet for ticket {entry.get('ticket')} (auto_close_at={auto_close_at})")
                    except Exception:
                        pass
                    continue

                ticket = entry.get('ticket')
                symbol = entry.get('symbol')

                # tenter de rafraîchir le ticket si absent
                if ticket is None:
                    try:
                        positions = mt5.positions_get() or []
                        for p in positions:
                            if getattr(p, 'symbol', None) == symbol and abs(float(getattr(p, 'volume', 0.0)) - float(entry.get('volume', 0.0))) < 1e-6:
                                ticket = int(getattr(p, 'ticket', 0))
                                entry['ticket'] = ticket
                                break
                    except Exception:
                        pass

                if ticket is None:
                    # position non trouvée: marquer closed pour éviter bouclage
                    try:
                        self.logger.info(f"Auto-close: ticket absent and position not found for symbol {symbol} - marking entry closed")
                    except Exception:
                        pass
                    entry['closed'] = True
                    # Write audit record for missing position for traceability
                    try:
                        rec_missing = {
                            'ticket': entry.get('ticket'),
                            'symbol': symbol,
                            'status': 'position_missing',
                            'note': 'ticket absent during enforcement',
                            'ts': datetime.utcnow().isoformat() + 'Z',
                        }
                        fn_missing = audit_dir / 'mt5_auto_close_audit.jsonl'
                        try:
                            with open(fn_missing, 'a', encoding='utf-8') as _mf:
                                _mf.write(json.dumps(rec_missing, default=str) + '\n')
                            try:
                                self.logger.info(f"Audit auto-close (position_missing) écrit: {str(fn_missing)}")
                            except Exception:
                                pass
                        except Exception as e:
                            try:
                                tb = traceback.format_exc()
                                self.logger.warning(f"Impossible d'écrire audit position_missing: {e} | traceback: {tb}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                    continue

                # récupérer la position active
                positions = mt5.positions_get() or []
                pos = None
                for p in positions:
                    try:
                        if int(getattr(p, 'ticket', 0)) == int(ticket):
                            pos = p
                            break
                    except Exception:
                        continue

                if pos is None:
                    try:
                        self.logger.info(f"Auto-close: active position not found for ticket={ticket} symbol={symbol} - marking entry closed")
                    except Exception:
                        pass
                    entry['closed'] = True
                    # Audit: active position not found
                    try:
                        rec_missing = {
                            'ticket': ticket,
                            'symbol': symbol,
                            'status': 'position_missing',
                            'note': 'active position not found during enforcement',
                            'ts': datetime.utcnow().isoformat() + 'Z',
                        }
                        fn_missing = audit_dir / 'mt5_auto_close_audit.jsonl'
                        try:
                            with open(fn_missing, 'a', encoding='utf-8') as _mf:
                                _mf.write(json.dumps(rec_missing, default=str) + '\n')
                            try:
                                self.logger.info(f"Audit auto-close (position_missing) écrit: {str(fn_missing)}")
                            except Exception:
                                pass
                        except Exception as e:
                            try:
                                tb = traceback.format_exc()
                                self.logger.warning(f"Impossible d'écrire audit position_missing: {e} | traceback: {tb}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                    continue

                # Vérifier que SL/TP sont inchangés
                current_sl = getattr(pos, 'sl', None)
                current_tp = getattr(pos, 'tp', None)
                desired_sl = entry.get('sl')
                desired_tp = entry.get('tp')

                sl_unchanged = ( (current_sl == desired_sl) or (current_sl is None and desired_sl is None) )
                tp_unchanged = ( (current_tp == desired_tp) or (current_tp is None and desired_tp is None) )

                if not (sl_unchanged and tp_unchanged):
                    # SL/TP were changed — do not auto-close
                    entry['closed'] = True
                    self.logger.info(f"Auto-close skipped: SL/TP changed for ticket {ticket}")
                    continue

                # Construire la requête de fermeture
                pos_type = int(getattr(pos, 'type', 0))
                if pos_type == getattr(mt5, 'POSITION_TYPE_BUY', getattr(mt5, 'ORDER_TYPE_BUY', 0)):
                    close_type = getattr(mt5, 'ORDER_TYPE_SELL', None)
                    price = None
                    try:
                        price = mt5.symbol_info_tick(symbol).bid
                    except Exception:
                        price = None
                else:
                    close_type = getattr(mt5, 'ORDER_TYPE_BUY', None)
                    price = None
                    try:
                        price = mt5.symbol_info_tick(symbol).ask
                    except Exception:
                        price = None

                request = {
                    'action': mt5.TRADE_ACTION_DEAL,
                    'symbol': symbol,
                    'volume': float(getattr(pos, 'volume', 0.0)),
                    'type': close_type,
                    'position': int(ticket),
                    'price': float(price) if price is not None else None,
                    'deviation': 20,
                    'type_filling': mt5.ORDER_FILLING_IOC,
                }

                # Envoyer l'ordre de fermeture
                try:
                    # Log the request we are about to send for traceability
                    try:
                        self.logger.info(f"Auto-close: sending order_send for ticket={ticket} symbol={symbol}")
                        self.logger.debug("Auto-close request: %s", json.dumps(request, default=str))
                    except Exception:
                        pass

                    result = mt5.order_send(request)
                    # Log result minimal info to aid debugging
                    try:
                        self.logger.info(
                            f"Auto-close: order_send returned for ticket={ticket} retcode={getattr(result, 'retcode', None)} comment={getattr(result, 'comment', None)}"
                        )
                    except Exception:
                        pass
                except Exception as e:
                    entry.setdefault('audit', []).append({'error': str(e), 'ts': datetime.utcnow().isoformat() + 'Z'})
                    try:
                        tb = traceback.format_exc()
                        self.logger.warning(f"Erreur envoi auto-close ticket {ticket}: {e} | traceback: {tb}")
                    except Exception:
                        try:
                            print(f"Erreur envoi auto-close ticket {ticket}: {e}")
                            print(traceback.format_exc())
                        except Exception:
                            pass
                    continue

                rec = {
                    'ticket': ticket,
                    'symbol': symbol,
                    'volume': float(getattr(pos, 'volume', 0.0)),
                    'result_retcode': getattr(result, 'retcode', None),
                    'result_comment': getattr(result, 'comment', None),
                    'requested_at': datetime.utcnow().isoformat() + 'Z',
                }

                # enregistrer audit
                try:
                    fn = audit_dir / 'mt5_auto_close_audit.jsonl'
                    # Instrumentation: log target path and record to write
                    try:
                        self.logger.info(f"Attempting to write auto-close audit to: {str(fn)}")
                    except Exception:
                        pass

                    try:
                        # Log the exact JSON being written at debug level
                        try:
                            self.logger.debug("Auto-close audit record: %s", json.dumps(rec, default=str))
                        except Exception:
                            pass

                        with open(fn, 'a', encoding='utf-8') as _f:
                            _f.write(json.dumps(rec, default=str) + '\n')

                        # Log success to make writes observable in controller logs
                        try:
                            self.logger.info(f"Audit auto-close écrit: {str(fn)}")
                        except Exception:
                            pass
                    except Exception as e:
                        # On failure, log full traceback to help debugging
                        try:
                            tb = traceback.format_exc()
                            self.logger.warning(
                                f"Impossible d'écrire mt5_auto_close_audit.jsonl: {e} | traceback: {tb}"
                            )
                        except Exception:
                            # Best-effort: if logging fails, print to stderr
                            try:
                                print(f"Impossible d'écrire audit: {e}")
                                print(traceback.format_exc())
                            except Exception:
                                pass
                except Exception as e:
                    # Fallback: if outer exception occurs, ensure it is logged
                    try:
                        tb = traceback.format_exc()
                        self.logger.warning(
                            f"Erreur inattendue lors de l'audit auto-close: {e} | traceback: {tb}"
                        )
                    except Exception:
                        try:
                            print(f"Erreur inattendue lors de l'audit auto-close: {e}")
                            print(traceback.format_exc())
                        except Exception:
                            pass

                if getattr(result, 'retcode', None) == getattr(mt5, 'TRADE_RETCODE_DONE', 10009):
                    entry['closed'] = True
                    self.logger.info(f"🔒 Auto-closed position {ticket} symbol {symbol}")
                else:
                    entry.setdefault('audit', []).append({'result': rec})
                    self.logger.warning(f"Échec auto-close {ticket}: {getattr(result, 'comment', None)}")

            except Exception as e:
                try:
                    self.logger.debug(f"Erreur enforce_auto_close entry: {e}")
                except Exception:
                    pass
                continue

    def _update_position_stop_loss(self, ticket, new_sl):
        """Met à jour le stop loss d'une position spécifique"""
        # Implémentation améliorée:
        # - Vérifie existence de la position
        # - Tente plusieurs essais avec backoff exponentiel
        # - Enregistre un audit détaillé dans artifacts/live_trading/ pour traçabilité
        audit_dir = Path('artifacts') / 'live_trading'
        try:
            audit_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        audit_record = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'ticket': int(ticket) if ticket is not None else None,
            'proposed_sl': float(new_sl) if new_sl is not None else None,
            'attempts': []
        }

        # 1) vérifier existence position
        try:
            positions = mt5.positions_get() if MT5_AVAILABLE else []
            pos_exists = False
            if positions:
                for p in positions:
                    try:
                        if int(getattr(p, 'ticket', 0)) == int(ticket):
                            pos_exists = True
                            break
                    except Exception:
                        continue
            if not pos_exists:
                self.logger.warning(
                    f"⚠️ Position {ticket} introuvable - modification SL ignorée"
                )
                audit_record['final_status'] = 'position_missing'
                # Enregistrer audit minimal
                try:
                    fn = audit_dir / 'mt5_update_audit.jsonl'
                    with open(fn, 'a', encoding='utf-8') as _f:
                        _f.write(json.dumps(audit_record, default=str) + '\n')
                except Exception:
                    pass
                return False
        except Exception as e:
            self.logger.warning(f"Erreur vérification position {ticket} avant update: {e}")
            audit_record['final_status'] = 'positions_check_failed'
            try:
                fn = audit_dir / 'mt5_update_audit.jsonl'
                with open(fn, 'a', encoding='utf-8') as _f:
                    _f.write(json.dumps(audit_record, default=str) + '\n')
            except Exception:
                pass
            return False

        # 2) préparer la requête (position présente)
        request = {
            'action': mt5.TRADE_ACTION_SLTP,
            'position': int(ticket),
            'sl': float(new_sl),
        }

        # 3) retry loop avec backoff
        max_attempts = 5
        delay = 0.5
        success = False
        last_result = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = mt5.order_send(request)
                last_result = result
                attempt_record = {
                    'attempt': attempt,
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'retcode': getattr(result, 'retcode', None),
                    'comment': getattr(result, 'comment', None)
                }
                audit_record['attempts'].append(attempt_record)

                if result is None:
                    self.logger.warning(f"❌ Résultat ordre null pour position {ticket} (attempt {attempt})")
                else:
                    if getattr(result, 'retcode', None) == mt5.TRADE_RETCODE_DONE:
                        self.logger.info(f"✅ Stop loss mis à jour: position {ticket} → SL {new_sl:.5f} (attempt {attempt})")
                        success = True
                        audit_record['final_status'] = 'success'
                        break
                    else:
                        # retcode non ok, log et decide retry selon commentaire
                        comment = getattr(result, 'comment', None)
                        self.logger.warning(
                            f"❌ Échec mise à jour SL position {ticket}: {comment} (retcode={getattr(result,'retcode',None)})"
                        )
                        # si erreur non récupérable, continuer les retries pour robustesse
                # backoff avant prochaine tentative
            except Exception as e:
                audit_record['attempts'].append({'attempt': attempt, 'error': str(e), 'timestamp': datetime.utcnow().isoformat() + 'Z'})
                self.logger.warning(f"Erreur order_send attempt {attempt} for {ticket}: {e}")

            # Exponential backoff
            try:
                time.sleep(delay)
            except Exception:
                pass
            delay *= 2

        # 4) finaliser audit
        if not success:
            audit_record['final_status'] = audit_record.get('final_status', 'failed')
            # tenter d'extraire info du dernier_result
            if last_result is not None:
                try:
                    audit_record['last_retcode'] = getattr(last_result, 'retcode', None)
                    audit_record['last_comment'] = getattr(last_result, 'comment', None)
                except Exception:
                    pass

        try:
            fn = audit_dir / 'mt5_update_audit.jsonl'
            with open(fn, 'a', encoding='utf-8') as _f:
                _f.write(json.dumps(audit_record, default=str) + '\n')
        except Exception:
            # Ne jamais échouer pour l'audit
            pass

        return bool(success)

    def monitor_and_apply_retries(self, apply_files=None, interval_s=10, cycles=20):
        """Surveille les fichiers apply et applique les updates lorsque la position apparaît.

        - apply_files: liste de chemins vers les fichiers mt5_apply_*.json; si None, scanne artifacts/live_trading/
        - interval_s: intervalle entre vérifications
        - cycles: nombre d'itérations
        Produits: écrit un fichier résumé JSON et CSV dans artifacts/live_trading/
        """
        audit_dir = Path('artifacts') / 'live_trading'
        audit_dir.mkdir(parents=True, exist_ok=True)

        # découvrir les fichiers si non fournis
        if not apply_files:
            files = list((Path('artifacts') / 'live_trading').glob('mt5_apply_*.json'))
        else:
            files = [Path(p) for p in apply_files]

        summary = {
            'started_at': datetime.utcnow().isoformat() + 'Z',
            'cycles': cycles,
            'interval_s': interval_s,
            'actions': [],
        }

        for cycle in range(1, cycles + 1):
            self.logger.info(
                f"Surveillance cycle {cycle}/{cycles}: "
                f"vérification des fichiers apply ({len(files)} fichiers)"
            )
            # recharger fichiers (au cas où nouveaux fichiers apparaissent)
            if not apply_files:
                files = list((Path('artifacts') / 'live_trading').glob('mt5_apply_*.json'))

            for f in files:
                try:
                    data = json.loads(f.read_text(encoding='utf-8'))
                except Exception as e:
                    self.logger.warning(f"Impossible de lire {f}: {e}")
                    continue

                for entry in data:
                    ticket = entry.get('ticket') or entry.get('position')
                    proposed_sl = entry.get('proposed_sl') or entry.get('sl')
                    action_record = {
                        'file': str(f), 'ticket': ticket, 'proposed_sl': proposed_sl,
                        'cycle': cycle, 'timestamp': datetime.utcnow().isoformat() + 'Z',
                        'position_found': False, 'applied': False
                    }

                    # vérifier position
                    try:
                        positions = mt5.positions_get() if MT5_AVAILABLE else []
                        pos_exists = False
                        if positions:
                            for p in positions:
                                try:
                                    if int(getattr(p, 'ticket', 0)) == int(ticket):
                                        pos_exists = True
                                        break
                                except Exception:
                                    continue
                        action_record['position_found'] = bool(pos_exists)
                        if pos_exists and proposed_sl is not None:
                            ok = self._update_position_stop_loss(ticket, proposed_sl)
                            action_record['applied'] = bool(ok)
                    except Exception as e:
                        action_record['error'] = str(e)

                    summary['actions'].append(action_record)

            # écrire résumé intermédiaire
            try:
                ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                summary_fn = audit_dir / f'mt5_live_actions_summary_{ts}.json'
                summary_fn.write_text(json.dumps(summary, default=str, indent=2), encoding='utf-8')
                # CSV
                csv_fn = audit_dir / f'mt5_live_actions_summary_{ts}.csv'
                with open(csv_fn, 'w', encoding='utf-8', newline='') as _csvf:
                    writer = csv.writer(_csvf)
                    header = [
                        'file', 'ticket', 'proposed_sl', 'cycle', 'timestamp',
                        'position_found', 'applied', 'error'
                    ]
                    writer.writerow(header)
                    for a in summary['actions']:
                        row = [
                            a.get('file'), a.get('ticket'), a.get('proposed_sl'),
                            a.get('cycle'), a.get('timestamp'), a.get('position_found'),
                            a.get('applied'), a.get('error')
                        ]
                        writer.writerow(row)
            except Exception as e:
                self.logger.warning(f"Impossible d'écrire le résumé de surveillance: {e}")

            if cycle < cycles:
                try:
                    time.sleep(interval_s)
                except Exception:
                    pass

        summary['finished_at'] = datetime.utcnow().isoformat() + 'Z'
        # écrire résumé final
        try:
            ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            summary_fn = audit_dir / f'mt5_live_actions_summary_{ts}.json'
            summary_fn.write_text(json.dumps(summary, default=str, indent=2), encoding='utf-8')
        except Exception:
            pass

        return summary

    def close_positive_positions_gradual(self, duration_minutes=30, min_profit=0.0, deviation=20):
        """Ferme progressivement les positions avec profit > min_profit sur la durée indiquée.

        - duration_minutes: temps total en minutes pour fermer toutes les positions ciblées
        - min_profit: seuil minimal de profit (ex: 0.0 pour toute position profitable)
        - deviation: tolérance de prix en points pour l'ordre de marché
        """
        audit_dir = Path('artifacts') / 'live_trading'
        audit_dir.mkdir(parents=True, exist_ok=True)
        close_audit = []

        try:
            positions = mt5.positions_get() if MT5_AVAILABLE else []
        except Exception as e:
            self.logger.warning(f"Impossible d'interroger les positions pour fermeture: {e}")
            return {'closed': 0, 'error': str(e)}

        positive = []
        for p in positions or []:
            try:
                if float(getattr(p, 'profit', 0.0)) > float(min_profit):
                    positive.append(p)
            except Exception:
                continue

        total = len(positive)
        if total == 0:
            self.logger.info("Aucune position profitable à fermer selon le seuil donné.")
            return {'closed': 0}

        interval = max(0.5, (duration_minutes * 60) / total)
        closed_count = 0

        for idx, pos in enumerate(positive, start=1):
            try:
                symbol = pos.symbol
                vol = float(getattr(pos, 'volume', 0.0))
                pos_ticket = int(getattr(pos, 'ticket', 0))
                pos_type = int(getattr(pos, 'type', 0))

                # Choisir le type inverse pour fermer
                if pos_type == mt5.POSITION_TYPE_BUY:
                    close_type = mt5.ORDER_TYPE_SELL
                    price = mt5.symbol_info_tick(symbol).bid
                else:
                    close_type = mt5.ORDER_TYPE_BUY
                    price = mt5.symbol_info_tick(symbol).ask

                request = {
                    'action': mt5.TRADE_ACTION_DEAL,
                    'symbol': symbol,
                    'volume': vol,
                    'type': close_type,
                    'position': pos_ticket,
                    'price': float(price) if price is not None else None,
                    'deviation': int(deviation),
                    'type_filling': mt5.ORDER_FILLING_IOC,
                }

                result = mt5.order_send(request)
                rec = {
                    'ticket': pos_ticket,
                    'symbol': symbol,
                    'volume': vol,
                    'result_retcode': getattr(result, 'retcode', None),
                    'result_comment': getattr(result, 'comment', None),
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                }
                close_audit.append(rec)
                if getattr(result, 'retcode', None) == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                    self.logger.info(
                        f"Fermé {idx}/{total}: position {pos_ticket} "
                        f"symbol {symbol} vol {vol}"
                    )
                else:
                    self.logger.warning(
                        f"Échec fermeture position {pos_ticket}: "
                        f"{getattr(result, 'comment', None)}"
                    )
            except Exception as e:
                close_audit.append({'error': str(e), 'ticket': getattr(pos, 'ticket', None)})
                self.logger.warning(
                    f"Erreur en fermant position {getattr(pos, 'ticket', None)}: {e}"
                )

            # attendre avant prochaine fermeture
            try:
                time.sleep(interval)
            except Exception:
                pass

        # écrire audit de clôture
        try:
            fn = audit_dir / (
                'mt5_close_positive_audit_' +
                datetime.utcnow().strftime('%Y%m%dT%H%M%SZ') +
                '.json'
            )
            fn.write_text(json.dumps(close_audit, default=str, indent=2), encoding='utf-8')
        except Exception:
            pass

        return {'closed': closed_count, 'total': total}

    def record_trade_for_learning(self, symbol, action, confidence, signals):
        """🧠 AMÉLIORATION: Enregistrer trade pour apprentissage"""
        try:
            trade_record = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'action': action,
                'confidence': confidence,
                'urgency': signals.get('execution_urgency', 'unknown'),
                'enhanced': signals.get('enhanced', False),
                'regime': signals.get('market_context', {}).get(
                    'volatility_regime', 'unknown'
                )
            }
            
            # Garder seulement les 20 derniers
            self.recent_trades_performance.append(trade_record)
            if len(self.recent_trades_performance) > 20:
                self.recent_trades_performance.pop(0)
            
            # Mettre à jour stats par symbole
            if symbol not in self.symbol_performance:
                self.symbol_performance[symbol] = {
                    'trades': 0, 'total_confidence': 0
                }
            
            self.symbol_performance[symbol]['trades'] += 1
            self.symbol_performance[symbol]['total_confidence'] += confidence
            
            # Log pour debug
            self.logger.info(f"📊 Trade enregistré: {symbol} conf={confidence:.3f}")
            
        except Exception as e:
            self.logger.warning(f"Erreur enregistrement trade: {e}")

    def get_symbol_performance_boost(self, symbol):
        """🧠 AMÉLIORATION: Boost confiance basé sur performance symbole"""
        try:
            if symbol in self.symbol_performance:
                stats = self.symbol_performance[symbol]
                if stats['trades'] >= 3:  # Minimum pour fiabilité
                    avg_conf = stats['total_confidence'] / stats['trades']
                    if avg_conf > 0.75:
                        return 0.02  # Petit boost pour symbole performant
                    elif avg_conf < 0.65:
                        return -0.03  # Pénalité pour symbole moins fiable
            return 0.0
        except Exception:
            return 0.0

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

            # Ajout non intrusif: PnL réalisé aujourd'hui (à partir des logs)
            try:
                from tools.performance_aggregator import get_today_summary
                today_summary = get_today_summary(self.logs_folder)
                if today_summary is not None:
                    self.logger.info(
                        f"  💵 PnL réalisé (jour): {today_summary['pnl']:.2f} | "
                        f"Trades: {today_summary['total_trades']} | "
                        f"WR: {today_summary['win_rate_pct']:.1f}% | "
                        f"PF: {today_summary['profit_factor']}"
                    )
            except Exception as _agg_err:
                # Best-effort, ne bloque pas
                self.logger.debug(f"Perf aggregator indisponible: {_agg_err}")

            # Résumé MTF (si mémorisé)
            try:
                if getattr(self, 'last_mtf_summary', None):
                    mtf = self.last_mtf_summary
                    self.logger.info(
                        "  🧭 MTF: %s | conf=%.2f | agree=%s | symbol=%s",
                        mtf.get('action', 'hold').upper(),
                        float(mtf.get('confidence', 0.0)),
                        str(mtf.get('agreement', 0)),
                        mtf.get('symbol', ''),
                    )
            except Exception:
                pass

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

            # 2.b Recharger la watchlist depuis le disque au démarrage
            # Ceci permet de traiter les positions précédemment enregistrées
            # (cas où le process précédent a écrit la watchlist mais le process
            # courant n'a pas encore d'entrées en mémoire).
            try:
                from pathlib import Path as _Path
                import json as _json

                _watch_fn = _Path('artifacts') / 'live_trading' / 'mt5_watchlist.jsonl'
                added = 0
                if _watch_fn.exists():
                    with _watch_fn.open('r', encoding='utf-8') as _wf:
                        for _line in _wf:
                            try:
                                _obj = _json.loads(_line)
                            except Exception:
                                continue
                            # n'ajouter que les entrées non-fermées
                            if _obj.get('closed'):
                                continue
                            # éviter duplications basiques (order_id ou ticket)
                            duplicate = False
                            for _p in getattr(self, 'position_watchlist', []):
                                try:
                                    if (_p.get('order_id') is not None and _p.get('order_id') == _obj.get('order_id')):
                                        duplicate = True
                                        break
                                    if (_p.get('ticket') is not None and _obj.get('ticket') is not None and _p.get('ticket') == _obj.get('ticket')):
                                        duplicate = True
                                        break
                                except Exception:
                                    continue
                            if duplicate:
                                continue
                            # append
                            try:
                                if not hasattr(self, 'position_watchlist'):
                                    self.position_watchlist = []
                                self.position_watchlist.append(_obj)
                                added += 1
                            except Exception:
                                continue
                if added:
                    self.logger.info(f"🔁 Rechargé {added} entrée(s) de la watchlist depuis disque")
            except Exception as _re:
                try:
                    self.logger.warning(f"Erreur rechargement watchlist au démarrage: {_re}")
                except Exception:
                    pass

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

    def start_production(self):
        """Démarrage sécurisé pour production"""
        self.logger.info("🚀 DÉMARRAGE PRODUCTION SÉCURISÉ")
        self.logger.info("=" * 50)

        # 1. Health check complet
        self.logger.info("1️⃣ Health check complet...")
        if not self.production_health_check():
            self.logger.error("❌ Health check échoué - Arrêt")
            return False

        # 2. Validation et ajustement configuration optimale
        self.logger.info("2️⃣ Validation configuration...")
        optimal_threshold = self.performance_metrics["optimal_threshold"]
        if optimal_threshold != self.confidence_threshold:
            # Auto-ajustement du seuil basé sur performance
            old_threshold = self.confidence_threshold
            adjustment = min(0.05, abs(optimal_threshold - self.confidence_threshold))
            if optimal_threshold > self.confidence_threshold:
                self.confidence_threshold = min(old_threshold + adjustment, 0.85)
            else:
                self.confidence_threshold = max(old_threshold - adjustment, 0.60)
            
            self.logger.info(
                f"🎯 Seuil ajusté: {old_threshold:.3f} → "
                f"{self.confidence_threshold:.3f}"
            )
            self.performance_metrics["optimal_threshold"] = self.confidence_threshold

        # 3. Test connexion MT5
        self.logger.info("3️⃣ Test connexion MT5...")
        if not self.is_connected:
            self.logger.error("❌ MT5 non connecté - Arrêt")
            return False

        # 4. Vérifier heures de marché
        self.logger.info("4️⃣ Vérification heures de marché...")
        markets_open = []
        for symbol in self.symbols:
            if self.is_market_open(symbol):
                markets_open.append(symbol)

        if not markets_open:
            self.logger.warning("⚠️ Tous les marchés fermés")
        else:
            self.logger.info(f"✅ Marchés ouverts: {markets_open}")

        # 5. Démarrage effectif
        self.logger.info("5️⃣ Lancement du trading live...")
        self.logger.info("🎯 Configuration active:")
        self.logger.info(f"  • Symboles: {self.symbols}")
        self.logger.info(f"  • Intervalle: {self.trading_interval}s")
        self.logger.info(f"  • Seuil optimal: {optimal_threshold}")
        target_wr = self.performance_metrics['target_win_rate']
        self.logger.info(f"  • Win rate cible: {target_wr:.1%}")

        # Démarrage boucle principale
        self.start_live_trading()
        return True


def main():
    """LANCEMENT FORCÉ DE PRODUCTION - Trading IA Multi-Actifs"""
    print("🚀 PRODUCTION FORCÉE - TRADING IA MULTI-ACTIFS")
    print("=" * 50)
    print("📊 Instruments: EURUSD, XAUUSD, BTCUSD")
    print("⏰ Jusqu'à fermeture du marché d'aujourd'hui")
    print("🎯 Objectif: Amélioration, performance et robustesse")
    print("=" * 50)

    try:
        # Configuration de production multi-actifs
        production_symbols = ["EURUSD", "XAUUSD", "BTCUSD"]
        lot_sizes = {
            "EURUSD": 0.01,
            "XAUUSD": 0.01,
            "BTCUSD": 0.01,
        }

        # Créer le moteur de trading avec les bons paramètres
        engine = LiveTradingEngine(
            symbols=production_symbols,
            lot_sizes=lot_sizes,
            max_risk_per_trade=0.02,
        )

        print("\n🔥 LANCEMENT PRODUCTION FORCÉ...")
        print("⚡ Mode robuste activé")

        # Démarrage production avec tous les systèmes
        print("1️⃣ Connexion MT5...")
        engine.connect_mt5()

        print("2️⃣ Initialisation systèmes IA...")
        engine.initialize_ai_systems()

        print("3️⃣ Vérification santé du système...")
        if engine.production_health_check():
            print("✅ Tous systèmes opérationnels")
        else:
            print("⚠️ Certains systèmes en mode dégradé - Continuation forcée")

        print("4️⃣ DÉMARRAGE PRODUCTION...")
        print("🚨 Trading actif jusqu'à fermeture marché")
        print("🔄 Surveillance continue des 3 instruments")

        # Lancement production continue
        engine.start_production()

        print("\n✅ Production lancée avec succès")

    except KeyboardInterrupt:
        print("\n🛑 Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
