import contextlib
import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import pandas as _pd

import config_simple as cfg
from engine_simple.ftmo_config import MAX_POS_PER_SYMBOL

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
from engine_simple.adaptive_intelligence import AdaptiveEngine
from engine_simple.audit_trail import AuditTrail
from engine_simple.broker import Broker
from engine_simple.feature_store import FeatureStore
from engine_simple.ftmo_protector import FTMOProtector
from engine_simple.indicators import adx as ind_adx
from engine_simple.indicators import atr as ind_atr
from engine_simple.monitoring import HealthServer, MetricsCollector
from engine_simple.mt5_connector import MT5Connector
from engine_simple.notifier import Notifier
from engine_simple.position_tracker import PositionTracker
from engine_simple.rate_cache import RateCache
from engine_simple.risk_manager import RiskManager
from engine_simple.trade_executor import TradeExecutor
from engine_simple.trade_journal import TradeJournal
from engine_simple.position_manager import PositionManager
from engine_simple.regime import RegimeDetector
from engine_simple.strategy import MOM20x3
from engine_simple.indicators import ema
from engine_simple.indicators import rsi as ind_rsi
from engine_simple.performance_monitor import update_challenge, get_monitor

# ── Phase 7-16 Modules ──
from engine_simple.strategy_selector import StrategySelector, get_strategy_params
from engine_simple.news_filter import NewsFilter, is_news_blocked
from engine_simple.volume_profile import VolumeProfile, analyze as vp_analyze
from engine_simple.order_flow import OrderFlowAnalyzer, analyze_bars as flow_analyze
from engine_simple.mtf_confirm import MultiTimeframeConfirmer, confirm as mtf_confirm
from engine_simple.adaptive_params import AdaptiveParameters, get_adapted_params
from engine_simple.walk_forward_opt import WalkForwardOptimizer, get_optimal_params
from engine_simple.portfolio_opt import PortfolioOptimizer, get_risk_allocation
from engine_simple.risk_parity import RiskParitySizer, calculate_lot as rp_calculate_lot
from engine_simple.dashboard import Dashboard, generate_report as dash_report

# ── Nouveaux modules Juin 2026 ──
from engine_simple.vwap_analyzer import VWAPAnalyzer, analyze as vwap_analyze_fn
from engine_simple.market_profile import MarketProfile, analyze as mp_analyze

warnings.filterwarnings("ignore", message="X does not have valid feature names")

STATE_FILE = "runtime/robot_state.json"
HEARTBEAT_FILE = "runtime/heartbeat.txt"
PID_FILE = "runtime/robot.pid"


def _atomic_write_json(path, data):
    """Écriture atomique JSON : temp → rename. Évite la corruption si crash pendant écriture."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str))
    tmp.replace(path)  # atomique sur NTFS


def _clean_orphan_tmp_files(glob_pattern="*.tmp"):
    """H-04: Nettoie les fichiers .tmp orphelins de sessions crashées."""
    import glob as _glob

    for f in _glob.glob(glob_pattern):
        try:
            Path(f).unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[CLEAN] Orphelin .tmp ignoré: {f} ({e})")
    # Nettoie aussi dans runtime/
    runtime_dir = Path("runtime")
    if runtime_dir.exists():
        for f in runtime_dir.glob("*.tmp"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")
        # H-04b: Nettoie aussi les *.json.tmp.* (atomic write residues)
        for f in runtime_dir.glob("*.json.tmp.*"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")


def _acquire_lock():
    """PID lock — atomic file creation (empêche les instances dupliquées)"""
    pid = os.getpid()

    # 🔒 Double vérification : scanner TOUS les processus python* qui exécutent main.py
    # (couvre python.exe ET pythonw.exe, même si le PID lock est stale)
    try:
        import subprocess

        result = subprocess.run(
            [
                "powershell",
                "-Command",
                'Get-CimInstance Win32_Process | Where-Object { ($_.Name -like "python*") '
                '-and $_.CommandLine -like "*main.py*" } | Select-Object -ExpandProperty ProcessId',
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and line.isdigit():
                existing_pid = int(line)
                if existing_pid != pid:
                    logger.critical(
                        f"PID lock: instance dupliquée détectée (PID {existing_pid}) via scan processus — abandon"
                    )
                    sys.exit(1)
    except Exception as e:
        logger.debug(f"PID lock: scan processus auxiliaire ignoré ({e})")

    lock = Path(PID_FILE)
    try:
        fd = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{pid}\n".encode())
        os.close(fd)
    except FileExistsError:
        try:
            with open(PID_FILE) as f:
                existing = int(f.read().strip())
        except (ValueError, OSError):
            lock.write_text(str(pid))
            return
        if os.name == "nt":
            import ctypes

            # Windows API constants for process detection
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            PROCESS_QUERY_INFORMATION = 0x0400
            STILL_ACTIVE = 259  # Windows: exit code when process is still running
            ERROR_ACCESS_DENIED = 5  # Windows: GetLastError() value for access denied
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, False, existing
            )
            if handle:
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                if exit_code.value == STILL_ACTIVE:
                    logger.critical(f"PID lock: instance deja active (PID {existing}) — abandon")
                    sys.exit(1)
                logger.warning(f"PID lock: zombie PID {existing} libere")
            else:
                # OpenProcess a échoué (NULL handle) — deux possibilités :
                # 1. Le processus n'existe plus (zombie) → safe to overwrite
                # 2. PROCESS_QUERY_INFORMATION refusé (processus d'un autre user/session)
                #    → CONSERVATEUR : considérer comme actif pour éviter les doublons
                last_error = ctypes.windll.kernel32.GetLastError()
                if last_error == ERROR_ACCESS_DENIED:
                    logger.critical(
                        f"PID lock: accès refusé au PID {existing} — considéré comme actif (GetLastError=5)"
                    )
                    sys.exit(1)
                logger.warning(f"PID lock: OpenProcess NULL (err={last_error}) — PID {existing} présumé zombie")
        else:
            try:
                os.kill(existing, 0)
                logger.critical(f"PID lock: instance deja active (PID {existing}) — abandon")
                sys.exit(1)
            except OSError:
                pass
        # Stale lock: overwrite
        lock.write_text(str(pid))
    logger.info(f"PID lock: {pid}")


def _release_lock():
    try:
        lock = Path(PID_FILE)
        if lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink(missing_ok=True)
    except (OSError, PermissionError):
        pass


log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
# Rotation taille + temps : 10MB max, 14 backups
handler = RotatingFileHandler(
    "logs/simple_robot.log",
    maxBytes=10_485_760,
    backupCount=14,
    encoding="utf-8",
)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logging.getLogger("graphviz").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("font_manager").setLevel(logging.WARNING)
logger = logging.getLogger("robot")


class FTMO_SIMPLE:
    def _validate_config(self):
        errors = []
        if cfg.TRADING_START_HOUR >= cfg.TRADING_END_HOUR:
            errors.append(f"TRADING_START_HOUR ({cfg.TRADING_START_HOUR}) >= TRADING_END_HOUR ({cfg.TRADING_END_HOUR})")
        if not cfg.SYMBOLS:
            errors.append("SYMBOLS is empty")
        if cfg.MT5_LOGIN <= 0:
            errors.append(f"MT5_LOGIN invalid: {cfg.MT5_LOGIN}")
        if cfg.MAX_DAILY_LOSS_PCT <= 0 or cfg.MAX_DAILY_LOSS_PCT > 0.05:
            errors.append(f"MAX_DAILY_LOSS_PCT={cfg.MAX_DAILY_LOSS_PCT} — doit être entre 0 et 5%")
        if cfg.MAX_DD_PCT <= 0 or cfg.MAX_DD_PCT > 0.12:
            errors.append(f"MAX_DD_PCT={cfg.MAX_DD_PCT} — doit être entre 0 et 12%")
        if cfg.MIN_RR_RATIO < 1.0:
            errors.append(f"MIN_RR_RATIO={cfg.MIN_RR_RATIO} < 1.0 — risque de non-rentabilité")
        if cfg.MAX_POSITIONS > 12:
            errors.append(f"MAX_POSITIONS={cfg.MAX_POSITIONS} trop élevé pour FTMO 200K")
        if cfg.RISK_PER_TRADE <= 0 or cfg.RISK_PER_TRADE > 0.02:
            errors.append(f"RISK_PER_TRADE={cfg.RISK_PER_TRADE} — doit être entre 0.001 et 0.02")
        if errors:
            msg = "Configuration invalide:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.critical(msg)
            sys.exit(1)
        logger.info("Configuration validee")

    def __init__(self):
        logger.info("=" * 50)
        logger.info("MT5 FTMO SIMPLE - v4.1.0")
        logger.info("=" * 50)

        self._validate_config()

        self._state = self._load_state()
        self.audit = AuditTrail()
        self.audit.log_state_change("robot_start", None, f"v{cfg.__version__}" if hasattr(cfg, "__version__") else "?")
        self.metrics = MetricsCollector()
        self.metrics.gauge("initial_balance", 0)
        self.health_server = HealthServer(port=9090, metrics=self.metrics, health_check=self._health_status)
        try:
            self.health_server.start()
            logger.info(f"[MONITORING] Health server demarre sur port 9090")
        except Exception as e:
            logger.warning(f"[MONITORING] Impossible de demarrer health server: {e}")
        raw_mt5 = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
        self.mt5 = Broker(raw_mt5, audit=self.audit)
        self.journal = TradeJournal()
        self.feature_store = FeatureStore()
        self.notifier = Notifier()
        if not self.notifier.is_enabled():
            logger.warning(
                "TELEGRAM NON CONFIGURE: les notifications de crash "
                "ne seront pas envoyees. Configure les tokens dans .env"
            )
        if not self.mt5.connect():
            self.audit.log_error("init", "Echec connexion MT5")
            sys.exit(1)
        self._state["connected"] = True
        logger.info("Connexion MT5 etablie (Broker mode)")

        # Persist initial_balance une fois pour toutes (critique FTMO)
        if "challenge_initial_balance" not in self._state:
            try:
                self._state["challenge_initial_balance"] = self._get_balance()
            except RuntimeError as e:
                logger.error(f"Cannot fetch initial balance: {e}")
                self._state["challenge_initial_balance"] = 200000
            self._save_state()
        challenge_init_bal = self._state["challenge_initial_balance"]
        logger.info(f"Challenge initial balance: ${challenge_init_bal:.0f} (persisted)")

        self._last_batch_time = time.time()  # dernier batch de signaux (batch_interval_sec=1s)
        self._last_signals = {}  # symbol -> dict pour mémoire de signaux entre cycles
        # M15: Restaurer les signaux pré-crash depuis last_signals.json pour éviter replay
        self._restore_last_signals()
        self._stop_trading = False  # Désactivé — mode production continue (sans arret)
        # MOM20x3 pur — strategy.py est l'unique source de signaux
        self.signals = None  # interface conservée pour compatibilité
        self.adaptive = AdaptiveEngine(self.mt5, calibration_path="runtime/calibration_state.json")

        # PHASE 2.2: MetaLearner intégré dans AdaptiveEngine
        # (instance self.adaptive.meta créée dans AdaptiveEngine.__init__)

        # PHASE 3: MarketMemory — S/R levels, MTF alignment, patterns
        self.market_memory = None
        try:
            from engine_simple.market_memory import MarketMemory

            self.market_memory = MarketMemory()
            self.market_memory.load_all()
            logger.info(f"[MARKET_MEMORY] Chargé pour {len(self.market_memory.profiles)} symboles")
        except Exception as e:
            logger.warning(f"[MARKET_MEMORY] Impossible de charger: {e}")
            self.market_memory = None

        # PHASE 3: RegimeEngine — Détection avancée de 7 régimes
        self.regime_engine = None
        try:
            from engine_simple.regime_engine import RegimeEngine

            self.regime_engine = RegimeEngine()
            logger.info("[REGIME_ENGINE] Chargé — 7 régimes actifs")
        except Exception as e:
            logger.warning(f"[REGIME_ENGINE] Impossible de charger: {e}")
            self.regime_engine = None

        # PHASE 5: SessionFilter — Filtre de session par symbole
        self.session_filter = None
        try:
            from engine_simple.session_filter import SessionFilter

            self.session_filter = SessionFilter()
            logger.info("[SESSION_FILTER] Chargé — 5 symboles")
        except Exception as e:
            logger.warning(f"[SESSION_FILTER] Impossible de charger: {e}")
            self.session_filter = None

        # PHASE 6: PortfolioController — Gestion exposition multi-symboles
        self.portfolio_controller = None
        try:
            from engine_simple.portfolio_controller import PortfolioController

            self.portfolio_controller = PortfolioController()
            logger.info("[PORTFOLIO_CONTROLLER] Chargé — corrélation active")
        except Exception as e:
            logger.warning(f"[PORTFOLIO_CONTROLLER] Impossible de charger: {e}")
            self.portfolio_controller = None

        # ── Phase 7-16 Modules ──
        # Phase 7: Strategy Selector
        self.strategy_selector = StrategySelector()
        logger.info("[STRATEGY_SELECTOR] Chargé — 7 régimes, 5 symboles")

        # Phase 8: News Filter
        self.news_filter = NewsFilter()
        logger.info("[NEWS_FILTER] Chargé — calendrier statique actif")

        # Phase 9: Volume Profile
        self.volume_profile = VolumeProfile()
        logger.info("[VOLUME_PROFILE] Chargé — 50 bins, 100 lookback")

        # Phase 10: Order Flow
        self.order_flow = OrderFlowAnalyzer()
        logger.info("[ORDER_FLOW] Chargé — analyse barres active")

        # Phase 11: MTF Confirmation
        self.mtf_confirm = MultiTimeframeConfirmer()
        logger.info("[MTF_CONFIRM] Chargé — confirmation multi-TF")

        # Phase 12-13: Adaptive + WFO (per-symbol, lazy init)
        self._adaptive_params: dict[str, AdaptiveParameters] = {}
        self._wfo: dict[str, WalkForwardOptimizer] = {}

        # Phase 14: Portfolio Optimizer
        self.portfolio_optimizer = PortfolioOptimizer()
        logger.info("[PORTFOLIO_OPT] Chargé — optimisation allocation")

        # Phase 15: Risk Parity
        self.risk_parity = RiskParitySizer()
        logger.info("[RISK_PARITY] Chargé — sizing vol-adjusted")

        # Phase 16: Dashboard
        self.dashboard = Dashboard()
        logger.info("[DASHBOARD] Chargé — monitoring temps réel")

        # Phase 17: VWAP Analyzer
        self.vwap_analyzer = VWAPAnalyzer()
        logger.info("[VWAP_ANALYZER] Chargé — premium/discount zones")

        # Phase 18: Market Profile
        self.market_profile = MarketProfile()
        logger.info("[MARKET_PROFILE] Chargé — Initial Balance + TAP")

        self.ftmo = FTMOProtector(
            self.mt5,
            dict(
                MAX_POSITIONS=cfg.MAX_POSITIONS,
                MAX_TRADES_PER_DAY=cfg.MAX_TRADES_PER_DAY,
                MIN_SIGNAL_SCORE=cfg.MIN_SIGNAL_SCORE,
                LOT_SIZE=cfg.LOT_SIZE,
                RISK_PER_TRADE=cfg.RISK_PER_TRADE,
                COOLDOWN_MINUTES=cfg.COOLDOWN_MINUTES,
                MAX_DAILY_LOSS_PCT=cfg.MAX_DAILY_LOSS_PCT,
                INITIAL_BALANCE=challenge_init_bal,
                MAX_DD_PCT=cfg.MAX_DD_PCT,
                PROFIT_TARGET_PCT=cfg.PROFIT_TARGET_PCT,
                CONSISTENCY_MAX_PCT=cfg.CONSISTENCY_MAX_PCT,
                MIN_TRADING_DAYS=cfg.MIN_TRADING_DAYS,
                MAGIC=cfg.ROBOT_MAGIC,
                MAX_SPREAD_POINTS=cfg.MAX_SPREAD_POINTS,
                MAX_RISK_AMOUNT=cfg.MAX_RISK_AMOUNT,
                TRADING_START_HOUR=cfg.TRADING_START_HOUR,
                TRADING_END_HOUR=cfg.TRADING_END_HOUR,
                DANGER_HOURS=cfg.DANGER_HOURS,
                SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
                # Clés ajoutées — audit Juin 2026 (étaient manquantes, utilisaient
                # les valeurs par défaut hardcodées dans ftmo_protector)
                DAILY_PROFIT_LIMIT_PCT=cfg.DAILY_PROFIT_LIMIT_PCT,
                ZONE2_LOSS_PCT=cfg.ZONE2_LOSS_PCT,
                ZONE3_LOSS_PCT=cfg.ZONE3_LOSS_PCT,
                AUTO_PAUSE_LOSSES=cfg.AUTO_PAUSE_LOSSES,
                MAX_CORRELATED_EXPOSURE=cfg.MAX_CORRELATED_EXPOSURE,
                CIRCUIT_BREAKER_DD_PCT=cfg.CIRCUIT_BREAKER_DD_PCT,
            ),
        )
        if self._state.get("peak_equity"):
            self.ftmo.peak_equity = self._state["peak_equity"]
        if "consecutive_losses" in self._state:
            self.ftmo.consecutive_losses = self._state["consecutive_losses"]
        if self._state.get("partial_closed"):
            self.ftmo.partial_closed = set(self._state["partial_closed"])
            logger.info(f"[STATE] Restored {len(self.ftmo.partial_closed)} partial_closed tickets")
        if self._state.get("trailing_peaks"):
            self.ftmo.trailing_peaks.update(self._state["trailing_peaks"])
        if self._state.get("position_regime"):
            self.ftmo.position_regime.update(self._state["position_regime"])
        if self._state.get("peak_profit"):
            self.ftmo.peak_profit.update(self._state["peak_profit"])
        # M16: Restore cooldowns per-symbol
        if self._state.get("cooldowns"):
            for k, v in self._state["cooldowns"].items():
                with contextlib.suppress(ValueError):
                    self.ftmo.cooldowns[k] = datetime.fromisoformat(v)
            logger.info(f"[STATE] Restored {len(self.ftmo.cooldowns)} cooldowns")
        # M17: Restore _symbol_consecutive_losses
        if self._state.get("symbol_consecutive_losses"):
            self.ftmo._symbol_consecutive_losses.update(self._state["symbol_consecutive_losses"])
            logger.info(f"[STATE] Restored {len(self.ftmo._symbol_consecutive_losses)} symbol consecutive losses")
        if self._state.get("challenge_status"):
            self.ftmo.challenge_status = self._state["challenge_status"]
        if self._state.get("consistency_violated"):
            self.ftmo.consistency_violated = True
        if self._state.get("daily_profit_reduced"):
            self.ftmo._daily_profit_reduced = True
        if self._state.get("trade_history"):
            # CRITICAL: set on challenge._trade_history directly, not ftmo._trade_history
            # because ftmo._trade_history is an alias that gets disconnected on reassignment
            th_raw = self._state["trade_history"]
            self.ftmo.challenge._trade_history = []
            for t in th_raw:
                try:
                    time_val = t.get("time", "")
                    if isinstance(time_val, str):
                        time_val = datetime.fromisoformat(time_val)
                    self.ftmo.challenge._trade_history.append(
                        {
                            "symbol": t.get("symbol", ""),
                            "profit": t.get("profit", 0),
                            "time": time_val,
                        }
                    )
                except (ValueError, TypeError):
                    pass
            # Re-establish the alias
            self.ftmo._trade_history = self.ftmo.challenge._trade_history
            logger.info(f"[STATE] Restored {len(self.ftmo.challenge._trade_history)} trade_history records")
        if self._state.get("daily_pnl_by_date"):
            self.ftmo.daily_pnl_by_date = {}
            for k, v in self._state["daily_pnl_by_date"].items():
                with contextlib.suppress(ValueError):
                    self.ftmo.daily_pnl_by_date[datetime.strptime(k, "%Y-%m-%d").date()] = v
        if self._state.get("trading_days_list"):
            self.ftmo.trading_days = set()
            for d in self._state["trading_days_list"]:
                with contextlib.suppress(ValueError):
                    self.ftmo.trading_days.add(datetime.strptime(d, "%Y-%m-%d").date())
        if self._state.get("daily_stats"):
            self.ftmo.daily_stats = self._state["daily_stats"]
        _dse = self._state.get("daily_start_equity")
        if _dse is not None and _dse > 0:
            self.ftmo.daily_start_equity = _dse
            logger.debug(f"[STATE] daily_start_equity restauré: {_dse}")
        else:
            logger.debug(f"[STATE] daily_start_equity ignoré: {_dse} (<=0 ou None)")

        # PHASE 2.2: Initialiser le Meta-Learner (dans AdaptiveEngine) à partir de l'historique
        if self.ftmo._trade_history:
            logger.info(f"[META] Initialisation à partir de {len(self.ftmo._trade_history)} trades historiques")
            self.adaptive.meta.initialize_from_history(self.ftmo._trade_history)
            meta_status = self.adaptive.meta.get_calibration_status()
            logger.info(f"[META] Calibration: {meta_status}")

        class _Cache:
            def __init__(self, mt5_conn):
                self._mt5 = mt5_conn
                self._cache = None

            def get(self):
                if self._cache is None:
                    self._cache = self._mt5.get_positions()
                return self._cache

            def invalidate(self):
                self._cache = None

        self._pos_cache = _Cache(self.mt5)
        self.tracker = PositionTracker(
            self.ftmo, self.journal, self.adaptive, self._pos_cache, mt5=self.mt5, audit=self.audit
        )
        self.executor = TradeExecutor(
            self.mt5, self.ftmo, self.journal, self.tracker, self.signals, self.adaptive, audit=self.audit
        )
        self.risk_manager = RiskManager(self.ftmo, audit=self.audit)

        # Modules refactorisés (strategy/regime) — monitoring parallèle
        self._regime_detector = RegimeDetector()
        self.pos_manager = PositionManager(
            mt5=self.mt5,
            ftmo=self.ftmo,
            adaptive=self.adaptive,
            signal_gen=self.signals,
            regime_detector=self._regime_detector,
            pos_cache=self._pos_cache,
        )

        self.running = False
        self.cycle_count = 0
        self.last_report_cycle = 0
        self._last_cycle_time = time.time()
        self._watchdog_failures = 0
        self._win_rate_checked = False
        self._last_vol_check = 0
        self._vol_cache = RateCache()
        self._vol_symbol_idx = 0

    def _health_status(self):
        try:
            info = self.mt5.get_account_info() if hasattr(self, "mt5") else None
            if info:
                return {
                    "status": "ok",
                    "balance": info.balance,
                    "equity": info.equity,
                    "floating": round(info.equity - info.balance, 2),
                    "positions": len(self._pos_cache.get()) if hasattr(self, "_pos_cache") else 0,
                    "consecutive_losses": self.ftmo.consecutive_losses if hasattr(self, "ftmo") else 0,
                    "challenge_status": self.ftmo.challenge_status if hasattr(self, "ftmo") else "N/A",
                }
        except (AttributeError, RuntimeError, ValueError):
            logger.debug("State report unavailable (MT5 not ready)")
        return {"status": "error"}

    def _get_balance(self):
        info = self.mt5.get_account_info()
        if info is None:
            raise RuntimeError("Cannot get account info - MT5 disconnected")
        return info.balance

    def _health_check(self):
        """Vérifie la connexion MT5. Ne stoppe JAMAIS le robot — skip le cycle si MT5 down."""
        if self.mt5.health_check():
            if not self._state.get("connected"):
                self._state["connected"] = True
                self._mt5_down_since = None  # Reset du timer MT5 down
                self._watchdog_failures = 0  # Reset watchdog après reconnection
                logger.info("[BROKER] Connexion retablie")
            return True
        # MT5 temporairement indisponible — on loggue et on continue
        self._state["connected"] = False
        self._mt5_down_since = getattr(self, "_mt5_down_since", None)
        if self._mt5_down_since is None:
            self._mt5_down_since = time.time()
            logger.warning(
                f"[BROKER] MT5 indisponible, skipping cycles (down depuis {time.time() - self._mt5_down_since:.0f}s)"
            )
        return False

    def _heartbeat(self):
        try:
            Path(HEARTBEAT_FILE).write_text(datetime.utcnow().isoformat())
        except Exception as e:
            logger.warning(f"Heartbeat write failed: {e}")

    def _save_state(self):
        try:
            state = dict(
                peak_equity=self.ftmo.peak_equity if hasattr(self, "ftmo") else 0,
                consecutive_losses=self.ftmo.consecutive_losses if hasattr(self, "ftmo") else 0,
                partial_closed=list(self.ftmo.partial_closed) if hasattr(self, "ftmo") else [],
                trailing_peaks={k: v for k, v in self.ftmo.trailing_peaks.items()} if hasattr(self, "ftmo") else {},
                position_regime={k: v for k, v in self.ftmo.position_regime.items()} if hasattr(self, "ftmo") else {},
                peak_profit={k: v for k, v in self.ftmo.peak_profit.items()} if hasattr(self, "ftmo") else {},
                challenge_initial_balance=self._state.get(
                    "challenge_initial_balance",
                    self._get_balance()
                    if self.mt5.health_check()
                    else self._state.get("challenge_initial_balance", 200000),
                ),
                restart_count=self._state.get("restart_count", 0),
                restart_timestamps=self._state.get("restart_timestamps", []),
                daily_profit_reduced=self.ftmo._daily_profit_reduced if hasattr(self, "ftmo") else False,
                trade_history=(
                    self.ftmo._trade_history[-500:] if hasattr(self, "ftmo") and self.ftmo._trade_history else []
                ),
                daily_pnl_by_date=(
                    {str(k): v for k, v in self.ftmo.daily_pnl_by_date.items()} if hasattr(self, "ftmo") else {}
                ),
                trading_days_list=[str(d) for d in self.ftmo.trading_days] if hasattr(self, "ftmo") else [],
                challenge_status=self.ftmo.challenge_status if hasattr(self, "ftmo") else "ACTIVE",
                consistency_violated=self.ftmo.consistency_violated if hasattr(self, "ftmo") else False,
                daily_stats=self.ftmo.daily_stats if hasattr(self, "ftmo") else None,
                daily_start_equity=(
                    self.ftmo.daily_start_equity if hasattr(self, "ftmo") and self.ftmo.daily_start_equity > 0 else None
                ),
                # M16: Persist cooldowns per-symbol (survie aux redémarrages)
                cooldowns={k: v.isoformat() for k, v in self.ftmo.cooldowns.items()} if hasattr(self, "ftmo") else {},
                # M17: Persist _symbol_consecutive_losses (survie aux redémarrages)
                symbol_consecutive_losses=dict(self.ftmo._symbol_consecutive_losses) if hasattr(self, "ftmo") else {},
            )
            _atomic_write_json(STATE_FILE, state)
        except Exception as e:
            logger.warning(f"State save failed: {e}")

    def _load_state(self):
        try:
            p = Path(STATE_FILE)
            if p.exists():
                data = json.loads(p.read_text())
                # Ensure defaults for keys that may not exist yet
                data.setdefault("restart_count", 0)
                data.setdefault("restart_timestamps", [])
                data.setdefault("daily_stats", None)
                data.setdefault("daily_start_equity", None)
                return data
        except Exception as e:
            logger.warning(f"State load failed: {e}")
        return {"restart_count": 0, "restart_timestamps": []}

    def start(self):
        self.running = True
        logger.info("Robot demarre - Mode trading FTMO")
        try:
            self.trading_loop()
        except KeyboardInterrupt:
            logger.info("Arret demande")
        except Exception as e:
            logger.error(f"Erreur fatale: {e}", exc_info=True)
            self.notifier.send(f"Robot crashed: {e}")
        finally:
            self.stop()
        return True

    def stop(self):
        self.running = False
        self._save_state()
        if hasattr(self, "audit"):
            self.audit.log_state_change("robot_stop", "running", "stopped")
            self.audit.close()
        self.tracker.feature_store.close()
        self.mt5.disconnect()
        _release_lock()
        logger.info("Robot arrete")

    def trading_loop(self):
        logger.info("=" * 60)
        logger.info("BOUCLE PRINCIPALE FTMO DEMARREE")
        logger.info("=" * 60)
        logger.info("[PHASE 1.4] Cycle timeout 120s activé — détection granulaire")
        self.tracker.init_tickets()
        self.tracker.import_history()
        # Reset watchdog timer après import_history (sinon le premier cycle
        # peut détecter un faux "cycle bloqué" si l'import prend du temps)
        self._last_cycle_time = time.time()

        while self.running:
            self.cycle_count += 1
            cycle_start = time.time()

            # Auto-stop flag DÉSACTIVÉ — mode production continue (sans arret)
            self._stop_trading = False

            # Watchdog: detect MT5 freeze / stuck cycles (augmenté 120s→180s)
            since_last = time.time() - self._last_cycle_time
            if since_last > 180:  # Augmenté de 120s → 180s (3 min)
                self._watchdog_failures += 1
                logger.error(f"WATCHDOG: {since_last:.0f}s since last cycle (failure #{self._watchdog_failures})")
                self.notifier.send(f"WATCHDOG: cycle bloque {since_last:.0f}s")
                self.mt5.disconnect()
                self.audit.log_error("watchdog", f"Cycle bloque {since_last:.0f}s")
                if self._watchdog_failures >= 3:
                    logger.critical("3 watchdog failures - restarting process")
                    self.notifier.send("WATCHDOG: 3 echecs -> restart process")
                    # Limiter les restarts: max 3 par heure
                    self._state["restart_count"] = self._state.get("restart_count", 0) + 1
                    now_ts = time.time()
                    timestamps = self._state.get("restart_timestamps", [])
                    timestamps.append(now_ts)
                    timestamps = [t for t in timestamps if now_ts - t < 3600]
                    self._state["restart_timestamps"] = timestamps
                    if len(timestamps) > 3:
                        logger.critical(f"{len(timestamps)} restarts dans l'heure — abandon")
                        self.notifier.send(f"WATCHDOG: {len(timestamps)} restarts/h — abandon")
                        self._save_state()
                        # Libérer le PID lock AVANT d'arrêter pour que le nouveau processus puisse démarrer
                        _release_lock()
                        sys.exit(1)
                    self._save_state()
                    # Libérer le PID lock avant de spawner le nouveau processus
                    _release_lock()
                    import subprocess

                    subprocess.Popen([sys.executable, "main.py"], cwd=os.path.dirname(os.path.abspath(__file__)))
                    sys.exit(1)
                if not self.mt5.reconnect():
                    logger.error("Watchdog: echec reconnexion MT5")
                    break
                self._state["connected"] = True
                self._last_cycle_time = time.time()
                continue

            if not self._health_check():
                # MT5 down — skip ce cycle au lieu de stopper le robot
                mt5_down_for = time.time() - getattr(self, "_mt5_down_since", time.time())
                if mt5_down_for > 600:  # 10 minutes max sans MT5
                    logger.critical(f"[BROKER] MT5 indisponible depuis {mt5_down_for:.0f}s — arret")
                    break
                logger.warning(
                    f"[BROKER] MT5 down depuis {mt5_down_for:.0f}s — skip cycle, {600 - mt5_down_for:.0f}s avant arret"
                )
                time.sleep(5)
                continue

            self._heartbeat()
            self._pos_cache.invalidate()

            # Circuit breaker — MONITORING ONLY (ne bloque jamais le trading)
            # Le robot doit toujours être prêt à trader les meilleurs signaux 24/5.
            # Les pertes sont gérées par FTMOProtector (daily loss, max DD, per-symbol cooldown).
            try:
                account_info = self.mt5.get_account_info()
                if account_info:
                    self.risk_manager.update(account_info.equity, self.ftmo.peak_equity or account_info.equity)
                    self.risk_manager.check_circuit(
                        account_info.equity,
                        self.ftmo.peak_equity or account_info.equity,
                        self.ftmo.consecutive_losses,
                        ftmo=self.ftmo,
                    )
            except (AttributeError, RuntimeError):
                logger.debug("[CIRCUIT] Erreur circuit breaker")

            # --- Chaque opération protégée INDIVIDUELLEMENT pour préserver le timing 15s ---
            op_t = time.time()
            try:
                self.tracker.check_closed()
            except Exception as e:
                logger.warning(f"tracker.check_closed failed: {e}")
            logger.debug(f"  [TIMING] check_closed: {time.time() - op_t:.2f}s")

            op_t = time.time()
            try:
                self.tracker.track_new()
            except Exception as e:
                logger.warning(f"tracker.track_new failed: {e}")
            logger.debug(f"  [TIMING] track_new: {time.time() - op_t:.2f}s")

            account = None
            op_t = time.time()
            try:
                account = self.mt5.get_account_info()
            except Exception as e:
                logger.warning(f"get_account_info failed: {e}")
            logger.debug(f"  [TIMING] get_account_info: {time.time() - op_t:.2f}s")

            if account:
                floating = account.equity - account.balance
                dd = max(0, self.ftmo.initial_balance - account.equity)
                dd_pct = dd / max(self.ftmo.initial_balance, 1) * 100
                pos_info = f"{len(self._pos_cache.get())}pos"
                logger.info(
                    f"[Cycle {self.cycle_count}] Balance={account.balance:.0f} Eq={account.equity:.0f} "
                    f"Fl={floating:+.0f} DD={dd:.0f}({dd_pct:.1f}%) {pos_info} "
                    f"Pertes_cons={self.ftmo.consecutive_losses}"
                )
                # Métriques
                self.metrics.gauge("balance", account.balance)
                self.metrics.gauge("equity", account.equity)
                self.metrics.gauge("drawdown_pct", dd_pct)
                self.metrics.gauge("consecutive_losses", self.ftmo.consecutive_losses)
                self.metrics.gauge("open_positions", len(self._pos_cache.get()))

            # Reset daily stats si changement de jour (avant toute opération)
            if hasattr(self, "ftmo") and self.ftmo:
                try:
                    old_day = self.ftmo.daily_stats.get("day")
                    self.ftmo._reset_daily()
                    new_day = self.ftmo.daily_stats.get("day")
                    if old_day is not None and old_day != new_day:
                        try:
                            pm = get_monitor()
                            pm.generate_report()
                            logger.info(f"[PERF] Rapport quotidien généré pour {old_day}")
                        except Exception as e:
                            logger.debug(f"[PERF] Rapport quotidien échoué: {e}")
                except Exception as e:
                    logger.warning(f"daily reset failed: {e}")

            try:
                self._manage_positions()
            except Exception as e:
                logger.warning(f"_manage_positions failed: {e}", exc_info=True)

            try:
                self._vigilance_scan()
            except Exception as e:
                logger.warning(f"_vigilance_scan failed: {e}")

            try:
                self._scan_signals()
            except Exception as e:
                import traceback

                logger.warning(f"_scan_signals failed: {e}")
                logger.debug(f"_scan_signals traceback: {traceback.format_exc()}")

            try:
                self._check_win_rate()
            except Exception as e:
                logger.warning(f"_check_win_rate failed: {e}")

            try:
                self._optimize_mom_periods()  # PHASE 3
            except Exception as e:
                logger.warning(f"_optimize_mom_periods failed: {e}")

            try:
                self._check_volatility()
            except Exception as e:
                logger.warning(f"_check_volatility failed: {e}")

            # Vérification MT5 reachability — tolérance 10 min (identique au pré-cycle ligne 598-606)
            if not self._health_check():
                mt5_down_for = time.time() - getattr(self, "_mt5_down_since", time.time())
                if mt5_down_for > 600:  # 10 minutes max sans MT5
                    logger.critical(f"[BROKER] MT5 indisponible depuis {mt5_down_for:.0f}s — arret")
                    break
                logger.warning(
                    f"[BROKER] MT5 down depuis {mt5_down_for:.0f}s après cycle ops — "
                    f"skip, {600 - mt5_down_for:.0f}s avant arret"
                )
            if self.cycle_count % 60 == 0:
                # Memory monitoring — alerte si > 1.5 GB
                if HAS_PSUTIL:
                    try:
                        proc = psutil.Process()
                        mem_mb = proc.memory_info().rss / 1_048_576
                        if mem_mb > 1500:
                            logger.warning(f"[MEM] Mémoire critique: {mem_mb:.0f} MB > 1500 MB")
                        elif mem_mb > 1000:
                            logger.warning(f"[MEM] Mémoire élevée: {mem_mb:.0f} MB > 1000 MB")
                        else:
                            logger.debug(f"[MEM] {mem_mb:.0f} MB")
                    except Exception:
                        pass
                # Calibration persistante + DL si disponible (auto-gardé interne)
                self.adaptive.train_dl_if_ready()
                self.adaptive.save_calibration()
                perf = self.tracker.performance_summary()
                if perf:
                    logger.info(f"  [PERF] {json.dumps(perf)}")
                if hasattr(cfg, "reload_config") and cfg.reload_config():
                    logger.info("[CONFIG] Configuration reloaded a chaud")
                    if hasattr(self, "ftmo") and hasattr(self.ftmo, "refresh_symbol_limits"):
                        self.ftmo.refresh_symbol_limits()

            if self.cycle_count - self.last_report_cycle >= 20:
                self._log_ftmo_report()
                self.last_report_cycle = self.cycle_count

                # ── Phase 16: Dashboard Report ──
                try:
                    robot_state = {
                        "balance": account.balance if account else 0,
                        "equity": account.equity if account else 0,
                        "total_trades": len(self.ftmo._trade_history),
                        "total_pnl": sum(t.get("profit", 0) for t in self.ftmo._trade_history),
                        "win_rate": sum(1 for t in self.ftmo._trade_history if t.get("profit", 0) > 0)
                        / max(len(self.ftmo._trade_history), 1),
                        "profit_factor": self._calc_pf(self.ftmo._trade_history),
                        "current_dd": dd_pct / 100 if "dd_pct" in dir() else 0,
                        "max_dd": 0,
                        "daily_pnl": self.ftmo.daily_pnl_by_date.get(datetime.now(timezone.utc).date(), 0),
                        "daily_loss_limit": self.ftmo.initial_balance * 0.02,
                    }
                    positions_data = []
                    for pos in self._pos_cache.get():
                        positions_data.append(
                            {
                                "symbol": pos.symbol,
                                "ticket": pos.ticket,
                                "type": 0 if pos.type == "BUY" else 1,
                                "price_open": pos.price_open,
                                "price_current": pos.price_current,
                                "volume": pos.volume,
                                "profit": pos.profit,
                                "time": pos.time if hasattr(pos, "time") else time.time(),
                            }
                        )

                    report = self.dashboard.generate_report(robot_state, positions_data)
                    if self.cycle_count % 100 == 0:  # Print full report every 100 cycles
                        self.dashboard.print_report(report)
                    self.dashboard.save_report(report)
                except Exception as e:
                    logger.debug(f"[DASHBOARD] Report failed: {e}")

            self._last_cycle_time = time.time()
            self._watchdog_failures = 0

            # 🧹 GC périodique : tous les 500 cycles (~2h à 15s/cycle)
            # Évite la fragmentation mémoire Python/numpy de s'accumuler au fil du temps.
            if self.cycle_count % 500 == 0 and self.cycle_count > 0:
                import gc

                collected = gc.collect()
                logger.debug(f"[MEM] GC collecte: {collected} objets libérés (cycle {self.cycle_count})")

            elapsed = time.time() - cycle_start
            sleep_time = max(5, cfg.CYCLE_SECONDS - elapsed)
            time.sleep(sleep_time)

    def _vigilance_scan(self):
        """Run DL/regime pipeline for ALL symbols every cycle."""
        self.pos_manager.vigilance_scan()

    def _get_rates_for_vigilance(self, symbol):
        return self.pos_manager._get_rates_for_vigilance(symbol)

    def _manage_positions(self):
        self.pos_manager.manage_positions()

    def _get_mom20x3_signal(self, symbol):
        """Génère un signal MOM20x3 avec filtres ADX slope / +DI/-DI + OL params.
        Timeframe par symbole depuis config: XAUUSD=H4, BTCUSD=H1, US500.cash=H1."""
        try:
            # === Timeframe spécifique au symbole (config YAML) ===
            tf = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")

            # === Récupérer les paramètres OnlineLearner pour ce symbole ===
            ol_thresh_trending = None
            ol_thresh_ranging = None
            ol_risk_mult = 1.0
            try:
                ol_params = self.adaptive.learner.get_params(symbol, base_thresh=2.5)
                ol_thresh = ol_params.get("thresh", 2.5)
                # OL thresh >= base → pas de changement (stratégie par défaut)
                # OL thresh < base → plus agressif (WR > 78%)
                # Mode modéré: on n'applique que si OL est PLUS agressif que le base
                from engine_simple.strategy import SYMBOL_CONFIG as _SYMBOL_CFG

                base_trending = _SYMBOL_CFG.get(symbol, {}).get("threshold_trending", 2.0)
                if ol_thresh < base_trending:
                    ol_thresh_trending = ol_thresh
                    ol_thresh_ranging = max(1.5, ol_thresh - 0.5)
                    logger.info(
                        f"  [OL-THRESH] {symbol}: OL thresh={ol_thresh} → "
                        f"custom trending={ol_thresh_trending}, ranging={ol_thresh_ranging}"
                    )
                else:
                    logger.debug(f"  [OL-THRESH] {symbol}: OL thresh={ol_thresh} >= base={base_trending} → using base")
                ol_risk_mult = ol_params.get("risk_mult", 1.0)
            except Exception as e:
                logger.debug(f"  [OL] {symbol}: params fallback to defaults ({e})")

            # === Signal sur le timeframe principal du symbole ===
            rates_tf = self.mt5.get_rates(symbol, tf, count=10000)
            if rates_tf is None or len(rates_tf) < 50:
                logger.debug(
                    f"  [MOM20x3] {symbol}: rates {tf} insufficient ({0 if rates_tf is None else len(rates_tf)} bars < 50)"
                )
                return None
            mom = MOM20x3(rates_tf, symbol, market_memory=self.market_memory)
            raw = mom.analyze(
                custom_thresh_trending=ol_thresh_trending,
                custom_thresh_ranging=ol_thresh_ranging,
            )
            if raw is None:
                return None

            # === Confirmation timeframe supérieur (non-bloquant) ===
            # Pour les symboles H1: confirmation H4 (EMA50)
            # Pour XAUUSD H4: confirmation D1 (EMA50)
            h4_conf = 1.0
            higher_tf = "D1" if tf == "H4" else "H4"
            try:
                higher_cached = self.mt5.get_rates(symbol, higher_tf, count=60)
                if higher_cached is not None and len(higher_cached) > 30:
                    hc = np.array([r[4] for r in higher_cached], dtype=float)
                    he = ema(hc, 50)
                    if len(he) > 0 and not np.isnan(he[-1]) and he[-1] > 0:
                        higher_ema50 = float(he[-1])
                        higher_price = float(hc[-1])
                        if raw["action"] == "BUY" and higher_price < higher_ema50 * 0.998:
                            h4_conf = 0.80
                        elif raw["action"] == "SELL" and higher_price > higher_ema50 * 1.002:
                            h4_conf = 0.80
            except Exception:
                pass

            # === MTF Alignment (4 timeframes via MarketMemory) ===
            tick = self.mt5.get_tick(symbol)
            if self.market_memory is not None:
                try:
                    entry_price = tick.ask if tick else 0
                    if entry_price > 0:
                        mtf = self.market_memory.get_mtf_alignment(symbol, entry_price)
                        bullish_count = sum(1 for v in mtf.values() if v == "bullish")
                        bearish_count = sum(1 for v in mtf.values() if v == "bearish")

                        # Pénalité si 3+ TF contredisent le signal
                        if raw["action"] == "BUY" and bearish_count >= 3:
                            h4_conf *= 0.85  # -15%
                            logger.debug(f"  [MTF] {symbol}: {bearish_count} TF bearish → h4_conf -15%")
                        elif raw["action"] == "SELL" and bullish_count >= 3:
                            h4_conf *= 0.85
                            logger.debug(f"  [MTF] {symbol}: {bullish_count} TF bullish → h4_conf -15%")

                        # Bonus si 3+ TF confirment
                        if raw["action"] == "BUY" and bullish_count >= 3:
                            h4_conf = min(1.0, h4_conf * 1.05)  # +5%
                        elif raw["action"] == "SELL" and bearish_count >= 3:
                            h4_conf = min(1.0, h4_conf * 1.05)
                except Exception as e:
                    logger.debug(f"  [MTF] {symbol}: erreur alignment: {e}")

            # === Enrichir ===
            entry = tick.ask if tick else 0
            signal = dict(raw)
            signal["symbol"] = symbol
            signal["timeframe"] = tf
            signal["details"] = f"MOM20x3_{tf}"
            signal["quality"] = min(1.0, (signal.get("confidence", 0.5) + 0.1) * h4_conf)
            # Réduire le score si la TF supérieure contredit le signal
            if h4_conf < 1.0 and signal.get("score", 0.6) > 0.5:
                signal["score"] = max(0.5, signal["score"] * 0.90)
            # Per-symbol risk_mult from config as BASE (XAUUSD=1.00, BTCUSD=0.65, etc.)
            # OL risk_mult adjusts ON TOP (multiplicative)
            symbol_config = cfg.SYMBOL_LIMITS.get(symbol, {})
            base_risk_mult = symbol_config.get("risk_mult", 1.0)
            signal["risk_mult"] = base_risk_mult * ol_risk_mult
            signal["entry_price"] = entry if raw["action"] == "BUY" else (tick.bid if tick else 0)
            signal["higher_tf_conf"] = round(h4_conf, 2)
            atr_price = signal.get("atr", 0)
            price = tick.bid if tick else 0
            signal["atr_pct"] = round(atr_price / price * 100, 4) if price > 0 else 0
            # FIX M1: Calculer le RSI réel (14 périodes) au lieu du placeholder 50
            try:
                close_prices = np.array([r[4] for r in rates_tf], dtype=float)
                rsi_arr = ind_rsi(close_prices, period=14)
                rsi_val = float(rsi_arr[-1]) if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50.0
                signal["rsi"] = round(rsi_val, 1)
            except Exception:
                signal["rsi"] = 50.0
            return signal
        except Exception as e:
            logger.exception(f"  [MOM20x3] {symbol}: echec generation: {e}")
            return None

    def _scan_signals(self):
        # Auto-stop DÉSACTIVÉ — mode production continue (sans arret)
        # self._stop_trading = False toujours (voir __init__)

        # Batch interval — signaux à chaque cycle (batch_interval_sec=1s)

        # PHASE 0.5: Batch interval — signaux à chaque cycle (batch_interval_sec=1s)
        # Le reste du cycle (position management, trailing, SL/TP) continue en 15s
        batch_elapsed = time.time() - self._last_batch_time
        if batch_elapsed < cfg.BATCH_INTERVAL_SEC:
            if self.cycle_count % 60 == 0:
                logger.debug(
                    f"[BATCH] Prochain batch de signaux dans {cfg.BATCH_INTERVAL_SEC - batch_elapsed:.0f}s "
                    f"(toutes les {cfg.BATCH_INTERVAL_SEC}s)"
                )
            return  # ← on skip la génération de signaux, mais les positions continuent d'être gérées

        # PHASE 2.1: Dégradés → réévaluer après 100 cycles (~25 min)
        degraded_symbols = self._state.get("degraded_symbols", {})
        for symbol in list(degraded_symbols.keys()):
            if self.cycle_count - degraded_symbols.get(symbol, 0) > 100:
                del degraded_symbols[symbol]
                logger.info(f"[DEGRADED] {symbol}: réévalué après 100 cycles → mode normal repris")
                self._state["degraded_symbols"] = degraded_symbols

        positions = self._pos_cache.get()
        pending = self.mt5.get_pending_orders()
        # Comptage par (symbole, direction) — 2 max par direction
        sym_dir_counts = {}
        sym_total_counts = {}  # FIX M3: comptage total par symbole (toutes directions)
        for p in positions:
            key = (p.symbol, p.type)  # 0=BUY, 1=SELL
            sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
            sym_total_counts[p.symbol] = sym_total_counts.get(p.symbol, 0) + 1
        for o in pending:
            key = (o.symbol, o.type)
            sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
            sym_total_counts[o.symbol] = sym_total_counts.get(o.symbol, 0) + 1
        # Comptage global pour le log
        sym_counts = {}
        for (sym, _), cnt in sym_dir_counts.items():
            sym_counts[sym] = sym_counts.get(sym, 0) + cnt
        logger.debug(f"Positions: {len(positions)}, Pending: {len(pending)}, Par symbole: {sym_counts}")

        # Prune les signaux mémorisés vieux de > 20 cycles (~5 min)
        stale = [
            s for s in list(self._last_signals.keys()) if self.cycle_count - self._last_signals[s].get("cycle", 0) > 20
        ]
        for s in stale:
            del self._last_signals[s]

        # Collect all valid signals across symbols first
        candidates = []
        degraded_symbols = self._state.get("degraded_symbols", {})
        for symbol in cfg.SYMBOLS:
            # FTMO + Risk pre-trade check (SANS signal — danger_hours skip ici,
            # vérifié plus tard dans can_trade() avec le signal + bypass score≥0.80)
            pre_ok, pre_checks = self.risk_manager.pre_trade(symbol)
            if not pre_ok:
                failed = [c["rule"] for c in pre_checks if not c["pass"]]
                logger.debug(f"  [PRECHECK] {symbol}: echec {failed}")
                continue

            # Signal: MOM20x3 pur en priorité, ICT/SMC désactivé (déprécié juin 2026).
            # La couche ICT produisait trop de faux signaux (killzone, FVG, OB).
            # Le MOM20x3 pur est plus robuste (ADX + momentum + DI).
            signal = self._get_mom20x3_signal(symbol)
            # ICT désactivé : signal = self._get_ict_signal(symbol) if signal is None else signal
            if signal is None:
                continue

            # Mode dégradé : symbole avec WR < 40% → lot minimum (0.01 au lieu de désactiver)
            if symbol in degraded_symbols:
                signal["_degraded"] = True
                logger.debug(f"  [DEGRADED] {symbol}: mode lot minimum (WR < 40%)")

            score = signal.get("score", 0.6)
            # Stocker une COPIE pour éviter mutation cumulative du risk_mult
            self._last_signals[symbol] = {"signal": dict(signal), "score": score, "cycle": self.cycle_count}

            # ADX threshold: adaptatif selon le régime + bypass sur score élevé
            signal_adx = signal.get("adx", 0)
            sym_cfg = cfg.SYMBOL_LIMITS.get(symbol, {})
            signal_score = signal.get("score", 0.6)

            # Fix #3: score MOM20x3 ≥ 0.80 → bypass ADX MAIS avec seuil minimum
            # 🔒 FIX C3: bypass interdit si ADX < 15 (marché sans tendance = faux signal)
            ADX_BYPASS_MIN = 15
            if signal_score >= 0.80 and signal_adx >= ADX_BYPASS_MIN:
                logger.debug(
                    f"  [ADX] {symbol}: bypass (score={signal_score:.2f} >= 0.80, ADX={signal_adx:.1f} >= {ADX_BYPASS_MIN})"
                )
            elif signal_score >= 0.80 and signal_adx < ADX_BYPASS_MIN:
                logger.info(
                    f"  [ADX] {symbol}: bypass REFUSÉ (score={signal_score:.2f} >= 0.80 MAIS ADX={signal_adx:.1f} < {ADX_BYPASS_MIN} → range pur)"
                )
                continue
            else:
                # Fix #2: ADX adaptatif — abaissé en RANGING/LOW_VOL
                # NOTE: on override le _regime du signal (structure H1 ICT) avec l'ADX réel
                # car en marché rangeant, la structure détecte parfois un faux TREND_UP/DOWN
                regime = "RANGING" if signal_adx < 22 else signal.get("_regime", "RANGING")
                adx_thresh = sym_cfg.get("adx_thresh", 20)
                if regime in ("RANGING", "LOW_VOL"):
                    adx_thresh = min(adx_thresh, 12)  # abaissé à 12 pour signaux ICT en range
                if signal_adx < adx_thresh:
                    logger.info(
                        f"  [ADX] {symbol}: ADX={signal_adx:.1f} < {adx_thresh} (score={signal_score:.2f}, regime={signal.get('_regime', '?')}), skip"
                    )
                    continue
            logger.debug(
                f"  [SIGNAL] {symbol}: score={signal['score']:.2f}, "
                f"conf={signal['confidence']:.2f}, action={signal['action']}, "
                f"strat={signal.get('details', '?')}, "
                f"+DI={signal.get('plus_di', '?'):>5} -DI={signal.get('minus_di', '?'):>5} "
                f"slope={signal.get('adx_slope', '?'):>5}"
            )

            # PHASE 5: SessionFilter — vérifier si la session est active
            if self.session_filter:
                hour_utc = datetime.now(timezone.utc).hour
                session_score = self.session_filter.get_session_score(symbol, hour_utc)
                if session_score < 0.3:
                    logger.debug(f"  [SESSION] {symbol}: score {session_score:.2f} < 0.3 → session inactive, skip")
                    continue
                signal["session_score"] = session_score

            # ── Phase 8: News Filter — vérifier événements économiques ──
            news_blocked, news_reason = self.news_filter.is_news_blocked(symbol)
            if news_blocked:
                logger.debug(f"  [NEWS] {symbol}: {news_reason} → skip")
                continue

            # ── Phase 7: Strategy Selector — adapter params par régime ──
            regime = signal.get("_regime", "RANGING")
            sig_action_temp = signal.get("action", "BUY")
            adjusted_regime = self.strategy_selector.get_regime_for_signal(regime, sig_action_temp)
            strat_params = self.strategy_selector.get_params(
                symbol, adjusted_regime, adx=signal_adx, atr_pct=signal.get("atr_pct", 0.5)
            )

            # Vérifier si le score est suffisant pour ce régime
            should_trade, trade_reason = self.strategy_selector.should_trade(
                symbol, adjusted_regime, signal_score, signal_adx
            )
            if not should_trade:
                logger.debug(f"  [STRAT_SEL] {symbol}: {trade_reason} → skip")
                continue

            # Appliquer le threshold adjustment du régime
            signal["strat_params"] = strat_params.to_dict() if hasattr(strat_params, "to_dict") else strat_params

            # ── Phase 9: Volume Profile — ajustement score selon POC/VAH/VAL ──
            try:
                tf_vp = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")
                recent_vp = self.mt5.get_rates(symbol, tf_vp, count=100)
                if recent_vp is not None and len(recent_vp) >= 50:
                    if not isinstance(recent_vp, _pd.DataFrame):
                        _cols = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
                        _ncols = min(len(_cols), len(recent_vp[0]) if len(recent_vp) > 0 else len(_cols))
                        recent_vp = _pd.DataFrame(
                            [list(r)[:_ncols] for r in recent_vp],
                            columns=_cols[:_ncols],
                        )
                    vp_levels = self.volume_profile.analyze(recent_vp)
                    if vp_levels.poc is not None:
                        current_price = signal.get("entry_price", 0)
                        if current_price == 0:
                            tick = self.mt5.get_tick(symbol)
                            current_price = tick.ask if tick else 0
                        if current_price > 0:
                            # Near POC → boost score (high volume = strong level)
                            dist_poc = abs(current_price - vp_levels.poc) / current_price * 100
                            if dist_poc < 0.1:
                                signal["score"] = min(0.95, signal["score"] * 1.1)
                                signal["vp_boost"] = "POC"
                                logger.debug(f"  [VP] {symbol}: prix près du POC ({dist_poc:.3f}%) → score +10%")
                            # Near VAH/VAL → potential reversal zone
                            elif vp_levels.vah and current_price > vp_levels.vah * 0.999:
                                if signal.get("action") == "BUY":
                                    signal["score"] *= 0.9  # Resistance above
                                    signal["vp_boost"] = "VAH_RESISTANCE"
                                    logger.debug(f"  [VP] {symbol}: prix ≈ VAH → score -10% (résistance)")
                            elif vp_levels.val and current_price < vp_levels.val * 1.001:
                                if signal.get("action") == "SELL":
                                    signal["score"] *= 0.9  # Support below
                                    signal["vp_boost"] = "VAL_SUPPORT"
                                    logger.debug(f"  [VP] {symbol}: prix ≈ VAL → score -10% (support)")
                            signal["vp_poc"] = vp_levels.poc
                            signal["vp_vah"] = vp_levels.vah
                            signal["vp_val"] = vp_levels.val
            except Exception as e:
                logger.debug(f"  [VP] {symbol}: erreur VolumeProfile: {e}")

            # ── Phase 10: Order Flow — ticks réels MT5 + divergence delta ──
            try:
                sig_action = signal.get("action", "BUY")
                # Priorité : ticks réels MT5, fallback barres OHLCV
                flow_metrics = self.order_flow.analyze_ticks_from_mt5(self.mt5, symbol, count=1000)
                if flow_metrics is None or flow_metrics.total_volume == 0:
                    # Fallback barres
                    tf_of = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")
                    recent_of = self.mt5.get_rates(symbol, tf_of, count=100)
                    if recent_of is not None and len(recent_of) >= 20:
                        if not isinstance(recent_of, _pd.DataFrame):
                            _cols_of = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
                            _ncols_of = min(len(_cols_of), len(recent_of[0]) if len(recent_of) > 0 else len(_cols_of))
                            recent_of = _pd.DataFrame(
                                [list(r)[:_ncols_of] for r in recent_of],
                                columns=_cols_of[:_ncols_of],
                            )
                        flow_metrics = self.order_flow.analyze_bars(recent_of)

                if flow_metrics and flow_metrics.total_volume > 0:
                    flow_adj = self.order_flow.get_flow_adjustment(flow_metrics, sig_action)
                    if flow_adj["score_adj"] != 1.0:
                        old_score = signal["score"]
                        signal["score"] = min(0.95, max(0.3, signal["score"] * flow_adj["score_adj"]))
                        signal["flow_adj"] = flow_adj["score_adj"]
                        signal["flow_divergence"] = flow_adj.get("divergence")
                        signal["flow_absorption"] = flow_adj.get("absorption")
                        signal["flow_tick_data"] = flow_adj.get("is_tick_data", False)
                        logger.debug(
                            f"  [FLOW] {symbol}: adj={flow_adj['score_adj']:.3f} "
                            f"(delta={flow_adj.get('net_delta', 0):.0f}, "
                            f"div={flow_adj.get('divergence', 'none')}) "
                            f"→ score {old_score:.3f}→{signal['score']:.3f}"
                        )
                        if signal["score"] < cfg.MIN_SIGNAL_SCORE:
                            logger.debug(
                                f"  [FLOW] {symbol}: score {signal['score']:.3f} < {cfg.MIN_SIGNAL_SCORE} → skip"
                            )
                            continue
            except Exception as e:
                logger.debug(f"  [FLOW] {symbol}: erreur OrderFlow: {e}")

            # ── Phase 11: MTF Confirmation — vérifier TF supérieur ──
            try:
                tf_signal = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")
                # Get higher TF data
                higher_tfs = {"H1": "H4", "H4": "D1", "D1": "W1"}
                tf_higher = higher_tfs.get(tf_signal)
                if tf_higher:
                    recent_higher = self.mt5.get_rates(symbol, tf_higher, count=100)
                    if recent_higher is not None and len(recent_higher) >= 50:
                        if not isinstance(recent_higher, _pd.DataFrame):
                            _cols = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
                            _ncols = min(len(_cols), len(recent_higher[0]) if len(recent_higher) > 0 else len(_cols))
                            recent_higher = _pd.DataFrame(
                                [list(r)[:_ncols] for r in recent_higher],
                                columns=_cols[:_ncols],
                            )
                        mtf_confirmed, mtf_factor = self.mtf_confirm.confirm(
                            None, recent_higher, signal.get("action", "BUY")
                        )
                        if mtf_factor != 1.0:
                            old_score = signal["score"]
                            signal["score"] = max(0.3, min(0.95, signal["score"] * mtf_factor))
                            signal["mtf_factor"] = mtf_factor
                            logger.debug(
                                f"  [MTF] {symbol}: TF supérieur {'confirme' if mtf_factor > 1 else 'contredit'} "
                                f"(factor={mtf_factor:.2f}) → score {old_score:.2f}→{signal['score']:.2f}"
                            )
            except Exception as e:
                logger.debug(f"  [MTF] {symbol}: erreur MTFConfirm: {e}")

            # ── Phase 12: Market Profile — Initial Balance + TAP + session type ──
            try:
                tf_mp = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")
                mp_data = self.mt5.get_rates(symbol, tf_mp, count=100)
                if mp_data is not None and len(mp_data) >= 10:
                    if not isinstance(mp_data, _pd.DataFrame):
                        _cols_m = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
                        _ncols_m = min(len(_cols_m), len(mp_data[0]) if len(mp_data) > 0 else len(_cols_m))
                        mp_data = _pd.DataFrame(
                            [list(r)[:_ncols_m] for r in mp_data],
                            columns=_cols_m[:_ncols_m],
                        )
                    mp_result = self.market_profile.analyze(mp_data, signal_action=signal.get("action"))
                    mp_adj = mp_result.get("score_adj", 1.0)
                    if mp_adj != 1.0:
                        old_score = signal["score"]
                        signal["score"] = max(0.3, min(0.95, signal["score"] * mp_adj))
                        signal["mp_adj"] = mp_adj
                        signal["mp_session_type"] = mp_result.get("session_type")
                        signal["mp_ib_direction"] = mp_result.get("ib_direction")
                        signal["mp_price_in_ib"] = mp_result.get("price_in_ib")
                        logger.debug(
                            f"  [MP] {symbol}: session={mp_result.get('session_type')}, "
                            f"ib_dir={mp_result.get('ib_direction')}, adj={mp_adj:.3f} "
                            f"→ score {old_score:.3f}→{signal['score']:.3f}"
                        )
                        if signal["score"] < cfg.MIN_SIGNAL_SCORE:
                            logger.debug(
                                f"  [MP] {symbol}: score {signal['score']:.3f} < {cfg.MIN_SIGNAL_SCORE} → skip"
                            )
                            continue
            except Exception as e:
                logger.debug(f"  [MP] {symbol}: erreur MarketProfile: {e}")

            # ── Phase 13: VWAP Premium/Discount — ajustement selon zone ──
            try:
                tf_vwap = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")
                vwap_data = self.mt5.get_rates(symbol, tf_vwap, count=100)
                if vwap_data is not None and len(vwap_data) >= 20:
                    if not isinstance(vwap_data, _pd.DataFrame):
                        _cols_v = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
                        _ncols_v = min(len(_cols_v), len(vwap_data[0]) if len(vwap_data) > 0 else len(_cols_v))
                        vwap_data = _pd.DataFrame(
                            [list(r)[:_ncols_v] for r in vwap_data],
                            columns=_cols_v[:_ncols_v],
                        )
                    vwap_result = self.vwap_analyzer.analyze(vwap_data)
                    vwap_adj = vwap_result.get("score_adj", 1.0)
                    if vwap_adj != 1.0:
                        old_score = signal["score"]
                        signal["score"] = max(0.3, min(0.95, signal["score"] * vwap_adj))
                        signal["vwap_adj"] = vwap_adj
                        signal["vwap_zone"] = vwap_result.get("zone")
                        signal["vwap_price"] = vwap_result.get("vwap")
                        signal["vwap_reason"] = vwap_result.get("reason")
                        logger.debug(
                            f"  [VWAP] {symbol}: zone={vwap_result.get('zone')}, "
                            f"adj={vwap_adj:.3f}, raison={vwap_result.get('reason')} "
                            f"→ score {old_score:.3f}→{signal['score']:.3f}"
                        )
                        if signal["score"] < cfg.MIN_SIGNAL_SCORE:
                            logger.debug(
                                f"  [VWAP] {symbol}: score {signal['score']:.3f} < {cfg.MIN_SIGNAL_SCORE} → skip"
                            )
                            continue
            except Exception as e:
                logger.debug(f"  [VWAP] {symbol}: erreur VWAPAnalyzer: {e}")

            # ── Phase 14: Adaptive Params — ajuster risk_mult selon performance ──
            try:
                if symbol not in self._adaptive_params:
                    from engine_simple.adaptive_params import AdaptiveParameters

                    self._adaptive_params[symbol] = AdaptiveParameters(symbol)
                ap = self._adaptive_params[symbol]
                adapted = ap.get_adapted_params()
                if adapted.sample_size >= 20:
                    # Appliquer risk_mult adaptatif (multiplier, pas remplacer)
                    current_rm = signal.get("risk_mult", 1.0)
                    signal["risk_mult"] = current_rm * adapted.risk_mult
                    signal["adaptive_params"] = adapted.to_dict()
                    logger.debug(
                        f"  [ADAPTIVE] {symbol}: WR={adapted.win_rate:.1%}, "
                        f"risk_mult={adapted.risk_mult:.2f} → signal risk={signal['risk_mult']:.2f}"
                    )
            except Exception as e:
                logger.debug(f"  [ADAPTIVE] {symbol}: erreur: {e}")

            # Per-direction position limit: dynamique selon la confidence du signal
            #   conf > 85% → max 3 positions/symbole (jusqu'à 3 dans la même direction)
            #   conf > 70% → max 2 positions/symbole
            #   sinon      → max 1 position/symbole (comportement par défaut)
            sig_action = signal.get("action")
            sig_dir = 0 if sig_action == "BUY" else 1 if sig_action == "SELL" else None
            sig_conf = signal.get("confidence", 0.0)

            if sig_conf > 0.85:
                max_per_symbol = 4
            elif sig_conf > 0.70:
                max_per_symbol = 3
            else:
                max_per_symbol = 1

            # Plafonner au hard-limit de la config (sécurité)
            hard_limit = MAX_POS_PER_SYMBOL.get(symbol, cfg.MAX_POSITIONS_PER_SYMBOL)
            max_per_symbol = min(max_per_symbol, hard_limit)

            # Injecter dans le signal pour que le TradeExecutor l'utilise
            signal["max_per_symbol"] = max_per_symbol

            # PHASE 6: PortfolioController — vérifier corrélation et exposition
            if self.portfolio_controller:
                can_open, reason = self.portfolio_controller.can_open_position(symbol, sig_action, [])
                if not can_open:
                    logger.debug(f"  [PORTFOLIO] {symbol}: {reason}")
                    continue

            # Vérifier la limite dans la direction du signal
            if sig_dir is not None:
                dir_count = sym_dir_counts.get((symbol, sig_dir), 0)
                if dir_count >= max_per_symbol:
                    logger.debug(
                        f"  [LIMIT] {symbol}: déjà {dir_count} position(s) {sig_action} "
                        f"(max={max_per_symbol}, conf={sig_conf:.2f})"
                    )
                    continue

            # Vérifier la limite TOTALE par symbole (BUY+SELL combinés)
            # max total = max_per_symbol (même direction) × 2 (les deux sens)
            max_pos_total = min(max_per_symbol * 2, hard_limit * 2, cfg.MAX_POSITIONS)
            total_count = sym_total_counts.get(symbol, 0)
            if total_count >= max_pos_total:
                logger.debug(
                    f"  [LIMIT] {symbol}: déjà {total_count} position(s) totales "
                    f"(max={max_pos_total}, conf={sig_conf:.2f})"
                )
                continue

            # Feed features to concept drift detector
            candidates.append((signal["score"], symbol, signal, positions))
            # Ne pas faire de check MAX_POSITIONS ici — la liste positions est figée
            # Le vrai check est fait après chaque exécution dans la boucle d'exec

        # Save signal debug info — seulement tous les 5 cycles (évite I/O excessif)
        if self.cycle_count % 5 == 0:
            self._save_signal_debug(candidates)

        # Execute only the best signals per cycle (sorted by score)
        candidates.sort(key=lambda x: x[0], reverse=True)
        max_per_cycle = cfg.MAX_SIGNALS_PER_CYCLE
        executed = 0
        for score, symbol, signal, positions in candidates:
            if executed >= max_per_cycle:
                logger.info(f"  [LIMIT] Max signaux par cycle ({max_per_cycle}) atteint")
                break
            # Re-fetch positions réelles à chaque itération pour éviter les dépassements
            live_positions = self._pos_cache.get()
            live_pending = self.mt5.get_pending_orders()
            live_total = len(live_positions) + len(live_pending)
            if live_total >= cfg.MAX_POSITIONS:
                logger.info(f"  [LIMIT] Max positions ({cfg.MAX_POSITIONS}) atteint ({live_total} en cours)")
                break
            can_trade, reason = self.ftmo.can_trade(symbol, signal, live_positions)
            if not can_trade:
                logger.debug(f"  [FTMO FINAL] {symbol}: {reason}")
                continue

            # Anticipation Engine: vérifier que le DL confirme la direction
            if hasattr(self, "anticipation") and self.anticipation is not None:
                try:
                    tf_ae = cfg.SYMBOL_TIMEFRAMES.get(symbol, "H1")
                    recent_ae = self.mt5.get_rates(symbol, tf_ae, count=60)
                    if recent_ae is not None and len(recent_ae) > 10:
                        # Convertir les rates MT5 (liste de tuples) en DataFrame
                        # si ce n'en est pas déjà un (market_memory attend un DataFrame).
                        if not isinstance(recent_ae, _pd.DataFrame):
                            _cols = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
                            # Prendre seulement les colonnes disponibles
                            _ncols = min(len(_cols), len(recent_ae[0]) if len(recent_ae) > 0 else len(_cols))
                            recent_ae = _pd.DataFrame(
                                [list(r)[:_ncols] for r in recent_ae],
                                columns=_cols[:_ncols],
                            )
                        current_price = signal.get("entry_price", 0)
                        if current_price == 0:
                            tick = self.mt5.get_tick(symbol)
                            current_price = tick.ask if tick else 0
                        if current_price > 0:
                            ctx = self.anticipation.anticipate(symbol, current_price, recent_ae)
                            signal_side = "HAUSSE" if signal.get("action") == "BUY" else "BAISSE"
                            ant_side = ctx["consensus"]["direction"]
                            ant_conf = ctx["consensus"]["confidence"]
                            # Si l'anticipation est FORTE et en désaccord → réduire risque
                            # MAIS seulement si Devil's Advocate n'a PAS déjà réduit (évite double peine)
                            if ant_side != "NEUTRE" and ant_side != signal_side:
                                if ant_conf > 0.65:
                                    current_rm = signal.get("risk_mult", 1.0)
                                    if current_rm >= 0.9:  # pas encore réduit par DA
                                        signal["risk_mult"] = current_rm * 0.5
                                        logger.info(
                                            f"  [ANTICIP] {symbol}: DL dit {ant_side} (conf={ant_conf:.2f}) "
                                            f"vs signal {signal_side} → risque /2"
                                        )
                                    else:
                                        logger.info(
                                            f"  [ANTICIP] {symbol}: DL dit {ant_side} mais déjà réduit "
                                            f"(risk_mult={current_rm:.2f}) → skip double peine"
                                        )
                                else:
                                    logger.debug(
                                        f"  [ANTICIP] {symbol}: DL en désaccord ({ant_side}, conf={ant_conf:.2f})"
                                    )
                            elif ant_side == signal_side and ant_conf > 0.65:
                                signal["risk_mult"] = signal.get("risk_mult", 1.0) * 1.2
                                logger.info(
                                    f"  [ANTICIP] {symbol}: DL confirme {signal_side} (conf={ant_conf:.2f}) → risque +20%"
                                )
                except Exception as e:
                    logger.debug(f"  [ANTICIP] {symbol}: erreur anticipation: {e}")
            # [SIGNAL] = signal validé AVANT exécution (debug, pas un trade réel)
            logger.debug(
                f"  [SIGNAL] >>> {symbol} {signal['action']} (score={score:.2f}, strat={signal.get('details', '?')})"
            )
            if hasattr(self, "audit"):
                self.audit.log_signal(
                    symbol,
                    signal["action"],
                    score,
                    signal.get("confidence", 0),
                    signal.get("_regime", "?"),
                    signal.get("details"),
                )
            self.metrics.inc("trade_signals", {"symbol": symbol, "action": signal["action"]})
            # Kelly sizing: multiplie le risk_mult de l'Anticipation Engine
            # par le ratio Kelly. Le signal est FRAIS chaque cycle (pas de cumul).
            # Cap à 1.5 max pour éviter les positions explosives.
            symbol_perf = self.tracker.performance.get(symbol)
            if symbol_perf and hasattr(self, "risk_manager"):
                rr = signal.get("rr", cfg.MIN_RR_RATIO * 1.5)
                kelly_risk = self.risk_manager.calculate_position_risk(symbol_perf, rr)
                kelly_factor = max(0.3, min(1.5, kelly_risk / cfg.RISK_PER_TRADE))  # borné [0.3, 1.5]
                signal["risk_mult"] = signal.get("risk_mult", 1.0) * kelly_factor
                # 🔒 FIX M2: Cap final du risk_mult par symbole (après toutes les multiplications)
                _FINAL_CAP = {"XAUUSD": 1.25, "BTCUSD": 1.00, "US500.cash": 1.15, "ETHUSD": 1.00}
                cap = _FINAL_CAP.get(symbol, 1.0)
                if signal["risk_mult"] > cap:
                    logger.info(f"  [RISK] {symbol}: risk_mult {signal['risk_mult']:.3f} capé à {cap} (post-Kelly)")
                    signal["risk_mult"] = cap
                logger.debug(
                    f"    [KELLY] {symbol}: risk_mult={signal['risk_mult']:.3f} (kelly_factor={kelly_factor:.2f})"
                )
            result = self.executor.execute(symbol, signal)
            # FIX #9: Ne compter et rafraîchir QUE si l'ordre a vraiment été placé
            if result is not None and getattr(result, "retcode", None) == 10009:
                executed += 1
                # [TRADE] = trade RÉELlement exécuté (info, trace dans logs)
                logger.info(
                    f"  [TRADE] >>> {symbol} {signal['action']} (score={score:.2f}, strat={signal.get('details', '?')})"
                )
                # Enregistrer le trade ouvert pour MAX_TRADES_PER_DAY
                self.ftmo.register_open_trade(symbol)
                # Invalider le cache pour que le prochain candidat voie la nouvelle position
                self._pos_cache.invalidate()
                # Mettre à jour sym_dir_counts pour éviter un doublon dans le même cycle
                sig_type = 0 if signal.get("action") == "BUY" else 1
                key = (symbol, sig_type)
                sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
                logger.debug(
                    f"  [EXEC] {symbol} {signal.get('action')} OK — positions {symbol}: {sym_dir_counts.get(key, 0)}"
                )
            elif result is not None:
                logger.warning(
                    f"  [EXEC] {symbol} {signal.get('action')} échec (retcode={result.retcode}) — pas compté"
                )
            # Si result is None, c'était un refus pré-exécution (rate limit, RR, etc.) — pas compté non plus

        # PHASE 0.5: Mettre à jour le timestamp du dernier batch
        # Le prochain batch sera dans BATCH_INTERVAL_SEC secondes
        self._last_batch_time = time.time()
        logger.info(
            f"[BATCH] Batch signaux terminé — {executed} trade(s), prochain batch dans {cfg.BATCH_INTERVAL_SEC}s"
        )

    def _restore_last_signals(self):
        """M15: Restaure les signaux pré-crash depuis last_signals.json.
        Évite le replay de signaux déjà envoyés après un redémarrage brutal.
        Les signaux restaurés sont marqués avec un cycle_count futur pour qu'ils
        expirent rapidement (20 cycles ~5 min)."""
        try:
            sig_path = Path("runtime/last_signals.json")
            if not sig_path.exists():
                return
            raw = sig_path.read_text()
            data = json.loads(raw)
            saved_cycle = data.get("cycle", 0)
            saved_signals = data.get("signals", [])
            if not saved_signals:
                return
            # On donne un offset de cycle pour que ces signaux expirent
            # dans ~20 cycles (5 min). On met cycle=saved_cycle-1 pour qu'ils
            # soient considérés comme "vieux" et ne bloquent pas les nouveaux.
            age = time.time() - sig_path.stat().st_mtime
            if age > 300:  # > 5 min → trop vieux, ignorer
                logger.info(f"[M15] last_signals.json trop vieux ({age:.0f}s) — ignoré")
                return
            for s in saved_signals:
                sym = s.get("symbol")
                if sym:
                    self._last_signals[sym] = {
                        "signal": {"action": s.get("action"), "score": s.get("score", 0)},
                        "score": s.get("score", 0),
                        "cycle": saved_cycle - 1,  # considéré comme déjà traité
                    }
            logger.info(
                f"[M15] Restauré {len(saved_signals)} signaux depuis last_signals.json "
                f"(cycle {saved_cycle}, age={age:.0f}s)"
            )
        except Exception as e:
            logger.debug(f"[M15] Restaure last_signals échouée: {e}")

    def _save_signal_debug(self, candidates):
        try:
            sigs = []
            for score, symbol, signal, _ in candidates[:10]:
                sigs.append(
                    {
                        "symbol": symbol,
                        "action": signal.get("action"),
                        "score": round(score, 2),
                        "confidence": round(signal.get("confidence", 0), 2),
                        "adx": signal.get("adx", 0),
                        "details": signal.get("details", ""),
                    }
                )
            Path("runtime/last_signals.json").write_text(
                json.dumps({"cycle": self.cycle_count, "signals": sigs}, indent=2)
            )
        except Exception as e:
            logger.debug(f"Signal debug save failed: {e}")

    def _log_ftmo_report(self):
        report = self.ftmo.get_progress_report()
        logger.info("=" * 50)
        logger.info("RAPPORT FTMO CHALLENGE")
        for k, v in report.items():
            logger.info(f"  {k}: {v}")
        logger.info("=" * 50)
        try:
            # Écriture atomique : tmp + rename pour éviter corruption concurrente
            tmp_path = Path("runtime/ftmo_report.json.tmp")
            dst_path = Path("runtime/ftmo_report.json")
            tmp_path.write_text(json.dumps(report, indent=2))
            tmp_path.replace(dst_path)
            # Performance Monitor — suivi du challenge et rapport périodique
            try:
                update_challenge(report)
                # Rapport périodique toutes les 60 cycles (~15 min)
                if self.cycle_count % 60 == 0:
                    get_monitor().generate_report()
            except Exception:
                logger.exception("Performance Monitor update failed")
        except Exception as e:
            logger.debug(f"FTMO report write failed: {e}")

    def _check_volatility(self):
        if self.cycle_count - self._last_vol_check < 60:
            return
        self._last_vol_check = self.cycle_count
        try:
            symbols = cfg.SYMBOLS
            # spread across multiple cycles: 3 symbols per cycle
            n = len(symbols)
            for i in range(3):
                idx = (self._vol_symbol_idx + i) % n
                symbol = symbols[idx]
                cached = self._vol_cache.get_volatility(symbol)
                if cached:
                    self._log_vol(symbol, cached)
                    continue
                data = self.mt5.get_rates(symbol, "H1", 50)
                if data is None or len(data) < 30:
                    continue
                cc = np.array([r[4] for r in data], dtype=float)
                hh = np.array([r[2] for r in data], dtype=float)
                ll = np.array([r[3] for r in data], dtype=float)
                cur = float(cc[-1])
                ma20 = float(np.mean(cc[-20:]))
                ma20_dist = (cur - ma20) / ma20 * 100
                atr_arr = ind_atr(hh, ll, cc, 14)
                atr_v = float(atr_arr[-1]) if len(atr_arr) > 0 and not np.isnan(atr_arr[-1]) else 0
                atr_pct = atr_v / cur * 100 if cur > 0 else 0
                adx_v = ind_adx(hh, ll, cc, 14)[0] if len(hh) >= 30 else 0
                tick = self.mt5.get_symbol_info(symbol)
                sp = (tick.ask - tick.bid) if tick else 0
                sp_pts = sp / (tick.point or 0.0001) if tick and tick.point > 0 else 0
                result = dict(
                    cur=cur, ma20=ma20, ma20_dist=ma20_dist, atr_v=atr_v, atr_pct=atr_pct, adx_v=adx_v, sp_pts=sp_pts
                )
                self._vol_cache.set_volatility(symbol, result, ttl=300)
                self._log_vol(symbol, result)
            self._vol_symbol_idx = (self._vol_symbol_idx + 3) % n
        except Exception as e:
            logger.debug(f"  [VOL] error: {e}")

    def _log_vol(self, symbol, v):
        neutral = v["atr_v"] if v["atr_v"] > 0 else 0.001
        zone = "neutral"
        if v["cur"] < v["ma20"] - neutral:
            zone = "below MA20"
        elif v["cur"] > v["ma20"] + neutral:
            zone = "above MA20"
        logger.info(
            f"  [VOL] {symbol}: {v['cur']:.5f} MA20={v['ma20']:.5f} ({v['ma20_dist']:+.2f}%) "
            f"ADX={v['adx_v']:.1f} ATR%={v['atr_pct']:.3f}% Spread={v['sp_pts']:.0f}pts [{zone}]"
        )

    def _check_win_rate(self):
        total = len(self.ftmo._trade_history)
        if total < 100:
            return
        # Utiliser les trades récents (dernier 200) pour la détection de dérive,
        # pas l'historique global qui peut masquer une dégradation récente
        recent_window = 200
        recent_trades = (
            self.ftmo._trade_history[-recent_window:]
            if len(self.ftmo._trade_history) >= recent_window
            else self.ftmo._trade_history
        )
        recent_wr = sum(1 for t in recent_trades if t["profit"] > 0) / max(len(recent_trades), 1)
        global_wr = sum(1 for t in self.ftmo._trade_history if t["profit"] > 0) / max(total, 1)
        logger.info(
            f"  [WR CHECK] {total} trades, global WR={global_wr:.1%}, recent ({len(recent_trades)}) WR={recent_wr:.1%}"
        )

        # PHASE 2.1: Check par symbole → degraded (lot minimum) si WR < 40% sur 20 trades
        degraded_symbols = self._state.get("degraded_symbols", {})
        for symbol in cfg.SYMBOLS:
            sym_trades = [t for t in recent_trades if t.get("symbol") == symbol]
            if len(sym_trades) >= 20:
                sym_wr = sum(1 for t in sym_trades if t["profit"] > 0) / len(sym_trades)
                sym_pf = self._calc_pf(sym_trades)
                # PHASE 3: Log détaillé par symbole
                logger.info(f"  [PHASE 3] {symbol}: {len(sym_trades)} trades, WR={sym_wr:.1%}, PF={sym_pf:.2f}")

                if sym_wr < 0.40:
                    # Mode dégradé au lieu de disable complet : le symbole continue à trader
                    # mais avec lot minimum (0.01) pour éviter de rater un retournement
                    if symbol not in degraded_symbols:
                        degraded_symbols[symbol] = self.cycle_count
                        self._state["degraded_symbols"] = degraded_symbols
                        logger.warning(
                            f"[DEGRADED] {symbol}: WR={sym_wr:.1%} < 40% (cycle {self.cycle_count}) → lot minimum"
                        )
                        self.notifier.send(f"DEGRADED: {symbol} WR={sym_wr:.1%} < 40% → lot min")
                elif sym_wr >= 0.50 and symbol in degraded_symbols:
                    # Rétablissement : WR repassé au-dessus de 50% → sortir du mode dégradé
                    del degraded_symbols[symbol]
                    self._state["degraded_symbols"] = degraded_symbols
                    logger.info(f"[DEGRADED] {symbol}: WR={sym_wr:.1%} ≥ 50% → retour mode normal")
                    self.notifier.send(f"DEGRADED: {symbol} WR={sym_wr:.1%} ≥ 50% → mode normal")
                elif sym_wr < 0.50:
                    logger.warning(f"[WR WATCH] {symbol}: WR={sym_wr:.1%} < 50% (à surveiller)")

        if not self._win_rate_checked and recent_wr < 0.55:
            logger.warning(f"  [WR CHECK] Recent WR={recent_wr:.1%} < 55% — ajustement seuils")
            self._win_rate_checked = True
            for symbol in cfg.SYMBOLS:
                p = dict(self.adaptive.learner.get_params(symbol))
                p["thresh"] = max(1.5, p.get("thresh", 2.5) - 0.3)
                p["risk_mult"] = min(1.0, p.get("risk_mult", 1.0) * 0.8)
                # Persist the adjusted params
                self.adaptive.learner.adapted_params[symbol] = p
            logger.info("  [WR CHECK] Seuils abaisses: thresh-0.3, risk_mult*0.8")
        elif self._win_rate_checked and total > 200:
            recent = (
                self.ftmo._trade_history[-100:] if len(self.ftmo._trade_history) >= 100 else self.ftmo._trade_history
            )
            recent_wr = sum(1 for t in recent if t["profit"] > 0) / max(len(recent), 1)
            if recent_wr >= 0.60:
                logger.info(f"  [WR CHECK] Recent WR={recent_wr:.1%} >= 60% — restauration seuils")
                self._win_rate_checked = False
                for symbol in cfg.SYMBOLS:
                    self.adaptive.learner._update_params(symbol)
        if not self._win_rate_checked and recent_wr >= 0.55:
            logger.info(f"  [WR CHECK] Recent WR={recent_wr:.1%} >= 55% — OK")
            self._win_rate_checked = True

    def _calc_pf(self, trades: list) -> float:
        """Calcule le Profit Factor à partir d'une liste de trades."""
        if not trades:
            return 1.0
        wins = sum(t.get("profit", 0) for t in trades if t.get("profit", 0) > 0)
        losses = abs(sum(t.get("profit", 0) for t in trades if t.get("profit", 0) < 0))
        if losses == 0:
            return wins if wins > 0 else 1.0
        return wins / losses if losses > 0 else 1.0

    def _optimize_mom_periods(self):
        """PHASE 3: Ajuster dynamiquement les périodes MOM20x3 basées sur WR.

        Règles :
        - WR < 45% : réduire la période (plus de signaux, plus réactif)
        - WR > 70% : augmenter la période (moins de faux signaux)

        Bornes absolues : min=12, max=28 (évite les extrêmes dangereux).
        Symboles désactivés (allow_buys=false AND allow_shorts=false) → ignorés.

        🔧 Anti-oscillation Juin 2026: cooldown de 50 trades entre ajustements
        pour éviter le cycle 20→18→16→reset→20 observé avec WR~38%.
        """
        if len(self.ftmo._trade_history) < 50:
            return  # Pas assez de données pour ajuster

        # Anti-oscillation: ne pas ajuster plus d'une fois tous les 50 trades
        if not hasattr(self, "_phase3_last_adjustment"):
            self._phase3_last_adjustment = 0
        trades_since_last = len(self.ftmo._trade_history) - self._phase3_last_adjustment
        if trades_since_last < 50:
            return  # Cooldown anti-oscillation

        from engine_simple.strategy import SYMBOL_MOMENTUM_PERIODS, SYMBOL_CONFIG

        # FIX m1: Sauvegarder les périodes initiales au premier appel (reset possible)
        if not hasattr(self, "_initial_mom_periods"):
            self._initial_mom_periods = dict(SYMBOL_MOMENTUM_PERIODS)

        recent_trades = self.ftmo._trade_history[-100:]
        adjustments = {}

        # Bornes absolues de sécurité — serrées pour éviter les extrêmes
        MIN_PERIOD = 12  # pas en dessous de 12 (trop de bruit)
        MAX_PERIOD = 28  # pas au-dessus de 28 (trop lent)

        for symbol in cfg.SYMBOLS:
            # Ignorer les symboles complètement désactivés
            sym_cfg = cfg.SYMBOL_LIMITS.get(symbol, {})
            if not sym_cfg.get("allow_buys", True) and not sym_cfg.get("allow_shorts", True):
                continue

            sym_trades = [t for t in recent_trades if t.get("symbol") == symbol]
            if len(sym_trades) < 15:
                continue

            sym_wr = sum(1 for t in sym_trades if t["profit"] > 0) / len(sym_trades)
            current_period = SYMBOL_MOMENTUM_PERIODS.get(symbol, 20)
            new_period = current_period

            if sym_wr < 0.45 and current_period > MIN_PERIOD + 2:
                # WR très mauvais → réduire pour plus de signaux
                new_period = max(MIN_PERIOD, current_period - 2)
                adjustments[symbol] = (current_period, new_period, "TROP_CONSERVATEUR")
            elif sym_wr < 0.55 and current_period > MIN_PERIOD + 4:
                # WR faible → légère réduction
                new_period = max(MIN_PERIOD + 2, current_period - 1)
                adjustments[symbol] = (current_period, new_period, "CONSERVATEUR")
            elif sym_wr > 0.70 and current_period < MAX_PERIOD - 2:
                # WR excellent → augmenter pour filtrer les faux signaux
                new_period = min(MAX_PERIOD, current_period + 1)
                adjustments[symbol] = (current_period, new_period, "AGGRESSIVE")

            if new_period != current_period:
                # Appliquer le changement de manière bornée et validée
                new_period = max(MIN_PERIOD, min(MAX_PERIOD, new_period))
                # FIX m1: Si la période a dérivé de plus de 4 unités de l'initial, reset
                initial = self._initial_mom_periods.get(symbol, 20)
                if abs(new_period - initial) > 4:
                    new_period = initial
                    logger.info(f"[PHASE 3] {symbol}: période reset à {initial} (dérive > 4 unités)")
                if new_period != current_period:
                    SYMBOL_MOMENTUM_PERIODS[symbol] = new_period
                    logger.info(
                        f"[PHASE 3] {symbol}: période {current_period}→{new_period} "
                        f"(WR={sym_wr:.1%}, raison: {adjustments[symbol][2]})"
                    )

        if adjustments:
            self._state["mom_period_adjustments"] = {k: v[:2] for k, v in adjustments.items()}
            self._save_state()


def main():
    Path("logs").mkdir(exist_ok=True)
    Path("runtime").mkdir(exist_ok=True)
    _clean_orphan_tmp_files()  # H-04: nettoie .tmp orphelins avant démarrage
    _acquire_lock()
    try:
        robot = FTMO_SIMPLE()
        robot.start()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
