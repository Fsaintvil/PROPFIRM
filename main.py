import contextlib
import json
import logging
import os
import sys
import threading
import time
import warnings
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

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
from engine_simple.state_manager import save_full_state

# ── Phase 7-16 Modules ──
from engine_simple.strategy_selector import StrategySelector
from engine_simple.news_filter import NewsFilter, is_news_blocked
from engine_simple.volume_profile import VolumeProfile, analyze as vp_analyze

# order_flow retiré — phases supprimées 25 Juin 2026
from engine_simple.mtf_confirm import MultiTimeframeConfirmer, confirm as mtf_confirm
from engine_simple.adaptive_params import AdaptiveParameters

# walk_forward_opt archivé dans retired/engine_simple/ (code mort, jamais intégré au flux trading)
from engine_simple.dashboard import Dashboard

# ── Nouveaux modules Juin 2026 ──
# vwap_analyzer + market_profile retirés — phases supprimées 25 Juin 2026

# ── P1: Signal Pipeline — filtrage multi-couches extrait de _scan_signals ──
from engine_simple.signal_pipeline import SignalPipeline

warnings.filterwarnings("ignore", message="X does not have valid feature names")

# ── Paths dynamiques (configurables via .env ou YAML) ────────────────
STATE_FILE = os.environ.get("ROBOT_STATE_FILE", "runtime/robot_state.json")
HEARTBEAT_FILE = os.environ.get("ROBOT_HEARTBEAT_FILE", "runtime/heartbeat.txt")
PID_FILE = os.environ.get("ROBOT_PID_FILE", "runtime/robot.pid")

# Named mutex Windows — plus fiable que le fichier PID (auto-libéré par l'OS)
_MUTEX_NAME = os.environ.get("ROBOT_MUTEX_NAME", "Global\\MT5_FTMO_MOM20x3")

# ── Symboles activement tradés — depuis .env, PAS cfg.SYMBOLS (qui a 27 symboles) ──
# 🔥 CRITIQUE: cfg.SYMBOLS contient 27 symboles (YAML).
# Seuls ceux dans .env:SYMBOLS doivent être tradés.
_env_syms = os.environ.get("SYMBOLS", "").strip()
ACTIVE_SYMBOLS: set[str] = set()
if _env_syms:
    ACTIVE_SYMBOLS = {s.strip() for s in _env_syms.split(",") if s.strip()}
if not ACTIVE_SYMBOLS:
    ACTIVE_SYMBOLS = {"XAUUSD", "BTCUSD", "US30.cash"}

# ── Catégories de symboles — SUPPRIMÉ 1er Juillet 2026 ──
# Les SYMBOL_CONFIDENCE_GATES et catégories CORE/TARGET_80/REACTIVATED
# ont été supprimés. Le filtrage est géré par :
#   - min_score=0.30 (config)
#   - Lot progressif WR-based (_get_wr_based_max_lot)
#   - Limites 3/2/1 par symbole-direction (signal_pipeline)

_mutex_handle = None


def _acquire_mutex():
    """Acquiert un named mutex Windows. Retourne True si acquis, False sinon.
    Le mutex est automatiquement libéré par l'OS si le processus crashe."""
    global _mutex_handle
    if _mutex_handle is not None:
        return True  # déjà acquis par ce processus — appel ré-entrant
    if os.name != "nt":
        return True  # Pas de mutex Windows sur Linux/Mac
    try:
        import ctypes

        # CreateMutexW retourne un handle existant si le mutex existe déjà
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if not handle:
            logger.warning("PID lock: CreateMutexW a échoué — fallback fichier")
            return False
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.kernel32.CloseHandle(handle)
            logger.critical("PID lock: mutex déjà détenu par une autre instance — abandon")
            return False
        _mutex_handle = handle
        logger.debug(f"PID lock: mutex Windows acquis")
        return True
    except Exception as e:
        logger.warning(f"PID lock: mutex Windows indisponible ({e}) — fallback fichier")
        return False


def _release_mutex():
    """Libère le named mutex Windows."""
    global _mutex_handle
    if _mutex_handle is not None:
        try:
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


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
        # H-04b: Nettoie aussi les *.json.tmp.* (anciens atomic write residues timestampés)
        for f in runtime_dir.glob("*.json.tmp.*"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")
        # H-04c: Nettoie les *.json.tmp (nouveau format atomic write, sans timestamp)
        for f in runtime_dir.glob("*.json.tmp"):
            try:
                f.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[CLEAN] Orphelin runtime/{f.name} ignoré: {e}")


def _acquire_lock():
    """PID lock — named mutex Windows (primaire) + fichier PID (fallback).
    Empêche les instances dupliquées même après crash (mutex auto-libéré par l'OS)."""
    pid = os.getpid()

    # 🔒 PRIORITÉ 1: Named mutex Windows (primaire, plus fiable)
    if _acquire_mutex():
        # Mutex acquis — écrire aussi le fichier PID pour compatibilité
        lock = Path(PID_FILE)
        try:
            lock.write_text(str(pid))
        except Exception:
            pass
        logger.info(f"PID lock: {pid} (mutex)")
        return

    # 🔒 PRIORITÉ 2: File-based lock (fallback Linux/Mac)
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
                last_error = ctypes.windll.kernel32.GetLastError()
                if last_error == 5:  # ERROR_ACCESS_DENIED
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
    logger.info(f"PID lock: {pid} (file)")


def _release_lock():
    """Libère le PID lock (mutex + fichier)."""
    _release_mutex()
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
        if cfg.MAX_POSITIONS > 100:
            errors.append(f"MAX_POSITIONS={cfg.MAX_POSITIONS} trop élevé (max 100 pour Mode MAX)")
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
        _health_port = int(os.environ.get("ROBOT_HEALTH_PORT", "9090"))
        self.health_server = HealthServer(port=_health_port, metrics=self.metrics, health_check=self._health_status)
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
            logger.info(
                "TELEGRAM NON CONFIGURE: les notifications de crash "
                "ne seront pas envoyees. Configure les tokens dans .env"
            )
        if not self.mt5.connect():
            self.audit.log_error("init", "Echec connexion MT5")
            sys.exit(1)
        self._state["connected"] = True
        logger.info("Connexion MT5 etablie (Broker mode)")
        # 🔧 FIX #3: Synchroniser l'horloge locale avec le serveur MT5
        # Les timestamps négatifs (-52min, -67min) dans le dashboard viennent
        # d'un décalage entre l'horloge système et l'horloge du serveur MT5.
        self._sync_mt5_clock()

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
        # ⚠️ calibration_path DOIT être différent de OnlineLearner.STATE_FILENAME ("runtime/ol_state.json")
        # pour éviter que _save_calibration() et OnlineLearner.save_state() s'écrasent mutuellement
        # (l'une écrit "online_history", l'autre écrit "history" — clés incompatibles).
        # Voir: adaptive_intelligence.py:OnlineLearner.STATE_FILENAME
        self.adaptive = AdaptiveEngine(self.mt5, calibration_path="runtime/calibration_state.json")

        # PHASE 2.2: MetaLearner intégré dans AdaptiveEngine
        # (instance self.adaptive.meta créée dans AdaptiveEngine.__init__)

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

        # Phase 10: Order Flow — RETIRÉ 25 Juin 2026 (phases mortes)

        # Phase 11: MTF Confirmation
        self.mtf_confirm = MultiTimeframeConfirmer()
        logger.info("[MTF_CONFIRM] Chargé — confirmation multi-TF")

        # Phase 12-13: Adaptive (per-symbol, lazy init)
        self._adaptive_params: dict[str, AdaptiveParameters] = {}
        # WFO retiré — walk_forward_opt.py archivé dans retired/ (code mort)

        # Phase 16: Dashboard
        self.dashboard = Dashboard()
        logger.info("[DASHBOARD] Chargé — monitoring temps réel")

        # Phase 17: VWAP Analyzer — RETIRÉ 25 Juin 2026 (phases mortes)
        # Phase 18: Market Profile — RETIRÉ 25 Juin 2026 (phases mortes)

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
                CIRCUIT_BREAKER_DD_PCT=cfg.CIRCUIT_BREAKER_DD_PCT,
            ),
        )
        if self._state.get("peak_equity"):
            self.ftmo.peak_equity = self._state["peak_equity"]
            self.ftmo.challenge.peak_equity = self._state["peak_equity"]  # Sync challenge tracker
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
            _now = datetime.utcnow()
            for k, v in self._state["cooldowns"].items():
                with contextlib.suppress(ValueError):
                    cd = datetime.fromisoformat(v)
                    # 🔧 FIX M5: Ne pas restaurer les cooldowns de >2h (périmés après restart)
                    if (_now - cd).total_seconds() < 7200:  # 2h
                        self.ftmo.cooldowns[k] = cd
            logger.info(f"[STATE] Restored {len(self.ftmo.cooldowns)} cooldowns")
        # M17: Restore _symbol_consecutive_losses
        # 🔧 FIX M6: Reset au restart pour repartir à zéro (les pertes consécutives
        # de la session précédente ne devraient pas impacter la nouvelle session)
        self.ftmo._symbol_consecutive_losses.clear()
        if self._state.get("symbol_consecutive_losses"):
            logger.info(
                f"[STATE] symbol_consecutive_losses reset (restart) — ancien: {self._state.get('symbol_consecutive_losses')}"
            )
        if self._state.get("challenge_status"):
            self.ftmo.challenge_status = self._state["challenge_status"]
        # P5: Restore global_cooldown_until (protection restart)
        if self._state.get("global_cooldown_until"):
            try:
                gcu = datetime.fromisoformat(self._state["global_cooldown_until"])
                if gcu > datetime.utcnow():
                    self.ftmo.global_cooldown_until = gcu
                    logger.info(f"[STATE] Restored global_cooldown_until: {gcu}")
                else:
                    # Cooldown expiré, reset consecutive_losses proprement
                    logger.info(
                        f"[STATE] global_cooldown_until expired ({gcu}), "
                        f"resetting consecutive_losses from {self.ftmo.consecutive_losses} to 0"
                    )
                    self.ftmo.consecutive_losses = 0
                    self.ftmo.challenge.consecutive_losses = 0
            except (ValueError, TypeError) as e:
                logger.warning(f"[STATE] Cannot restore global_cooldown_until: {e}")
        if self._state.get("consistency_violated"):
            self.ftmo.consistency_violated = True
            self.ftmo.challenge.consistency_violated = True  # sync source (ChallengeTracker)
        if self._state.get("daily_profit_reduced"):
            self.ftmo._daily_profit_reduced = True
        if self._state.get("trade_history"):
            # CRITICAL: set on challenge._trade_history directly, not ftmo._trade_history
            # because ftmo._trade_history is an alias that gets disconnected on reassignment
            th_raw = self._state["trade_history"]
            active_symbols = set(cfg.SYMBOLS)
            self.ftmo.challenge._trade_history = []
            skipped = 0
            for t in th_raw:
                sym = t.get("symbol", "")
                if sym not in active_symbols:
                    skipped += 1
                    continue
                try:
                    time_val = t.get("time", "")
                    if isinstance(time_val, (int, float)):
                        time_val = datetime.fromtimestamp(time_val)
                    elif isinstance(time_val, str):
                        time_val = datetime.fromisoformat(time_val)
                    self.ftmo.challenge._trade_history.append(
                        {
                            "symbol": sym,
                            "profit": t.get("profit", 0),
                            "time": time_val,
                            "historical": t.get("historical", False),
                        }
                    )
                except (ValueError, TypeError):
                    pass
            if skipped:
                logger.info(f"[STATE] Filtrés {skipped} trades de symboles inactifs à la restauration")
            # Re-establish the alias
            self.ftmo._trade_history = self.ftmo.challenge._trade_history
            logger.info(
                f"[STATE] Restored {len(self.ftmo.challenge._trade_history)} trade_history records (symboles actifs)"
            )
            # Also rebuild trading_days and daily_pnl_by_date from filtered history
            # to avoid contamination from skipped trades in the reconstruction below
        if self._state.get("daily_pnl_by_date"):
            self.ftmo.daily_pnl_by_date.clear()
            for k, v in self._state["daily_pnl_by_date"].items():
                with contextlib.suppress(ValueError):
                    self.ftmo.daily_pnl_by_date[datetime.strptime(k, "%Y-%m-%d").date()] = v
        if self._state.get("trading_days_list"):
            self.ftmo.trading_days.clear()
            for d in self._state["trading_days_list"]:
                with contextlib.suppress(ValueError):
                    self.ftmo.trading_days.add(datetime.strptime(d, "%Y-%m-%d").date())
        # 🔒 FIX v2: Reconstruire trading_days + daily_pnl_by_date depuis trade_history
        # trade_history est la source de vérité car elle persiste 500 trades (vs.
        # daily_pnl_by_date/trading_days_list qui ne couvrent que la session courante
        # et sont perdus au redémarrage). Cette reconstruction remplace les valeurs
        # chargées depuis daily_pnl_by_date/trading_days_list quand trade_history existe.
        # ⚠️ Utiliser clear()/update() au lieu de = pour préserver les alias
        #    (self.ftmo.trading_days = self.challenge.trading_days via alias dans ftmo_protector.py:59)
        if hasattr(self, "ftmo") and self.ftmo._trade_history:
            self.ftmo.trading_days.clear()
            self.ftmo.daily_pnl_by_date.clear()
            historical_count = 0
            age_skipped = 0
            now = datetime.utcnow()
            for t in self.ftmo._trade_history:
                if t.get("historical"):
                    historical_count += 1
                    continue
                try:
                    time_val = t.get("time")
                    if isinstance(time_val, datetime):
                        d = time_val.date()
                        # Skip trades > 48h old (imported from MT5 history without flag)
                        if (now - time_val).total_seconds() > 48 * 3600:
                            age_skipped += 1
                            continue
                    elif isinstance(time_val, str):
                        d = datetime.fromisoformat(time_val).date()
                    else:
                        continue
                    self.ftmo.trading_days.add(d)
                    self.ftmo.daily_pnl_by_date[d] = self.ftmo.daily_pnl_by_date.get(d, 0) + t.get("profit", 0)
                except (ValueError, TypeError, AttributeError):
                    pass
            logger.info(
                f"[STATE] Reconstruit {len(self.ftmo.trading_days)} jours trading, "
                f"{len(self.ftmo.daily_pnl_by_date)} daily_pnl depuis trade_history "
                f"(filtrés {historical_count} historiques + {age_skipped} âgés)"
            )
            # Recalculer la règle de consistance FTMO à partir des daily_pnl_by_date reconstruits
            self.ftmo._check_consistency()
            logger.info(
                f"[STATE] consistency_violated={self.ftmo.consistency_violated} "
                f"(après recalcul depuis {len(self.ftmo.daily_pnl_by_date)} jours)"
            )
        if self._state.get("daily_stats"):
            self.ftmo.daily_stats = self._state["daily_stats"]
        # 🔧 FIX #1: Restaurer _opened_today depuis l'état persistant
        # Évite le bypass de MAX_TRADES_PER_DAY au redémarrage.
        # Le compteur est partagé entre FTMOProtector et ChallengeTracker via alias.
        _ot = self._state.get("opened_today")
        if _ot is not None and isinstance(_ot, (int, float)):
            self.ftmo._opened_today = max(0, int(_ot))
            self.ftmo.challenge._opened_today = max(0, int(_ot))
            if int(_ot) > 0:
                logger.info(f"[STATE] _opened_today restauré: {int(_ot)}")
        else:
            # 🔧 FIX 7 Juillet 2026: Si _opened_today non trouvé/nul dans state, forcer 0
            # (cause: état corrompu après crash, valeurs fantômes persistées)
            self.ftmo._opened_today = 0
            self.ftmo.challenge._opened_today = 0
        # 🔧 FIX 7 Juillet 2026: Forcer opened_today=0 après restauration
        # pour éviter les valeurs fantômes persistées (91/75).
        # Cause identifiée: _opened_today peut être réhydraté depuis des trades
        # historiques importés lors de import_history() qui précède la boucle.
        # Solution: reset garanti à 0 avant la boucle trading.
        if hasattr(self, "ftmo") and getattr(self.ftmo, "_opened_today", 0) != 0:
            logger.warning(f"[STATE] _opened_today={self.ftmo._opened_today} avant reset forcé!")
            self.ftmo._opened_today = 0
            self.ftmo.challenge._opened_today = 0
        _dse = self._state.get("daily_start_equity")
        if _dse is not None and _dse > 0:
            self.ftmo.daily_start_equity = _dse
            if hasattr(self.ftmo, "challenge"):
                self.ftmo.challenge.daily_start_equity = _dse
            logger.debug(f"[STATE] daily_start_equity restauré: {_dse} (ftmo + challenge)")
        else:
            logger.debug(f"[STATE] daily_start_equity ignoré: {_dse} (<=0 ou None)")
        # 🔧 FIX H3: Forcer le recalage de daily_start_equity après restart
        # pour éviter qu'il reste bloqué à l'initial_balance (200000).
        # Le _reset_daily() est appelé dans la boucle trading, mais si on est
        # dans le même jour UTC, il ne se déclenche pas. On force ici.
        # ATTENTION: _reset_daily() copie challenge→ftmo, donc on modifie LES DEUX.
        import datetime as _dt

        _saved_day = self._state.get("daily_stats", {}).get("day")
        _today = _dt.datetime.utcnow().date()
        if self.mt5:
            _acct = self.mt5.get_account_info()
            if _acct:
                if str(_today) != str(_saved_day):
                    # Jour différent → _reset_daily() va normalement corriger
                    pass
                # 🔧 FIX 7 Juillet 2026: Toujours forcer le recalage, pas seulement
                # quand equity != daily_start_equity. Le challenge.daily_start_equity
                # peut être resté à initial_balance si la restauration d'état ne l'a
                # pas touché (line 613 ne set que ftmo, pas challenge).
                # Comparer avec le challenge ET ftmo pour couvrir les deux cas.
                _challenge_dse = getattr(self.ftmo, "challenge", None)
                _challenge_dse_val = _challenge_dse.daily_start_equity if _challenge_dse else None
                if _acct.equity != _challenge_dse_val:
                    # Même jour, restart dans la journée → on recale sur l'equity actuelle
                    _old_ftmo = self.ftmo.daily_start_equity
                    _old_challenge = _challenge_dse_val
                    _new_eq = _acct.equity
                    # Modifier les DEUX (protector + challenge) car _reset_daily()
                    # copie challenge→protector à chaque cycle
                    self.ftmo.daily_start_equity = _new_eq
                    if _challenge_dse:
                        _challenge_dse.daily_start_equity = _new_eq
                    logger.info(
                        f"[STATE] daily_start_equity recalculé: ftmo={_old_ftmo} challenge={_old_challenge}→{_new_eq}"
                    )

        class _Cache:
            def __init__(self, mt5_conn):
                self._mt5 = mt5_conn
                self._cache = None
                self._last_fetch = 0.0
                self._ttl = 150  # 150s entre refetch MT5 (limite FTMO 2000 req/jour)

            def get(self, force_refresh=False):
                import time

                now = time.time()
                if self._cache is None or force_refresh or (now - self._last_fetch) > self._ttl:
                    self._cache = self._mt5.get_positions()
                    self._last_fetch = now
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

        # P1: Signal Pipeline — filtrage multi-couches extrait de _scan_signals
        self.pipeline = SignalPipeline(
            mt5=self.mt5,
            ftmo=self.ftmo,
            adaptive=self.adaptive,
            news_filter=self.news_filter,
            strategy_selector=self.strategy_selector,
            volume_profile=self.volume_profile,
            mtf_confirm=self.mtf_confirm,
            risk_manager=self.risk_manager,
            config=cfg,
            symbol_limits=cfg.SYMBOL_LIMITS,
            symbol_timeframes=cfg.SYMBOL_TIMEFRAMES,
        )
        logger.info("[SIGNAL_PIPELINE] Chargé — phases de filtrage")

        # Modules refactorisés (strategy/regime) — monitoring parallèle
        self._regime_detector = RegimeDetector()
        self.pos_manager = PositionManager(
            mt5=self.mt5,  # type: ignore[arg-type]  # Broker wraps MT5Connector
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

        # MT5 Terminal restart watchdog
        self._last_mt5_restart_attempt = 0
        self._mt5_restart_count = 0

        # Log throttling: track cycle count of last log per category
        self._log_throttle = {"ol_thresh": 0, "degraded": {}, "limit": {}}

        # ── External watchdog thread (Fix 6 Juillet 2026) ──────────────
        # Le watchdog DANS la boucle de cycle (ligne 986) ne peut PAS détecter
        # les freezes MT5 car si get_rates() bloque, le code n'atteint jamais
        # la vérification. Ce thread externe tourne TOUT LE TEMPS (daemon)
        # et détecte les cycles bloqués en vérifiant _last_cycle_time.
        self._watchdog_thread = None
        self._watchdog_stall_count = 0

    def _start_external_watchdog(self) -> None:
        """Démarre un thread watchdog externe qui vérifie _last_cycle_time.

        🔧 FIX 6 Juillet 2026: Le watchdog DANS la boucle (ligne 986) ne peut
        pas détecter les appels MT5 bloqués. Ce thread tourne en parallèle et
        surveille le timestamp du dernier cycle terminé.

        Seuil: 300s (5 min) sans cycle → tente reconnect MT5
        Seuil critique: 600s (10 min) sans cycle → force restart process
        """
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return  # déjà démarré

        def _watchdog_loop():
            wd_logger = logging.getLogger("watchdog.ext")
            wd_logger.info("[WATCHDOG EXT] Thread démarré (check toutes les 30s)")
            check_interval = 30
            stall_threshold = int(os.environ.get("ROBOT_WATCHDOG_SECONDS", "180"))
            # On utilise un seuil plus large pour le thread externe (5 min)
            # car le seuil du cycle (180s) est déjà vérifié dans la boucle.
            ext_threshold = max(stall_threshold * 2, 300)  # au moins 5 min
            crash_threshold = ext_threshold * 2  # 10 min → force restart

            while self.running:
                time.sleep(check_interval)
                if not self.running:
                    break

                elapsed = time.time() - self._last_cycle_time

                # ⚠️ Si aucun signe de vie depuis plus que le seuil externe
                if elapsed > ext_threshold:
                    self._watchdog_stall_count += 1
                    wd_logger.error(
                        f"[WATCHDOG EXT] {elapsed:.0f}s depuis dernier cycle "
                        f"(stall #{self._watchdog_stall_count}, seuil={ext_threshold}s)"
                    )

                    # Tentative 1: déconnecter MT5 (peut débloquer l'appel bloqué)
                    try:
                        wd_logger.info("[WATCHDOG EXT] Tentative déconnexion MT5...")
                        self.mt5.disconnect()
                    except Exception as e:
                        wd_logger.warning(f"[WATCHDOG EXT] Erreur disconnect: {e}")

                    # ⛔ Si le blocage persiste après 3 checks (∼90s de plus)
                    if self._watchdog_stall_count >= 3:
                        wd_logger.critical(f"[WATCHDOG EXT] Stall persist {elapsed:.0f}s — force restart process")
                        self._watchdog_stall_count = 0

                        # Spawn new process, then exit
                        import subprocess as _sp

                        _sp.Popen([sys.executable, "main.py"], cwd=os.path.dirname(os.path.abspath(__file__)))
                        time.sleep(5)
                        _release_lock()
                        os._exit(1)  # Force exit même si thread MT5 bloqué
                else:
                    # Reset stall counter si le cycle reprend
                    if self._watchdog_stall_count > 0:
                        wd_logger.info(f"[WATCHDOG EXT] Cycle repris après {elapsed:.0f}s — reset stall counter")
                        self._watchdog_stall_count = 0

        self._watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True)
        self._watchdog_thread.start()
        logger.info("[WATCHDOG EXT] Thread watchdog externe démarré")

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

    def _sync_mt5_clock(self):
        """🔧 FIX #8: Synchronise l'horloge locale avec le serveur MT5.

        Les timestamps négatifs (-52min, -67min) dans le dashboard viennent
        d'un décalage entre l'horloge système et l'horloge du serveur MT5.
        Cette méthode détecte et logue le décalage sans modifier l'horloge système.
        Utilise le dernier tick EURUSD comme référence (get_server_time n'existe pas).
        """
        import time as _time

        try:
            # Utiliser le dernier tick d'un symbole connu (EURUSD)
            tick = self.mt5.get_tick("EURUSD")
            dt = None
            if tick is not None:
                tick_time = getattr(tick, "time", None)
                if tick_time is not None:
                    dt = datetime.fromtimestamp(float(tick_time))
            if dt is not None:
                local_now = datetime.utcnow()
                diff = (local_now - dt).total_seconds()
                if abs(diff) > 5:
                    logger.warning(
                        f"[CLOCK SYNC] Horloge système décalée de {diff:.0f}s "
                        f"(locale={local_now}, MT5={dt}) — timestamps peuvent être négatifs"
                    )
                    # Stocker le décalage pour corriger les calculs de durée
                    self._mt5_clock_offset = diff
                else:
                    logger.info(f"[CLOCK SYNC] Horloge synchronisée (diff={diff:.0f}s)")
                    self._mt5_clock_offset = 0.0
            else:
                logger.warning("[CLOCK SYNC] Impossible d'obtenir le temps serveur MT5 (tick EURUSD indisponible)")
                self._mt5_clock_offset = 0.0
        except Exception as e:
            logger.warning(f"[CLOCK SYNC] Échec synchronisation: {e}")
            self._mt5_clock_offset = 0.0

    def _health_check(self):
        """Vérifie la connexion MT5 avec tolérance aux glitchs passagers.
        Ne démarre le timer MT5 down qu'après 3 échecs consécutifs."""
        # Compteur d'échecs consécutifs
        if not hasattr(self, "_hc_failures"):
            self._hc_failures = 0

        if self.mt5.health_check():
            self._hc_failures = 0  # Reset compteur
            if not self._state.get("connected"):
                self._state["connected"] = True
                self._mt5_down_since = None  # Reset du timer MT5 down
                self._watchdog_failures = 0  # Reset watchdog après reconnection
                logger.info("[BROKER] Connexion retablie")
            return True

        # Échec — incrémenter le compteur
        self._hc_failures += 1

        # Tolérance: ne PAS déclencher le timer MT5 down avant 3 échecs consécutifs
        if self._hc_failures < 3:
            logger.debug(f"[BROKER] Health check échec #{self._hc_failures}/3 — glitch possible, on réessaie")
            return True  # On donne le bénéfice du doute

        # 3+ échecs consécutifs — MT5 vraiment down
        self._state["connected"] = False
        self._mt5_down_since = getattr(self, "_mt5_down_since", None)
        if self._mt5_down_since is None:
            self._mt5_down_since = time.time()
            logger.warning(
                f"[BROKER] MT5 indisponible (3 echecs consecutifs), skipping cycles "
                f"(down depuis {time.time() - self._mt5_down_since:.0f}s)"
            )
            # Tentative de reconnexion rapide dès le 3ème échec
            logger.info("[BROKER] Tentative de reconnexion rapide MT5...")
            try:
                if self.mt5.reconnect():
                    self._hc_failures = 0
                    self._mt5_down_since = None
                    self._state["connected"] = True
                    logger.info("[BROKER] Reconnexion rapide réussie")
                    return True
            except Exception as e:
                logger.warning(f"[BROKER] Reconnexion rapide échouée: {e}")

        # MT5 Terminal restart watchdog: si down > 300s, tenter restart du terminal
        mt5_down_for = time.time() - getattr(self, "_mt5_down_since", time.time())
        if mt5_down_for > 300 and hasattr(self, "_last_mt5_restart_attempt"):
            since_last_restart = time.time() - self._last_mt5_restart_attempt
            if since_last_restart > 600 and self._mt5_restart_count < 3:
                self._last_mt5_restart_attempt = time.time()
                self._mt5_restart_count += 1
                logger.warning(
                    f"[BROKER] MT5 down depuis {mt5_down_for:.0f}s — tentative #{self._mt5_restart_count} "
                    f"de redémarrage du terminal MT5"
                )
                try:
                    import subprocess

                    # Tuer le processus MT5 terminal
                    subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"], timeout=10)
                    time.sleep(3)
                    # Relancer MT5 via le raccourci
                    mt5_path = os.environ.get("MT5_TERMINAL_PATH", "")
                    if mt5_path:
                        subprocess.Popen([mt5_path], shell=True)
                        logger.info("[BROKER] Terminal MT5 relancé")
                    else:
                        logger.warning("[BROKER] MT5_TERMINAL_PATH non défini dans .env")
                except Exception as e:
                    logger.error(f"[BROKER] Échec redémarrage terminal MT5: {e}")
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
                trading_days_list=sorted([str(d) for d in self.ftmo.trading_days]) if hasattr(self, "ftmo") else [],
                challenge_status=self.ftmo.challenge_status if hasattr(self, "ftmo") else "ACTIVE",
                consistency_violated=self.ftmo.challenge.consistency_violated if hasattr(self, "ftmo") else False,
                daily_stats=self.ftmo.daily_stats if hasattr(self, "ftmo") else None,
                daily_start_equity=(
                    self.ftmo.daily_start_equity if hasattr(self, "ftmo") and self.ftmo.daily_start_equity > 0 else None
                ),
                # M16: Persist cooldowns per-symbol (survie aux redémarrages)
                cooldowns={k: v.isoformat() for k, v in self.ftmo.cooldowns.items()} if hasattr(self, "ftmo") else {},
                # P5: Persist global_cooldown_until (survie aux redémarrages)
                global_cooldown_until=self.ftmo.global_cooldown_until.isoformat()
                if hasattr(self, "ftmo") and self.ftmo.global_cooldown_until
                else None,
                # M17: Persist _symbol_consecutive_losses (survie aux redémarrages)
                symbol_consecutive_losses=dict(self.ftmo._symbol_consecutive_losses) if hasattr(self, "ftmo") else {},
                # 🔧 FIX #1: Persist _opened_today (survie aux redémarrages)
                # Évite le bypass de MAX_TRADES_PER_DAY au restart (compteur repartait à 0)
                opened_today=self.ftmo._opened_today if hasattr(self, "ftmo") else 0,
            )
            save_full_state(STATE_FILE, state)
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
                # 🔧 FIX 6 Juillet 2026: daily_stats["day"] est string après JSON,
                # doit être date pour _check_daily_limits et _reset_daily
                ds = data.get("daily_stats")
                if ds and isinstance(ds.get("day"), str):
                    try:
                        ds["day"] = datetime.strptime(ds["day"], "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        ds["day"] = datetime.utcnow().date()
                return data
        except Exception as e:
            logger.warning(f"State load failed: {e}")
        return {"restart_count": 0, "restart_timestamps": []}

    def start(self):
        self.running = True
        # Enregistrer le timestamp de ce démarrage dans l'état persistant
        now_ts = time.time()
        timestamps = self._state.get("restart_timestamps", [])
        timestamps.append(now_ts)
        timestamps = [t for t in timestamps if now_ts - t < 3600 * 24 * 7]  # garder 7 jours
        self._state["restart_timestamps"] = timestamps
        self._state["restart_count"] = self._state.get("restart_count", 0) + 1
        self._state["last_restart_utc"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
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
        # Fermer toutes les positions MT5 avant de déconnecter (bug C2 fix)
        try:
            logger.info("[STOP] Fermeture de toutes les positions MT5...")
            self.mt5.close_all_positions(magic=cfg.ROBOT_MAGIC)
        except Exception as e:
            logger.error(f"[STOP] Erreur fermeture positions: {e}")
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
        logger.info(f"[TRACE _opened_today] AVANT import_history: {self.ftmo._opened_today}")
        self.tracker.import_history()
        logger.info(f"[TRACE _opened_today] APRES import_history: {self.ftmo._opened_today}")
        # 🔧 FIX 6 Juillet 2026: Réconcilier _opened_today avec les positions ouvertes aujourd'hui
        # Évite le bypass de MAX_TRADES_PER_DAY après redémarrage :
        # les positions déjà ouvertes ne comptaient pas dans _opened_today,
        # permettant d'ouvrir 75 NOUVEAUX trades en plus des existants.
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today_positions = 0
        for p in self._pos_cache.get():
            if p.magic == cfg.ROBOT_MAGIC and getattr(p, "time", 0) >= today_start:
                today_positions += 1
        if today_positions > 0 and hasattr(self, "ftmo"):
            old = self.ftmo._opened_today
            self.ftmo._opened_today = max(self.ftmo._opened_today, today_positions)
            self.ftmo.challenge._opened_today = max(self.ftmo.challenge._opened_today, today_positions)
            if self.ftmo._opened_today != old:
                logger.info(
                    f"[DAILY LIMIT] {today_positions} positions ouvertes aujourd'hui — "
                    f"_opened_today: {old} → {self.ftmo._opened_today}"
                )
        # Reset watchdog timer après import_history (sinon le premier cycle
        # peut détecter un faux "cycle bloqué" si l'import prend du temps)
        self._last_cycle_time = time.time()

        # 🔧 FIX 6 Juillet 2026: Démarrer le thread watchdog EXTERNE
        # Ce thread tourne toutes les 30s et détecte les appels MT5 bloqués
        # que le watchdog interne (dans la boucle) ne peut pas voir.
        self._start_external_watchdog()

        while self.running:
            self.cycle_count += 1
            cycle_start = time.time()

            # Auto-stop flag DÉSACTIVÉ — mode production continue (sans arret)
            self._stop_trading = False

            # Watchdog: detect MT5 freeze / stuck cycles (augmenté 120s→180s)
            since_last = time.time() - self._last_cycle_time
            _wd_threshold = int(os.environ.get("ROBOT_WATCHDOG_SECONDS", "180"))
            if since_last > _wd_threshold:  # Augmenté de 120s → 180s (3 min)
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
                        # SPAWN d'abord, PUIS libérer le lock (évite la fenêtre de race condition)
                        import subprocess as _sp

                        _sp.Popen([sys.executable, "main.py"], cwd=os.path.dirname(os.path.abspath(__file__)))
                        time.sleep(5)  # 🔧 FIX_SUPREME_COUNCIL: 5s (était 1.5s) pour éviter race condition
                        _release_lock()
                        sys.exit(1)
                    self._save_state()
                    # SPAWN d'abord, PUIS libérer le lock
                    import subprocess as _sp

                    _sp.Popen([sys.executable, "main.py"], cwd=os.path.dirname(os.path.abspath(__file__)))
                    time.sleep(5)  # 🔧 FIX_SUPREME_COUNCIL: 5s (était 1.5s) pour éviter race condition
                    _release_lock()
                if not self.mt5.reconnect():
                    logger.error("Watchdog: echec reconnexion MT5")
                    break
                self._state["connected"] = True
                self._last_cycle_time = time.time()
                continue

            # Toujours écrire le heartbeat, même si MT5 est down (évite faux positif watchdog)
            self._heartbeat()

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

            dd_pct = 0  # initialisé avant le bloc pour éviter NameError si account=None
            if account:
                floating = account.equity - account.balance
                dd = max(0, self.ftmo.initial_balance - account.equity)
                dd_pct = dd / max(self.ftmo.initial_balance, 1) * 100
                pos_count = len(self._pos_cache.get())
                pos_info = f"{pos_count}pos"
                logger.info(
                    f"[Cycle {self.cycle_count}] Balance={account.balance:.0f} Eq={account.equity:.0f} "
                    f"Fl={floating:+.0f} DD={dd:.0f}({dd_pct:.1f}%) {pos_info} "
                    f"Pertes_cons={self.ftmo.consecutive_losses}"
                )
                # Action #10: Alerte si DD > 5%
                if dd_pct > 5.0 and hasattr(self, "notifier"):
                    self.notifier.send(f"⚠️ ALERTE DD {dd_pct:.1f}% — Eq=${account.equity:.0f} Positions={pos_count}")
                # Métriques
                self.metrics.gauge("balance", account.balance)
                self.metrics.gauge("equity", account.equity)
                self.metrics.gauge("drawdown_pct", dd_pct)
                self.metrics.gauge("consecutive_losses", self.ftmo.consecutive_losses)
                self.metrics.gauge("open_positions", len(self._pos_cache.get()))

                # Per-symbol DD tracking pour PortfolioController
                if self.portfolio_controller:
                    try:
                        live_positions = self._pos_cache.get()
                        sym_pnl: dict[str, float] = {}
                        for p in live_positions:
                            sym = getattr(p, "symbol", "?")
                            sym_pnl[sym] = sym_pnl.get(sym, 0.0) + getattr(p, "profit", 0.0)
                        for sym, pnl in sym_pnl.items():
                            # DD par symbole = perte flottante / balance
                            sym_dd = max(0, -pnl) / max(account.balance, 1)
                            self.portfolio_controller.update_symbol_dd(sym, sym_dd)
                    except Exception as e:
                        logger.debug(f"  [PORTFOLIO_DD] per-symbol DD failed: {e}")

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

            # Nettoyage auto des logs auxiliaires toutes les ~240 cycles (1h à 15s/cycle)
            if not hasattr(self, "_last_log_cleanup_cycle"):
                self._last_log_cleanup_cycle = 0
            if self.cycle_count - self._last_log_cleanup_cycle >= 240:
                self._last_log_cleanup_cycle = self.cycle_count
                try:
                    self._cleanup_old_logs(max_age_days=14)
                except Exception as e:
                    logger.warning(f"[LOG_CLEANUP] Échec: {e}")

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
            if self.cycle_count % 4 == 0:
                # ❤️ Heartbeat toutes les 60s — permet de détecter les cycles figés
                pos_count = len(self._pos_cache.get()) if hasattr(self, "_pos_cache") else 0
                eq = account.equity if account is not None else 0
                bal = account.balance if account is not None else 0
                pnl_val = (eq - bal) if eq and bal else 0
                logger.info(
                    f"[HEARTBEAT] Cycle {self.cycle_count} | {pos_count} pos | "
                    f"Eq=${eq:.0f} Bal=${bal:.0f} PnL=${pnl_val:.0f} | "
                    f"6H mem check au cycle {(self.cycle_count // 900 + 1) * 900}"
                )
            if self.cycle_count % 60 == 0:
                # Memory monitoring — alerte si > 1.5 GB
                if HAS_PSUTIL:
                    try:
                        import psutil as _psutil

                        proc = _psutil.Process()
                        mem_mb = proc.memory_info().rss / 1_048_576
                        if mem_mb > 1500:
                            logger.warning(f"[MEM] Mémoire critique: {mem_mb:.0f} MB > 1500 MB")
                        elif mem_mb > 1000:
                            logger.warning(f"[MEM] Mémoire élevée: {mem_mb:.0f} MB > 1000 MB")
                        else:
                            logger.debug(f"[MEM] {mem_mb:.0f} MB")
                    except Exception:
                        pass
                # Vérification mutex Windows (tous les 600 cycles ~2.5h)
                if self.cycle_count % 600 == 0 and os.name == "nt":
                    try:
                        import ctypes

                        h = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
                        if h:
                            err = ctypes.windll.kernel32.GetLastError()
                            ctypes.windll.kernel32.CloseHandle(h)
                            if err == 183:  # ERROR_ALREADY_EXISTS
                                logger.debug(f"[MUTEX] OK — détenu par PID {os.getpid()}")
                            else:
                                logger.error(f"[MUTEX] INATTENDU — err={err}, mutex non détenu par nous?")
                        else:
                            logger.error("[MUTEX] CreateMutexW a échoué — mutex perdu?")
                    except Exception as e:
                        logger.debug(f"[MUTEX] Vérification impossible: {e}")
                # Calibration persistante + DL si disponible (auto-gardé interne)
                self.adaptive.train_dl_if_ready()
                self.adaptive.save_calibration()
                perf = self.tracker.performance_summary()
                if perf:
                    logger.info(f"  [PERF] {json.dumps(perf)}")
                if hasattr(cfg, "reload_config") and cfg.reload_config():
                    logger.info("[CONFIG] Configuration reloaded a chaud")
                # Toujours rafraîchir les symbol_limits (même sans hot-reload)
                # Nécessaire car le hot-reload YAML peut ne pas détecter les changements de mtime
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
                        "current_dd": dd_pct / 100 if dd_pct is not None else 0,
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
                                "type": 0 if pos.type == 0 else 1,  # MT5: 0=BUY, 1=SELL
                                "price_open": pos.price_open,
                                "price_current": pos.price_current,
                                "volume": pos.volume,
                                "profit": pos.profit,
                                "time": pos.time if hasattr(pos, "time") else time.time(),
                            }
                        )

                    symbol_metrics = self.tracker.performance_summary() if hasattr(self, "tracker") else None
                    report = self.dashboard.generate_report(robot_state, positions_data, metrics=symbol_metrics)
                    if self.cycle_count % 100 == 0:  # Print full report every 100 cycles
                        self.dashboard.print_report(report)
                    self.dashboard.save_report(report)
                except Exception as e:
                    logger.debug(f"[DASHBOARD] Report failed: {e}")

            self._last_cycle_time = time.time()
            self._watchdog_failures = 0

            # Persistance périodique (tous les 20 cycles = ~5min)
            # Évite la perte de trade_history/daily_pnl_by_date en cas de crash
            if self.cycle_count % 20 == 0:
                self._save_state()

            # 🧹 GC périodique : tous les 500 cycles (~2h à 15s/cycle)
            # Évite la fragmentation mémoire Python/numpy de s'accumuler au fil du temps.
            if self.cycle_count % 500 == 0 and self.cycle_count > 0:
                import gc

                collected = gc.collect()
                logger.debug(f"[MEM] GC collecte: {collected} objets libérés (cycle {self.cycle_count})")

            elapsed = time.time() - cycle_start
            _min_sleep = int(os.environ.get("ROBOT_MIN_CYCLE_SLEEP", "5"))
            sleep_time = max(_min_sleep, cfg.CYCLE_SECONDS - elapsed)
            time.sleep(sleep_time)

    def _vigilance_scan(self):
        """Run DL/regime pipeline for ALL symbols every cycle."""
        self.pos_manager.vigilance_scan()

    def _get_rates_for_vigilance(self, symbol):
        return self.pos_manager._get_rates_for_vigilance(symbol)

    def _manage_positions(self):
        self.pos_manager.manage_positions()

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

        # P1: Déléguer le filtrage multi-couches au SignalPipeline
        candidates = []
        degraded_symbols = self._state.get("degraded_symbols", {})
        for symbol in ACTIVE_SYMBOLS & set(cfg.SYMBOLS):
            try:
                result = self.pipeline.process(
                    symbol=symbol,
                    cycle_count=self.cycle_count,
                    degraded_symbols=degraded_symbols,
                    sym_dir_counts=sym_dir_counts,
                    sym_total_counts=sym_total_counts,
                    config_limits=MAX_POS_PER_SYMBOL,
                    last_signals=self._last_signals,
                    log_throttle=self._log_throttle,
                )
            except Exception as e:
                logger.exception(f"[PIPELINE] {symbol}: erreur dans le pipeline: {e}")
                continue
            if result is None:
                continue
            signal = result.signal
            score = result.score
            # Stocker une COPIE pour éviter mutation cumulative du risk_mult
            self._last_signals[symbol] = {"signal": dict(signal), "score": score, "cycle": self.cycle_count}
            # PortfolioController — vérifier corrélation et exposition (avec positions RÉELLES)
            if self.portfolio_controller:
                try:
                    live_now = self._pos_cache.get()
                    high_conf = signal.get("high_confidence", False)
                    can_open, reason = self.portfolio_controller.can_open_position(
                        symbol, signal["action"], live_now, high_confidence=high_conf
                    )
                    if not can_open:
                        logger.debug(f"  [PORTFOLIO] {symbol}: {reason}")
                        continue
                except Exception as e:
                    logger.warning(f"  [PORTFOLIO] {symbol}: erreur ({e}) — bypass")
            candidates.append((score, symbol, signal, positions))

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
            # 🔥 HIGH CONFIDENCE BYPASS: pas de limite de positions
            if not signal.get("high_confidence", False):
                if live_total >= cfg.MAX_POSITIONS:
                    logger.info(f"  [LIMIT] Max positions ({cfg.MAX_POSITIONS}) atteint ({live_total} en cours)")
                    break
            can_trade, reason = self.ftmo.can_trade(symbol, signal, live_positions)
            if not can_trade:
                logger.debug(f"  [FTMO FINAL] {symbol}: {reason}")
                continue

            # P7: Anticipation Engine SUPPRIMÉ (DL désactivé, code mort)
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
                # 🔒 FIX M11: Cap final du risk_mult par symbole (27 symboles — 1er Juillet 2026)
                _FINAL_CAP = {
                    "XAUUSD": 1.50,
                    "BTCUSD": 1.25,
                    "US30.cash": 1.30,
                    "ETHUSD": 1.15,
                    "US100.cash": 1.20,
                    "US500.cash": 1.15,
                    "XAGUSD": 1.10,
                    "EURUSD": 1.15,
                    "GBPUSD": 1.15,
                    "USDJPY": 1.15,
                    "USDCAD": 1.15,
                    "AUDUSD": 1.15,
                    "NZDUSD": 1.15,
                    "USDCHF": 1.15,
                    "EURJPY": 1.10,
                    "GBPJPY": 1.10,
                    "EURGBP": 1.10,
                    "AUDJPY": 1.10,
                    "USOIL.cash": 1.10,
                    "UKOIL.cash": 1.10,
                    "NATGAS.cash": 1.05,
                    "SOLUSD": 1.10,
                    "LNKUSD": 1.10,
                    "BNBUSD": 1.10,
                    "JP225.cash": 1.15,
                    "GER40.cash": 1.15,
                    "UK100.cash": 1.15,
                }
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
                # 🆕 Phase 14b: Sauvegarder les features + prédictions pour retraining futur
                # Appelé IMMÉDIATEMENT après exécution pour capturer l'état avant que
                # track_new() ne crée son propre meta (add_meta pré-remplit _position_meta)
                try:
                    ticket = getattr(result, "order", 0)
                    if ticket:
                        meta_data = {
                            "_features": signal.get("_features", {}),
                            "predictions": signal.get("_model_predictions", {}),
                            "feature_adj": signal.get("feature_adj", 1.0),
                            "feature_reasons": signal.get("feature_reasons", {}),
                        }
                        self.tracker.add_meta(ticket, meta_data)
                except Exception as _e:
                    logger.debug(f"[LGB META] Sauvegarde features ouverture échouée: {_e}")
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
        # Diagnostic quand aucun trade n'est exécuté (affiche les scores finaux des candidats)
        if executed == 0 and candidates:
            diag = "; ".join(f"{sym}: score={sc:.2f}" for sc, sym, sig, _ in candidates)
            logger.info(f"  [DIAG] Signaux filtrés — {diag}")

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
        # Throttle: logs WR CHECK / PHASE 3 à 1x par minute max (toutes les 4 cycles)
        if not hasattr(self, "_last_wr_check_cycle"):
            self._last_wr_check_cycle = 0
        if self.cycle_count - self._last_wr_check_cycle < 4:
            return
        self._last_wr_check_cycle = self.cycle_count
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

        # PHASE 2.1: Check par symbole → degraded (lot minimum) si WR < 35% sur 20 trades
        degraded_symbols = self._state.get("degraded_symbols", {})
        for symbol in ACTIVE_SYMBOLS & set(cfg.SYMBOLS):
            sym_trades = [t for t in recent_trades if t.get("symbol") == symbol]
            if len(sym_trades) >= 20:
                sym_wr = sum(1 for t in sym_trades if t["profit"] > 0) / len(sym_trades)
                sym_pf = self._calc_pf(sym_trades)
                # PHASE 3: Log détaillé par symbole
                # 🔧 FIX H4: Capper l'affichage du PF à 5.0 pour éviter les PF aberrants
                # (EURUSD PF=42.37, contamination données historiques)
                _display_pf = min(sym_pf, 5.0)
                logger.info(
                    f"  [PHASE 3] {symbol}: {len(sym_trades)} trades, WR={sym_wr:.1%}, PF={_display_pf:.2f}"
                    + (" (capé)" if sym_pf > 5.0 else "")
                )
                # Utiliser le PF réel (non capé) pour les décisions
                # Si PF > 5.0, le gel période s'applique (lignes 1724+)

                if sym_wr < 0.35:
                    # Mode dégradé au lieu de disable complet : le symbole continue à trader
                    # mais avec lot minimum (0.05 ×5) pour éviter de rater un retournement
                    if symbol not in degraded_symbols:
                        degraded_symbols[symbol] = self.cycle_count
                        self._state["degraded_symbols"] = degraded_symbols
                        logger.warning(
                            f"[DEGRADED] {symbol}: WR={sym_wr:.1%} < 35% (cycle {self.cycle_count}) → lot minimum"
                        )
                        self.notifier.send(f"DEGRADED: {symbol} WR={sym_wr:.1%} < 35% → lot min")
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
            for symbol in ACTIVE_SYMBOLS & set(cfg.SYMBOLS):
                # 🏆 XAUUSD/EURUSD exemptés : WR individuel 73.9%/59.5% justifie un traitement spécial
                if symbol in ("XAUUSD", "EURUSD"):
                    logger.info(f"  [WR CHECK] {symbol} exempté (WR individuel élevé)")
                    continue
                p = dict(self.adaptive.learner.get_params(symbol))
                p["thresh"] = max(1.5, p.get("thresh", 2.5) - 0.3)
                p["risk_mult"] = min(1.0, p.get("risk_mult", 1.0) * 0.8)
                # Persist the adjusted params
                self.adaptive.learner.adapted_params[symbol] = p
            logger.info("  [WR CHECK] Seuils abaisses: thresh-0.3, risk_mult*0.8 (sauf XAUUSD/EURUSD)")
        elif self._win_rate_checked and total > 200:
            recent = (
                self.ftmo._trade_history[-100:] if len(self.ftmo._trade_history) >= 100 else self.ftmo._trade_history
            )
            recent_wr = sum(1 for t in recent if t["profit"] > 0) / max(len(recent), 1)
            if recent_wr >= 0.60:
                logger.info(f"  [WR CHECK] Recent WR={recent_wr:.1%} >= 60% — restauration seuils")
                self._win_rate_checked = False
                for symbol in ACTIVE_SYMBOLS & set(cfg.SYMBOLS):
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

        # Anti-spam: tracker le dernier log pour chaque type de message (ne pas spammer chaque cycle)
        if not hasattr(self, "_last_spam_log"):
            self._last_spam_log = {}
        import time as _time

        def _should_log(tag, interval=60):
            now = _time.time()
            last = self._last_spam_log.get(tag, 0)
            if now - last >= interval:
                self._last_spam_log[tag] = now
                return True
            return False

        # Anti-oscillation: ne pas ajuster plus d'une fois tous les 100 trades
        if not hasattr(self, "_phase3_last_adjustment"):
            self._phase3_last_adjustment = 0
        trades_since_last = len(self.ftmo._trade_history) - self._phase3_last_adjustment
        if trades_since_last < 100:
            return  # Cooldown anti-oscillation

        from engine_simple.strategy import get_momentum_period, set_momentum_period, SYMBOL_CONFIG

        # FIX P8: Sauvegarder les périodes initiales depuis SYMBOL_CONFIG
        if not hasattr(self, "_initial_mom_periods"):
            self._initial_mom_periods = {sym: cfg.get("momentum_period", 20) for sym, cfg in SYMBOL_CONFIG.items()}

        recent_trades = self.ftmo._trade_history[-100:]
        adjustments = {}

        # Bornes absolues de sécurité — serrées pour éviter les extrêmes
        MIN_PERIOD = 12  # pas en dessous de 12 (trop de bruit)
        MAX_PERIOD = 28  # pas au-dessus de 28 (trop lent)

        for symbol in ACTIVE_SYMBOLS & set(cfg.SYMBOLS):
            # Ignorer les symboles complètement désactivés
            sym_cfg = cfg.SYMBOL_LIMITS.get(symbol, {})
            if not sym_cfg.get("allow_buys", True) and not sym_cfg.get("allow_shorts", True):
                continue

            sym_trades = [t for t in recent_trades if t.get("symbol") == symbol]
            if len(sym_trades) < 15:
                continue

            sym_wr = sum(1 for t in sym_trades if t["profit"] > 0) / len(sym_trades)

            # 🔧 24 Juin 2026: Geler la période si WR < 35% (mode dégradé, seuil abaissé)
            if sym_wr < 0.35:
                if _should_log(f"wr_low_{symbol}"):
                    logger.debug(
                        f"[PHASE 3] {symbol}: WR={sym_wr:.1%} < 35% → gel période (mode dégradé, pas d'ajustement)"
                    )
                continue

            # 🔧 19 Juin 2026: PF > 5.0 = données contaminées (impossible en live)
            # Ne pas ajuster la période sur des données non fiables
            sym_profits = [t["profit"] for t in sym_trades if t.get("profit") is not None]
            if sym_profits:
                total_gain = sum(p for p in sym_profits if p > 0)
                total_loss = abs(sum(p for p in sym_profits if p < 0))
                sym_pf = total_gain / total_loss if total_loss > 0 else float("inf")
                if sym_pf > 5.0:
                    if _should_log(f"pf_contam_{symbol}"):
                        logger.debug(f"[PHASE 3] {symbol}: PF={sym_pf:.1f} > 5.0 (contaminé) → gel période")
                    continue

            current_period = get_momentum_period(symbol) or SYMBOL_CONFIG.get(symbol, {}).get("momentum_period", 20)
            new_period = current_period

            # Hystérésis : tracker la zone précédente par symbole
            # pour éviter l'oscillation quand WR=45% est pile sur le seuil
            if not hasattr(self, "_phase3_zone"):
                self._phase3_zone = {}
            prev_zone = self._phase3_zone.get(symbol, "OK")

            if prev_zone == "TROP_CONSERVATEUR":
                # Nécessite WR >= 0.47 pour sortir de TROP_CONSERVATEUR
                if sym_wr >= 0.47:
                    self._phase3_zone[symbol] = "OK"
                    # Laisser new_period = current_period (pas de changement)
                else:
                    self._phase3_zone[symbol] = "TROP_CONSERVATEUR"
                    if current_period > MIN_PERIOD + 2:
                        new_period = max(MIN_PERIOD, current_period - 2)
                        adjustments[symbol] = (current_period, new_period, "TROP_CONSERVATEUR", sym_wr)
            elif prev_zone == "CONSERVATEUR":
                # Nécessite WR >= 0.57 pour sortir de CONSERVATEUR
                if sym_wr >= 0.57:
                    self._phase3_zone[symbol] = "OK"
                else:
                    self._phase3_zone[symbol] = "CONSERVATEUR"
                    if current_period > MIN_PERIOD + 4 and sym_wr < 0.55:
                        new_period = max(MIN_PERIOD + 2, current_period - 1)
                        adjustments[symbol] = (current_period, new_period, "CONSERVATEUR", sym_wr)
            elif prev_zone == "AGGRESSIVE":
                # Nécessite WR <= 0.68 pour sortir de AGGRESSIVE
                if sym_wr <= 0.68:
                    self._phase3_zone[symbol] = "OK"
                else:
                    self._phase3_zone[symbol] = "AGGRESSIVE"
                    if current_period < MAX_PERIOD - 2:
                        new_period = min(MAX_PERIOD, current_period + 1)
                        adjustments[symbol] = (current_period, new_period, "AGGRESSIVE", sym_wr)
            else:
                # Zone OK : entrée dans une zone ajustée avec seuils stricts
                if sym_wr < 0.43 and current_period > MIN_PERIOD + 2:
                    # WR très mauvais → réduire (entrée: < 0.43)
                    new_period = max(MIN_PERIOD, current_period - 2)
                    adjustments[symbol] = (current_period, new_period, "TROP_CONSERVATEUR", sym_wr)
                    self._phase3_zone[symbol] = "TROP_CONSERVATEUR"
                elif sym_wr < 0.53 and current_period > MIN_PERIOD + 4:
                    # WR faible → légère réduction (entrée: < 0.53)
                    new_period = max(MIN_PERIOD + 2, current_period - 1)
                    adjustments[symbol] = (current_period, new_period, "CONSERVATEUR", sym_wr)
                    self._phase3_zone[symbol] = "CONSERVATEUR"
                elif sym_wr > 0.72 and current_period < MAX_PERIOD - 2:
                    # WR excellent → augmenter (entrée: > 0.72)
                    new_period = min(MAX_PERIOD, current_period + 1)
                    adjustments[symbol] = (current_period, new_period, "AGGRESSIVE", sym_wr)
                    self._phase3_zone[symbol] = "AGGRESSIVE"

            if new_period != current_period:
                # Appliquer le changement de manière bornée et validée
                new_period = max(MIN_PERIOD, min(MAX_PERIOD, new_period))
                # FIX m1: Si la période a dérivé de plus de 4 unités de l'initial, reset
                initial = self._initial_mom_periods.get(symbol, 20)
                if abs(new_period - initial) > 4:
                    new_period = initial
                    logger.info(f"[PHASE 3] {symbol}: période reset à {initial} (dérive > 4 unités)")
                if new_period != current_period:
                    set_momentum_period(symbol, new_period)
                    logger.info(
                        f"[PHASE 3] {symbol}: période {current_period}→{new_period} "
                        f"(WR={sym_wr:.1%}, raison: {adjustments[symbol][2]})"
                    )

        if adjustments:
            now_utc = datetime.now(timezone.utc).isoformat()
            details = {}
            for sym, (old_p, new_p, reason, wr_val) in adjustments.items():
                details[sym] = {
                    "old_period": old_p,
                    "new_period": new_p,
                    "reason": reason,
                    "timestamp": now_utc,
                    "wr": wr_val,
                }
            self._state["mom_period_adjustments"] = details
            self._state["mom_period_last_adjustment_utc"] = now_utc
            self._save_state()

    # ── Phase 14c: LightGBM retraining — SUPPRIMÉ (module désactivé) ────────

    # ── Phase 14d: Nettoyage automatique des logs auxiliaires ───────────────
    def _cleanup_old_logs(self, max_age_days=14):
        """Supprime les fichiers de log auxiliaires plus vieux que max_age_days.
        Le fichier principal simple_robot.log est géré par RotatingFileHandler.
        """
        import shutil
        import time as _time

        now = _time.time()
        max_age_sec = max_age_days * 86400
        log_dir = Path("logs")
        if not log_dir.exists():
            return

        # Fichiers protégés (gérés par RotatingFileHandler)
        protected = {
            "simple_robot.log",
            "simple_robot.log.1",
            "simple_robot.log.2",
            "simple_robot.log.3",
            "simple_robot.log.4",
            "simple_robot.log.5",
            "simple_robot.log.6",
            "simple_robot.log.7",
            "simple_robot.log.old",
        }

        removed = 0
        for f in log_dir.iterdir():
            if not f.is_file():
                continue
            if f.name in protected:
                continue
            # Ne toucher qu'aux fichiers .log
            if not f.name.endswith(".log"):
                continue
            try:
                mtime = f.stat().st_mtime
                age = now - mtime
                if age > max_age_sec:
                    f.unlink(missing_ok=True)
                    removed += 1
                    logger.debug(f"[LOG_CLEANUP] Supprimé: {f.name} (âge: {age / 86400:.1f}j)")
            except (OSError, PermissionError):
                pass

        if removed:
            logger.info(f"[LOG_CLEANUP] {removed} fichier(s) de log supprimé(s) (>={max_age_days} jours)")
        else:
            logger.debug(f"[LOG_CLEANUP] Aucun fichier à nettoyer (âge max: {max_age_days}j)")


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
