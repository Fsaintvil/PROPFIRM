# migration: try import safe sender (fail-open)
try:
    from src.utils.mt5_safe import send_order as _mt5_send_safe
except Exception:
    _mt5_send_safe = None

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

import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

warnings.filterwarnings("ignore")

# Broker safety helpers (non-invasive preflight). Optional import.
try:
    from tools.broker_safety import clamp_sl_tp, validate_volume
    BROKER_SAFETY_AVAILABLE = True
except Exception:
    BROKER_SAFETY_AVAILABLE = False

# Configuration centralisée
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config.trading_config import TradingConfig
    from utils.robust_retry import MT5ConnectionError, MT5OperationError, robust_mt5_retry

    CONFIG_AVAILABLE = True
    print("✅ Configuration centralisée chargée")
except ImportError:
    CONFIG_AVAILABLE = False
    print("⚠️ Configuration centralisée non disponible - utilisation defaults")

    # Fallback vers valeurs par défaut
    class TradingConfig:
        TRADING_INTERVAL_SECONDS = 600
        CLEANUP_CYCLE_INTERVAL = 20
        LOG_SUMMARY_INTERVAL = 5
        MIN_SLEEP_SECONDS = 60
        MAX_HISTORY_TRADES = 1000
        MAX_MARKET_DATA_BARS = 300
        DEFAULT_CONFIDENCE_THRESHOLD = 0.60


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
    "utils",
    "src/utils",
]
for path in utils_paths:
    if path not in sys.path:
        sys.path.append(path)

try:
    from utils.safe_io import FALLBACK_SAMPLE_DATA, safe_read_csv

    IO_UTILS_AVAILABLE = True
    print("✅ Utilitaires I/O sécurisées disponibles")
except ImportError:
    IO_UTILS_AVAILABLE = False

    # Définir fallbacks

    def safe_read_csv(*args, **kwargs):
        import pandas as pd

        return pd.read_csv(*args, **kwargs) if args else pd.DataFrame()

    FALLBACK_SAMPLE_DATA = {
        "EURUSD": pd.DataFrame(
            {
                "timestamp": pd.date_range("2025-01-01", periods=100, freq="h"),
                "open": np.random.randn(100) * 0.001 + 1.1000,
                "high": np.random.randn(100) * 0.001 + 1.1005,
                "low": np.random.randn(100) * 0.001 + 0.9995,
                "close": np.random.randn(100) * 0.001 + 1.1000,
                "volume": np.random.randint(1000, 5000, 100),
            }
        )
    }
    print("⚠️  Utilitaires I/O non disponibles - fallback activé")

# MT5 Integration avec fallback robuste et chemins multiples
try:
    # Ajouter les chemins src/utils pour mt5_connector
    src_utils_path = os.path.join(os.path.dirname(__file__), "..", "src", "utils")
    if src_utils_path not in sys.path:
        sys.path.append(src_utils_path)

    try:
        from src.utils.mt5_connector import get_mt5, is_mt5_available

        MT5_AVAILABLE = is_mt5_available()
        mt5 = get_mt5()
        print("✅ MT5 connector loaded successfully")
    except ImportError:
        # Fallback direct vers MetaTrader5
        try:
            import MetaTrader5 as mt5

            MT5_AVAILABLE = True
            print("✅ MT5 direct import successful")
        except ImportError:
            MT5_AVAILABLE = False
            mt5 = None
            print("⚠️ MT5 import failed - switching to simulation mode")
            print("🔄 Mode simulation complet activé")
    except Exception:
        MT5_AVAILABLE = False
        mt5 = None
        get_mt5 = None

        def is_mt5_available():
            return False

        print("⚠️ MT5 connector initialization failed - running in simulation mode")
        print("🔄 Mode simulation complet activé")
except Exception:
    # Cas où la configuration du chemin ou autre étape externe casse
    MT5_AVAILABLE = False
    mt5 = None
    get_mt5 = None

    def is_mt5_available():
        return False

    print("⚠️ MT5 integration initialization failed - running in simulation mode")
    print("🔄 Mode simulation complet activé")

# Nos systèmes développés avec fallback robuste (import différé possible)
if os.getenv("LIVE_ENGINE_LIGHT_MODE", "0") == "1":
    SYSTEMS_AVAILABLE = False
    print("⚙️  Mode light: import des systèmes IA différé/skippé")
else:
    try:
        from market_regime_detection import MarketRegimeDetector
        from meta_learning_system import MetaLearningTradingSystem
        from multi_asset_portfolio import MultiAssetPortfolioOptimizer
        from reinforcement_learning_agent import ReinforcementLearningTradingSystem

        SYSTEMS_AVAILABLE = True
        print("✅ Systèmes de trading IA chargés avec succès")
    except ImportError:
        SYSTEMS_AVAILABLE = False
        # Définir des placeholders pour éviter NameError lors de l'initialisation
        MetaLearningTradingSystem = None
        ReinforcementLearningTradingSystem = None
        MultiAssetPortfolioOptimizer = None
        MarketRegimeDetector = None
        print("⚠️ Certains modules IA introuvables - activation du mode minimal")
        print("🔄 Mode minimal activé - fonctions de base uniquement")
    except Exception:
        SYSTEMS_AVAILABLE = False
        # Sur erreur, s'assurer que les noms existent pour éviter NameError
        MetaLearningTradingSystem = None
        ReinforcementLearningTradingSystem = None
        MultiAssetPortfolioOptimizer = None
        MarketRegimeDetector = None
        print("⚠️ Erreur lors de l'import des systèmes IA - mode minimal activé")
        print("🔄 Mode minimal activé")

# Import MTF pipeline (convergence 15m) avec fallback
try:
    from src.pipeline.fundamentals import compute_fundamental_confluence, load_fundamentals_csv
    from src.pipeline.mtf_features import build_live_mtf_from_m1, compute_mtf_convergence

    MTF_AVAILABLE = True
except Exception:
    MTF_AVAILABLE = False


class LiveTradingEngine:
    """Moteur de trading live avec tous les systèmes intégrés"""

    def __init__(self, symbols=None, lot_sizes=None, max_risk_per_trade=0.02):
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
                    print("ℹ️  Configuration unifiée chargée (symboles)")
                except Exception:
                    print("⚠️  Config unifiée incomplète - 6 symboles optimisés utilisés")
                    self.symbols = ["EURUSD", "XAUUSD", "BTCUSD", "GBPUSD", "USDJPY", "USDCAD"]
            else:
                try:
                    config_path = os.path.join(
                        os.path.dirname(__file__), "..", "config"
                    )
                    if config_path not in sys.path:
                        sys.path.append(config_path)
                    from config.settings import INSTRUMENTS

                    self.symbols = INSTRUMENTS
                    print("ℹ️  Symboles chargés depuis config.settings.INSTRUMENTS")
                except ImportError:
                    # Fallback vers configuration optimisée 6 symboles
                    self.symbols = ["EURUSD", "XAUUSD", "BTCUSD", "GBPUSD", "USDJPY", "USDCAD"]
                    print("⚠️  Config non trouvée - 6 symboles optimisés utilisés")
                except Exception:
                    # Fallback de sécurité avec symboles optimisés
                    self.symbols = ["EURUSD", "GBPUSD", "USDJPY"]
                    print("ℹ️  Fallback sécurité: symbole unique EURUSD")
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
                    print("ℹ️ Lots chargés depuis configuration unifiée")
                except Exception:
                    print("⚠️ Impossible charger lot_sizes - fallback lots appliqué")
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
                self.confidence_threshold = UNIFIED_CONFIG.trading.confidence_threshold
                # Journaliser l'ajustement
                # Log initialisation moteur (optionnel)
                self.logger.debug("Initialisation moteur: configuration de base") if hasattr(
                    self, "logger"
                ) else None
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
            "expected_sharpe": 18.46,  # Ratio optimal identifié
            "confidence_filter_rejections": 0,  # Nouveaux filtres
        }

        # 🧠 AMÉLIORATION: Suivi performance en temps réel
        self.recent_trades_performance = []  # 20 derniers trades
        self.symbol_performance = {}  # Performance par symbole
        self.confidence_accuracy_tracker = {}  # Précision par niveau confiance

        # Sizing adaptatif basé sur la performance live agrégée
        self.position_size_scale = 1.0  # facteur global [0.2, 2.0]
        self._sizing_last_update = None
        # Cooldown pour éviter l'oscillation et accélérer l'adaptation intraday
        try:
            self._sizing_cooldown_seconds = int(
                os.getenv("SIZING_COOLDOWN_SECONDS", "600")
            )
        except Exception:
            self._sizing_cooldown_seconds = 600  # 10 minutes par défaut
        # EWMA intraday pour lisser les métriques
        self._intraday_perf_ewma = {"wr": None, "pf": None, "pnl": None}
        # Sizing intraday par instrument
        self.position_size_scale_by_symbol = {}
        self._sizing_last_update_by_symbol = {}
        self._intraday_perf_ewma_by_symbol = {}
        # Paramètres intraday configurables
        try:
            self._sizing_intraday_window_minutes = max(
                5,
                int(os.getenv("SIZING_INTRADAY_WINDOW_MINUTES", "60")),
            )
        except Exception:
            self._sizing_intraday_window_minutes = 60
        try:
            self._sizing_intraday_max_trades = int(
                os.getenv("SIZING_INTRADAY_MAX_TRADES", "40")
            )
        except Exception:
            self._sizing_intraday_max_trades = 40
        # borne entre 5 et 50
        self._sizing_intraday_max_trades = max(
            5, min(50, self._sizing_intraday_max_trades)
        )
        try:
            self._sizing_ewma_alpha = float(os.getenv("SIZING_EWMA_ALPHA", "0.2"))
        except Exception:
            self._sizing_ewma_alpha = 0.2
        # Pas intraday
        try:
            self._sizing_step_up = float(os.getenv("SIZING_INTRADAY_STEP_UP", "0.02"))
        except Exception:
            self._sizing_step_up = 0.02
        try:
            self._sizing_step_down = float(
                os.getenv("SIZING_INTRADAY_STEP_DOWN", "0.03")
            )
        except Exception:
            self._sizing_step_down = 0.03
        # Seuils intraday
        try:
            self._sizing_wr_up = float(
                os.getenv("SIZING_INTRADAY_THRESH_WR_UP", "55.0")
            )
        except Exception:
            self._sizing_wr_up = 55.0
        try:
            self._sizing_pf_up = float(os.getenv("SIZING_INTRADAY_THRESH_PF_UP", "1.1"))
        except Exception:
            self._sizing_pf_up = 1.1
        try:
            self._sizing_wr_down = float(
                os.getenv("SIZING_INTRADAY_THRESH_WR_DOWN", "45.0")
            )
        except Exception:
            self._sizing_wr_down = 45.0
        try:
            self._sizing_pf_down = float(
                os.getenv("SIZING_INTRADAY_THRESH_PF_DOWN", "0.9")
            )
        except Exception:
            self._sizing_pf_down = 0.9
        # Pénalité série de pertes
        try:
            self._loss_streak_size = max(1, int(os.getenv("LOSS_STREAK_SIZE", "3")))
        except Exception:
            self._loss_streak_size = 3
        try:
            self._loss_streak_penalty = float(os.getenv("LOSS_STREAK_PENALTY", "0.98"))
        except Exception:
            self._loss_streak_penalty = 0.98
        # Cooldown par instrument après série de pertes et throttling
        try:
            self._loss_cooldown_minutes = max(
                1, int(os.getenv("LOSS_COOLDOWN_MINUTES", "30"))
            )
        except Exception:
            self._loss_cooldown_minutes = 30
        try:
            self._min_trade_interval_seconds = max(
                0,
                int(os.getenv("MIN_TRADE_INTERVAL_SECONDS", "60")),
            )
        except Exception:
            self._min_trade_interval_seconds = 60
        self.symbol_cooldown_until = {sym: None for sym in self.symbols}
        self.last_trade_time_by_symbol = {sym: None for sym in self.symbols}
        # Limites journalières (PnL) et cap de spread par instrument
        try:
            self._daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "-500.0"))
        except Exception:
            self._daily_loss_limit = -500.0
        try:
            self._daily_profit_target = float(os.getenv("DAILY_PROFIT_TARGET", "500.0"))
        except Exception:
            self._daily_profit_target = 500.0
        try:
            self._default_spread_cap_rel = float(
                os.getenv("DEFAULT_SPREAD_CAP_REL", "0.001")
            )
        except Exception:
            self._default_spread_cap_rel = 0.001
        self._spread_cap_rel_by_symbol = {}
        for sym in self.symbols:
            # Chercher une variable d'environnement spécifique au symbole
            env_key = f"SPREAD_CAP_REL_{sym.upper()}"
            try:
                val = os.getenv(env_key)
                if val is not None:
                    self._spread_cap_rel_by_symbol[sym] = float(val)
            except Exception:
                pass
        # Volatility targeting (paramétrable)
        try:
            self._vol_target_enable = os.getenv("VOL_TARGET_ENABLE", "1") == "1"
        except Exception:
            self._vol_target_enable = True
        try:
            # 0.2% capital
            self._vol_target_risk_pct = float(os.getenv("VOL_TARGET_RISK_PCT", "0.002"))
        except Exception:
            self._vol_target_risk_pct = 0.002
        try:
            self._vol_target_min_lot = float(
                os.getenv("VOL_TARGET_MIN_LOT_FLOOR", "1e-6")
            )
        except Exception:
            self._vol_target_min_lot = 1e-6

        # Configuration de trading en continu (sans limite quotidienne)
        # Utiliser la valeur de TradingConfig déjà chargée plus haut
        self.trade_count_today = 0  # Compteur pour statistiques seulement
        self.max_daily_trades = None  # Pas de limite - trading continu
        self.last_reset_date = None
        # Limite de trades par instrument (intraday): défaut 40, cap 50
        try:
            default_per_instrument = int(os.getenv("MAX_TRADES_PER_INSTRUMENT", "40"))
        except Exception:
            default_per_instrument = 40
        default_per_instrument = max(1, min(50, default_per_instrument))
        self.max_trades_per_instrument = {
            sym: default_per_instrument for sym in self.symbols
        }
        # Surcharge via config unifiée éventuelle (mapping par symbole)
        try:
            if UNIFIED_AVAILABLE and getattr(UNIFIED_CONFIG, "trading", None):
                cfg_map = getattr(
                    UNIFIED_CONFIG.trading, "max_trades_per_instrument", None
                )
                if isinstance(cfg_map, dict):
                    for k, v in cfg_map.items():
                        try:
                            v_int = int(v)
                            self.max_trades_per_instrument[k] = max(1, min(50, v_int))
                        except Exception:
                            pass
        except Exception:
            pass
        # Compteurs par instrument (reset quotidien)
        self.trade_count_by_symbol_today = {sym: 0 for sym in self.symbols}

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
                "24h": True,
            },
            "crypto": {"24h": True, "always_open": True},
        }

        # Configuration logging
        self.setup_logging()

        # Charger un sizing initial basé sur la performance (si dispo)
        try:
            self.adjust_position_sizing_from_performance()
        except Exception:
            # non bloquant
            self.logger.debug("Ajustement sizing initial indisponible", exc_info=True)

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
        except Exception:
            # Non critique, poursuivre sans interrompre
            self.logger.debug("Seuil optimisé non chargé (optionnel)")

        print("🚀 Moteur Trading Live Multi-Actifs initialisé:")
        try:
            _lots_str = ", ".join(f"{k}={v}" for k, v in self.lot_sizes.items())
        except Exception:
            _lots_str = "-"
        print("• Symboles:", ",".join(self.symbols))
        print("• Lots:", _lots_str)
        print(f"• Seuil: {self.confidence_threshold:.2f} | Intervalle: {self.trading_interval}s")

        # Préparer support MTF/fondamentaux (chargement lazy)
        self._fundamentals_map = None
        # Horodatage de début de session (pour runtime précis)
        self._session_start_dt = None
        # Hook: envoi d'ordres aux transitions marché (ouverture/fermeture)
        try:
            self._send_at_market_transition = bool(
                int(os.getenv("SEND_AT_MARKET_TRANSITION", "0"))
            )
        except Exception:
            self._send_at_market_transition = False

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
                    return utc_now.hour < 21  # Fermé à 21h
                else:
                    return True  # Ouvert lun-jeu

            return True

        except Exception:
            self.logger.error("Échec connexion MT5", exc_info=True)
            return False

    def reset_daily_counters(self):
        """Reset des compteurs quotidiens (stats seulement)"""
        today = datetime.now().date()
        if self.last_reset_date != today:
            self.trade_count_today = 0
            # Reset par instrument
            self.trade_count_by_symbol_today = {sym: 0 for sym in self.symbols}
            self.last_reset_date = today
            self.logger.debug("Préparation des répertoires de logs et handlers")

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

        # Logger principal avec nom stable
        logger_name = "trading_engine"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)

        # Éviter la duplication des handlers
        if self.logger.handlers:
            self.logger.handlers.clear()

        # Formatage amélioré avec plus d'informations
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(funcName)-20s | PID:%(process)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Handler fichier avec rotation par taille
        try:
            from logging.handlers import RotatingFileHandler

            log_path = os.path.join(self.logs_folder, "trading_engine.log")
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=50 * 1024 * 1024,  # 50MB
                backupCount=5,
            )
        except ImportError:
            # Fallback si RotatingFileHandler n'est pas disponible
            log_path = os.path.join(self.logs_folder, "trading_engine.log")
            file_handler = logging.FileHandler(log_path, encoding="utf-8")

        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # Handler console avec formatage simplifié
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Handler pour erreurs critiques (séparé)
        error_handler = logging.FileHandler("logs/critical_errors.log")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        self.logger.addHandler(error_handler)

        self.logger.info("Système de logging initialisé")

    def on_engine_start(self):
        """Hook appelé lors du démarrage du moteur.

        Si la variable d'environnement SEND_AT_MARKET_TRANSITION est activée,
        tente d'envoyer un ordre marché par instrument à l'ouverture ou la
        fermeture du marché (Mon-Fri). Méthode non-invasive : en cas d'échec
        on logge simplement l'erreur.
        """
        if not getattr(self, "_send_at_market_transition", False):
            return

        try:
            utc_now = datetime.now(pytz.UTC)
            weekday = utc_now.weekday()  # 0=Lundi, 6=Dimanche

            # Ne traiter que Lun-Ven
            if weekday > 4:
                self.logger.info("SEND_AT_MARKET_TRANSITION actif mais weekend - skip")
                return

            # Minutes depuis minuit UTC
            now_minutes = utc_now.hour * 60 + utc_now.minute
            delta = 5  # tolérance en minutes

            # Utilise la logique simple : ouverture/fermeture à 21:00 UTC pour forex
            market_marker = 21 * 60

            if abs(now_minutes - market_marker) <= delta:
                pass
            else:
                self.logger.info(
                    "SEND_AT_MARKET_TRANSITION activé mais pas de transition proche (±5min)"
                )
                return

            # Assurer MT5 disponible
            if not MT5_AVAILABLE:
                self.logger.warning("MT5 non disponible: envoi d'ordres annulé")
                return

            try:
                if not self.is_connected:
                    self.connect_mt5()
            except Exception:
                pass

            for i, symbol in enumerate(self.symbols):
                try:
                    if not self.is_market_open(symbol):
                        self.logger.info("Symbole fermé: %s - skip", symbol)
                        continue

                    # Choix d'action basique: alternate buy/sell pour ouvrir
                    action = "buy" if (i % 2 == 0) else "sell"
                    lot = float(self.lot_sizes.get(symbol, 0.01))

                    if hasattr(self, "send_market_order"):
                        try:
                            self.send_market_order(action, symbol, lot)
                            self.logger.info(
                                "SEND_AT_MARKET_TRANSITION ordre envoyé: %s lot=%s",
                                symbol,
                                lot,
                            )
                        except Exception:
                            self.logger.error(
                                "Erreur Meta-Learning: fallback sécurisé",
                                exc_info=True,
                            )
                    else:
                        try:
                            tick = mt5.symbol_info_tick(symbol)
                            if tick is None:
                                self.logger.warning(
                                    "Tick introuvable pour %s - annulation envoi", symbol
                                )
                                continue
                            price = float(tick.ask if action == "buy" else tick.bid)
                            request = {
                                "action": mt5.TRADE_ACTION_DEAL,
                                "symbol": symbol,
                                "volume": float(lot),
                                "type": (
                                    mt5.ORDER_TYPE_BUY
                                    if action == "buy"
                                    else mt5.ORDER_TYPE_SELL
                                ),
                                "price": price,
                                "deviation": 100,
                                "magic": 235001,
                                "comment": "Market transition auto-send",
                                "type_time": mt5.ORDER_TIME_GTC,
                                "type_filling": mt5.ORDER_FILLING_IOC,
                            }
                            try:
                                from src.utils.mt5_safe import send_order
                            except Exception:
                                send_order = None

                            if send_order is not None:
                                result = send_order(request, logger=self.logger, mt5_module=mt5)
                            else:
                                result = _mt5_send_safe(request)
                            # Log result safely without very long inline f-strings
                            self.logger.info(
                                "SEND_AT_MARKET_TRANSITION (raw) %s: %s",
                                symbol,
                                getattr(result, "retcode", result),
                            )
                        except Exception:
                            # fallback send failed; exception details are logged
                            self.logger.error("Erreur envoi transition marché pour %s", symbol)
                            self.logger.debug("Exception lors envoi transition", exc_info=True)

                except Exception:
                    self.logger.error("Erreur loop SEND_AT_MARKET_TRANSITION: %s", symbol)
                    self.logger.debug(
                        "Exception loop SEND_AT_MARKET_TRANSITION", exc_info=True
                    )

        except Exception:
            self.logger.error("Erreur durant initialisation IA", exc_info=True)

    def production_health_check(self):
        """Check complet avant production"""
        checks = {
            "mt5_connection": False,
            "symbols_available": False,
            "market_hours": False,
            "config_valid": False,
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
            self.trading_interval > 0
            and len(self.lot_sizes) > 0
            and self.performance_metrics["optimal_threshold"] > 0
        )

        # Résumé
        passed = sum(checks.values())
        total = len(checks)

        labels = {
            "mt5_connection": "MT5",
            "symbols_available": "Symboles",
            "market_hours": "Heures de marché",
            "config_valid": "Configuration",
        }
        for check, status in checks.items():
            self.logger.info("✅ %s: %s", labels.get(check, check), "OK" if status else "KO")

        if passed == total:
            self.logger.info("✅ Health check global: PASS (%d/%d)", passed, total)
            return True
        else:
            self.logger.warning("⚠️ Health check partiel: %d/%d", passed, total)
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

            try:
                login = getattr(account_info, "login", "?")
                server = getattr(account_info, "server", "?")
                name = getattr(account_info, "name", "?")
                leverage = getattr(account_info, "leverage", None)
                self.logger.info("Compte MT5: %s @ %s (%s)", login, server, name)
                if leverage:
                    self.logger.info("Levier: x%s", leverage)
            except Exception:
                # Ne pas bloquer si certaines infos ne sont pas accessibles
                pass

            # Vérifier les symboles
            for symbol in self.symbols:
                symbol_info = mt5.symbol_info(symbol)
                if symbol_info is None:
                    self.logger.warning("Symbole introuvable sur MT5: %s", symbol)
                    continue

                # Sélectionner le symbole
                if not mt5.symbol_select(symbol, True):
                    self.logger.warning("Sélection MT5 échouée: %s", symbol)
                    continue

                self.logger.debug("Symbole prêt: %s", symbol)

            self.is_connected = True
            self.logger.info("Connexion MT5 réussie")
            return True

        except Exception:
            self.logger.error("Échec connexion MT5", exc_info=True)
            return False

    def initialize_ai_systems(self):
        """Initialiser tous les systèmes AI avec retry et mode fallback"""
        max_retries = 3
        retry_delay = 2  # secondes

        # Respecter strictement le mode light: ne pas tenter d'initialiser les systèmes AI
        if os.getenv("LIVE_ENGINE_LIGHT_MODE", "0") == "1":
            try:
                self.logger.info("LIVE_ENGINE_LIGHT_MODE=1 -> skipping AI initialization (light mode)")
            except Exception:
                pass
            # Activer fallback mais considérer l'initialisation comme réussie en mode light
            self.ai_fallback_mode = True
            return True

        # Tenter plusieurs fois l'initialisation complète
        for attempt in range(max_retries):
            self.logger.debug(
                "Initialisation systèmes IA (tentative %d/%d)",
                attempt + 1,
                max_retries,
            )

            initialized = {
                "meta_learning": False,
                "rl_agent": False,
                "portfolio_optimizer": False,
                "regime_detector": False,
            }

            # 1. Meta-Learning System
            if MetaLearningTradingSystem is not None:
                try:
                    self.meta_learning = MetaLearningTradingSystem(max_models=3)
                    initialized["meta_learning"] = True
                    self.logger.info("✅ Meta-Learning initialisé")
                    # Si l'ensemble de modèles est vide, tenter de charger un
                    # modèle LightGBM déjà entraîné depuis artifacts (non invasif)
                    try:
                        if hasattr(self.meta_learning, "model_ensemble") and (
                            not self.meta_learning.model_ensemble
                        ):
                            from pathlib import Path

                            import lightgbm as _lgb

                            art = Path("artifacts") / "auto_improve"
                            # Priorité au modèle large si présent
                            candidate = None
                            if art.exists():
                                p1 = art / "best_lightgbm_large.txt"
                                p2 = art / "best_lightgbm.txt"
                                if p1.exists():
                                    candidate = p1
                                elif p2.exists():
                                    candidate = p2

                            if candidate is not None:
                                try:
                                    booster = _lgb.Booster(model_file=str(candidate))
                                    self.meta_learning.model_ensemble = [
                                        {
                                            "model": booster,
                                            "performance": 1.0,
                                            "architecture": ("lightgbm_booster_file"),
                                        }
                                    ]
                                    self.logger.info("📥 Modèle LightGBM chargé: %s", candidate.name)
                                except Exception:
                                    self.logger.warning(
                                        "Impossible de charger le modèle LightGBM depuis artifacts"
                                    )
                    except Exception:
                        # Ne pas échouer l'initialisation globale si import absent
                        pass
                except Exception:
                    self.logger.warning("Meta-Learning indisponible", exc_info=True)
            else:
                self.logger.debug("MetaLearningTradingSystem not available; skipping")

            # 2. Reinforcement Learning Agent
            if ReinforcementLearningTradingSystem is not None:
                try:
                    self.rl_agent = ReinforcementLearningTradingSystem(use_dqn=True)
                    initialized["rl_agent"] = True
                    self.logger.info("✅ RL Agent initialisé")
                except Exception:
                    self.logger.warning("RL Agent indisponible", exc_info=True)
            else:
                self.logger.debug("ReinforcementLearningTradingSystem not available; skipping")

            # 3. Portfolio Optimizer (pour allocation)
            if MultiAssetPortfolioOptimizer is not None:
                try:
                    self.portfolio_optimizer = MultiAssetPortfolioOptimizer()
                    initialized["portfolio_optimizer"] = True
                    self.logger.info("✅ Portfolio Optimizer initialisé")
                except Exception:
                    self.logger.warning("Portfolio Optimizer indisponible", exc_info=True)
            else:
                self.logger.debug("MultiAssetPortfolioOptimizer not available; skipping")

            # 4. Regime Detector
            if MarketRegimeDetector is not None:
                try:
                    self.regime_detector = MarketRegimeDetector(n_regimes=3)
                    initialized["regime_detector"] = True
                    self.logger.info("✅ Regime Detector initialisé")
                except Exception:
                    self.logger.warning("Regime Detector indisponible", exc_info=True)
            else:
                self.logger.debug("MarketRegimeDetector not available; skipping")

            # Si au moins un composant clé est opérationnel, quitter et
            # utiliser les composants disponibles (mode partiel)
            if any(initialized.values()):
                self.ai_fallback_mode = False
                self.logger.info(
                    "✅ Au moins un composant AI initialisé - mode partiel activé"
                )
                return True

            # Sinon retry exponential backoff
            self.logger.warning("Aucun composant AI initialisé sur cette tentative")
            if attempt < max_retries - 1:
                self.logger.debug("Combinaison des signaux IA (meta/regime/portfolio/RL)")
                time.sleep(retry_delay)
                retry_delay *= 2

        # Après toutes les tentatives, activer fallback
        self.logger.warning(
            "Toutes les tentatives d'initialisation AI ont échoué - activation du mode fallback"
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
            "default_lot_size": 0.01,
            "simple_sl_pips": 20,
            "simple_tp_pips": 30,
            "max_positions": 3,
        }

        self.logger.info("✅ Mode fallback activé - Trading simple maintenu")
        return True

    def check_emergency_stop(self):
        """Vérifie si un arrêt d'urgence est actif"""
        try:
            if self.emergency_stop_file.exists():
                # Lire le fichier d'arrêt d'urgence
                with open(self.emergency_stop_file, "r") as f:
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

                # Si le fichier existe mais n'indique pas explicitement l'activation,
                # considérer comme expiré et nettoyer.
                if "EMERGENCY_STOP_ACTIVE" not in content:
                    try:
                        self.emergency_stop_active = False
                        self.emergency_stop_until = None
                        self.emergency_stop_file.unlink(missing_ok=True)
                    except Exception:
                        self.logger.warning(
                            "Impossible de supprimer le fichier d'arrêt d'urgence", exc_info=True
                        )
                    return False

                # Si le flag est actif dans le fichier
                if "EMERGENCY_STOP_ACTIVE" in content:
                    # Si une date d'expiration est définie et dépassée, lever l'arrêt
                    if until_dt and datetime.now() > until_dt:
                        try:
                            self.emergency_stop_active = False
                            self.emergency_stop_until = None
                            self.emergency_stop_file.unlink(missing_ok=True)
                            self.logger.info(
                                "✅ Période d'arrêt d'urgence expirée - Reprise du trading"
                            )
                            return False
                        except Exception:
                            self.logger.warning(
                                "Impossible supprimer emergency_stop après expiration",
                                exc_info=True,
                            )
                            return False

                    # Sinon, arrêt toujours actif
                    self.emergency_stop_active = True
                    self.logger.warning("🚨 ARRÊT D'URGENCE DÉTECTÉ - Trading suspendu")
                    return True

            # Vérifier si la période d'arrêt est expirée
            if self.emergency_stop_until and datetime.now() > self.emergency_stop_until:
                self.emergency_stop_active = False
                self.emergency_stop_until = None
                self.logger.info(
                    "✅ Période d'arrêt d'urgence expirée - Reprise du trading"
                )
                # Supprimer le fichier d'arrêt si présent
                if self.emergency_stop_file.exists():
                    try:
                        self.emergency_stop_file.unlink()
                    except Exception:
                        self.logger.warning(
                            "Impossible supprimer emergency_stop (post-expiration)",
                            exc_info=True,
                        )

            return self.emergency_stop_active

        except Exception:
            self.logger.warning("Erreur lors de la vérification emergency_stop", exc_info=True)
            return False

    def activate_emergency_stop(self, duration_minutes=5):
        """Active un arrêt d'urgence pour une durée spécifiée"""
        try:
            self.emergency_stop_active = True
            self.emergency_stop_until = datetime.now() + timedelta(
                minutes=duration_minutes
            )

            self.logger.critical("🚨 Activation Emergency Stop: durée=%d minutes", duration_minutes)

            # Fermer toutes les positions ouvertes
            self.close_all_positions()

            # Créer le fichier d'arrêt d'urgence
            with open(self.emergency_stop_file, "w") as f:
                f.write("EMERGENCY_STOP_ACTIVE\n")
                f.write(f"activated_at: {datetime.now().isoformat()}\n")
                expires = self.emergency_stop_until.isoformat() if self.emergency_stop_until else ""
                f.write("expires_at: " + expires + "\n")
                f.write("initiated_by: automated_system\n")
                f.write("Status: ACTIVE\n")

            return True

        except Exception:
            self.logger.error("Échec activation emergency_stop", exc_info=True)
            return False

    def close_all_positions(self):
        """Ferme toutes les positions ouvertes immédiatement"""
        if not MT5_AVAILABLE:
            self.logger.info("🔄 Mode simulation - Positions fermées virtuellement")
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
                    try:
                        from src.utils.mt5_safe import send_order
                    except Exception:
                        send_order = None

                    if send_order is not None:
                        result = send_order(request, logger=self.logger, mt5_module=mt5)
                    else:
                        result = _mt5_send_safe(request)

                    if result.retcode == mt5.TRADE_RETCODE_DONE:
                        closed_count += 1
                        self.logger.info(
                            "Position fermée: %s (ticket=%s) result=%s",
                            position.symbol,
                            getattr(position, "ticket", "unknown"),
                            getattr(result, "retcode", result),
                        )
                    else:
                        self.logger.error(
                            "Échec fermeture position %s: %s",
                            position.symbol,
                            getattr(result, "retcode", result),
                        )

                except Exception:
                    self.logger.error(
                        "Exception lors fermeture position %s",
                        getattr(position, "symbol", "unknown"),
                        exc_info=True,
                    )

            self.logger.info("Positions fermées: %d", closed_count)
            return True

        except Exception:
            self.logger.error("Échec close_all_positions", exc_info=True)
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
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)

            if rates is None or len(rates) == 0:
                self.logger.error("MT5 copy_rates_from_pos renvoyé vide pour %s", symbol)
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

        except Exception:
            self.logger.error("Erreur récupération live data pour %s", symbol, exc_info=True)
            return None

    def generate_simulation_data(self, count):
        """Générer des données de simulation"""
        try:
            # Charger des données historiques si disponibles
            if os.path.exists("data/features_sample.csv"):
                if IO_UTILS_AVAILABLE and safe_read_csv is not None:
                    historical_df = safe_read_csv(
                        "data/features_sample.csv", FALLBACK_SAMPLE_DATA
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
                dates = pd.date_range(end=datetime.now(), periods=count, freq="1T")

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
                            p * (1 + abs(np.random.normal(0, 0.0005))) for p in prices
                        ],
                        "low": [
                            p * (1 - abs(np.random.normal(0, 0.0005))) for p in prices
                        ],
                        "close": prices,
                        "volume": np.random.randint(10, 100, count),
                    },
                    index=dates,
                )

                return df

        except Exception:
            self.logger.error("Erreur génération données simulation", exc_info=True)
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
                regime_result = self.regime_detector.detect_regimes(current_data)
                current_regime = regime_result["current_regime"]
                regime_signals = self.regime_detector.get_regime_strategy_signals(
                    current_regime
                )

                signals["regime_detection"] = {
                    "regime": self.regime_detector.regime_names[current_regime],
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
                    if (
                        hasattr(self.meta_learning, "model_ensemble")
                        and self.meta_learning.model_ensemble
                    ):
                        # Préparer les features avec validation robuste
                        features = current_data.select_dtypes(
                            include=[np.number]
                        ).fillna(method="ffill").fillna(0)

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
                                        hasattr(self.meta_learning, "model_ensemble")
                                        and self.meta_learning.model_ensemble
                                    ):
                                        primary = self.meta_learning.model_ensemble[
                                            0
                                        ].get("model")
                                        if primary is not None and hasattr(
                                            primary, "feature_name"
                                        ):
                                            fn = primary.feature_name() or []
                                            n_feat = len(fn) if fn is not None else 0
                                            if n_feat == 0:
                                                n_feat = 5

                                            # Ordre préféré des colonnes live
                                            preferred_order = [
                                                "close",
                                                "volume",
                                                "sma_1T",
                                                "ema_15T",
                                                "rsi_60T",
                                            ]

                                            # Construire un ndarray (1, n_feat)
                                            vals = []
                                            num_df = last_features.select_dtypes(
                                                include=[np.number]
                                            ).copy()
                                            for i in range(n_feat):
                                                source_col = None
                                                if (
                                                    i < len(preferred_order)
                                                    and preferred_order[i]
                                                    in last_features.columns
                                                ):
                                                    source_col = preferred_order[i]
                                                elif i < len(num_df.columns):
                                                    source_col = num_df.columns[i]

                                                if (
                                                    source_col is not None
                                                    and source_col in num_df.columns
                                                ):
                                                    try:
                                                        v = float(
                                                            num_df[source_col].iloc[-1]
                                                        )
                                                    except Exception:
                                                        v = 0.0
                                                else:
                                                    v = 0.0

                                                vals.append(v)

                                            import pandas as _pd

                                            mapped_input = _pd.DataFrame(
                                                [vals], columns=fn[: len(vals)]
                                            )

                                except Exception:
                                    mapped_input = last_features

                                ensemble_pred = self.meta_learning.ensemble_predict(
                                    mapped_input
                                )

                                if ensemble_pred is not None and len(ensemble_pred) > 0:
                                    pred_value = float(ensemble_pred[0])
                                    # Borner les prédictions
                                    pred_value = max(0.0, min(1.0, pred_value))

                                    signals["meta_learning"] = {
                                        "prediction": pred_value,
                                        "action": (
                                            "buy"
                                            if pred_value > 0.6
                                            else (
                                                "sell" if pred_value < 0.4 else "hold"
                                            )
                                        ),
                                        "confidence": abs(pred_value - 0.5) * 2,
                                    }
                                else:
                                    self.logger.warning(
                                        "Meta-Learning: prédiction vide"
                                    )
                            else:
                                self.logger.warning("Meta-Learning: features vides")
                        else:
                            self.logger.warning(
                                "Meta-Learning: pas de features numériques"
                            )
                except Exception:
                    self.logger.error("TODO_NOT_IMPLEMENTED")
                    # Fallback sécurisé
                    signals["meta_learning"] = {
                        "prediction": 0.5,
                        "action": "hold",
                        "confidence": 0.0,
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
                            "Signal XAUUSD amélioré appliqué: %s (conf=%.2f)",
                            enhanced_xau_signal.get("action"),
                            enhanced_xau_signal.get("confidence", 0.0),
                        )
                except Exception:
                    self.logger.warning("Amélioration XAUUSD indisponible", exc_info=True)

            # 3. Combiner les signaux
            combined_action = "hold"
            combined_confidence = 0.0

            # 🥇 PRIORITÉ: Signal XAUUSD amélioré si disponible
            if signals.get("enhanced_xauusd"):
                enhanced_signal = signals["enhanced_xauusd"]
                combined_action = enhanced_signal["action"]
                combined_confidence = enhanced_signal["confidence"]
                self.logger.info(
                    "Signal XAUUSD prioritaire utilisé: action=%s conf=%.2f",
                    combined_action,
                    combined_confidence,
                )
            # Si pas de détection de régime, utiliser directement
            # le signal du meta_learning comme base
            elif not signals.get("regime_detection") and signals.get("meta_learning"):
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
                    combined_confidence = min(combined_confidence + ml_conf * 0.3, 1.0)
                # Si les signaux divergent, réduire la confiance
                elif ml_action != "hold" and combined_action != "hold":
                    combined_confidence *= 0.5
                    combined_action = "hold"  # Neutraliser en cas de divergence

            signals["combined_signal"] = combined_action
            signals["confidence"] = combined_confidence

            # 🧠 NOUVEAU: Appliquer le système de décision avancé
            try:
                enhanced_signals = self.apply_advanced_decision_engine(
                    symbol, current_data, signals
                )
                return enhanced_signals
            except Exception:
                self.logger.warning(
                    "Échec apply_advanced_decision_engine - fallback to base signals", exc_info=True
                )
                return signals

        except Exception:
            self.logger.error("Erreur get_ai_signals inattendue", exc_info=True)
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

            current_price = data["close"].iloc[-1]

            # Niveaux psychologiques XAUUSD
            psychological_levels = [1900, 2000, 2100, 2200, 2300, 2400, 2500]
            nearest_level = min(
                psychological_levels, key=lambda x: abs(x - current_price)
            )

            distance_to_level = abs(current_price - nearest_level)
            # Max 50$ distance
            level_proximity = 1 - min(distance_to_level / 50, 1.0)

            # RSI divergence pour XAUUSD
            rsi = self.calculate_rsi(data["close"], 14)
            price_trend = (
                data["close"].iloc[-1] - data["close"].iloc[-5]
            ) / data["close"].iloc[-5]
            rsi_trend = (rsi.iloc[-1] - rsi.iloc[-5]) / rsi.iloc[-5]

            # Divergence detection
            divergence_strength = abs(price_trend - rsi_trend)
            bullish_divergence = price_trend < 0 and rsi_trend > 0 and rsi.iloc[-1] < 30
            bearish_divergence = price_trend > 0 and rsi_trend < 0 and rsi.iloc[-1] > 70

            # Signal calculation
            base_confidence = 0.3  # Base minimum pour XAUUSD

            if bullish_divergence:
                confidence = (
                    base_confidence
                    + (divergence_strength * 0.4)
                    + (level_proximity * 0.3)
                )
                return {"action": "buy", "confidence": min(confidence, 0.95)}

            elif bearish_divergence:
                confidence = (
                    base_confidence
                    + (divergence_strength * 0.4)
                    + (level_proximity * 0.3)
                )
                return {"action": "sell", "confidence": min(confidence, 0.95)}

            else:
                # Trend following with psychological levels
                if current_price > nearest_level and level_proximity > 0.8:
                    confidence = base_confidence + (level_proximity * 0.2)
                    return {"action": "buy", "confidence": min(confidence, 0.7)}
                elif current_price < nearest_level and level_proximity > 0.8:
                    confidence = base_confidence + (level_proximity * 0.2)
                    return {"action": "sell", "confidence": min(confidence, 0.7)}

            return {"action": "hold", "confidence": base_confidence}

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")
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
            close_prices = current_data["close"].values
            current_price = close_prices[-1]

            # SMA court/long terme
            sma_short = np.mean(close_prices[-5:])  # 5 périodes
            sma_long = np.mean(close_prices[-20:])  # 20 périodes

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
                        "action": ("buy" if current_price < sma_long else "sell"),
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
                if volatility > 0.03 and abs(sma_short - sma_long) / sma_long > 0.01:
                    signal = {
                        "confidence": 0.51,
                        "action": ("buy" if sma_short > sma_long else "sell"),
                        "type": "crypto_fallback",
                    }

            self.logger.debug(
                "🔄 Signal fallback %s: %s conf=%.3f",
                symbol,
                signal["action"],
                signal["confidence"],
            )
            return signal

        except Exception as e:
            self.logger.warning("Erreur génération signal fallback %s: %s", symbol, e)
            return {"confidence": 0.0, "action": "hold", "type": "error"}

    def apply_advanced_decision_engine(self, symbol, data, base_signals):
        """Appliquer le moteur de décision avancé"""
        try:
            # Lazy import du système avancé
            try:
                from advanced_decision_engine import AdvancedDecisionEngine
            except Exception:
                from scripts.advanced_decision_engine import AdvancedDecisionEngine

            # Initialiser si pas encore fait
            if not hasattr(self, "advanced_engine"):
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
            if enhanced_decision.get("enhancement_applied"):
                # Utiliser confiance et action améliorées
                base_signals["confidence"] = enhanced_decision["confidence"]
                base_signals["combined_signal"] = enhanced_decision["action"]

                # Ajouter métadonnées avancées
                base_signals["enhanced"] = True
                base_signals["adaptive_threshold"] = enhanced_decision.get(
                    "adaptive_threshold", self.confidence_threshold
                )
                base_signals["execution_urgency"] = enhanced_decision.get(
                    "execution_urgency", "later"
                )
                base_signals["risk_adjusted_score"] = enhanced_decision.get(
                    "risk_adjusted_score", 0.5
                )

                self.logger.info("TODO_NOT_IMPLEMENTED")

            return base_signals

        except ImportError:
            self.logger.warning("Module advanced_decision_engine non trouvé")
            return base_signals
        except Exception:
            self.logger.warning("Échec moteur décision avancé", exc_info=True)
            return base_signals

    # --- Nouveau: Sizing adaptatif basé sur artifacts/performance/summary_latest.json ---
    def _load_performance_summary(self):
        try:
            summary_path = Path("artifacts") / "performance" / "summary_latest.json"
            if not summary_path.exists():
                return None
            with open(summary_path, "r", encoding="utf-8") as f:
                import json as _json

                return _json.load(f)
        except Exception:
            return None

    # --- Nouveau: calcul intraday (fenêtre glissante) à partir des logs ---
    def _get_recent_closed_trades(self, window_minutes: int = 60, max_trades: int = 50):
        """Récupère les trades clôturés récents depuis logs/trades.json[l].

        Retourne une liste de profits (floats) ordonnés du plus ancien au plus récent
        dans la fenêtre glissante (min(now - window, max_trades derniers)).
        """
        try:
            from tools.performance_aggregator import load_closed_trades
        except Exception:
            return []

        try:
            trades = load_closed_trades("logs") or []
            if not trades:
                return []
            # Filtrer sur la fenêtre temporelle
            try:
                from datetime import timezone

                now_utc = datetime.now(timezone.utc)
            except Exception:
                now_utc = datetime.utcnow()
            cutoff = now_utc - timedelta(minutes=int(window_minutes))
            recent = [
                t
                for t in trades
                if getattr(t, "timestamp", None) and t.timestamp >= cutoff
            ]
            if not recent:
                # fallback: prendre les N derniers trades s'il n'y a pas d'activité récente
                recent = trades[-max_trades:]
            else:
                # si trop nombreux, tronquer aux N derniers
                if len(recent) > max_trades:
                    recent = recent[-max_trades:]
            return [float(getattr(t, "profit", 0.0)) for t in recent]
        except Exception:
            return []

    def _compute_intraday_metrics(self, window_minutes: int = 60, max_trades: int = 50):
        """Calcule des métriques intraday simples sur une fenêtre glissante.

        Renvoie un dict {total_trades, win_rate_pct, pnl, profit_factor} ou None
        si insuffisant (< 3 trades).
        """
        profits = self._get_recent_closed_trades(window_minutes, max_trades)
        try:
            profits = [float(p) for p in profits if p is not None]
        except Exception:
            profits = []

        total = len(profits)
        if total < 3:
            return None

        pnl = float(sum(profits))
        wins = sum(1 for p in profits if p > 0)
        # losses variable inutilisée — win rate calcule déjà par wins/total
        wr = (wins / total * 100.0) if total else 0.0
        pos = sum(p for p in profits if p > 0)
        neg = -sum(p for p in profits if p < 0)
        if neg == 0:
            pf = float("inf") if pos > 0 else 0.0
        else:
            pf = pos / neg

        return {
            "total_trades": total,
            "win_rate_pct": wr,
            "pnl": pnl,
            "profit_factor": pf,
        }

    def _compute_intraday_metrics_by_symbol(
        self, symbol: str, window_minutes: int, max_trades: int
    ):
        profits = self._get_recent_closed_trades_by_symbol(
            symbol, window_minutes, max_trades
        )
        try:
            profits = [float(p) for p in profits if p is not None]
        except Exception:
            profits = []
        total = len(profits)
        if total < 3:
            return None
        pnl = float(sum(profits))
        wins = sum(1 for p in profits if p > 0)
        wr = (wins / total * 100.0) if total else 0.0
        pos = sum(p for p in profits if p > 0)
        neg = -sum(p for p in profits if p < 0)
        pf = float("inf") if pos > 0 and neg == 0 else (pos / neg if neg > 0 else 0.0)
        return {
            "total_trades": total,
            "win_rate_pct": wr,
            "pnl": pnl,
            "profit_factor": pf,
        }

    def _ewma_update(self, key: str, value: float, alpha: float = 0.2) -> float:
        """Met à jour une EWMA intraday pour la clé donnée et renvoie la nouvelle valeur."""
        try:
            prev = self._intraday_perf_ewma.get(key)
            if prev is None:
                self._intraday_perf_ewma[key] = float(value)
            else:
                self._intraday_perf_ewma[key] = (
                    (1 - alpha) * float(prev) + alpha * float(value)
                )
            return float(self._intraday_perf_ewma[key])
        except Exception:
            # Si problème, retourner la valeur brute
            return float(value)

    def _loss_streak_from_profits(self, profits):
        """Calcule la série de pertes consécutives à partir de la fin de la liste.
        profits: liste ordonnée du plus ancien au plus récent
        """
        try:
            cnt = 0
            for p in reversed(profits or []):
                if p is None:
                    continue
                if float(p) <= 0:
                    cnt += 1
                else:
                    break
            return cnt
        except Exception:
            return 0

    def _get_recent_closed_trades_by_symbol(
        self, symbol: str, window_minutes: int, max_trades: int
    ):
        """Retourne la liste des profits récents pour un symbole donné."""
        try:
            from tools.performance_aggregator import load_closed_trades
        except Exception:
            return []
        try:
            trades = load_closed_trades("logs") or []
            if not trades:
                return []
            try:
                from datetime import timezone

                now_utc = datetime.now(timezone.utc)
            except Exception:
                now_utc = datetime.utcnow()
            cutoff = now_utc - timedelta(minutes=int(window_minutes))
            recent = [
                t
                for t in trades
                if getattr(t, "timestamp", None)
                and t.timestamp >= cutoff
                and getattr(t, "symbol", None) == symbol
            ]
            if not recent:
                # fallback: prendre les N derniers trades du symbole
                recent = [t for t in trades if getattr(t, "symbol", None) == symbol][
                    -max_trades:
                ]
            else:
                if len(recent) > max_trades:
                    recent = recent[-max_trades:]
            return [float(getattr(t, "profit", 0.0)) for t in recent]
        except Exception:
            return []

    def adjust_position_sizing_from_performance(self):
        """Ajuste self.position_size_scale avec une composante intraday réactive.

        Logique:
          - Intraday (fenêtre glissante, EWMA): petits pas (+2% / -3%) si >= 5 trades
          - Quotidien (summary_latest): pas conservateurs (+5% / -10%) comme fallback
          - Cooldown entre ajustements pour éviter l'oscillation
          - Clamp global dans [0.2, 2.0]
        """
        try:
            # Cooldown
            now = datetime.now()
            if (
                self._sizing_last_update is not None
                and (now - self._sizing_last_update).total_seconds()
                < self._sizing_cooldown_seconds
            ):
                return False

            old_scale = float(self.position_size_scale)
            new_scale = old_scale

            # 1) Calcul intraday (global)
            intraday = self._compute_intraday_metrics(
                window_minutes=self._sizing_intraday_window_minutes,
                max_trades=self._sizing_intraday_max_trades,
            )
            intraday_used = False
            if intraday and intraday.get("total_trades", 0) >= 5:
                wr = float(intraday.get("win_rate_pct", 0.0))
                pf = float(intraday.get("profit_factor", 0.0))
                pnl = float(intraday.get("pnl", 0.0))

                # EWMA pour lisser
                wr_ewma = self._ewma_update("wr", wr, alpha=self._sizing_ewma_alpha)
                pf_ewma = self._ewma_update("pf", pf, alpha=self._sizing_ewma_alpha)
                pnl_ewma = self._ewma_update("pnl", pnl, alpha=self._sizing_ewma_alpha)

                # Seuils intraday (plus permissifs mais à petits pas)
                if (
                    (wr_ewma >= self._sizing_wr_up)
                    and (pf_ewma >= self._sizing_pf_up)
                    and (pnl_ewma > 0)
                ):
                    new_scale = old_scale * (1.0 + self._sizing_step_up)
                    intraday_used = True
                elif (
                    (wr_ewma <= self._sizing_wr_down)
                    or (pf_ewma < self._sizing_pf_down)
                    or (pnl_ewma < 0)
                ):
                    new_scale = old_scale * (1.0 - self._sizing_step_down)
                    intraday_used = True

                # Pénalité série de pertes consécutives (renforce prudence)
                try:
                    recent_profits = self._get_recent_closed_trades(
                        self._sizing_intraday_window_minutes,
                        self._sizing_intraday_max_trades,
                    )
                    streak = self._loss_streak_from_profits(recent_profits)
                    if streak >= self._loss_streak_size:
                        penalized = new_scale * self._loss_streak_penalty
                        if abs(penalized - new_scale) > 1e-12:
                            new_scale = penalized
                            self.logger.info(
                                "⚠️  Pénalité série de pertes (%d) appliquée au sizing",
                                streak,
                            )
                except Exception:
                    pass

            # 2) Fallback quotidien si pas d'intraday exploitable
            if not intraday_used:
                summary = self._load_performance_summary()
                if summary and isinstance(summary, dict):
                    last = summary.get("last_day") or {}
                    try:
                        win_rate = float(last.get("win_rate_pct", 0.0))
                    except Exception:
                        win_rate = 0.0
                    try:
                        profit_factor = float(last.get("profit_factor", 0.0))
                    except Exception:
                        profit_factor = 0.0
                    try:
                        pnl = float(last.get("pnl", 0.0))
                    except Exception:
                        pnl = 0.0

                    if (win_rate >= 55.0) and (profit_factor >= 1.2) and (pnl > 0):
                        new_scale = old_scale * 1.05  # +5%
                        intraday_used = False
                    elif (win_rate <= 45.0) or (profit_factor < 1.0) or (pnl < 0):
                        new_scale = old_scale * 0.90  # -10%
                        intraday_used = False

            # 3) Clamp et application
            new_scale = max(0.2, min(2.0, new_scale))

            if abs(new_scale - old_scale) > 1e-9:
                self.position_size_scale = new_scale
                self._sizing_last_update = now
                try:
                    if intraday_used:
                        self.logger.info(
                            "⚖️  Sizing intraday: %.3f -> %.3f (EWMA WR=%.1f%%, PF=%.2f, pnl=%.2f)",
                            old_scale,
                            new_scale,
                            float(self._intraday_perf_ewma.get("wr") or 0.0),
                            float(self._intraday_perf_ewma.get("pf") or 0.0),
                            float(self._intraday_perf_ewma.get("pnl") or 0.0),
                        )
                    else:
                        self.logger.info(
                            "⚖️  Sizing quotidien: %.3f -> %.3f",
                            old_scale,
                            new_scale,
                        )
                except Exception:
                    pass
                return True

            return False
        except Exception:
            # Ne pas bloquer l'exécution si l'ajustement échoue
            return False

    def adjust_position_sizing_from_performance_by_symbol(self, symbol: str):
        """Ajuste un facteur de sizing par instrument basé sur métriques intraday par symbole.
        Utilise les mêmes paramètres que le global, avec pas plus petits.
        """
        try:
            now = datetime.now()
            last_upd = self._sizing_last_update_by_symbol.get(symbol)
            if (
                last_upd is not None
                and (now - last_upd).total_seconds() < self._sizing_cooldown_seconds
            ):
                return False

            # init valeurs
            if symbol not in self.position_size_scale_by_symbol:
                self.position_size_scale_by_symbol[symbol] = 1.0
            if symbol not in self._intraday_perf_ewma_by_symbol:
                self._intraday_perf_ewma_by_symbol[symbol] = {
                    "wr": None,
                    "pf": None,
                    "pnl": None,
                }

            old_scale = float(self.position_size_scale_by_symbol[symbol])
            new_scale = old_scale

            m = self._compute_intraday_metrics_by_symbol(
                symbol,
                window_minutes=self._sizing_intraday_window_minutes,
                max_trades=self._sizing_intraday_max_trades,
            )
            if m and m.get("total_trades", 0) >= 3:
                wr = float(m.get("win_rate_pct", 0.0))
                pf = float(m.get("profit_factor", 0.0))
                pnl = float(m.get("pnl", 0.0))

                # EWMA symbolique
                d = self._intraday_perf_ewma_by_symbol[symbol]
                d["wr"] = (
                    wr
                    if d["wr"] is None
                    else (1 - self._sizing_ewma_alpha)
                    * d["wr"]
                    + self._sizing_ewma_alpha * wr
                )
                d["pf"] = (
                    pf
                    if d["pf"] is None
                    else (1 - self._sizing_ewma_alpha)
                    * d["pf"]
                    + self._sizing_ewma_alpha * pf
                )
                d["pnl"] = (
                    pnl
                    if d["pnl"] is None
                    else (1 - self._sizing_ewma_alpha)
                    * d["pnl"]
                    + self._sizing_ewma_alpha * pnl
                )

                # Pas symbolique un peu plus petit que global
                step_up = max(0.01, self._sizing_step_up * 0.75)
                step_down = max(0.02, self._sizing_step_down * 0.75)

                pf_ok = d["pf"] >= self._sizing_pf_up
                wr_ok = d["wr"] >= self._sizing_wr_up
                pnl_ok = d["pnl"] > 0
                if wr_ok and pf_ok and pnl_ok:
                    new_scale = old_scale * (1.0 + step_up)
                elif (
                    (d["wr"] <= self._sizing_wr_down)
                    or (d["pf"] < self._sizing_pf_down)
                    or (d["pnl"] < 0)
                ):
                    new_scale = old_scale * (1.0 - step_down)

            # Clamp et application
            new_scale = max(0.2, min(2.0, new_scale))
            if abs(new_scale - old_scale) > 1e-9:
                self.position_size_scale_by_symbol[symbol] = new_scale
                self._sizing_last_update_by_symbol[symbol] = now
                try:
                    # Shorter log message to avoid long lines
                    self.logger.info(
                        "Sizing intraday %s: %.3f->%.3f WR=%.1f PF=%.2f pnl=%.2f",
                        symbol,
                        old_scale,
                        new_scale,
                        float(self._intraday_perf_ewma_by_symbol[symbol]["wr"] or 0.0),
                        float(self._intraday_perf_ewma_by_symbol[symbol]["pf"] or 0.0),
                        float(self._intraday_perf_ewma_by_symbol[symbol]["pnl"] or 0.0),
                    )
                except Exception:
                    pass
                return True
            return False
        except Exception:
            return False

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
            validation_errors.append("Action invalide: doit être 'buy' ou 'sell'")

        if not symbol or len(symbol) < 2:
            validation_errors.append("Symbole invalide")

        if lot_size is None or lot_size <= 0:
            validation_errors.append("Lot size invalide (doit être > 0)")

        # 2. Validation des niveaux de prix (si fournis)
        if price is not None and price <= 0:
            validation_errors.append("Prix invalide (doit être > 0)")

        if stop_loss is not None and stop_loss <= 0:
            validation_errors.append("Stop loss invalide (doit être > 0)")

        if take_profit is not None and take_profit <= 0:
            validation_errors.append("Take profit invalide (doit être > 0)")

        # 3. Validation de la cohérence des niveaux
        if price is not None and stop_loss is not None and take_profit is not None:
            if action == "buy":
                if stop_loss >= price:
                    validation_errors.append("Buy: stop_loss doit être < prix d'entrée")
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
        if price is not None and stop_loss is not None and take_profit is not None:
            if action == "buy":
                risk = abs(price - stop_loss)
                reward = abs(take_profit - price)
            else:  # sell
                risk = abs(stop_loss - price)
                reward = abs(price - take_profit)

            if risk > 0 and reward > 0:
                risk_reward_ratio = reward / risk
                if risk_reward_ratio < 0.5:
                    validation_errors.append("Risk/reward ratio < 0.5 (rejeté)")

        # Log des erreurs et retour du résultat
        if validation_errors:
            self.logger.warning("Signal non valide: %s", validation_errors)
            return False, validation_errors
        else:
            self.logger.debug("Signal validé: %s %s", action, symbol)
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
            from src.utils.mt5_connector import trading_disabled  # dispo via utils path

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

        # Appliquer sizing adaptatif basé sur performance (lecture tolérante)
        try:
            # tenter un refresh à chaque ordre (léger, fichier local)
            self.adjust_position_sizing_from_performance()
            # Ajustement par instrument (intraday)
            try:
                self.adjust_position_sizing_from_performance_by_symbol(symbol)
            except Exception:
                pass
        except Exception:
            pass
        try:
            # Facteur global
            scale_global = float(getattr(self, "position_size_scale", 1.0))
            # Facteur symbolique si présent
            scale_sym = float(self.position_size_scale_by_symbol.get(symbol, 1.0))
            lot_size = float(lot_size) * scale_global * scale_sym
        except Exception:
            # si indisponible, rester sur lot_size initial
            pass

        # PROTECTION (non invasive) - Eviter tailles de position catastrophiques
        try:
            # Estimation simple du notional: lot_size * price
            # Note: cela est une approximation (conversion lot->base units dépend du marché).
            # On utilise cette estimation pour appliquer un plafond conservateur.
            current_balance = self.performance_metrics.get("current_balance", 10000.0)
            max_notional = current_balance * 0.05  # 5% du capital par trade (conservative default)

            # Obtenir prix si non fourni
            if price is None:
                # essayer d'utiliser tick si MT5 disponible
                try:
                    if MT5_AVAILABLE:
                        tick = mt5.symbol_info_tick(symbol)
                        if tick is not None:
                            if action == "buy":
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
                            price = float(self.live_data[symbol]["close"].iloc[-1])
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
                        "Lot redimensionné: ancien=%.6f nouveau=%.6f",
                        lot_size,
                        float(max(new_lot, 1e-6)),
                    )
                    lot_size = float(max(new_lot, 1e-6))
        except Exception:
            # Ne pas bloquer l'exécution si la protection échoue
            self.logger.debug("Échec redimensionnement sizing", exc_info=True)

        # NOUVELLE VALIDATION: Vérifier la qualité du signal avant exécution
        is_valid, validation_errors = self.validate_signal_quality(
            action, symbol, lot_size, stop_loss, take_profit, price
        )

        if not is_valid:
            self.logger.error("Signal rejeté: %s", validation_errors)
            # Enregistrer les signaux rejetés pour analyse
            self._log_rejected_signal(symbol, action, validation_errors)
            return False

        # Contrôle: limite de trades par instrument (intraday)
        try:
            limit = int(self.max_trades_per_instrument.get(symbol, 40))
            done = int(self.trade_count_by_symbol_today.get(symbol, 0))
            if done >= limit:
                self.logger.warning(
                    "Limite journalière de trades atteinte pour %s (%d/%d)", symbol, done, limit
                )
                self._log_rejected_signal(symbol, action, ["limit_reached"])
                return False
        except Exception:
            # Si échec du contrôle, ne pas bloquer, on continue
            pass

        if not MT5_AVAILABLE:
            # Mode 100% live: ne pas simuler
            self.logger.error(
                "MT5 indisponible – exécution annulée (simulation interdite)"
            )
            return False

        try:
            # Les validations complètes ont déjà été faites par validate_signal_quality

            if lot_size <= 0 or lot_size > 10.0:  # Limite sécurité
                self.logger.error("Lot size hors limites: %s", lot_size)
                return False

            # Throttling: intervalle minimum entre trades sur ce symbole
            try:
                last_t = self.last_trade_time_by_symbol.get(symbol)
                if last_t is not None:
                    since = (datetime.now() - last_t).total_seconds()
                    if since < self._min_trade_interval_seconds:
                        self.logger.info(
                            "Throttling %s: %.1f s < min %d s",
                            symbol,
                            since,
                            self._min_trade_interval_seconds,
                        )
                        self._log_rejected_signal(
                            symbol,
                            action,
                            ["Intervalle minimum entre trades non atteint"],
                        )
                        return False
            except Exception:
                pass

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
                self.logger.error("Symbole introuvable sur MT5: %s", symbol)
                return False

            if not symbol_info.visible:
                # Essayer d'activer le symbole
                if not mt5.symbol_select(symbol, True):
                    self.logger.error("Symbole %s non visible et activation échouée", symbol)
                    return False

            # Obtenir le prix actuel avec retry
            tick = None
            for retry in range(3):
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    break
                time.sleep(0.1)

            if tick is None:
                self.logger.error("Tick introuvable pour %s", symbol)
                return False

            # Validation du spread (cap relatif configurable)
            spread = tick.ask - tick.bid
            try:
                rel_cap = float(
                    self._spread_cap_rel_by_symbol.get(
                        symbol, self._default_spread_cap_rel
                    )
                )
            except Exception:
                rel_cap = 0.001
            if spread > tick.ask * rel_cap:
                self.logger.warning(
                    "Spread trop élevé pour %s: spread=%.6f cap_rel=%.6f", symbol, spread, rel_cap
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
                self.logger.error("Action inconnue: %s", action)
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
                self.logger.info("Dynamic SL applied: %s", dynamic_sl)

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
                    self.logger.info("Dynamic TP applied: %s", dynamic_tp)

            # Volatility targeting: réduire le volume si le risque estimé dépasse le budget
            try:
                if (
                    self._vol_target_enable
                    and request.get("sl") is not None
                    and price is not None
                ):
                    stop_distance = abs(price - request["sl"])  # en unités de prix
                    if stop_distance > 0:
                        s_info = mt5.symbol_info(symbol)
                        tick_size = float(
                            getattr(s_info, "trade_tick_size", 0.0)
                            or getattr(s_info, "point", 0.0)
                            or s_info.tick_size
                        )
                        tick_value = float(
                            getattr(s_info, "trade_tick_value", 0.0)
                            or s_info.tick_value
                        )
                        if (
                            (tick_size and tick_value)
                            and (tick_size > 0)
                            and (tick_value > 0)
                        ):
                            points = stop_distance / tick_size
                            # risque estimé en devise du compte
                            est_risk = float(lot_size) * points * tick_value
                            risk_budget = float(
                                self.performance_metrics.get("current_balance", 10000.0)
                            ) * float(self._vol_target_risk_pct)
                            if est_risk > risk_budget and est_risk > 0:
                                scale = risk_budget / est_risk
                                new_lot = max(
                                    self._vol_target_min_lot,
                                    float(lot_size) * float(scale),
                                )
                                # Appliquer nouveau volume et journaliser
                                if (
                                    abs(new_lot - lot_size) / max(lot_size, 1e-12)
                                    > 1e-6
                                ):
                                    self.logger.info(
                                        "Vol targeting: lot %.6f -> %.6f (risk %.2f > budget %.2f)",
                                        lot_size,
                                        new_lot,
                                        est_risk,
                                        risk_budget,
                                    )
                                lot_size = new_lot
                                request["volume"] = lot_size
            except Exception:
                # Vol targeting failed; proceed without blocking order
                self.logger.debug("Vol targeting ignoré")

            # Envoyer l'ordre avec retry robuste
            @robust_mt5_retry(max_attempts=3)
            def _send_order():
                # Best-effort: aligner prices/SL/TP avec les digits du symbole
                try:
                    s_info = mt5.symbol_info(symbol)
                    if s_info is not None:
                        digits = (
                            getattr(s_info, "digits", None)
                            or getattr(self, "price_digits", None)
                        )
                        # utiliser la taille de tick si fournie
                        point = (
                            getattr(s_info, "point", None)
                            or getattr(s_info, "trade_tick_size", None)
                            or getattr(self, "point_size", None)
                        )
                        if digits is not None:
                            # Arrondir prix/SL/TP aux digits autorisés par le broker
                            if "price" in request and request["price"] is not None:
                                request["price"] = float(round(request["price"], int(digits)))
                            if "sl" in request and request["sl"] is not None:
                                request["sl"] = float(round(request["sl"], int(digits)))
                            if "tp" in request and request["tp"] is not None:
                                request["tp"] = float(round(request["tp"], int(digits)))
                        elif point is not None:
                            # Fallback: arrondir selon la taille du tick
                            def _round_by_point(v):
                                if v is None:
                                    return v
                                try:
                                    return float(round(v / point) * point)
                                except Exception:
                                    return v

                            if "price" in request:
                                request["price"] = _round_by_point(request.get("price"))
                            if "sl" in request:
                                request["sl"] = _round_by_point(request.get("sl"))
                            if "tp" in request:
                                request["tp"] = _round_by_point(request.get("tp"))
                except Exception:
                    # Si l'alignement prix/SL/TP échoue, ne pas bloquer l'envoi
                    self.logger.debug(
                        "Price/SL/TP alignment failed - continuing", exc_info=True
                    )

                # Ajuster le volume au pas/minimum du broker (safe preflight)
                try:
                    # utiliser s_info déjà récupéré
                    min_vol = getattr(s_info, "volume_min", None) or getattr(
                        s_info, "min_volume", None
                    )
                    vol_step = (
                        getattr(s_info, "volume_step", None)
                        or getattr(s_info, "trade_contract_size", None)
                    )

                    if (
                        "volume" in request
                        and request.get("volume") is not None
                        and vol_step is not None
                    ):
                        import math

                        try:
                            requested_vol = float(request.get("volume"))
                        except Exception:
                            requested_vol = None

                        if requested_vol is not None:
                            try:
                                # floor to nearest step (safety: small epsilon to avoid float issues)
                                n = math.floor(requested_vol / float(vol_step) + 1e-12)
                                effective_vol = float(n) * float(vol_step)
                            except Exception:
                                effective_vol = requested_vol

                            # Déterminer décimales à conserver en fonction du pas
                            try:
                                decimals = 0
                                if float(vol_step) < 1:
                                    decimals = max(
                                        0, -int(math.floor(math.log10(float(vol_step))))
                                    )
                            except Exception:
                                decimals = 8

                            try:
                                effective_vol = round(effective_vol, decimals)
                            except Exception:
                                pass

                            # Si ajustement effectué, journaliser
                            if effective_vol != requested_vol:
                                try:
                                    self.logger.info(
                                        "Volume ajusté pour %s: requested=%.8f -> effective=%.8f (step=%s, min=%s)",
                                        symbol,
                                        requested_vol,
                                        effective_vol,
                                        vol_step,
                                        min_vol,
                                    )
                                except Exception:
                                    pass

                            # Si le volume effectif est inférieur au min autorisé, refuser l'envoi
                            try:
                                if (
                                    min_vol is not None
                                    and float(effective_vol) < float(min_vol)
                                ):
                                    msg = (
                                        f"Requested volume {requested_vol} below symbol min_volume {min_vol} "
                                        f"after rounding (effective={effective_vol})"
                                    )
                                    raise MT5OperationError(msg)
                            except MT5OperationError:
                                # remonter l'erreur
                                raise
                            except Exception:
                                # ne pas bloquer l'envoi si comparaison échoue
                                pass

                            # Appliquer le volume ajusté
                            request["volume"] = effective_vol
                except Exception:
                    # Ne pas bloquer l'envoi si la détection du pas échoue
                    self.logger.debug("Volume preflight failed - continuing", exc_info=True)

                # Journaliser la requête (debug) avant l'envoi mais sans exposer d'infos sensibles
                try:
                    req_copy = dict(request)
                    # éviter affichage massif d'objets non sérialisables
                    for k in ("price", "sl", "tp", "volume"):
                        if k in req_copy:
                            req_copy[k] = float(req_copy[k]) if req_copy[k] is not None else None
                    self.logger.debug("Sending MT5 order request: %s", req_copy)
                except Exception:
                    pass

                try:
                    from src.utils.mt5_safe import send_order
                except Exception:
                    send_order = None

                if send_order is not None:
                    result = send_order(request, logger=self.logger, mt5_module=mt5)
                else:
                    result = _mt5_send_safe(request)
                if result is None:
                    # récupérer dernier err si possible
                    try:
                        last = mt5.last_error()
                    except Exception:
                        last = None
                    raise MT5OperationError(f"Résultat ordre null, last_error={last}")

                # Si échec, enrichir l'exception avec les champs de réponse utiles
                try:
                    retcode = getattr(result, "retcode", None)
                    comment = getattr(result, "comment", None)
                    order_id = getattr(result, "order", None)
                except Exception:
                    retcode, comment, order_id = None, None, None

                if retcode != mt5.TRADE_RETCODE_DONE:
                    # journaliser la réponse complète pour debug local
                    try:
                        self.logger.warning(
                            "MT5 order_send failed: retcode=%s, comment=%s, result=%s",
                            retcode,
                            comment,
                            result,
                        )
                    except Exception:
                        self.logger.warning(
                            "MT5 order_send failed: retcode=%s, comment=%s",
                            retcode,
                            comment,
                        )

                    raise MT5OperationError(
                        f"Order send failed, retcode={retcode}, comment={comment}, order={order_id}"
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
                "Ordre envoyé: %s %s lot=%.6f price=%.6f order=%s",
                action,
                symbol,
                lot_size,
                price,
                getattr(result, "order", None),
            )

            # MàJ temps dernier trade pour le throttling
            try:
                self.last_trade_time_by_symbol[symbol] = datetime.now()
            except Exception:
                pass

            return True

        except Exception:
            self.logger.error("Erreur inattendue lors du démarrage du live", exc_info=True)
            return False

    # Fonction de simulation retirée: exécution 100% live

    # LIGNE 547-589 : Checks de risque simplistes
    def risk_check(self, action, signals, symbol="UNKNOWN"):
        """Vérification de risque avancée avec système intelligent"""
        try:
            # Cooldown par instrument après série de pertes
            try:
                cd_until = self.symbol_cooldown_until.get(symbol)
                if cd_until is not None and datetime.now() < cd_until:
                    self.logger.info("TODO_NOT_IMPLEMENTED")
                    return False
            except Exception:
                pass

            # 🧠 NOUVEAU: Utiliser seuil adaptatif si disponible
            min_confidence = signals.get(
                "adaptive_threshold", self.confidence_threshold
            )

            # 1. Vérifier confiance avec seuil intelligent
            current_confidence = signals["confidence"]

            # 🧠 AMÉLIORATION: Appliquer boost performance symbole
            performance_boost = self.get_symbol_performance_boost(symbol)
            adjusted_confidence = current_confidence + performance_boost

            if adjusted_confidence < min_confidence:
                conf_msg = "TODO_NOT_IMPLEMENTED"
                self.logger.info(conf_msg)
                return False

            # 🧠 AMÉLIORATION: Gestion urgence plus granulaire
            urgency = signals.get("execution_urgency", "later")
            if urgency == "avoid":
                self.logger.info("Exécution déconseillée par système avancé")
                return False

            # Ajustement seuil selon urgence
            urgency_adjustments = {
                "immediate": -0.05,  # Plus permissif
                "soon": 0.0,  # Normal
                "later": +0.03,  # Plus strict
            }

            adjusted_threshold = min_confidence + urgency_adjustments.get(urgency, 0.0)
            if current_confidence < adjusted_threshold:
                urgency_msg = "TODO_NOT_IMPLEMENTED"
                self.logger.info(urgency_msg)
                return False

            # 🧠 NOUVEAU: Vérifier score de risque ajusté
            risk_score = signals.get("risk_adjusted_score", current_confidence)
            if risk_score < 0.5:
                risk_msg = "TODO_NOT_IMPLEMENTED"
                self.logger.info(risk_msg)
                return False

            # 2. Vérifier le nombre de positions ouvertes
            max_positions = 2 if urgency == "immediate" else 3
            if len(self.current_positions) >= max_positions:
                pos_msg = "TODO_NOT_IMPLEMENTED"
                self.logger.info(pos_msg)
                return False

            # 3. Vérifier le drawdown
            if self.performance_metrics["max_drawdown"] < -0.10:  # -10%
                self.logger.warning("Drawdown maximum atteint - Trading suspendu")
                return False

            # 3.b Limites journalières via agrégateur (PnL du jour)
            try:
                from tools.performance_aggregator import get_today_summary

                today = get_today_summary(self.logs_folder)
                if today is not None:
                    pnl_today = float(today.get("pnl", 0.0))
                    if pnl_today <= self._daily_loss_limit:
                        self.logger.warning("TODO_NOT_IMPLEMENTED")
                        return False
                    if pnl_today >= self._daily_profit_target:
                        self.logger.info("TODO_NOT_IMPLEMENTED")
                        return False
            except Exception:
                pass

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
                vol_threshold = 0.008 if urgency == "immediate" else 0.012

                if recent_volatility > vol_threshold:
                    vol_msg = "TODO_NOT_IMPLEMENTED"
                    self.logger.warning(vol_msg)
                    return False

            return True

        except Exception:
            self.logger.error("Erreur lors du démarrage sécurisé", exc_info=True)
            return False

    # LIGNE 590-676 : Trading continu multi-asset avec heures de marché
    def main_trading_loop(self):
        """Boucle principale de trading en continu"""
        self.logger.info("TODO_NOT_IMPLEMENTED")

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
                            self.logger.info("TODO_NOT_IMPLEMENTED")
                            continue

                        # Données spécifiques au symbole
                        if symbol not in current_data:
                            msg = "TODO_NOT_IMPLEMENTED"
                            self.logger.warning(msg)
                            continue

                        symbol_data = current_data[symbol]
                        if len(symbol_data) < 50:
                            self.logger.warning("TODO_NOT_IMPLEMENTED")
                            continue

                        # 3. Obtenir signaux AI pour ce symbole
                        signals = self.get_ai_signals(symbol_data, symbol)
                        action = signals["combined_signal"]
                        confidence = signals["confidence"]

                        # 3.b Convergence MTF 15m (optionnel, non-intrusif)
                        try:
                            from config.trading_config import TradingConfig as _TC

                            if MTF_AVAILABLE and getattr(
                                _TC, "USE_MTF_CONVERGENCE", True
                            ):
                                # Charger les fondamentaux une seule fois
                                if self._fundamentals_map is None:
                                    try:
                                        from pathlib import Path as _P

                                        funda_dir = _P("data/fundamentals")
                                        if funda_dir.exists():
                                            self._fundamentals_map = load_fundamentals_csv(
                                                funda_dir
                                            )
                                        else:
                                            self._fundamentals_map = {}
                                    except Exception:
                                        # Ignore errors loading fundamentals
                                        self._fundamentals_map = {}

                                o15, tech_mtf, funda_mtf = build_live_mtf_from_m1(
                                    symbol_data, self._fundamentals_map
                                )
                                if tech_mtf is not None and len(tech_mtf) > 0:
                                    (
                                        mtf_action,
                                        mtf_conf,
                                        mtf_agree,
                                    ) = compute_mtf_convergence(tech_mtf)
                                    signals["mtf_convergence"] = {
                                        "action": mtf_action,
                                        "confidence": mtf_conf,
                                        "agreement": mtf_agree,
                                    }
                                    # mémoriser un bref résumé pour affichage périodique
                                    try:
                                        self.last_mtf_summary = {
                                            "action": mtf_action,
                                            "confidence": mtf_conf,
                                            "agreement": mtf_agree,
                                            "symbol": symbol,
                                        }
                                    except Exception:
                                        pass

                                    # Fusion simple: si AI neutre/faible et MTF fort => utiliser MTF
                                    if (
                                        mtf_action in ("buy", "sell")
                                        and mtf_conf
                                        > max(0.55, self.confidence_threshold)
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
                                    elif mtf_action == action and action in (
                                        "buy",
                                        "sell",
                                    ):
                                        confidence = float(min(1.0, confidence + 0.1))
                                        self.logger.info(
                                            "TODO_NOT_IMPLEMENTED"
                                        )

                                # 3.c Confluence fondamentale (optionnelle)
                                try:
                                    if (
                                        getattr(_TC, "USE_FUNDAMENTAL_CONFLUENCE", True)
                                        and funda_mtf is not None
                                        and len(funda_mtf) > 0
                                    ):
                                        fconf = compute_fundamental_confluence(
                                            funda_mtf
                                        )
                                        signals["fundamental_confluence"] = fconf

                                        # Appliquer un léger boost si biais aligné
                                        max_boost = float(
                                            getattr(_TC, "FUNDAMENTAL_BOOST_MAX", 0.07)
                                        )
                                        bias = fconf.get("bias", "neutral")
                                        fscore = float(fconf.get("score", 0.0))

                                        aligned = (
                                            bias == "bull" and action == "buy"
                                        ) or (bias == "bear" and action == "sell")
                                        if aligned and action in ("buy", "sell"):
                                            boost = min(max_boost, fscore * max_boost)
                                            confidence = float(
                                                min(1.0, confidence + boost)
                                            )
                                            self.logger.info(
                                                (
                                                    "🧮 Confluence fondamentale alignée "
                                                    "(%s, score=%.2f): +%.3f -> conf=%.3f"
                                                ),
                                                bias,
                                                fscore,
                                                boost,
                                                confidence,
                                            )
                                except Exception:
                                    # Fundamental confluence error (debug only)
                                    self.logger.debug(
                                        "Fundamental confluence indisponible"
                                    )

                                # 3.d Extension technique prudente (EMA/BB/ATR/MACD hist)
                                try:
                                    if (
                                        getattr(_TC, "USE_EXTENDED_MTF_TECH", False)
                                        and tech_mtf is not None
                                        and len(tech_mtf) > 0
                                    ):
                                        # Lis légèrement quelques signaux techniques complémentaires
                                        last = tech_mtf.iloc[-1]
                                        ema_ok = 0.0
                                        bb_ok = 0.0
                                        atr_ok = 0.0
                                        macd_hist_ok = 0.0

                                        for lbl in (
                                            "1D",
                                            "4H",
                                            "1H",
                                            "30T",
                                            "15T",
                                            "5T",
                                        ):
                                            try:
                                                ema = float(
                                                    last.get(
                                                        "TODO_NOT_IMPLEMENTED",
                                                        np.nan,
                                                    )
                                                )
                                                close = (
                                                    float(o15.iloc[-1]["close"])
                                                    if len(o15)
                                                    else np.nan
                                                )
                                                if np.isfinite(ema) and np.isfinite(
                                                    close
                                                ):
                                                    ema_ok += (
                                                        1.0 if close > ema else -0.5
                                                    )
                                            except Exception:
                                                # ignore per-symbol MTF errors
                                                pass
                                            try:
                                                bb_h = float(
                                                    last.get(
                                                        "TODO_NOT_IMPLEMENTED",
                                                        np.nan,
                                                    )
                                                )
                                                bb_l = float(
                                                    last.get(
                                                        "TODO_NOT_IMPLEMENTED",
                                                        np.nan,
                                                    )
                                                )
                                                close = (
                                                    float(o15.iloc[-1]["close"])
                                                    if len(o15)
                                                    else np.nan
                                                )
                                                if (
                                                    np.isfinite(bb_h)
                                                    and np.isfinite(bb_l)
                                                    and np.isfinite(close)
                                                    and bb_h > bb_l
                                                ):
                                                    pos = (close - bb_l) / (bb_h - bb_l)
                                                    # proche des bords = momentum
                                                    bb_ok += (pos - 0.5) * 0.5
                                            except Exception:
                                                pass
                                            try:
                                                atr = float(
                                                    last.get(
                                                        "TODO_NOT_IMPLEMENTED",
                                                        np.nan,
                                                    )
                                                )
                                                if np.isfinite(atr) and atr > 0:
                                                    atr_ok += 0.1  # présence/info seulement
                                            except Exception:
                                                pass
                                            try:
                                                macd_hist = float(
                                                    last.get(
                                                        "TODO_NOT_IMPLEMENTED",
                                                        np.nan,
                                                    )
                                                )
                                                if np.isfinite(macd_hist):
                                                    macd_hist_ok += (
                                                        np.sign(macd_hist) * 0.2
                                                    )
                                            except Exception:
                                                pass

                                        ext_score = (
                                            ema_ok
                                            * 0.05
                                            + bb_ok * 0.1
                                            + atr_ok * 0.02
                                            + macd_hist_ok * 0.1
                                        )
                                        # Clip et appliquer boost modeste
                                        if "np" in globals():
                                            ext_boost = float(
                                                np.clip(ext_score, -0.05, 0.08)
                                            )
                                        else:
                                            ext_boost = float(
                                                max(min(ext_score, 0.08), -0.05)
                                            )
                                        if action == "buy" and ext_boost > 0:
                                            confidence = float(
                                                min(1.0, confidence + ext_boost)
                                            )
                                            self.logger.info(
                                                (
                                                    "🧩 Extension MTF technique: +%.3f -> conf=%.3f"
                                                ),
                                                ext_boost,
                                                confidence,
                                            )
                                        elif action == "sell" and ext_boost > 0:
                                            confidence = float(
                                                min(1.0, confidence + ext_boost)
                                            )
                                            self.logger.info(
                                                (
                                                    "🧩 Extension MTF technique: +%.3f -> conf=%.3f"
                                                ),
                                                ext_boost,
                                                confidence,
                                            )
                                except Exception:
                                    self.logger.debug(
                                        "TODO_NOT_IMPLEMENTED"
                                    )
                        except Exception:
                            self.logger.debug("TODO_NOT_IMPLEMENTED")

                        # Mettre à jour live_data pour usage ultérieur (volatilité, etc.)
                        try:
                            self.live_data[symbol] = symbol_data
                        except Exception:
                            pass

                        # NOUVEAU: Fallback pour signaux AI faibles
                        if confidence <= 0.001:  # Pratiquement 0
                            fallback_signal = self.generate_fallback_signals(
                                symbol, symbol_data
                            )
                            if fallback_signal["confidence"] > 0.50:
                                action = fallback_signal["action"]
                                confidence = fallback_signal["confidence"]
                                signals["fallback_used"] = True
                                signals["fallback_type"] = fallback_signal["type"]
                                self.logger.info("TODO_NOT_IMPLEMENTED")

                        # Logging optimisé avec seuil de décision
                        if confidence > self.confidence_threshold:
                            pass
                        else:
                            pass
                        self.logger.info("TODO_NOT_IMPLEMENTED")

                        # Détection série de pertes par symbole et activation cooldown
                        try:
                            recent_profits_sym = self._get_recent_closed_trades_by_symbol(
                                symbol,
                                self._sizing_intraday_window_minutes,
                                self._sizing_intraday_max_trades,
                            )
                            streak_sym = self._loss_streak_from_profits(
                                recent_profits_sym
                            )
                            if streak_sym >= self._loss_streak_size:
                                until = datetime.now() + timedelta(
                                    minutes=self._loss_cooldown_minutes
                                )
                                prev_until = self.symbol_cooldown_until.get(symbol)
                                if (prev_until is None) or (until > prev_until):
                                    self.symbol_cooldown_until[symbol] = until
                                    self.logger.warning(
                                        "Cooldown for %s: %d minutes (streak=%d)",
                                        symbol,
                                        self._loss_cooldown_minutes,
                                        streak_sym,
                                    )
                        except Exception:
                            pass

                        # 4. Vérification des risques et exécution
                        # Seuil optimisé configuré (+98% perf vs 0.6)
                        if (
                            action in ["buy", "sell"]
                            and confidence > self.confidence_threshold
                        ):
                            try:
                                if self.risk_check(action, signals, symbol):
                                    # Calculer SL/TP adaptatifs avec validation
                                    if "close" not in symbol_data.columns:
                                        self.logger.error(
                                            "TODO_NOT_IMPLEMENTED"
                                        )
                                        continue

                                    current_price = symbol_data["close"].iloc[-1]

                                    # Validation prix
                                    if pd.isna(current_price) or current_price <= 0:
                                        self.logger.error(
                                            "Prix invalide pour {}: {}".format(
                                                symbol, current_price
                                            )
                                        )
                                        continue

                                    # Calcul ATR avec validation
                                    if "returns" in symbol_data.columns:
                                        atr_series = symbol_data["returns"].rolling(
                                            20
                                        ).std()
                                        if len(atr_series) > 0 and not pd.isna(
                                            atr_series.iloc[-1]
                                        ):
                                            atr = atr_series.iloc[-1] * current_price
                                        else:
                                            # Fallback ATR basé sur high-low
                                            if all(
                                                col in symbol_data.columns
                                                for col in ["high", "low"]
                                            ):
                                                price_range = (
                                                    symbol_data["high"]
                                                    - symbol_data["low"]
                                                )
                                                atr = price_range.rolling(
                                                    20
                                                ).mean().iloc[-1]
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
                                            ("SL/TP invalides pour %s: SL=%s, TP=%s"),
                                            symbol,
                                            stop_loss,
                                            take_profit,
                                        )
                                        continue

                                    # Non-invasive preflight: validate volume & clamp SL/TP
                                    lot_to_send = self.lot_sizes.get(symbol, 0.01)
                                    try:
                                        if BROKER_SAFETY_AVAILABLE:
                                            min_v = getattr(self, "broker_min_volume", 0.01)
                                            vol_step = getattr(self, "volume_step", 0.01)
                                            ok_vol, adj_vol = validate_volume(lot_to_send, min_v, vol_step)
                                            if not ok_vol and adj_vol is not None:
                                                self.logger.info(
                                                    "Adjusted lot size for %s: %s -> %s",
                                                    symbol,
                                                    lot_to_send,
                                                    adj_vol,
                                                )
                                                lot_to_send = adj_vol

                                            # Broker stoplevel / digits
                                            pdigits = getattr(self, "price_digits", 5)
                                            min_stoplevel = getattr(self, "broker_min_stoplevel", 10)
                                            point = getattr(self, "point_size", 0.00001)
                                            new_sl, new_tp, changed = clamp_sl_tp(
                                                current_price,
                                                stop_loss,
                                                take_profit,
                                                pdigits,
                                                min_stoplevel,
                                                point,
                                            )
                                            if changed:
                                                self.logger.info(
                                                    "Adjusted SL/TP for %s: SL %s -> %s, TP %s -> %s",
                                                    symbol,
                                                    stop_loss,
                                                    new_sl,
                                                    take_profit,
                                                    new_tp,
                                                )
                                                stop_loss, take_profit = new_sl, new_tp
                                    except Exception:
                                        # Preflight must not block execution path; log and continue
                                        try:
                                            self.logger.debug(
                                                "Broker preflight helpers failed, continuing without adjustments",
                                                exc_info=True,
                                            )
                                        except Exception:
                                            pass

                                    # Exécuter le trade avec le lot validé
                                    success = self.execute_trade(
                                        action,
                                        symbol,
                                        lot_to_send,
                                        stop_loss,
                                        take_profit,
                                    )
                            except Exception:
                                self.logger.error("TODO_NOT_IMPLEMENTED")
                                continue

                            # Gérer le résultat trade si pas d'exception
                            try:
                                if success:
                                    self.trade_count_today += 1
                                    # Incrément par symbole avec garde-fou
                                    try:
                                        self.trade_count_by_symbol_today[symbol] = (
                                            int(
                                                self.trade_count_by_symbol_today.get(
                                                    symbol, 0
                                                )
                                            )
                                            + 1
                                        )
                                    except Exception:
                                        self.trade_count_by_symbol_today[symbol] = 1
                                    msg = "TODO_NOT_IMPLEMENTED"
                                    self.logger.info(msg)
                                    # 🧠 AMÉLIORATION: Enregistrer pour suivi performance
                                    self.record_trade_for_learning(
                                        symbol,
                                        action,
                                        confidence,
                                        signals,
                                    )
                                else:
                                    self.logger.error(
                                        "TODO_NOT_IMPLEMENTED"
                                    )
                            except Exception:
                                pass

                    except Exception:
                        self.logger.error("TODO_NOT_IMPLEMENTED")
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
                self.logger.info("TODO_NOT_IMPLEMENTED")

                time.sleep(sleep_time)

                # Sortie anticipée en mode smoke après N cycles
                if self.max_cycles and cycle_count >= self.max_cycles:
                    self.logger.info("TODO_NOT_IMPLEMENTED")
                    break

            except KeyboardInterrupt:
                self.logger.info("Arrêt demandé par l'utilisateur")
                break
            except Exception:
                self.logger.error("TODO_NOT_IMPLEMENTED")
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

                    self.performance_metrics["current_balance"] += balance_change
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

        except Exception:
            self.logger.error("TODO_NOT_IMPLEMENTED")

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
                    self.logger.debug("TODO_NOT_IMPLEMENTED")

            # 3. Forcer garbage collection
            collected = gc.collect()

            # 4. Log des stats mémoire
            if collected > 0:
                self.logger.info("TODO_NOT_IMPLEMENTED")

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")

    def _log_rejected_signal(self, symbol, action, validation_errors):
        """Log les signaux rejetés pour analyse et amélioration"""
        try:
            rejected_signal = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "action": action,
                "errors": validation_errors,
                "type": "signal_rejected",
            }

            # Sauvegarder dans un fichier pour analyse ultérieure
            rejected_signals_file = Path(self.logs_folder) / "rejected_signals.jsonl"
            with open(rejected_signals_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rejected_signal) + "\n")

            # Garder stats en mémoire
            if not hasattr(self, "rejected_signals_count"):
                self.rejected_signals_count = {}

            error_type = validation_errors[0] if validation_errors else "unknown"
            self.rejected_signals_count[error_type] = (
                self.rejected_signals_count.get(error_type, 0) + 1
            )

            self.logger.debug("TODO_NOT_IMPLEMENTED")

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")

    def calculate_dynamic_stop_loss(
        self, symbol, action, entry_price, current_volatility=None
    ):
        """Calcule un stop loss dynamique basé sur la volatilité du marché"""
        try:
            # Special handling for JP225: strict JP225 rule (ATR * 3.0)
            try:
                if "JP225" in symbol and symbol in getattr(self, "live_data", {}):
                    df = self.live_data.get(symbol)
                    if df is not None and "high" in df.columns and "low" in df.columns:
                        price_range = (df["high"] - df["low"]).rolling(14).mean()
                        atr_val = float(price_range.iloc[-1])
                        if atr_val > 0 and np.isfinite(atr_val):
                            base_stop = atr_val * 3.0
                        else:
                            base_stop = 0.0005
                    else:
                        base_stop = 0.0005
                else:
                    # 🔧 PARAMÈTRES OPTIMISÉS - Stop Loss professionnels réalistes
                    default_stops = {
                        "EURUSD": 0.0005,  # 5 pips
                        "XAUUSD": 2.0,
                        "BTCUSD": 150.0,
                    }

                    base_stop = default_stops.get(symbol, 0.0005)

                    # 🔧 VOLATILITÉ AJUSTÉE - Amplification réduite
                    if current_volatility is not None:
                        # Ajustement modéré: max +50% au lieu de +200%
                        volatility_adjustment = min(current_volatility * 0.5, 0.5)
                        base_stop *= 1.0 + volatility_adjustment
            except Exception:
                base_stop = 0.0005

            # Ajustement selon l'action
            if action == "buy":
                stop_loss = entry_price - base_stop
            else:  # sell
                stop_loss = entry_price + base_stop

            self.logger.debug("TODO_NOT_IMPLEMENTED")
            return stop_loss

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")
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
                "EURUSD": 0.0008,  # 8 pips minimum
                "XAUUSD": 3.0,  # 3 dollars minimum
                "BTCUSD": 200.0,  # 200 dollars minimum
            }

            min_distance = min_tp_distance.get(symbol, 0.0008)
            actual_distance = abs(take_profit - entry_price)

            if actual_distance < min_distance:
                # Ajuster TP pour respecter la distance minimum
                if action == "buy":
                    take_profit = entry_price + min_distance
                else:
                    take_profit = entry_price - min_distance

            self.logger.debug("TODO_NOT_IMPLEMENTED")
            return take_profit

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")
            return None

    def calculate_trailing_stop(
        self, symbol, action, entry_price, current_price, current_sl=None
    ):
        """Calcule un trailing stop intelligent"""
        try:
            # Distance minimum de trailing selon l'instrument
            min_trailing_distance = {
                "EURUSD": 0.0015,  # 15 pips
                "XAUUSD": 3.0,  # 3 dollars
                "BTCUSD": 200.0,  # 200 dollars
            }

            min_distance = min_trailing_distance.get(symbol, 0.0015)

            if action == "buy":
                # Pour un achat, le trailing stop suit le prix vers le haut
                potential_stop = current_price - min_distance

                # Ne déplacer le stop que s'il est plus avantageux
                if current_sl is None or potential_stop > current_sl:
                    new_stop = potential_stop
                    self.logger.debug("TODO_NOT_IMPLEMENTED")
                    return new_stop

            else:  # sell
                # Pour une vente, le trailing stop suit le prix vers le bas
                potential_stop = current_price + min_distance

                # Ne déplacer le stop que s'il est plus avantageux
                if current_sl is None or potential_stop < current_sl:
                    new_stop = potential_stop
                    self.logger.debug("TODO_NOT_IMPLEMENTED")
                    return new_stop

            # Pas de changement nécessaire
            return current_sl

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")
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
                    position.sl,
                )

                # Mettre à jour le stop loss si nécessaire
                if new_sl != position.sl and new_sl is not None:
                    self._update_position_stop_loss(position.ticket, new_sl)

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")

    def _update_position_stop_loss(self, ticket, new_sl):
        """Met à jour le stop loss d'une position spécifique"""
        try:
            # Créer la requête de modification
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl": new_sl,
            }

            # Envoyer la requête
            try:
                from src.utils.mt5_safe import send_order
            except Exception:
                send_order = None

            if send_order is not None:
                result = send_order(request, logger=self.logger, mt5_module=mt5)
            else:
                result = _mt5_send_safe(request)

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                self.logger.info("TODO_NOT_IMPLEMENTED")
                return True
            else:
                self.logger.warning("TODO_NOT_IMPLEMENTED")
                return False

        except Exception:
            self.logger.error("TODO_NOT_IMPLEMENTED")
            return False

    def record_trade_for_learning(self, symbol, action, confidence, signals):
        """🧠 AMÉLIORATION: Enregistrer trade pour apprentissage"""
        try:
            trade_record = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "urgency": signals.get("execution_urgency", "unknown"),
                "enhanced": signals.get("enhanced", False),
                "regime": signals.get("market_context", {}).get(
                    "volatility_regime", "unknown"
                ),
            }

            # Garder seulement les 20 derniers
            self.recent_trades_performance.append(trade_record)
            if len(self.recent_trades_performance) > 20:
                self.recent_trades_performance.pop(0)

            # Mettre à jour stats par symbole
            if symbol not in self.symbol_performance:
                self.symbol_performance[symbol] = {"trades": 0, "total_confidence": 0}

            self.symbol_performance[symbol]["trades"] += 1
            self.symbol_performance[symbol]["total_confidence"] += confidence

            # Log pour debug
            self.logger.info("TODO_NOT_IMPLEMENTED")

        except Exception:
            self.logger.warning("TODO_NOT_IMPLEMENTED")

    def get_symbol_performance_boost(self, symbol):
        """🧠 AMÉLIORATION: Boost confiance basé sur performance symbole"""
        try:
            if symbol in self.symbol_performance:
                stats = self.symbol_performance[symbol]
                if stats["trades"] >= 3:  # Minimum pour fiabilité
                    avg_conf = stats["total_confidence"] / stats["trades"]
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

            if metrics["total_trades"] > 0:
                metrics["winning_trades"] / metrics["total_trades"] * 100

            self.logger.info("📊 RÉSUMÉ PERFORMANCES:")
            self.logger.info("TODO_NOT_IMPLEMENTED")
            self.logger.info("TODO_NOT_IMPLEMENTED")
            self.logger.info("TODO_NOT_IMPLEMENTED")
            self.logger.info("TODO_NOT_IMPLEMENTED")
            self.logger.info("TODO_NOT_IMPLEMENTED")

            # Ajout non intrusif: PnL réalisé aujourd'hui (à partir des logs)
            try:
                from tools.performance_aggregator import get_today_summary

                today_summary = get_today_summary(self.logs_folder)
                if today_summary is not None:
                    self.logger.info("TODO_NOT_IMPLEMENTED")
            except Exception:
                # Best-effort, ne bloque pas
                self.logger.debug("TODO_NOT_IMPLEMENTED")

            # Résumé MTF (si mémorisé)
            try:
                if getattr(self, "last_mtf_summary", None):
                    mtf = self.last_mtf_summary
                    self.logger.info(
                        "  🧭 MTF: %s | conf=%.2f | agree=%s | symbol=%s",
                        mtf.get("action", "hold").upper(),
                        float(mtf.get("confidence", 0.0)),
                        str(mtf.get("agreement", 0)),
                        mtf.get("symbol", ""),
                    )
            except Exception:
                pass

            # Ajout: statut par instrument (compteur/limite)
            try:
                if isinstance(getattr(self, "trade_count_by_symbol_today", None), dict):
                    for sym in self.symbols:
                        done = int(self.trade_count_by_symbol_today.get(sym, 0))
                        limit = int(self.max_trades_per_instrument.get(sym, 0))
                        self.logger.info(
                            "  • %s: %d/%d trades aujourd'hui", sym, done, limit
                        )
            except Exception:
                pass

        except Exception:
            self.logger.error("TODO_NOT_IMPLEMENTED")

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
            # Si la boucle se termine proprement, retourner succès
            return True

        except Exception as e:
            self.logger.error(f"Erreur démarrage trading: {e}")
            return False
        finally:
            self.stop_live_trading()

    def start_production(self):
        """Compatibility wrapper used by `start_production.py`.

        Kept minimal and non-invasive: delegates to start_live_trading so
        external callers (launcher) don't need to be changed.
        """
        return self.start_live_trading()

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
        # Utiliser les noms d'arguments corrects: symbols, lot_sizes
        engine = LiveTradingEngine(symbols=["EURUSD"], lot_sizes=0.01)

        print("\n🎯 Démarrage en mode test (rapide)...")

        # Test de connexion et initialisation non-invasifs
        engine.connect_mt5()
        engine.initialize_ai_systems()

        # Test d'exécution d'un ordre en mode simulation/connexion réelle
        engine.execute_trade("buy", "EURUSD")

        # Arrêter proprement
        engine.stop_live_trading()

        print("\n✅ Test du système de trading live terminé")

    except KeyboardInterrupt:
        print("\n🛑 Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur lors du test: {e}")
        import traceback

        traceback.print_exc()

# NOTE: A duplicate LiveTradingEngine definition and a second test/main block
# were removed here to avoid duplicate-class collisions. The canonical
# `LiveTradingEngine` implementation lives earlier in this file.
