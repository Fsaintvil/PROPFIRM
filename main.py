import contextlib
import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import pandas as _pd

import config_simple as cfg
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
from engine_simple.adaptive_intelligence import AdaptiveEngine
from engine_simple.audit_trail import AuditTrail
from engine_simple.broker import Broker
from engine_simple.feature_store import FeatureStore
from engine_simple.concept_drift import ConceptDriftDetector
from engine_simple.ftmo_protector import FTMOProtector
from engine_simple.indicators import adx as ind_adx
from engine_simple.indicators import atr as ind_atr
from engine_simple.monitoring import HealthServer, MetricsCollector
from engine_simple.mt5_connector import MT5Connector
from engine_simple.notifier import Notifier
from engine_simple.position_tracker import PositionTracker
from engine_simple.rate_cache import RateCache
from engine_simple.retraining_pipeline import RetrainingPipeline
from engine_simple.risk_manager import RiskManager
# SignalGenerator désactivé (Juin 2026) — MOM20x3 pur via strategy.py
# from engine_simple.signals import SignalGenerator  # code mort conservé
from engine_simple.trade_executor import TradeExecutor
from engine_simple.trade_journal import TradeJournal
from engine_simple.anticipation import AnticipationEngine
from engine_simple.position_manager import PositionManager
from engine_simple.regime import RegimeDetector
from engine_simple.strategy import MOM20x3
from engine_simple.shield import FTMOAccount
from engine_simple.indicators import ema
from engine_simple.performance_monitor import update_challenge, get_monitor

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
            ['powershell', '-Command',
             'Get-CimInstance Win32_Process | Where-Object { ($_.Name -like "python*") '
             '-and $_.CommandLine -like "*main.py*" } | Select-Object -ExpandProperty ProcessId'],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line and line.isdigit():
                existing_pid = int(line)
                if existing_pid != pid:
                    logger.critical(f"PID lock: instance dupliquée détectée (PID {existing_pid}) via scan processus — abandon")
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
            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x1000, False, existing)
            if handle:
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                if exit_code.value == 259:  # STILL_ACTIVE
                    logger.critical(f"PID lock: instance deja active (PID {existing}) — abandon")
                    sys.exit(1)
                logger.warning(f"PID lock: zombie PID {existing} libere")
            else:
                # OpenProcess a échoué (NULL handle) — deux possibilités :
                # 1. Le processus n'existe plus (zombie) → safe to overwrite
                # 2. PROCESS_QUERY_INFORMATION refusé (processus d'un autre user/session)
                #    → CONSERVATEUR : considérer comme actif pour éviter les doublons
                last_error = ctypes.windll.kernel32.GetLastError()
                if last_error == 5:  # ERROR_ACCESS_DENIED
                    logger.critical(f"PID lock: accès refusé au PID {existing} — "
                                    f"considéré comme actif (GetLastError=5)")
                    sys.exit(1)
                logger.warning(f"PID lock: OpenProcess NULL (err={last_error}) — "
                              f"PID {existing} présumé zombie")
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
    "logs/simple_robot.log", maxBytes=10_485_760,
    backupCount=14, encoding="utf-8",
)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()]
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
        logger.info("MT5 FTMO SIMPLE - v2.1.0")
        logger.info("=" * 50)

        self._validate_config()

        self._state = self._load_state()
        self.audit = AuditTrail()
        self.audit.log_state_change("robot_start", None, f"v{cfg.__version__}" if hasattr(cfg, '__version__') else "?")
        self.metrics = MetricsCollector()
        self.metrics.gauge("initial_balance", 0)
        self.health_server = HealthServer(port=9090, metrics=self.metrics, health_check=self._health_status)
        try:
            self.health_server.start()
        except Exception as e:
            logger.warning(f"[MONITORING] Impossible de demarrer health server: {e}")
        raw_mt5 = MT5Connector(cfg.MT5_LOGIN, cfg.MT5_PASSWORD, cfg.MT5_SERVER)
        self.mt5 = Broker(raw_mt5, audit=self.audit)
        self.journal = TradeJournal()
        self.feature_store = FeatureStore()
        self.notifier = Notifier()
        if not self.notifier.is_enabled():
            logger.warning("TELEGRAM NON CONFIGURE: les notifications de crash "
                           "ne seront pas envoyees. Configure les tokens dans .env")
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

        self._last_batch_time = time.time()  # dernier batch de signaux (batch_interval_sec=300s)
        self._last_signals = {}  # symbol -> dict pour mémoire de signaux entre cycles
        # M15: Restaurer les signaux pré-crash depuis last_signals.json pour éviter replay
        self._restore_last_signals()
        self._stop_trading = Path("runtime/stop_for_day.flag").exists()  # Vérifie si flag stop présent au démarrage
        if self._stop_trading:
            logger.warning("[AUTO STOP] Flag stop_for_day.flag présent au démarrage — trading suspendu jusqu'à minuit UTC")
        # MOM20x3 pur — strategy.py est l'unique source de signaux
        self.signals = None  # interface conservée pour compatibilité
        self.adaptive = AdaptiveEngine(self.mt5, calibration_path="runtime/calibration_state.json")
        
        # PHASE 2.2: MetaLearner intégré dans AdaptiveEngine
        # (instance self.adaptive.meta créée dans AdaptiveEngine.__init__)
        self.ftmo = FTMOProtector(self.mt5, dict(
            MAX_POSITIONS=cfg.MAX_POSITIONS, MAX_TRADES_PER_DAY=cfg.MAX_TRADES_PER_DAY,
            MIN_SIGNAL_SCORE=cfg.MIN_SIGNAL_SCORE,
            LOT_SIZE=cfg.LOT_SIZE, RISK_PER_TRADE=cfg.RISK_PER_TRADE,
            COOLDOWN_MINUTES=cfg.COOLDOWN_MINUTES,
            MAX_DAILY_LOSS_PCT=cfg.MAX_DAILY_LOSS_PCT,
            INITIAL_BALANCE=challenge_init_bal,
            MAX_DD_PCT=cfg.MAX_DD_PCT, PROFIT_TARGET_PCT=cfg.PROFIT_TARGET_PCT,
            CONSISTENCY_MAX_PCT=cfg.CONSISTENCY_MAX_PCT,
            MIN_TRADING_DAYS=cfg.MIN_TRADING_DAYS, MAGIC=cfg.ROBOT_MAGIC,
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
        ))
        if self._state.get("peak_equity"):
            self.ftmo.peak_equity = self._state["peak_equity"]
        if "consecutive_losses" in self._state:
            self.ftmo.consecutive_losses = self._state["consecutive_losses"]
        if self._state.get("partial_closed"):
            self.ftmo.partial_closed = set(self._state["partial_closed"])
            logger.info(f"[STATE] Restored {len(self.ftmo.partial_closed)} partial_closed tickets")
        if self._state.get("partial_closed"):
            self.ftmo.partial_closed = set(self._state["partial_closed"])
        if self._state.get("trailing_peaks"):
            self.ftmo.trailing_peaks.update(self._state["trailing_peaks"])
        if self._state.get("position_regime"):
            self.ftmo.position_regime.update(self._state["position_regime"])
        if self._state.get("peak_profit"):
            self.ftmo.peak_profit.update(self._state["peak_profit"])
        if self._state.get("challenge_status"):
            self.ftmo.challenge_status = self._state["challenge_status"]
        if self._state.get("consistency_violated"):
            self.ftmo.consistency_violated = True
        if self._state.get("daily_profit_reduced"):
            self.ftmo._daily_profit_reduced = True
        if self._state.get("trade_history"):
            self.ftmo._trade_history = self._state["trade_history"]
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
        self.tracker = PositionTracker(self.ftmo, self.journal, self.adaptive, self._pos_cache,
                                        mt5=self.mt5, audit=self.audit)
        self.executor = TradeExecutor(self.mt5, self.ftmo, self.journal, self.tracker,
                                       self.signals, self.adaptive, audit=self.audit)
        self.risk_manager = RiskManager(self.ftmo, audit=self.audit)

        # Modules refactorisés (shield/strategy/regime) — monitoring parallèle
        self._regime_detector = RegimeDetector()
        self._shield_account = FTMOAccount(
            initial_balance=challenge_init_bal,
            peak_equity=self.ftmo.peak_equity,
            current_balance=challenge_init_bal,
        )
        self.pos_manager = PositionManager(
            mt5=self.mt5, ftmo=self.ftmo,
            adaptive=self.adaptive, signal_gen=self.signals,
            regime_detector=self._regime_detector, pos_cache=self._pos_cache,
        )

        # Anticipation Engine — connaissance profonde du marché
        try:
            self.anticipation = AnticipationEngine()
            self.anticipation.initialize(retrain=False)
            logger.info("Anticipation Engine chargé avec succès")
        except Exception as e:
            self.anticipation = None
            logger.warning(f"Anticipation Engine non disponible: {e}")

        # ML Pipeline (optional, graceful fallback — skip si DL désactivé)
        if self.adaptive.dl.available:
            drift_cfg = getattr(cfg, 'CONCEPT_DRIFT', {})
            self.drift_detector = ConceptDriftDetector(
                window_size=drift_cfg.get("window_size", 100),
                psi_threshold_light=drift_cfg.get("psi_threshold_light", 0.10),
                psi_threshold_moderate=drift_cfg.get("psi_threshold_moderate", 0.20),
                psi_threshold_severe=drift_cfg.get("psi_threshold_severe", 0.25),
            )
            self.retraining_pipeline = RetrainingPipeline(
                self.journal, self.feature_store, self.adaptive.dl,
                config=drift_cfg,
            )
            self._last_retrain_count = 0
            self._last_retrain_time = 0
            logger.info("[ML] Pipeline ML initialisé (concept drift + retraining)")
        else:
            self.drift_detector = None
            self.retraining_pipeline = None
            self._last_retrain_count = 0
            self._last_retrain_time = 0
            logger.info("[ML] Pipeline ML désactivé — DL non disponible")

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
            info = self.mt5.get_account_info() if hasattr(self, 'mt5') else None
            if info:
                return {
                    "status": "ok",
                    "balance": info.balance,
                    "equity": info.equity,
                    "floating": round(info.equity - info.balance, 2),
                    "positions": len(self._pos_cache.get()) if hasattr(self, '_pos_cache') else 0,
                    "consecutive_losses": self.ftmo.consecutive_losses if hasattr(self, 'ftmo') else 0,
                    "challenge_status": self.ftmo.challenge_status if hasattr(self, 'ftmo') else "N/A",
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
            logger.warning(f"[BROKER] MT5 indisponible, skipping cycles (down depuis {time.time() - self._mt5_down_since:.0f}s)")
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
                    self._get_balance() if self.mt5.health_check() else self._state.get("challenge_initial_balance", 200000)
                ),
                restart_count=self._state.get("restart_count", 0),
                restart_timestamps=self._state.get("restart_timestamps", []),
                daily_profit_reduced=self.ftmo._daily_profit_reduced if hasattr(self, "ftmo") else False,
                trade_history=(
                    self.ftmo._trade_history[-500:]
                    if hasattr(self, "ftmo") and self.ftmo._trade_history
                    else []
                ),
                daily_pnl_by_date=(
                    {str(k): v for k, v in self.ftmo.daily_pnl_by_date.items()}
                    if hasattr(self, "ftmo")
                    else {}
                ),
                trading_days_list=[str(d) for d in self.ftmo.trading_days] if hasattr(self, "ftmo") else [],
                challenge_status=self.ftmo.challenge_status if hasattr(self, "ftmo") else "ACTIVE",
                consistency_violated=self.ftmo.consistency_violated if hasattr(self, "ftmo") else False,
                daily_stats=self.ftmo.daily_stats if hasattr(self, "ftmo") else None,
                daily_start_equity=(self.ftmo.daily_start_equity if hasattr(self, "ftmo") and self.ftmo.daily_start_equity > 0 else None),
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
        if hasattr(self, 'audit'):
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

            # 🔒 Auto-stop flag : vérification rapide du flag stop_for_day.flag
            # (créé par ai-manager.ps1 si daily loss > 1.5% ou perte > $1,500)
            # → bloque TOUS les nouveaux trades, continue la gestion des positions
            flag_exists = Path("runtime/stop_for_day.flag").exists()
            if flag_exists and not self._stop_trading:
                logger.warning("[AUTO STOP] Flag stop_for_day.flag présent → trading suspendu (positions existantes gérées)")
                self._stop_trading = True
            elif not flag_exists and self._stop_trading:
                # Flag nettoyé (minuit UTC passé) → réactivation
                logger.info("[AUTO STOP] Flag stop_for_day.flag supprimé → reprise du trading")
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
                logger.warning(f"[BROKER] MT5 down depuis {mt5_down_for:.0f}s — skip cycle, {600 - mt5_down_for:.0f}s avant arret")
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
            logger.debug(f"  [TIMING] check_closed: {time.time()-op_t:.2f}s")

            op_t = time.time()
            try:
                self.tracker.track_new()
            except Exception as e:
                logger.warning(f"tracker.track_new failed: {e}")
            logger.debug(f"  [TIMING] track_new: {time.time()-op_t:.2f}s")

            account = None
            op_t = time.time()
            try:
                account = self.mt5.get_account_info()
            except Exception as e:
                logger.warning(f"get_account_info failed: {e}")
            logger.debug(f"  [TIMING] get_account_info: {time.time()-op_t:.2f}s")

            if account:
                floating = account.equity - account.balance
                dd = max(0, self.ftmo.initial_balance - account.equity)
                dd_pct = dd / max(self.ftmo.initial_balance, 1) * 100
                pos_info = f"{len(self._pos_cache.get())}pos"
                logger.info(f"[Cycle {self.cycle_count}] Balance={account.balance:.0f} Eq={account.equity:.0f} "
                    f"Fl={floating:+.0f} DD={dd:.0f}({dd_pct:.1f}%) {pos_info} "
                    f"Pertes_cons={self.ftmo.consecutive_losses}")
                # Métriques
                self.metrics.gauge("balance", account.balance)
                self.metrics.gauge("equity", account.equity)
                self.metrics.gauge("drawdown_pct", dd_pct)
                self.metrics.gauge("consecutive_losses", self.ftmo.consecutive_losses)
                self.metrics.gauge("open_positions", len(self._pos_cache.get()))

            # Reset daily stats si changement de jour (avant toute opération)
            if hasattr(self, 'ftmo') and self.ftmo:
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
                logger.warning(f"_scan_signals failed: {e}")

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
                logger.warning(f"[BROKER] MT5 down depuis {mt5_down_for:.0f}s après cycle ops — "
                              f"skip, {600 - mt5_down_for:.0f}s avant arret")
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
                if hasattr(cfg, 'reload_config') and cfg.reload_config():
                    logger.info("[CONFIG] Configuration reloaded a chaud")
                    if hasattr(self, 'ftmo') and hasattr(self.ftmo, 'refresh_symbol_limits'):
                        self.ftmo.refresh_symbol_limits()
                # ML Pipeline: concept drift monitoring + retraining
                self._ml_pipeline_tick()

            if self.cycle_count - self.last_report_cycle >= 20:
                self._log_ftmo_report()
                self.last_report_cycle = self.cycle_count

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

    def _get_ict_signal(self, symbol):
        """Génère un signal ICT/SMC (FVG + Order Blocks + analyse sessions).
        Retourne None si pas de signal valide."""
        try:
            signal = self.signals.analyze(symbol)
            if signal is None or signal.get("action") not in ("BUY", "SELL"):
                return None
            return signal
        except Exception as e:
            logger.debug(f"  [MOM20x3] {symbol}: echec generation: {e}", exc_info=True)
            return None

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
                ol_params = self.adaptive.learner.get_params(symbol, base_thresh=3.0)
                ol_thresh = ol_params.get("thresh", 2.5)
                # OL thresh = 2.5 → conservateur (pas de changement)
                # OL thresh < 2.5 → plus agressif (abaisser le seuil du signal)
                if ol_thresh < 2.5:
                    ol_thresh_trending = ol_thresh
                    ol_thresh_ranging = max(1.5, ol_thresh - 0.5)
                    logger.info(f"  [OL-THRESH] {symbol}: OL thresh={ol_thresh} → "
                                f"custom trending={ol_thresh_trending}, ranging={ol_thresh_ranging}")
                ol_risk_mult = ol_params.get("risk_mult", 1.0)
            except Exception:
                pass

            # === Signal sur le timeframe principal du symbole ===
            rates_tf = self.mt5.get_rates(symbol, tf, count=100)
            if rates_tf is None or len(rates_tf) < 50:
                return None
            mom = MOM20x3(rates_tf, symbol)
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

            # === Enrichir ===
            tick = self.mt5.get_tick(symbol)
            entry = tick.ask if tick else 0
            signal = dict(raw)
            signal["symbol"] = symbol
            signal["timeframe"] = tf
            signal["details"] = f"MOM20x3_{tf}"
            signal["quality"] = min(1.0, (signal.get("confidence", 0.5) + 0.1) * h4_conf)
            signal["risk_mult"] = ol_risk_mult
            signal["entry_price"] = entry if raw["action"] == "BUY" else (tick.bid if tick else 0)
            signal["higher_tf_conf"] = round(h4_conf, 2)
            atr_price = signal.get("atr", 0)
            price = tick.bid if tick else 0
            signal["atr_pct"] = round(atr_price / price * 100, 4) if price > 0 else 0
            signal["rsi"] = 50  # placeholder
            return signal
        except Exception as e:
            logger.exception(f"  [MOM20x3] {symbol}: echec generation: {e}")
            return None

    def _scan_signals(self):
        # PHASE 0: Vérification du flag stop_for_day (créé par ai-manager.ps1)
        if self._stop_trading:
            logger.debug("[AUTO STOP] Flag stop_for_day actif → skip signaux (positions gérées séparément)")
            return

        # (Blocage horaire 12:00-13:59 supprimé — mode MAX, trade 24/5)
        
        # PHASE 0.5: Batch interval — n'exécute des signaux que toutes les 5 min
        # Le reste du cycle (position management, trailing, SL/TP) continue en 15s
        batch_elapsed = time.time() - self._last_batch_time
        if batch_elapsed < cfg.BATCH_INTERVAL_SEC:
            if self.cycle_count % 60 == 0:
                logger.debug(f"[BATCH] Prochain batch de signaux dans {cfg.BATCH_INTERVAL_SEC - batch_elapsed:.0f}s "
                             f"(toutes les {cfg.BATCH_INTERVAL_SEC}s)")
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
        for p in positions:
            key = (p.symbol, p.type)  # 0=BUY, 1=SELL
            sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
        for o in pending:
            key = (o.symbol, o.type)
            sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
        # Comptage global pour le log
        sym_counts = {}
        for (sym, _), cnt in sym_dir_counts.items():
            sym_counts[sym] = sym_counts.get(sym, 0) + cnt
        logger.debug(f"Positions: {len(positions)}, Pending: {len(pending)}, Par symbole: {sym_counts}")

        # Prune les signaux mémorisés vieux de > 20 cycles (~5 min)
        stale = [s for s in list(self._last_signals.keys())
                 if self.cycle_count - self._last_signals[s].get("cycle", 0) > 20]
        for s in stale:
            del self._last_signals[s]

        # Collect all valid signals across symbols first
        candidates = []
        degraded_symbols = self._state.get("degraded_symbols", {})
        for symbol in cfg.SYMBOLS:
            # Première passe SANS signal — on skip DANGER_HOURS ici car
            # la vraie vérification (avec bypass score≥0.80) se fait dans
            # la deuxième passe can_trade() ligne 961 où le signal est disponible.
            can_trade, reason = self.ftmo.can_trade(symbol, check_danger_hours=False)
            if not can_trade:
                logger.debug(f"  [FTMO] {symbol}: {reason}")
                continue
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

            # Fix #3: score MOM20x3 ≥ 0.80 → bypass total ADX (signal assez fort)
            if signal_score >= 0.80:
                logger.debug(f"  [ADX] {symbol}: bypass (score={signal_score:.2f} >= 0.80, ADX={signal_adx:.1f})")
            else:
                # Fix #2: ADX adaptatif — abaissé en RANGING/LOW_VOL
                # NOTE: on override le _regime du signal (structure H1 ICT) avec l'ADX réel
                # car en marché rangeant, la structure détecte parfois un faux TREND_UP/DOWN
                regime = "RANGING" if signal_adx < 25 else signal.get("_regime", "RANGING")
                adx_thresh = sym_cfg.get("adx_thresh", 20)
                if regime in ("RANGING", "LOW_VOL"):
                    adx_thresh = min(adx_thresh, 12)  # abaissé à 12 pour signaux ICT en range
                if signal_adx < adx_thresh:
                    logger.info(f"  [ADX] {symbol}: ADX={signal_adx:.1f} < {adx_thresh} (score={signal_score:.2f}, regime={signal.get('_regime','?')}), skip")
                    continue
            logger.debug(f"  [SIGNAL] {symbol}: score={signal['score']:.2f}, "
                f"conf={signal['confidence']:.2f}, action={signal['action']}, "
                f"strat={signal.get('details','?')}, "
                f"+DI={signal.get('plus_di','?'):>5} -DI={signal.get('minus_di','?'):>5} "
                f"slope={signal.get('adx_slope','?'):>5}")

            # Per-direction position limit: max 2 trades dans la même direction sur un symbole
            sig_action = signal.get("action")
            sig_dir = 0 if sig_action == "BUY" else 1 if sig_action == "SELL" else None
            if sig_dir is not None:
                dir_count = sym_dir_counts.get((symbol, sig_dir), 0)
                if dir_count >= cfg.MAX_POSITIONS_PER_SYMBOL:
                    logger.debug(f"  [LIMIT] {symbol}: max {cfg.MAX_POSITIONS_PER_SYMBOL} positions "
                                 f"en direction {sig_action} ({dir_count})")
                    continue

            # Feed features to concept drift detector
            drift_feats = {
                "adx": signal.get("adx", 0),
                "atr_pct": signal.get("atr_pct", 0),
                "score": signal.get("score", 0.5),
                "confidence": signal.get("confidence", 0.5),
                "rsi": signal.get("rsi", 50),
                "quality": signal.get("quality", 0.5),
            }
            if self.drift_detector is not None:
                self.drift_detector.add_sample(drift_feats)
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
                                        logger.info(f"  [ANTICIP] {symbol}: DL dit {ant_side} (conf={ant_conf:.2f}) "
                                                     f"vs signal {signal_side} → risque /2")
                                    else:
                                        logger.info(f"  [ANTICIP] {symbol}: DL dit {ant_side} mais déjà réduit "
                                                     f"(risk_mult={current_rm:.2f}) → skip double peine")
                                else:
                                    logger.debug(f"  [ANTICIP] {symbol}: DL en désaccord ({ant_side}, conf={ant_conf:.2f})")
                            elif ant_side == signal_side and ant_conf > 0.65:
                                signal["risk_mult"] = signal.get("risk_mult", 1.0) * 1.2
                                logger.info(f"  [ANTICIP] {symbol}: DL confirme {signal_side} (conf={ant_conf:.2f}) → risque +20%")
                except Exception as e:
                    logger.debug(f"  [ANTICIP] {symbol}: erreur anticipation: {e}")
            logger.info(f"  [TRADE] >>> {symbol} {signal['action']} "
                         f"(score={score:.2f}, strat={signal.get('details','?')})")
            if hasattr(self, 'audit'):
                self.audit.log_signal(symbol, signal['action'], score,
                                       signal.get('confidence', 0),
                                       signal.get('_regime', '?'),
                                       signal.get('details'))
            self.metrics.inc("trade_signals", {"symbol": symbol, "action": signal["action"]})
            # Kelly sizing: multiplie le risk_mult de l'Anticipation Engine
            # par le ratio Kelly. Le signal est FRAIS chaque cycle (pas de cumul).
            # Cap à 1.5 max pour éviter les positions explosives.
            symbol_perf = self.tracker.performance.get(symbol)
            if symbol_perf and hasattr(self, 'risk_manager'):
                rr = signal.get("rr", cfg.MIN_RR_RATIO * 1.5)
                kelly_risk = self.risk_manager.calculate_position_risk(symbol_perf, rr)
                kelly_factor = max(0.3, min(1.5, kelly_risk / cfg.RISK_PER_TRADE))  # borné [0.3, 1.5]
                signal["risk_mult"] = signal.get("risk_mult", 1.0) * kelly_factor
                logger.debug(f"    [KELLY] {symbol}: risk_mult={signal['risk_mult']:.3f} "
                             f"(kelly_factor={kelly_factor:.2f})")
            result = self.executor.execute(symbol, signal)
            # FIX #9: Ne compter et rafraîchir QUE si l'ordre a vraiment été placé
            if result is not None and getattr(result, 'retcode', None) == 10009:
                executed += 1
                # Enregistrer le trade ouvert pour MAX_TRADES_PER_DAY
                self.ftmo.register_open_trade(symbol)
                # Invalider le cache pour que le prochain candidat voie la nouvelle position
                self._pos_cache.invalidate()
                # Mettre à jour sym_dir_counts pour éviter un doublon dans le même cycle
                sig_type = 0 if signal.get("action") == "BUY" else 1
                key = (symbol, sig_type)
                sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
                logger.debug(f"  [EXEC] {symbol} {signal.get('action')} OK — "
                             f"positions {symbol}: {sym_dir_counts.get(key,0)}")
            elif result is not None:
                logger.warning(f"  [EXEC] {symbol} {signal.get('action')} échec "
                               f"(retcode={result.retcode}) — pas compté")
            # Si result is None, c'était un refus pré-exécution (rate limit, RR, etc.) — pas compté non plus

        # PHASE 0.5: Mettre à jour le timestamp du dernier batch
        # Le prochain batch sera dans BATCH_INTERVAL_SEC secondes
        self._last_batch_time = time.time()
        logger.info(f"[BATCH] Batch signaux terminé — {executed} trade(s), "
                    f"prochain batch dans {cfg.BATCH_INTERVAL_SEC}s")

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
            import time as _time
            age = _time.time() - sig_path.stat().st_mtime
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
            logger.info(f"[M15] Restauré {len(saved_signals)} signaux depuis last_signals.json "
                        f"(cycle {saved_cycle}, age={age:.0f}s)")
        except Exception as e:
            logger.debug(f"[M15] Restaure last_signals échouée: {e}")

    def _save_signal_debug(self, candidates):
        try:
            sigs = []
            for score, symbol, signal, _ in candidates[:10]:
                sigs.append({
                    "symbol": symbol, "action": signal.get("action"),
                    "score": round(score, 2),
                    "confidence": round(signal.get("confidence", 0), 2),
                    "adx": signal.get("adx", 0),
                    "details": signal.get("details", ""),
                })
            Path("runtime/last_signals.json").write_text(json.dumps(
                {"cycle": self.cycle_count, "signals": sigs}, indent=2))
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
                result = dict(cur=cur, ma20=ma20, ma20_dist=ma20_dist, atr_v=atr_v,
                              atr_pct=atr_pct, adx_v=adx_v, sp_pts=sp_pts)
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
        logger.info(f"  [VOL] {symbol}: {v['cur']:.5f} MA20={v['ma20']:.5f} ({v['ma20_dist']:+.2f}%) "
                   f"ADX={v['adx_v']:.1f} ATR%={v['atr_pct']:.3f}% Spread={v['sp_pts']:.0f}pts [{zone}]")


    def _ml_pipeline_tick(self):
        """Concept drift monitoring + retraining pipeline (tous les 60 cycles)."""
        # Early exit: DL désactivé → tout le pipeline ML est mort
        if not getattr(self.adaptive, 'dl', None) or not self.adaptive.dl.available:
            return
        trade_count = len(self.ftmo._trade_history)
        if trade_count < 10:
            return
        try:
            # Drift report
            if self.drift_detector.should_retrain():
                report = self.drift_detector.get_report()
                logger.info(f"  [DRIFT] aggregate={report['aggregate_drift']:.3f} "
                            f"category={report['drift_category']} "
                            f"retrain={report['should_retrain']}")
                if report.get("per_feature_psi"):
                    top_drift = sorted(report["per_feature_psi"].items(), key=lambda x: -x[1])[:5]
                    logger.info(f"  [DRIFT] Top features: {dict(top_drift)}")

            # Auto-retrain triggered by drift or trade count
            if getattr(cfg, 'CONCEPT_DRIFT', {}).get("auto_retrain", True):
                do_retrain = False
                if self.drift_detector.should_retrain():
                    cooldown = getattr(cfg, 'CONCEPT_DRIFT', {}).get("retrain_cooldown_hours", 24) * 3600
                    if time.time() - self._last_retrain_time > cooldown:
                        do_retrain = True
                        logger.info("  [RETRAIN] Triggered by concept drift")
                schedule = getattr(cfg, 'RETRAINING', {}).get("schedule_trades", 500)
                if trade_count - self._last_retrain_count >= schedule:
                    if time.time() - self._last_retrain_time > 3600:  # min 1h entre retrains
                        do_retrain = True
                        logger.info(f"  [RETRAIN] Triggered by trade count ({trade_count} trades)")

                if do_retrain:
                    self._last_retrain_count = trade_count
                    self._last_retrain_time = time.time()
                    report = self.retraining_pipeline.run_retraining(
                        symbols=cfg.SYMBOLS,
                        days=getattr(cfg, 'RETRAINING', {}).get("days", 90),
                        min_samples=getattr(cfg, 'RETRAINING', {}).get("min_samples", 50),
                        epochs=getattr(cfg, 'RETRAINING', {}).get("epochs", 10),
                        log_mlflow=getattr(cfg, 'RETRAINING', {}).get("log_mlflow", True),
                    )
                    if report.get("status") == "completed":
                        logger.info(f"  [RETRAIN] Pipeline OK: {report.get('symbols_trained', 0)} symbols trained, "
                                    f"{report.get('total_samples', 0)} samples")
                        # Reset drift detector after retraining
                        self.drift_detector.reset_current()
                        # Reload models (seulement si DL actif)
                        if self.adaptive.dl.available:
                            self.adaptive.dl._load_pretrained()
                    else:
                        logger.info(f"  [RETRAIN] Pipeline status: {report.get('status')}")
        except Exception as e:
            logger.debug(f"  [ML PIPELINE] error: {e}")

    def _check_win_rate(self):
        total = len(self.ftmo._trade_history)
        if total < 100:
            return
        # Utiliser les trades récents (dernier 200) pour la détection de dérive,
        # pas l'historique global qui peut masquer une dégradation récente
        recent_window = 200
        recent_trades = self.ftmo._trade_history[-recent_window:] if len(self.ftmo._trade_history) >= recent_window else self.ftmo._trade_history
        recent_wr = sum(1 for t in recent_trades if t["profit"] > 0) / max(len(recent_trades), 1)
        global_wr = sum(1 for t in self.ftmo._trade_history if t["profit"] > 0) / max(total, 1)
        logger.info(f"  [WR CHECK] {total} trades, global WR={global_wr:.1%}, "
                    f"recent ({len(recent_trades)}) WR={recent_wr:.1%}")
        
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
                        logger.warning(f"[DEGRADED] {symbol}: WR={sym_wr:.1%} < 40% "
                                       f"(cycle {self.cycle_count}) → lot minimum")
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
            recent = self.ftmo._trade_history[-100:] if len(self.ftmo._trade_history) >= 100 else self.ftmo._trade_history
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
        - WR < 50% : réduire la période (plus de signaux, plus réactif)
        - WR > 65% : augmenter la période (moins de faux signaux)
        
        Bornes absolues : min=8, max=30 (évite les extrêmes dangereux).
        Symboles désactivés (allow_buys=false AND allow_shorts=false) → ignorés.
        """
        if len(self.ftmo._trade_history) < 50:
            return  # Pas assez de données pour ajuster
        
        from engine_simple.strategy import SYMBOL_MOMENTUM_PERIODS
        
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
                if new_period != current_period:
                    SYMBOL_MOMENTUM_PERIODS[symbol] = new_period
                    logger.info(f"[PHASE 3] {symbol}: période {current_period}→{new_period} "
                               f"(WR={sym_wr:.1%}, raison: {adjustments[symbol][2]})")
        
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
