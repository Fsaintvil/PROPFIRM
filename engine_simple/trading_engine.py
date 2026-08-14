import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import warnings
from datetime import datetime, timezone
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
from engine_simple.dashboard import HealthServer, MetricsCollector
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

# ── Symboles activement tradés — depuis .env, sinon cfg.SYMBOLS (source de vérité) ──
# 🔧 FIX 14 Aout 2026: le fallback codé en dur (5 symboles) ignorait les nouveaux
# symboles activés (XAUUSD + paires primaires). Désormais le fallback = cfg.SYMBOLS
# qui est la source de vérité (trading.symbols dans default.yaml/production.yaml).
_env_syms = os.environ.get("SYMBOLS", "").strip()
ACTIVE_SYMBOLS: set[str] = set()
if _env_syms:
    ACTIVE_SYMBOLS = {s.strip() for s in _env_syms.split(",") if s.strip()}
if not ACTIVE_SYMBOLS:
    ACTIVE_SYMBOLS = set(cfg.SYMBOLS)

# ── Catégories de symboles — SUPPRIMÉ 1er Juillet 2026 ──
# Les SYMBOL_CONFIDENCE_GATES et catégories CORE/TARGET_80/REACTIVATED
# ont été supprimés. Le filtrage est géré par :
#   - min_score=0.30 (config)
#   - Lot progressif WR-based (_get_wr_based_max_lot)
#   - Limites 3/2/1 par symbole-direction (signal_pipeline)

_mutex_handle = None


def _acquire_mutex():
    """Acquiert un named mutex Windows. Retourne True si acquis, False sinon.
    Le mutex est automatiquement libéré par l'OS si le processus crashe.
    🔧 FIX 21 Juillet 2026: Gère les mutex ABANDONED (processus tué par kill -9)."""
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
            # 🔧 FIX 21 Juillet 2026: Vérifier si le mutex est ABANDONED
            # (processus précédent tué par kill -9 sans libérer le mutex)
            # On ouvre le mutex existant et on attend 0ms pour voir s'il est abandonné
            existing = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, _MUTEX_NAME)
            if existing:
                WAIT_ABANDONED = 0x00000080
                WAIT_TIMEOUT = 0x00000102
                wait_result = ctypes.windll.kernel32.WaitForSingleObject(existing, 0)
                ctypes.windll.kernel32.CloseHandle(existing)
                if wait_result == WAIT_ABANDONED:
                    # Le précédent propriétaire est mort — on peut prendre possession
                    # Release + Close le handle obtenu par CreateMutexW, puis ré-essayer
                    ctypes.windll.kernel32.ReleaseMutex(handle)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    # Réessai: CreateMutexW devrait maintenant réussir
                    handle2 = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
                    if handle2:
                        _mutex_handle = handle2
                        logger.warning("PID lock: mutex ABANDONED récupéré (ancien processus tué)")
                        return True
                    else:
                        logger.warning("PID lock: récupération mutex ABANDONED échouée — fallback fichier")
                        return False
            # Vrai conflit: un autre processus détient le mutex
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


logger = logging.getLogger("robot")


class TradingEngine:
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
        # 🐛 FIX 03 Aout 2026: cooldown per-symbol anti-doublon — initialisé AVANT le restore
        # d'état (l'ancien code l'initialisait lazily dans le batch loop, le restore d'état
        # en __init__ plantait en AttributeError).
        self._last_symbol_trade_time = {}
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
                # 🔧 07 Août 2026 (mode preuve): désactive le mode conservation
                # FTMO pour permettre la collecte des 100+ trades de preuve.
                CONSERVATION_MODE_ENABLED=cfg.CONSERVATION_MODE_ENABLED,
            ),
        )
        if self._state.get("peak_equity"):
            self.ftmo.peak_equity = self._state["peak_equity"]
            self.ftmo.challenge.peak_equity = self._state["peak_equity"]  # Sync challenge tracker
        if "consecutive_losses" in self._state:
            self.ftmo.consecutive_losses = self._state["consecutive_losses"]
            self.ftmo.challenge.consecutive_losses = self._state[
                "consecutive_losses"
            ]  # 🐛 FIX 20 Juillet 2026: Sync challenge
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
        # 🐛 FIX 03 Aout 2026: Restore _last_symbol_trade_time (cooldown per-symbol MIN_TRADE_INTERVAL_SEC)
        # Garde uniquement les timestamps < 2× l'intervalle (600s) pour ne pas bloquer les symboles
        # qui n'ont pas tradé depuis longtemps. Empêche la re-entrée immédiate après restart.
        if self._state.get("last_symbol_trade_time"):
            _cooldown_keep = cfg.MIN_TRADE_INTERVAL_SEC * 2
            _now_ts = time.time()
            restored_trades = 0
            for sym, ts in self._state["last_symbol_trade_time"].items():
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    continue
                if _now_ts - ts < _cooldown_keep:
                    self._last_symbol_trade_time[sym] = ts
                    restored_trades += 1
            if restored_trades:
                logger.info(f"[STATE] Restored {restored_trades} symbol trade cooldowns (anti-doublon restart)")
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
                    # Cooldown expiré → on vide simplement le cooldown.
                    # 🐛 FIX 10 Août 2026 (Bug #4): NE PAS reset consecutive_losses ici !
                    # Ce reset (comme celui des paliers du circuit breaker) détruisait
                    # l'escalade 3→5→10 au restart : le compteur persisté repartait à 0
                    # sans qu'aucune perte réelle n'ait été annulée, donc le HARD STOP
                    # à 10 pertes n'était jamais atteint. Le compteur ne descend QUE
                    # sur une victoire (record_trade_result).
                    logger.info(
                        f"[STATE] global_cooldown_until expired ({gcu}), cooldown vidé "
                        f"(consecutive_losses={self.ftmo.consecutive_losses} conservé pour l'escalade)"
                    )
                    self.ftmo.global_cooldown_until = None
                    self.ftmo.challenge.global_cooldown_until = None
            except (ValueError, TypeError) as e:
                logger.warning(f"[STATE] Cannot restore global_cooldown_until: {e}")
        if self._state.get("consistency_violated"):
            self.ftmo.consistency_violated = True
            self.ftmo.challenge.consistency_violated = True  # sync source (ChallengeTracker)
        if self._state.get("daily_profit_reduced"):
            self.ftmo._daily_profit_reduced = True
        # 🔒 FIX 05 Aout 2026 (régression trading_days 6→2): Un jour de trading FTMO
        # appartient au COMPTE, pas au symbole. Les trades des symboles inactifs
        # (ex: USOIL.cash, EURGBP, NZDUSD qui étaient actifs au 31 Juillet) comptent
        # quand même pour le challenge. On collecte donc les jours depuis TOUS les
        # trades non-historiques AVANT le filtrage par symboles actifs, puis on
        # fusionne dans la reconstruction ci-dessous.
        account_trading_days: set = set()
        account_skipped_trades: list = []
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
                    # Collecter le jour de trading du compte même pour un symbole inactif
                    if not t.get("historical"):
                        try:
                            _tv = t.get("time", "")
                            if isinstance(_tv, (int, float)):
                                _tdt = datetime.fromtimestamp(_tv)
                            elif isinstance(_tv, str):
                                _tdt = datetime.fromisoformat(_tv)
                            else:
                                _tdt = None
                            if _tdt is not None:
                                account_trading_days.add(_tdt.date())
                                account_skipped_trades.append(t)
                        except (ValueError, TypeError):
                            pass
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
                            "action": t.get("action"),  # 🐛 FIX Bug #6: direction réelle (BUY/SELL)
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
            # 🔒 FIX 05 Aout 2026 v3: Préserver les jours de trading du COMPTE chargés
            # depuis trading_days_list (état persisté). La reconstruction ci-dessous ne
            # doit JAMAIS jeter des jours FTMO valides : elle n'ajoute que les jours
            # détectés dans trade_history, jamais en retirer.
            persisted_trading_days = set(self.ftmo.trading_days)
            persisted_daily_pnl = dict(self.ftmo.daily_pnl_by_date)
            self.ftmo.trading_days.clear()
            self.ftmo.daily_pnl_by_date.clear()
            historical_count = 0
            stale_pnl_skipped = 0
            now = datetime.utcnow()
            for t in self.ftmo._trade_history:
                if t.get("historical"):
                    historical_count += 1
                    continue
                try:
                    time_val = t.get("time")
                    if isinstance(time_val, datetime):
                        d = time_val.date()
                        # ✅ Un jour de trading compte TOUJOURS, même vieux
                        self.ftmo.trading_days.add(d)
                        # 🔒 Pour le daily_pnl, ne garder que les 48h (PnL frais pour daily check)
                        if (now - time_val).total_seconds() <= 48 * 3600:
                            self.ftmo.daily_pnl_by_date[d] = self.ftmo.daily_pnl_by_date.get(d, 0) + t.get("profit", 0)
                        else:
                            stale_pnl_skipped += 1
                    elif isinstance(time_val, str):
                        d = datetime.fromisoformat(time_val).date()
                        self.ftmo.trading_days.add(d)
                        self.ftmo.daily_pnl_by_date[d] = self.ftmo.daily_pnl_by_date.get(d, 0) + t.get("profit", 0)
                    else:
                        continue
                except (ValueError, TypeError, AttributeError):
                    pass
            logger.info(
                f"[STATE] Reconstruit {len(self.ftmo.trading_days)} jours trading, "
                f"{len(self.ftmo.daily_pnl_by_date)} daily_pnl depuis trade_history "
                f"(filtrés {historical_count} historiques + {stale_pnl_skipped} PnL âgés ignorés)"
            )
            # 🔒 FIX 05 Aout 2026 v3: Réunion des jours persistés (compte FTMO).
            # Quand trade_history ne contient que les symboles actifs (les inactifs
            # sont filtrés à la restauration), les jours stockés dans trading_days_list
            # restent la source de vérité du challenge — on ne doit JAMAIS les perdre.
            _days_before_union = len(self.ftmo.trading_days)
            self.ftmo.trading_days.update(persisted_trading_days)
            if persisted_daily_pnl:
                # Les valeurs persistées incluent les symboles inactifs (plus complètes)
                # → elles priment sur la reconstruction partielle pour les jours communs.
                self.ftmo.daily_pnl_by_date.update(persisted_daily_pnl)
            if len(self.ftmo.trading_days) > _days_before_union:
                logger.info(
                    f"[STATE] Union {len(self.ftmo.trading_days) - _days_before_union} jours persistés "
                    f"(trading_days_list) → {len(self.ftmo.trading_days)} jours au total (compte FTMO)"
                )
            # 🔒 FIX 05 Aout 2026: Fusionner les jours de trading du COMPTE collectés
            # avant filtrage par symbole (trades de symboles inactifs comptent pour FTMO).
            if account_trading_days:
                _before = len(self.ftmo.trading_days)
                self.ftmo.trading_days.update(account_trading_days)
                logger.info(
                    f"[STATE] Fusion {len(account_trading_days)} jours de trading de symboles "
                    f"inactifs → {len(self.ftmo.trading_days)} jours au total (compte FTMO)"
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
            # Si _opened_today non trouvé/nul dans state, forcer 0
            self.ftmo._opened_today = 0
            self.ftmo.challenge._opened_today = 0
        # 🐛 FIX 10 Août 2026 (Bug #2): SUPPRIMÉ le reset forcé à 0 qui suivait.
        # Il annulait complètement la restauration ci-dessus (FIX #1) et permettait
        # le bypass de MAX_TRADES_PER_DAY au redémarrage : le compteur repartait
        # à 0, autorisant 75 nouveaux trades en plus de ceux déjà ouverts le jour.
        # La valeur restaurée est cohérente (elle reflète les trades du jour courant
        # persistés dans l'état), et _reset_daily() la remet à 0 au changement de jour.
        _dse = self._state.get("daily_start_equity")
        if _dse is not None and _dse > 0:
            self.ftmo.daily_start_equity = _dse
            if hasattr(self.ftmo, "challenge"):
                self.ftmo.challenge.daily_start_equity = _dse
            logger.debug(f"[STATE] daily_start_equity restauré: {_dse} (ftmo + challenge)")
        else:
            logger.debug(f"[STATE] daily_start_equity ignoré: {_dse} (<=0 ou None)")
        # 🛡️ FIX P0-1 (13 Août 2026, Robot Manager): SUPPRIMÉ le bloc H3 de recalage
        # forcé de daily_start_equity après restart.
        # ────────────────────────────────────────────────────────────────────────
        # Le bloc H3 recalculait daily_start_equity sur l'equity courante à CHAQUE
        # redémarrage dans la même journée UTC. Après une perte + restart, le DSE
        # descendait au niveau réduit → daily_equity_change ≈ 0 → la limite FTMO
        # daily-loss 2% ne déclenchait JAMAIS. C'était exactement le bug que le
        # FIX #5 du 10/08 (ftmo_protector.py:216-229) prétendait corriger, mais
        # la porte restait ouverte ici.
        # ── Règle désormais en vigueur ──
        #  * daily_start_equity n'est recalculé QUE par _reset_daily() à minuit UTC
        #    (boucle trading). Aucun recalage intra-jour, y compris au restart.
        #  * La restauration ci-dessus (lignes 676-683) restaure la valeur persistée
        #    de l'état — c'est la seule source légitime de DSE au démarrage.
        #  * Conséquence : si _reset_daily() ne s'exécute pas (période de gel),
        #    le DSE peut rester à la valeur du jour précédent → au pire la protection
        #    daily-loss est PLUS stricte, jamais contournée.
        # Références: FIX H3 (07 Juillet) supprimé, FIX Bug #5 (10 Août) restauré
        # dans sa totalité. Tests: pytest tests/test_main_integration.py -q

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
                    fresh = self._mt5.get_positions()
                    # 🐛 FIX 20 Juillet 2026: Ne JAMAIS stocker [] dans le cache
                    # Si get_positions() time out (single-thread executor bloqué
                    # par order_send), il retourne []. Stocker [] fait croire au
                    # pipeline et au portfolio controller qu'il y a 0 positions,
                    # ce qui bypass toutes les limites de duplication.
                    if fresh is not None and len(fresh) > 0:
                        self._cache = fresh
                    elif self._cache is None:
                        self._cache = fresh  # only on first ever fetch
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
            symbol_execution_timeframes=cfg.SYMBOL_EXECUTION_TIMEFRAMES,
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
        # 🔧 22 Juillet: Restaurer win_rate_checked depuis l'état persistant
        # Évite le compound WR check à chaque redémarrage (risk_mult réduit ×0.8 à chaque restart)
        self._win_rate_checked = self._state.get("win_rate_checked", False)
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
        self._watchdog_stall_count = 0
        self._watchdog_process = None

    def _start_process_watchdog(self) -> None:
        """Démarre un PROCESSUS watchdog séparé qui surveille le heartbeat.

        🔧 FIX 21 Juillet 2026: Le thread watchdog NE PEUT PAS détecter les freezes
        MT5 car les appels C de MT5 bloquent le GIL (Global Interpreter Lock),
        empêchant TOUS les autres threads Python de s'exécuter. Résultat: blocks de
        4h30 et 12h25 observés.

        SOLUTION: Un processus COMPLÈTEMENT SÉPARÉ (pas un thread) qui :
        1. Lit le fichier heartbeat toutes les 30s
        2. Si heartbeat > 300s (5min), kill le processus principal via taskkill /F
        3. Le mutex Windows est auto-libéré par l'OS à la mort du processus
        4. Spawn un nouveau main.py pour remplacer le tué

        Ce script (scripts/process_watchdog.py) n'importe AUCUNE librairie MT5
        et n'est PAS affecté par les blocks GIL du processus principal.
        """
        if self._watchdog_process is not None:
            # Vérifier si l'ancien processus est encore vivant
            poll = self._watchdog_process.poll()
            if poll is None:
                logger.debug("[WATCHDOG PROC] Déjà actif")
                return
            logger.info(f"[WATCHDOG PROC] Ancien processus terminé (code={poll})")

        # 🔧 FIX 07 Août 2026: Tue TOUS les process_watchdog.py orphelins AVANT
        # d'en spawner un nouveau. Chaque redémarrage (auto-resurrection ou
        # robot.ps1 manuel) spawnait un NOUVEAU watchdog sans tuer les anciens.
        # Résultat: des watchdogs orphelins surveillaient des PIDs morts (voire
        # réutilisés par d'autres process Windows) → ne détectaient jamais la
        # mort de leur cible → restaient vivants indéfiniment, capables de
        # spawner des main.py en double (risque de doublons de positions).
        # Désormais: exactement UN watchdog par génération de robot.
        self._kill_orphan_watchdogs()

        # 🔧 FIX 30 Juillet 2026: Path absolu depuis la racine du projet
        # Avait: Path(__file__).parent = engine_simple/ → engine_simple/scripts/ (INVALIDE)
        # Maintenant: Path(__file__).parent.parent = racine → scripts/ (CORRECT)
        # Conséquence du bug: le watchdog processus n'a jamais démarré → freeze 14h le 30 Juillet
        # Un processus externe est le SEUL moyen de détecter les blocks GIL (MT5 C API).
        watchdog_script = Path(__file__).resolve().parent.parent / "scripts" / "process_watchdog.py"
        if not watchdog_script.exists():
            logger.critical(
                f"[WATCHDOG PROC] CRITIQUE: Script introuvable: {watchdog_script} — AUCUNE PROTECTION CONTRE LES FREEZES GIL!"
            )
            return

        pid = os.getpid()
        # 🔧 FIX 31 Juillet 2026: Chemin ABSOLU du heartbeat.
        # Le watchdog est lancé avec cwd=engine_simple/ → un chemin relatif
        # "runtime/heartbeat.txt" y était introuvable → warning permanent et
        # AUCUNE protection contre les freezes GIL (le watchdog ne pouvait pas
        # lire l'âge du heartbeat). Sans ce fix, un blocage MT5 de 14h (30 Juillet)
        # aurait été invisible.
        heartbeat_file = os.path.abspath(HEARTBEAT_FILE)
        timeout = max(int(os.environ.get("ROBOT_WATCHDOG_SECONDS", "180")) * 2, 300)

        try:
            # 🐛 FIX 05 Août 2026: Capturer stderr du watchdog dans un fichier dédié.
            # Avant, stderr partait dans le vide (Popen sans redirection) → impossible
            # de diagnostiquer pourquoi un gel de 4h (02:39→06:39, 05 Août) n'a PAS été
            # tué par le watchdog externe. Désormais chaque événement watchdog est tracé.
            watchdog_log = Path(__file__).resolve().parent.parent / "logs" / "watchdog_external.log"
            watchdog_log.parent.mkdir(exist_ok=True)
            _wd_err = open(watchdog_log, "a", encoding="utf-8")
            # 🔧 FIX 10 Août 2026: cwd = RACINE du projet (et non engine_simple/).
            # Ancien cwd=os.path.dirname(__file__) = engine_simple/ → le watchdog
            # tournait dans le mauvais répertoire. Aujourd'hui inoffensif (chemins
            # absolus partout : heartbeat, spawn_new_instance), mais fragile et
            # trompeur. La racine est l'environnement de travail naturel du robot
            # (logs/, runtime/, config/).
            project_root = Path(__file__).resolve().parent.parent
            self._watchdog_process = subprocess.Popen(
                [sys.executable, str(watchdog_script), str(pid), heartbeat_file, str(timeout)],
                cwd=str(project_root),
                stdout=_wd_err,
                stderr=_wd_err,
            )
            logger.info(
                f"[WATCHDOG PROC] Démarré (PID={self._watchdog_process.pid}, timeout={timeout}s, log={watchdog_log.name})"
            )
        except Exception as e:
            logger.error(f"[WATCHDOG PROC] Échec démarrage: {e}")

    def _kill_orphan_watchdogs(self) -> int:
        """Tue tous les process_watchdog.py orphelins des générations précédentes.

        🔧 FIX 07 Août 2026: Chaque redémarrage du robot spawnait un nouveau
        watchdog sans tuer les anciens → des orphelins surveillaient des PIDs
        morts (réutilisés par d'autres process Windows) et restaient vivants
        indéfiniment. Ils pouvaient spawner des main.py en double (risque de
        doublons de positions sur le compte MT5).

        Retourne le nombre de watchdogs tués. Le robot courant n'a pas encore
        son propre watchdog à cet instant (il est spawné juste après), donc on
        peut tuer TOUS les process_watchdog.py sans risque de suicide.
        """
        if not HAS_PSUTIL:
            logger.warning("[WATCHDOG PROC] psutil absent — nettoyage des orphelins impossible")
            return 0
        try:
            import psutil  # import local (pyright: HSA_PSUTIL=True garanti)
        except ImportError:
            logger.warning("[WATCHDOG PROC] psutil absent — nettoyage des orphelins impossible")
            return 0
        killed = 0
        try:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = proc.info.get("cmdline") or []
                    is_watchdog = any("process_watchdog.py" in (c or "") for c in cmdline)
                    if is_watchdog:
                        proc.kill()
                        proc.wait(timeout=5)
                        killed += 1
                        logger.info(f"[WATCHDOG PROC] Orphelin tué (PID={proc.info['pid']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.error(f"[WATCHDOG PROC] Erreur nettoyage orphelins: {e}")
        if killed:
            logger.info(f"[WATCHDOG PROC] {killed} watchdog(s) orphelin(s) nettoyé(s)")
        return killed

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
        if not hasattr(self, "_last_ftmo_refresh_reconnect"):
            self._last_ftmo_refresh_reconnect = 0.0

        # 🔧 FIX 10 Juillet 2026: Reconnexion préventive avant la fenêtre FTMO 23:01
        # FTMO fait une réinitialisation de session quotidienne à 23:01 UTC
        # qui dure ~40-55s. On reconnecte préventivement à 23:00:45 pour
        # établir une session fraîche avant la coupure.
        _now_utc = datetime.now(timezone.utc)
        if _now_utc.hour == 23 and _now_utc.minute == 0 and _now_utc.second >= 45:
            _elapsed = time.time() - self._last_ftmo_refresh_reconnect
            if _elapsed > 120:
                logger.info("[FTMO REFRESH] Fenêtre 23:00:45 — reconnexion préventive avant refresh FTMO")
                try:
                    if self.mt5.reconnect():
                        self._last_ftmo_refresh_reconnect = time.time()
                        self._hc_failures = 0
                        logger.info("[FTMO REFRESH] Reconnexion préventive réussie")
                        return True
                except Exception as e:
                    logger.warning(f"[FTMO REFRESH] Reconnexion préventive échouée: {e}")

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

        # 🐛 FIX 29 Juillet 2026: Minimum 120s entre reconnexions pour éviter
        # la boucle IPC timeout → reconnect → IPC timeout (~65s cycle).
        # Si la dernière reconnexion date de <120s, on attend patiemment.
        if not hasattr(self, "_last_reconnect_attempt"):
            self._last_reconnect_attempt = 0.0
        _since_last_recon = time.time() - self._last_reconnect_attempt
        if _since_last_recon < 120:
            logger.debug(
                f"[BROKER] Dernière reconnexion il y a {_since_last_recon:.0f}s (<120s) "
                f"— skip reconnexion, on laisse MT5 récupérer"
            )
            return True  # Patienter — MT5 peut récupérer seul

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
                    self._last_reconnect_attempt = time.time()
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
            # 🐛 FIX 05 Août 2026: écriture ATOMIQUE (tmp + rename) au lieu de
            # write_text direct. Le watchdog externe lisait parfois le fichier
            # PENDANT l'écriture (write_text = truncate + write) → "Heartbeat
            # file empty" ou timestamp corrompu → false positive "stale".
            _hb_path = Path(HEARTBEAT_FILE)
            _tmp = _hb_path.with_suffix(".tmp")
            _tmp.write_text(datetime.utcnow().isoformat())
            _tmp.replace(_hb_path)  # atomique sur Windows (même volume)
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
                # 🔧 FIX 22 Juillet 2026: Persist win_rate_checked pour éviter
                # que le WR check réduise le risk_mult à CHAQUE redémarrage (effet compound)
                win_rate_checked=self._win_rate_checked,
                # 🐛 FIX 03 Aout 2026: Persist _last_symbol_trade_time — le cooldown MIN_TRADE_INTERVAL_SEC
                # (300s) était effacé à chaque redémarrage ⇒ re-entrée immédiate sur signal encore valide
                # (source de doublons: 2 positions même symbole/même direction après restart).
                last_symbol_trade_time=dict(self._last_symbol_trade_time),
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
        # 🐛 FIX 10 Août 2026 (Bug #1): NE PLUS fermer les positions ici.
        # Le `finally:` de run() et le signal handler appelaient stop() sur
        # TOUT arrêt (crash, erreur, SIGTERM), ce qui liquidait le portefeuille
        # entier à chaque redémarrage (-142.8$ le 10/08 sur un gel).
        # Le kill-switch externe (.opencode/agents/kill-switch.md, règle 1)
        # est le SEUL mécanisme qui peut fermer toutes les positions.
        # stop() ne fait que sauvegarder l'état et libérer les ressources :
        # les positions restent ouvertes et sont reprises au redémarrage.
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

        # 🔧 FIX 11 Août 2026: EMPÊCHER LA VEILLE MACHINE pendant que le robot tourne.
        # Cause racine des "gels" 05/08 (02:39→06:39) et 11/08 (01:55→06:07) :
        # le LAPTOP entrait en veille S3 sur batterie (Event 42, motif Battery),
        # suspendant robot ET watchdog externe pendant 4h13m. Le watchdog externe
        # ne se réveillait pas correctement après la reprise (waitable-timer perdu).
        # SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED) déclare au noyau
        # que ce process est "système critique" → la veille automatique est repoussée
        # tant que le robot tourne. (powercfg /change standby-timeout-dc 0 appliqué
        # en complément au niveau de l'alimentation globale.)
        try:
            import ctypes

            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            logger.info("[POWER] Veille machine désactivée pendant l'exécution (SetThreadExecutionState)")
        except Exception as _e:
            logger.warning(f"[POWER] Impossible de désactiver la veille: {_e}")

        # 🔧 FIX 21 Juillet 2026: Démarrer le PROCESSUS watchdog EXTERNE
        # Le thread watchdog NE PEUT PAS détecter les freezes MT5 car les appels
        # C de MT5 bloquent le GIL, empêchant tous les autres threads Python.
        # Un processus séparé (scripts/process_watchdog.py) n'est PAS affecté.
        self._start_process_watchdog()

        while self.running:
            self.cycle_count += 1
            cycle_start = time.time()
            # Heartbeat TOUT EN DÉBUT de cycle (avant tout check) pour que le
            # processus watchdog voie toujours un heartbeat récent, même si MT5
            # est down. Évite les faux positifs "MT5 down → heartbeat pas écrit".
            self._heartbeat()

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

                        # 🛡️ FIX P0-2 (13 Août 2026, Robot Manager): cwd corrigé vers la RACINE
                        # du projet. Ancien cwd=os.path.dirname(__file__) = engine_simple/ →
                        # main.py introuvable (FileNotFoundError) → la résurrection interne
                        # était MORTA. Même logique que le watchdog externe (process_watchdog.py)
                        # et le watchdog interne (ligne 883 : project_root = parent.parent).
                        _sp.Popen([sys.executable, "main.py"], cwd=str(Path(__file__).resolve().parent.parent))
                        time.sleep(5)  # 🔧 FIX_SUPREME_COUNCIL: 5s (était 1.5s) pour éviter race condition
                        _release_lock()
                        sys.exit(1)
                    self._save_state()
                    # SPAWN d'abord, PUIS libérer le lock
                    import subprocess as _sp

                    # 🛡️ FIX P0-2 (13 Août 2026, Robot Manager): cwd = racine projet.
                    # Ancien cwd=engine_simple/ → main.py introuvable → résurrection morte.
                    _sp.Popen([sys.executable, "main.py"], cwd=str(Path(__file__).resolve().parent.parent))
                    time.sleep(5)  # 🔧 FIX_SUPREME_COUNCIL: 5s (était 1.5s) pour éviter race condition
                    _release_lock()
                    logger.warning("Watchdog: spawn nouveau processus, arrêt de l'ancien")
                    sys.exit(0)

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

            # 🔧 FIX 16 Juillet 2026: WAL checkpoint tous les 1000 cycles (~4h)
            # Évite l'accumulation de données orphelines dans les fichiers .db-wal
            # qui peuvent atteindre 4+ MB (ratio WAL/DB = 11:1 observé pour rate_cache).
            if self.cycle_count % 1000 == 0 and self.cycle_count > 0:
                try:
                    self._vol_cache.wal_checkpoint()
                except Exception as e:
                    logger.debug(f"[WAL] rate_cache checkpoint: {e}")
                try:
                    self.journal.wal_checkpoint()
                except Exception as e:
                    logger.debug(f"[WAL] journal checkpoint: {e}")
                try:
                    self.feature_store.wal_checkpoint()
                except Exception as e:
                    logger.debug(f"[WAL] feature_store checkpoint: {e}")

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

        # 🔧 FIX DUPLICATION 9 Juillet 2026: tracker per-symbol du dernier trade exécuté
        if not hasattr(self, "_last_symbol_trade_time"):
            self._last_symbol_trade_time = {}

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
            # 🔧 FIX 28 Juillet 2026: Re-vérifier PortfolioController avec positions fraîches
            # Le PC est vérifié pendant la génération des candidats (ci-dessus), mais si
            # un trade a déjà été exécuté dans ce cycle, le cache est invalidé et les
            # positions suivantes doivent être re-vérifiées avec les données à jour.
            if self.portfolio_controller:
                try:
                    pc_high_conf = signal.get("high_confidence", False)
                    pc_can, pc_reason = self.portfolio_controller.can_open_position(
                        symbol, signal["action"], live_positions, high_confidence=pc_high_conf
                    )
                    if not pc_can:
                        logger.debug(f"  [PORTFOLIO] {symbol}: {pc_reason} (re-vérification exec)")
                        continue
                except Exception as e:
                    logger.debug(f"  [PORTFOLIO] {symbol}: re-vérification error ({e}) — bypass")
            # 🐛 FIX 10 Août 2026 (Bug #3): La limite globale MAX_POSITIONS s'applique
            # TOUJOURS, même en high_confidence. Le veto risk-compliance impose
            # max_pos=8 absolu. Les relaxations high_confidence (par symbole/direction)
            # sont gérées par le PortfolioController, mais le plafond GLOBAL ne peut
            # jamais être dépassé.
            if live_total >= cfg.MAX_POSITIONS:
                logger.info(f"  [LIMIT] Max positions ({cfg.MAX_POSITIONS}) atteint ({live_total} en cours)")
                break
            # 🔧 FIX DUPLICATION 9 Juillet: per-symbol min interval check
            last_trade = self._last_symbol_trade_time.get(symbol, 0)
            since_last_trade = time.time() - last_trade
            if since_last_trade < cfg.MIN_TRADE_INTERVAL_SEC:
                logger.debug(
                    f"  [COOLDOWN] {symbol}: {since_last_trade:.0f}s < {cfg.MIN_TRADE_INTERVAL_SEC}s "
                    f"depuis dernier trade → skip"
                )
                continue

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
                # 🔧 FIX 22 Juillet 2026: Éviter double comptage risque
                # Si l'OL a déjà ajusté le threshold (ol_thresh_applied=True), il a déjà
                # appliqué son risk_mult via get_params()/analyze(). Kelly doublerait la pénalité.
                # Scénario: OL risk_mult=0.70 × Kelly 0.80 = 0.56 au lieu de 0.70 uniquement.
                if signal.get("ol_thresh_applied", False):
                    signal["risk_mult"] = signal.get("risk_mult", 1.0)
                    logger.debug(
                        f"    [RISK] {symbol}: OL déjà ajusté, skip Kelly "
                        f"(risk_mult={signal['risk_mult']:.3f}, kelly_factor={kelly_factor:.2f})"
                    )
                else:
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
                    "EURGBP": 1.50,  # 🔓 DÉBLOQUÉ 22 Juil 2026 — WR 73.5%, PF 2.04
                    "AUDJPY": 1.10,
                    "USOIL.cash": 1.10,
                    "UKOIL.cash": 1.10,
                    "NATGAS.cash": 1.05,
                    "SOLUSD": 1.10,
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
                # 🔧 FIX DUPLICATION 9 Juillet: enregistrer timestamp pour per-symbol cooldown
                self._last_symbol_trade_time[symbol] = time.time()
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
                # Mettre à jour sym_dir_counts + sym_total_counts pour éviter doublon dans le même cycle
                sig_type = 0 if signal.get("action") == "BUY" else 1
                key = (symbol, sig_type)
                sym_dir_counts[key] = sym_dir_counts.get(key, 0) + 1
                sym_total_counts[symbol] = sym_total_counts.get(symbol, 0) + 1
                logger.debug(
                    f"  [EXEC] {symbol} {signal.get('action')} OK — positions {symbol}: "
                    f"{sym_dir_counts.get(key, 0)}/{sym_total_counts.get(symbol, 0)}"
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

    def _current_trades(self) -> list[dict]:
        """Retourne uniquement les trades du challenge actuel (filtre les historiques)."""
        return [t for t in self.ftmo._trade_history if not t.get("historical", False)]

    def _check_win_rate(self):
        current = self._current_trades()
        total_current = len(current)
        total_all = len(self.ftmo._trade_history)
        if total_current < 20:
            logger.info(
                f"  [WR CHECK] {total_all} trades dont {total_current} challenge — "
                f"pas assez de trades challenge (<20) pour décider"
            )
            return
        # Throttle: logs WR CHECK / PHASE 3 à 1x par minute max (toutes les 4 cycles)
        if not hasattr(self, "_last_wr_check_cycle"):
            self._last_wr_check_cycle = 0
        if self.cycle_count - self._last_wr_check_cycle < 4:
            return
        self._last_wr_check_cycle = self.cycle_count

        # Fenêtre récente: derniers 200 trades CHALLENGE ou tous si moins
        recent_window = min(200, total_current)
        recent_trades = current[-recent_window:]

        recent_wr = sum(1 for t in recent_trades if t["profit"] > 0) / max(len(recent_trades), 1)
        global_wr = sum(1 for t in current if t["profit"] > 0) / max(total_current, 1)
        logger.info(
            f"  [WR CHECK] {total_all} trades ({total_current} challenge), "
            f"challenge WR={global_wr:.1%}, recent ({len(recent_trades)}) WR={recent_wr:.1%}"
        )

        # ═══════════════════════════════════════════════════════════════════
        # 🚫 PHASE 3 DEGRADED MODE — DÉSACTIVÉ 04 Aout 2026 (Robot Manager)
        # ═══════════════════════════════════════════════════════════════════
        # DÉGEL TOTAL: l'utilisateur a choisi "Config pic COMPLÈTE + dégel total"
        # (retour au niveau historique le plus performant — commit 4011b396b, 23 Juin).
        # Le degraded mode (lot minimum si WR<35% sur 20 trades) a été ajouté le
        # 31 Juillet 2026, APRÈS le pic. Il gelait EURUSD/EURGBP/USOIL au lot minimum
        # et empêchait toute récupération. Désactivé pour restaurer le comportement pic.
        # Le code est conservé commenté ci-dessous pour référence/réactivation.
        if False:  # DÉSACTIVÉ — DÉGEL TOTAL 04 Aout 2026
            # PHASE 2.1: Check par symbole → degraded (lot minimum) si WR < 35% sur 20 trades
            degraded_symbols = self._state.get("degraded_symbols", {})
            for symbol in ACTIVE_SYMBOLS & set(cfg.SYMBOLS):
                sym_trades = [t for t in recent_trades if t.get("symbol") == symbol]
                if len(sym_trades) >= 20:
                    sym_wr = sum(1 for t in sym_trades if t["profit"] > 0) / len(sym_trades)
                    sym_pf = self._calc_pf(sym_trades)
                    _display_pf = min(sym_pf, 5.0)
                    logger.info(
                        f"  [PHASE 3] {symbol}: {len(sym_trades)} trades, WR={sym_wr:.1%}, PF={_display_pf:.2f}"
                        + (" (capé)" if sym_pf > 5.0 else "")
                    )

                    if sym_wr < 0.35:
                        if symbol not in degraded_symbols:
                            degraded_symbols[symbol] = self.cycle_count
                            self._state["degraded_symbols"] = degraded_symbols
                            logger.warning(
                                f"[DEGRADED] {symbol}: WR={sym_wr:.1%} < 35% (cycle {self.cycle_count}) → lot minimum"
                            )
                            self.notifier.send(f"DEGRADED: {symbol} WR={sym_wr:.1%} < 35% → lot min")
                    elif sym_wr >= 0.50 and symbol in degraded_symbols:
                        del degraded_symbols[symbol]
                        self._state["degraded_symbols"] = degraded_symbols
                        logger.info(f"[DEGRADED] {symbol}: WR={sym_wr:.1%} ≥ 50% → retour mode normal")
                        self.notifier.send(f"[DEGRADED] {symbol}: WR={sym_wr:.1%} ≥ 50% → mode normal")
                    elif sym_wr < 0.50:
                        logger.warning(f"[WR WATCH] {symbol}: WR={sym_wr:.1%} < 50% (à surveiller)")

        # 🔧 FIX 29 Juillet 2026: WR CHECK DÉSACTIVÉ.
        # Ce mécanisme créait une double peine : l'OnlineLearner réduisait déjà
        # le risk_mult (0.50-0.60), ET le WR CHECK le réduisait encore de 20%
        # (×0.80), donnant un risk_mult net de 0.40-0.48.
        # La combinaison avec le mode Conservation (×0.50 supplémentaire) tombait
        # à 0.20-0.24 — les trades ne pouvaient plus rien gagner.
        # Le WR CHECK est remplacé par les mécanismes existants :
        #   1. OnlineLearner (adaptive) — ajuste thresh/risk par symbole
        #   2. Mode Conservation (ftmo_protector) — filtre les signaux faibles
        #   3. Dégraded mode — lot minimum si WR < 35% par symbole
        if not self._win_rate_checked and recent_wr < 0.55:
            logger.info(f"  [WR CHECK] Recent WR={recent_wr:.1%} < 55% — ignoré (WR CHECK désactivé)")
            self._win_rate_checked = True
        elif not self._win_rate_checked and recent_wr >= 0.55:
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
            # 🔧 FIX 16 Juillet 2026: Mettre à jour le compteur APRÈS ajustement.
            # Sans cela, _phase3_last_adjustment reste à 0 et l'anti-oscillation
            # (trades_since_last < 100) est bypassé à chaque cycle.
            self._phase3_last_adjustment = len(self.ftmo._trade_history)
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
    def _cleanup_old_logs(self, max_age_days=14, max_size_mb=100):
        """Supprime les fichiers de log auxiliaires plus vieux que max_age_days
        et tronque les fichiers runtime/robot_*.log qui dépassent max_size_mb.
        Le fichier principal simple_robot.log est géré par RotatingFileHandler.

        🔧 FIX 16 Juillet 2026: Extension aux logs runtime/ + size-based truncation.
        robot_stderr.log (28 MB) et robot_stdout.log (3.9 MB) n'avaient AUCUNE rotation.
        """
        import shutil
        import time as _time

        now = _time.time()
        max_age_sec = max_age_days * 86400
        removed = 0

        # ── 1. logs/ directory (age-based) ────────────────────────────
        log_dir = Path("logs")
        if log_dir.exists():
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

            for f in log_dir.iterdir():
                if not f.is_file():
                    continue
                if f.name in protected:
                    continue
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

        # ── 2. runtime/ directory (age-based + size-based truncation) ──
        runtime_dir = Path("runtime")
        if runtime_dir.exists():
            max_size_bytes = max_size_mb * 1_048_576
            for f in runtime_dir.iterdir():
                if not f.is_file():
                    continue
                if not f.name.endswith(".log"):
                    continue
                # Protection: ne jamais supprimer le fichier actif si le robot tourne
                # (le PID lock prouve que le processus est actif)
                try:
                    # Size-based truncation: si > max_size_mb, garder les dernières 10 MB
                    fsize = f.stat().st_size
                    if fsize > max_size_bytes:
                        # Lire les 10 derniers MB et réécrire
                        keep_bytes = 10 * 1_048_576
                        with open(f, "rb") as fh:
                            fh.seek(-min(fsize, keep_bytes), 2)
                            tail = fh.read()
                        with open(f, "wb") as fh:
                            fh.write(b"--- [LOG_TRUNCATED at ")
                            fh.write(_time.strftime("%Y-%m-%d %H:%M:%S UTC", _time.gmtime()).encode())
                            fh.write(
                                f"] truncated from {fsize / 1_048_576:.0f} MB to {keep_bytes / 1_048_576:.0f} MB ---\n".encode()
                            )
                            fh.write(tail)
                        removed += 1
                        logger.warning(
                            f"[LOG_CLEANUP] TRONQUÉ: {f.name} ({fsize / 1_048_576:.0f} MB → {keep_bytes / 1_048_576:.0f} MB)"
                        )
                    else:
                        # Age-based cleanup (fallback)
                        mtime = f.stat().st_mtime
                        age = now - mtime
                        if age > max_age_sec:
                            f.unlink(missing_ok=True)
                            removed += 1
                            logger.debug(f"[LOG_CLEANUP] Supprimé: {f.name} (âge: {age / 86400:.1f}j)")
                except (OSError, PermissionError):
                    pass

        if removed:
            logger.info(
                f"[LOG_CLEANUP] {removed} fichier(s) de log traités (age>{max_age_days}j ou size>{max_size_mb}MB)"
            )
        else:
            logger.debug(f"[LOG_CLEANUP] Aucun fichier à nettoyer (âge max: {max_age_days}j)")


# NOTE: main() et if __name__ sont dans main.py (orchestrateur)
# Cette classe est importée par main.py comme TradingEngine
