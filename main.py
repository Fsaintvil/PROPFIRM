import contextlib
import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import numpy as np

import config_simple as cfg
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
from engine_simple.signals import SignalGenerator
from engine_simple.trade_executor import TradeExecutor
from engine_simple.trade_journal import TradeJournal
from engine_simple.anticipation import AnticipationEngine

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


def _acquire_lock():
    """PID lock — atomic file creation (empêche les instances dupliquées)"""
    pid = os.getpid()
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
handler = TimedRotatingFileHandler(
    "logs/simple_robot.log", when="midnight",
    interval=1, backupCount=14, encoding="utf-8",
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
        if cfg.MIN_RR_RATIO < 1.5:
            errors.append(f"MIN_RR_RATIO={cfg.MIN_RR_RATIO} < 1.5 — risque de non-rentabilité")
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

        self._last_signals = {}  # symbol -> dict pour mémoire de signaux entre cycles
        self.signals = SignalGenerator(self.mt5)
        self.adaptive = AdaptiveEngine(self.mt5, calibration_path="runtime/calibration_state.pkl")
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
            SYMBOL_LIMITS=cfg.SYMBOL_LIMITS,
        ))
        if self._state.get("peak_equity"):
            self.ftmo.peak_equity = self._state["peak_equity"]
        if self._state.get("consecutive_losses"):
            self.ftmo.consecutive_losses = self._state["consecutive_losses"]
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
        if self._state.get("daily_start_equity") is not None:
            self.ftmo.daily_start_equity = self._state["daily_start_equity"]

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
        from engine_simple.regime import RegimeDetector
        from engine_simple.strategy import MOM20x3
        from engine_simple.shield import FTMOAccount, PositionGuard
        self._regime_detector = RegimeDetector()
        self._shield_account = FTMOAccount(
            initial_balance=challenge_init_bal,
            peak_equity=self.ftmo.peak_equity,
            current_balance=challenge_init_bal,
        )
        self._position_guard = PositionGuard()

        # Anticipation Engine — connaissance profonde du marché
        try:
            self.anticipation = AnticipationEngine()
            self.anticipation.initialize(retrain=False)
            logger.info("Anticipation Engine chargé avec succès")
        except Exception as e:
            self.anticipation = None
            logger.warning(f"Anticipation Engine non disponible: {e}")

        # ML Pipeline (optional, graceful fallback)
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
            pass
        return {"status": "error"}

    def _get_balance(self):
        info = self.mt5.get_account_info()
        if info is None:
            raise RuntimeError("Cannot get account info - MT5 disconnected")
        return info.balance

    def _health_check(self):
        if self.mt5.health_check():
            return True
        logger.error("Health check failed")
        self.notifier.send("MT5 health check failed")
        if self.mt5.reconnect():
            self._state["connected"] = True
            self.notifier.send("Robot reconnecte apres interruption MT5")
            return True
        self._state["connected"] = False
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
                daily_start_equity=self.ftmo.daily_start_equity if hasattr(self, "ftmo") else None,
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
        self.tracker.init_tickets()

        while self.running:
            self.cycle_count += 1
            cycle_start = time.time()

            # Watchdog: detect MT5 freeze / stuck cycles
            since_last = time.time() - self._last_cycle_time
            if since_last > 120:
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
                logger.error("Health check failed, stopping")
                break

            self._heartbeat()
            self._pos_cache.invalidate()

            # Circuit breaker institutionnel
            try:
                account_info = self.mt5.get_account_info()
                if account_info:
                    self.risk_manager.update(account_info.equity, self.ftmo.peak_equity or account_info.equity)
                    if self.risk_manager.check_circuit(
                        account_info.equity,
                        self.ftmo.peak_equity or account_info.equity,
                        self.ftmo.consecutive_losses,
                    ):
                        logger.warning("[CIRCUIT] Trading suspendu par le circuit breaker")
                        self.notifier.send("CIRCUIT BREAKER: trading suspendu 30min")
                        time.sleep(30)
                        continue
            except (AttributeError, RuntimeError):
                logger.exception("[CIRCUIT] Erreur circuit breaker")

            try:
                self.tracker.check_closed()
                self.tracker.track_new()

                account = self.mt5.get_account_info()
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
                    old_day = self.ftmo.daily_stats.get("day")
                    self.ftmo._reset_daily()
                    new_day = self.ftmo.daily_stats.get("day")
                    # Si le jour a changé, générer un rapport de fin de journée
                    if old_day is not None and old_day != new_day:
                        try:
                            from engine_simple.performance_monitor import get_monitor
                            pm = get_monitor()
                            pm.generate_report()
                            logger.info(f"[PERF] Rapport quotidien généré pour {old_day}")
                        except Exception as e:
                            logger.debug(f"[PERF] Rapport quotidien échoué: {e}")

                self._manage_positions()
                self._vigilance_scan()
                self._scan_signals()
                self._check_win_rate()
                self._check_volatility()
            except Exception as e:
                logger.exception(f"MT5 operation failed mid-cycle: {e}")
                if not self._health_check():
                    logger.error("MT5 unreachable after mid-cycle failure, stopping")
                    break
                time.sleep(5)  # Pause minimale pour éviter boucle serrée + watchdog intempestif
                self._last_cycle_time = time.time()  # Reset watchdog timer
                continue
            if self.cycle_count % 60 == 0:
                self.adaptive.train_dl_if_ready()
                self.adaptive.save_calibration()
                perf = self.tracker.performance_summary()
                if perf:
                    logger.info(f"  [PERF] {json.dumps(perf)}")
                if hasattr(cfg, 'reload_config') and cfg.reload_config():
                    logger.info("[CONFIG] Configuration reloaded a chaud")
                # ML Pipeline: concept drift monitoring + retraining
                self._ml_pipeline_tick()

            if self.cycle_count - self.last_report_cycle >= 20:
                self._log_ftmo_report()
                self.last_report_cycle = self.cycle_count

            self._last_cycle_time = time.time()
            self._watchdog_failures = 0

            elapsed = time.time() - cycle_start
            sleep_time = max(5, cfg.CYCLE_SECONDS - elapsed)
            time.sleep(sleep_time)

    def _vigilance_scan(self):
        """Run DL/regime pipeline for ALL symbols every cycle — rien n'echappe a la vigilance."""
        positions = {p.symbol: p for p in self._pos_cache.get() if p.magic == cfg.ROBOT_MAGIC}
        for symbol in cfg.SYMBOLS:
            try:
                rates = self._get_rates_for_vigilance(symbol)
                if rates is None:
                    continue
                result = self.adaptive.vigilance(symbol, rates)
                if result is None:
                    continue
                # RegimeDetector: detection parallèle via le nouveau module
                h1 = rates.get("H1")
                if h1 is not None and len(h1) >= 30:
                    hh = np.array([r[2] for r in h1], dtype=float)
                    ll = np.array([r[3] for r in h1], dtype=float)
                    cc = np.array([r[4] for r in h1], dtype=float)
                    new_regime, new_meta = self._regime_detector.detect(hh, ll, cc)
                    if new_regime != result.get("regime", ""):
                        logger.debug(f"  [REGIME COMPARE] {symbol}: "
                                     f"old={result.get('regime','?')} new={new_regime} (adx={new_meta.get('adx',0):.0f})")
                # MOM20x3: signal parallèle
                from engine_simple.strategy import MOM20x3
                mom = MOM20x3(rates, symbol)
                mom_signal = mom.analyze(
                    regime=result.get("regime", "RANGING"),
                    adx_val=result.get("adx", 0),
                    atr_val=result.get("atr", 0.005),
                )
                if mom_signal and mom_signal.is_valid():
                    old_action = result.get("action", "HOLD")
                    if mom_signal.action != old_action:
                        logger.debug(f"  [STRAT COMPARE] {symbol}: "
                                     f"old={old_action} new={mom_signal.action} score={mom_signal.score:.2f}")
                # If this symbol has an open position, compare current vs entry
                pos = positions.get(symbol)
                if pos:
                    entry_regime = pos.comment.replace("ADAPT_", "")[:5] if pos.comment.startswith("ADAPT_") else "?"
                    if entry_regime not in ("?", "LIMIT") and result["regime"] not in ("?", "LIMIT"):
                        if result["regime"] != entry_regime:
                            logger.info(f"  [REGIME SHIFT] {symbol}: {entry_regime} "
                                        f"→ {result['regime']} (position ouverte)")
                    if pos.sl > 0:
                        dist = abs(pos.price_open - pos.sl)
                        logger.debug(f"  [POS] {symbol}: SL={pos.sl:.5f} dist={dist:.5f} profit={pos.profit:+.2f}")
            except Exception as e:
                logger.debug(f"  [VIGIL] {symbol}: error: {e}")

    def _get_rates_for_vigilance(self, symbol):
        """Get cached or fresh rates for vigilance scan."""
        cache = getattr(self, '_vigilance_rate_cache', {})
        now = time.time()
        cached = cache.get(symbol)
        if cached and now - cached["time"] < 60:
            return cached["rates"]
        rates = self.mt5.get_rates_multi_tf(symbol, ["H1", "M15", "M5"], count=100)
        if not rates:
            return None
        if not hasattr(self, '_vigilance_rate_cache'):
            self._vigilance_rate_cache = {}
        self._vigilance_rate_cache[symbol] = {"rates": rates, "time": now}
        return rates

    def _manage_positions(self):
        our = [p for p in self._pos_cache.get() if p.magic == cfg.ROBOT_MAGIC]
        self.ftmo._reconcile_positions(our)
        # Nettoyer les tickets fermés du PositionGuard (évite fuite mémoire)
        if hasattr(self, '_position_guard'):
            self._position_guard.reconcile([str(p.ticket) for p in our])
        pos_summary = []
        total_fl = 0.0
        for pos in our:
            self.ftmo.check_invariants(pos)
            total_fl += pos.profit
            pos_summary.append(f"{pos.symbol}={pos.profit:+.0f}")
            # PositionGuard: surveillance parallèle
            ticket = str(pos.ticket)
            if ticket not in self._position_guard.open_times:
                regime_code = (pos.comment or "").replace("ADAPT_", "")[:5] if (pos.comment or "").startswith("ADAPT_") else "RANGING"
                rmap = {"TRE": "TREND_UP", "DOW": "TREND_DOWN", "RAN": "RANGING", "HIG": "HIGH_VOL", "LOW": "LOW_VOL"}
                self._position_guard.track(ticket, rmap.get(regime_code, "RANGING"), pos.price_open)
            age = (datetime.utcnow() - self._position_guard.open_times.get(ticket, datetime.utcnow())).total_seconds() / 60
            guard_result = self._position_guard.check(ticket, pos.price_current, age, 0.005, pos.price_open, pos.sl or 0, pos.tp)
            if guard_result["action"] in ("close", "trail", "partial"):
                logger.debug(f"  [GUARD] {pos.symbol} ticket={ticket} action={guard_result['action']} reason={guard_result['reason']}")
        if pos_summary:
            logger.info(f"  Positions: {' | '.join(pos_summary)}  →  Total: {total_fl:+.2f}")

    def _scan_signals(self):
        positions = self._pos_cache.get()
        pending = self.mt5.get_pending_orders()
        sym_counts = {}
        for p in positions:
            sym_counts[p.symbol] = sym_counts.get(p.symbol, 0) + 1
        for o in pending:
            sym_counts[o.symbol] = sym_counts.get(o.symbol, 0) + 1
        logger.debug(f"Positions: {len(positions)}, Pending: {len(pending)}, Par symbole: {sym_counts}")

        # Prune les signaux mémorisés vieux de > 20 cycles (~5 min)
        stale = [s for s in list(self._last_signals.keys())
                 if self.cycle_count - self._last_signals[s].get("cycle", 0) > 20]
        for s in stale:
            del self._last_signals[s]

        # Collect all valid signals across symbols first
        candidates = []
        for symbol in cfg.SYMBOLS:
            can_trade, reason = self.ftmo.can_trade(symbol)
            if not can_trade:
                logger.debug(f"  [FTMO] {symbol}: {reason}")
                continue
            pre_ok, pre_checks = self.risk_manager.pre_trade(symbol)
            if not pre_ok:
                failed = [c["rule"] for c in pre_checks if not c["pass"]]
                logger.debug(f"  [PRECHECK] {symbol}: echec {failed}")
                continue

            # Symbole max positions check
            if sym_counts.get(symbol, 0) >= cfg.MAX_POSITIONS_PER_SYMBOL:
                logger.debug(f"  [LIMIT] {symbol}: max positions ({cfg.MAX_POSITIONS_PER_SYMBOL}) atteint")
                continue

            # Signal MOM20x3 pur (pas de DL, pas de meta-learner, pas d'OnlineLearner)
            signal = self.signals.analyze(symbol)
            if signal is None:
                last = self._last_signals.get(symbol)
                if last and self.cycle_count - last["cycle"] < 4:
                    # Copie profonde pour éviter la mutation cumulative du risk_mult
                    # par can_trade/ftmo_protector (correlation, anticipation)
                    last_sig = dict(last["signal"])
                    signal = last_sig
                    score = last["score"]
                    # Mise à jour de l'entry_price pour éviter un prix obsolète
                    tick = self.mt5.get_tick(symbol)
                    if tick:
                        signal["entry_price"] = tick.ask if signal.get("action") == "BUY" else tick.bid
                    signal["_reused"] = True
                    signal["_reuse_age_cycles"] = self.cycle_count - last["cycle"]
                    logger.debug(f"  [SIGNAL] {symbol}: reusing signal from cycle {last['cycle']} "
                                 f"(price refreshed)")
                else:
                    continue
            else:
                score = signal.get("score", 0.6)
                # Stocker une COPIE du signal pour éviter la mutation cumulative
                # du risk_mult par Anticipation/Kelly/OnlineLearner dans les cycles suivants
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
                regime = signal.get("_regime", "RANGING")
                adx_thresh = sym_cfg.get("adx_thresh", 20)
                if regime in ("RANGING", "LOW_VOL"):
                    adx_thresh = min(adx_thresh, 15)  # < 15 seulement en range
                if signal_adx < adx_thresh:
                    logger.info(f"  [ADX] {symbol}: ADX={signal_adx:.1f} < {adx_thresh} (score={signal_score:.2f}), skip")
                    continue
            logger.debug(f"  [SIGNAL] {symbol}: score={signal['score']:.2f}, "
                f"conf={signal['confidence']:.2f}, action={signal['action']}, "
                f"strat={signal.get('details','?')}")

            # Feed features to concept drift detector
            drift_feats = {
                "adx": signal.get("adx", 0),
                "atr_pct": signal.get("atr_pct", 0),
                "score": signal.get("score", 0.5),
                "confidence": signal.get("confidence", 0.5),
                "rsi": signal.get("rsi", 50),
                "quality": signal.get("quality", 0.5),
            }
            self.drift_detector.add_sample(drift_feats)
            candidates.append((signal["score"], symbol, signal, positions))
            # Ne pas faire de check MAX_POSITIONS ici — la liste positions est figée
            # Le vrai check est fait après chaque exécution dans la boucle d'exec

        # Save signal debug info
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
                    recent_h1 = self.mt5.get_rates(symbol, "H1", count=60)
                    if recent_h1 is not None and len(recent_h1) > 10:
                        current_price = signal.get("entry_price", 0)
                        if current_price == 0:
                            tick = self.mt5.get_tick(symbol)
                            current_price = tick.ask if tick else 0
                        if current_price > 0:
                            ctx = self.anticipation.anticipate(symbol, current_price, recent_h1)
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
            # Kelly sizing: ajuste le risk_mult dans le signal
            symbol_perf = self.tracker.performance.get(symbol)
            if symbol_perf and hasattr(self, 'risk_manager'):
                rr = signal.get("rr", cfg.MIN_RR_RATIO * 1.5)
                kelly_risk = self.risk_manager.calculate_position_risk(symbol_perf, rr)
                signal["risk_mult"] = signal.get("risk_mult", 1.0) * (kelly_risk / cfg.RISK_PER_TRADE)
                logger.debug(f"    [KELLY] {symbol}: risk_mult={signal['risk_mult']:.3f}")
            self.executor.execute(symbol, signal)
            executed += 1

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
            Path("runtime/ftmo_report.json").write_text(json.dumps(report, indent=2))
            # Performance Monitor — suivi du challenge et rapport périodique
            try:
                from engine_simple.performance_monitor import update_challenge, get_monitor
                update_challenge(report)
                # Rapport périodique toutes les 60 cycles (~15 min)
                if self.cycle_count % 60 == 0:
                    get_monitor().generate_report()
            except Exception:
                pass
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
                adx_v = ind_adx(hh, ll, cc, 14) if len(hh) >= 30 else 0
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
                trade_count = len(self.ftmo._trade_history)
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
                        # Reload models
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


def main():
    Path("logs").mkdir(exist_ok=True)
    Path("runtime").mkdir(exist_ok=True)
    _acquire_lock()
    try:
        robot = FTMO_SIMPLE()
        robot.start()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
