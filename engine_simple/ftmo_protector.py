import logging
import random
import re
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import numpy as np

import config_simple as cfg
from engine_simple.ftmo_config import (
    ATR_CACHE_TTL, BE_BUFFER_BY_REGIME, FIRST_LOCK_ATR, TRAILING_BY_REGIME,
    get_trailing_for_symbol, get_be_buffer_for_symbol,
)
from engine_simple.news_filter import is_news_blocked
from engine_simple.structure_analyzer import structure_exit_signal

logger = logging.getLogger("ftmo")


class FTMOProtector:
    def __init__(self, mt5, config):
        self.mt5 = mt5
        self.config = config

        self.initial_balance = config.get("INITIAL_BALANCE", 200000)
        self.max_dd_pct = config.get("MAX_DD_PCT", 0.10)
        self.max_daily_loss_pct = config.get("MAX_DAILY_LOSS_PCT", 0.02)
        self.profit_target_pct = config.get("PROFIT_TARGET_PCT", 0.10)
        self.consistency_max_pct = config.get("CONSISTENCY_MAX_PCT", 0.30)
        self.min_trading_days = config.get("MIN_TRADING_DAYS", 10)
        self.max_trading_days = config.get("MAX_TRADING_DAYS", 0)
        self.max_spread_points = config.get("MAX_SPREAD_POINTS", 30)
        self.cooldown_minutes = config.get("COOLDOWN_MINUTES", 15)
        self.symbol_limits = config.get("SYMBOL_LIMITS", {})
        self.max_risk_amount = config.get("MAX_RISK_AMOUNT", 0)
        # Seuil d'auto-disable par symbole (WR < ce seuil sur rolling 20 trades → bloqué)
        self._symbol_auto_disable_wr_threshold = 0.20  # 20%

        self.peak_equity = self.initial_balance
        self.daily_start_equity = self.initial_balance
        self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": datetime.utcnow().date()}
        self._opened_today = 0  # compteur de trades ouverts aujourd'hui (vs daily_stats["trades"] qui compte les fermés)
        self.consecutive_losses = 0
        self.cooldowns = {}
        self._symbol_consecutive_losses = {}  # perte consécutives par symbole
        self.global_cooldown_until = None  # pause globale après 3 pertes consécutives
        self._trade_history = []
        self.trading_days = set()

        self.position_open_times = {}
        self.partial_closed = set()
        self.peak_profit = {}  # ticket -> peak profit in ATR units
        self.trailing_peaks = {}  # ticket -> best price reached (for trailing SL)
        self.position_regime = {}  # ticket_key -> regime string (set at trade open)
        self.position_meta = {}    # ticket_key -> dict(max_profit, ...) pour time-stop basé sur meilleur PnL
        self._time_stop_cooldown = {}  # ticket -> last_attempt_timestamp (évite retcode 10018 flood)
        self._atr_cache = {}  # symbol -> (value, timestamp) pour TTL cache
        self._rates_cache = {}  # symbol -> (rates_array, timestamp) pour structure exit cache

        self.daily_pnl_by_date = {}  # date -> total PnL for consistency
        self._daily_trades_per_symbol = {}  # symbol -> count pour limite quotidienne par symbole
        self._symbol_trade_history: dict[str, list[dict]] = {}  # symbol -> [{profit, time}, ...]
        self.consistency_violated = False
        self.challenge_status = "ACTIVE"  # ACTIVE | PASSED | FAILED_CONSISTENCY | FAILED_DD
        self._daily_profit_reduced = False  # True quand daily profit > DAILY_PROFIT_LIMIT_PCT
        self._daily_loss_violated = False  # H-05: flag de daily loss (check coordonné)

        self._corr_matrix = {}
        self._profile_cache = {}
        self._corr_timestamp = 0
        self._corr_ttl = 3600  # recalc toutes les 1h
        self._corr_max_threshold = 0.70  # Pearson max entre deux positions same-direction

        # ADX market filter: si >50% des symboles ont ADX<22, risque réduit
        self._adx_cache_ts = 0.0
        self._adx_cache_mult = 1.0
        self._adx_cache_ttl = 900  # 15 min cache

    def check_price_staleness(self, symbol, max_age=60):
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return False
        tick_time = getattr(tick, 'time', None)
        if tick_time is None:
            return False
        try:
            age = time.time() - float(tick_time)
        except (TypeError, ValueError):
            # Si on ne peut pas déterminer l'âge, CONSERVATEUR : considérer comme stale
            return False
        if age > max_age:
            logger.warning(f"  [STALE] {symbol}: tick age={age:.0f}s > {max_age}s")
            return False
        return True

    def _reconcile_positions(self, positions):
        open_tickets = {str(p.ticket) for p in positions}
        for p in positions:
            ticket_key = str(p.ticket)
            if ticket_key not in self.position_open_times:
                raw = getattr(p, 'time', None)
                if raw is None:
                    open_time = datetime.utcnow()
                elif isinstance(raw, (int, float)):
                    open_time = datetime.utcfromtimestamp(raw)
                else:
                    open_time = raw
                self.position_open_times[ticket_key] = {
                    "open_time": open_time, "symbol": p.symbol
                }
            if ticket_key not in self.position_regime:
                comment = getattr(p, 'comment', '') or ''
                self._parse_comment_regime(comment, ticket_key)
        # Nettoyer les entrées obsolètes (positions fermées)
        for t in list(self.trailing_peaks.keys()):
            if t not in open_tickets:
                del self.trailing_peaks[t]
        for t in list(self.position_open_times.keys()):
            if t not in open_tickets:
                del self.position_open_times[t]
        for t in list(self.position_regime.keys()):
            if t not in open_tickets:
                del self.position_regime[t]
        for t in list(self.peak_profit.keys()):
            if t not in open_tickets:
                del self.peak_profit[t]
        self.partial_closed &= open_tickets

    def _parse_comment_regime(self, comment, ticket_key):
        m = re.match(r"ADAPT_(\w{3})", comment)
        short = m.group(1) if m else "RAN"
        self.position_regime[ticket_key] = self.REGIME_FROM_COMMENT.get(short, "RANGING")

    # Corrélations empiriques inter-symboles (H1 quotidien, 2020-2026).
    # Classes d'actifs différentes → corrélations faibles sauf BTC↔ETH (même écosystème 0.89).
    # XAUUSD (or) décorrélé des crypto. US500.cash retiré Juin 2026 (PF=1.00), code conservé.
    _FALLBACK_CORR = {
        "XAUUSD":    {"BTCUSD": 0.15, "ETHUSD": 0.12, "US500.cash": 0.20},
        "BTCUSD":    {"XAUUSD": 0.15, "ETHUSD": 0.89, "US500.cash": 0.30},
        "ETHUSD":    {"XAUUSD": 0.12, "BTCUSD": 0.89, "US500.cash": 0.25},
        "US500.cash":{"XAUUSD": 0.20, "BTCUSD": 0.30, "ETHUSD": 0.25},
    }

    def _get_empirical_corr(self, symbol_a, symbol_b):
        """Retourne la corrélation empirique entre deux symboles, ou 0 si inconnu."""
        row = self._FALLBACK_CORR.get(symbol_a, {})
        return row.get(symbol_b, 0.0)

    def _fetch_correlation_matrix(self):
        """Compute Pearson correlation matrix from H1 returns (rolling 24h)."""
        now = time.time()
        if now - self._corr_timestamp < self._corr_ttl and self._corr_matrix:
            return self._corr_matrix
        symbols = cfg.SYMBOLS
        closes = {}
        for sym in symbols:
            rates = self.mt5.get_rates(sym, "H1", 48)
            if rates is None or len(rates) < 24:
                closes[sym] = None
                continue
            closes[sym] = np.array([r[4] for r in rates[-24:]], dtype=np.float64)
        valid = {s: c for s, c in closes.items() if c is not None}
        names = list(valid.keys())
        n = len(names)
        if n < 2:
            # Fallback: construire matrice empirique pour TOUS les symboles actifs
            # (évite le trou de protection "0 corrélation" qui laisse passer les trades corrélés)
            logger.warning("[CORR] < 2 symboles avec données MT5 — utilisation matrice empirique")
            emp_matrix = {}
            active = [s for s in symbols if s in self._FALLBACK_CORR]
            for s in active:
                emp_matrix[s] = {t: self._get_empirical_corr(s, t) for t in active if t != s}
            self._corr_matrix = emp_matrix
            self._corr_timestamp = now
            return emp_matrix
        matrix = np.eye(n)
        returns = {s: np.diff(valid[s]) / valid[s][:-1] for s in names}
        for i in range(n):
            for j in range(i+1, n):
                ri, rj = returns[names[i]], returns[names[j]]
                if len(ri) < 5 or len(rj) < 5:
                    corr = 0.0
                else:
                    corr = np.corrcoef(ri, rj)[0, 1]
                    corr = 0.0 if np.isnan(corr) else corr
                matrix[i, j] = matrix[j, i] = corr
        self._corr_matrix = dict(zip(names, [{n: matrix[i, k] for k, n in enumerate(names)} for i in range(n)], strict=False))
        self._corr_timestamp = now
        corr_str = ", ".join(
            f"{names[i]}/{names[j]}={matrix[i,j]:+.2f}"
            for i in range(n) for j in range(i+1, n)
        )
        logger.debug(f"  [CORR] Matrice ({n} symbols): {corr_str}")
        return self._corr_matrix

    def _get_profile(self, symbol):
        """Retourne le profil institutionnel du symbole (caché)."""
        if symbol not in self._profile_cache:
            try:
                from engine_simple.symbol_profile import get_profile
                self._profile_cache[symbol] = get_profile(symbol)
            except (ImportError, RuntimeError, KeyError):
                self._profile_cache[symbol] = None
        return self._profile_cache[symbol]

    def _check_correlation(self, symbol, action, our_positions):
        """Correlation-aware position sizing — désactivé (mode MAX)."""
        return True, None, 1.0

    def _check_correlation_portfolio(self, symbol: str, action: str, our_positions: list) -> tuple[bool, str | None, float]:
        """Vérifie l'exposition corrélée du portefeuille — désactivé (mode MAX)."""
        return True, None, 1.0

    def can_trade(self, symbol, signal=None, positions=None, check_danger_hours=True):
        """Vérifie si un trade est autorisé pour le symbole.
        
        Args:
            symbol: symbole à trader
            signal: dict du signal MOM20x3 (optionnel, requis pour bypass DANGER_HOURS)
            positions: liste des positions actuelles (optionnel)
            check_danger_hours: False pour la pré-vérification sans signal (main.py première passe)
        """
        # _reset_daily() est appelé dans _scan_signals() (main.py) — une fois par cycle



        # 🔒 AUTO-DISABLE : symbole avec WR < 20% sur les 20 derniers trades
        if signal is not None:
            sym_history = self._symbol_trade_history.get(symbol, [])
            last20 = [t for t in sym_history[-20:] if t.get("profit", 0) != 0]
            if len(last20) >= 10:
                wins = sum(1 for t in last20 if t["profit"] > 0)
                wr = wins / len(last20)
                if wr < self._symbol_auto_disable_wr_threshold:
                    return False, (f"[AUTO-DISABLE] {symbol} WR={wr:.0%} sur {len(last20)} trades "
                                   f"< {self._symbol_auto_disable_wr_threshold:.0%}")

        # 🔒 AUTO STOP : arrêt automatique si le marché est trop rangeant (ADX<22 sur >50% des symboles)
        # Réévalué toutes les 5 min. Pause minimum 30 min.
        try:
            from scripts.auto_stop import decision as auto_stop_decision
            auto_verdict, _ = auto_stop_decision()
            if auto_verdict in ("STOP", "WAIT"):
                return False, f"[AUTO STOP] Marché RANGING — trading suspendu (verdict={auto_verdict})"
        except ImportError:
            pass  # auto_stop pas disponible, on continue
        except Exception as e:
            logger.debug(f"Auto-stop check error: {e}")

        # Spread check — ratio ATR-based pour les symboles à haute valeur
        info = self.mt5.get_symbol_info(symbol)
        if info and hasattr(info, 'point') and info.point > 0:
            tick = self.mt5.get_tick(symbol)
            if tick is None:
                return False, f"No tick data for {symbol} — spread check impossible"
            spread = tick.ask - tick.bid
            sym_cfg = self.symbol_limits.get(symbol, {})
            max_sp = sym_cfg.get("max_spread_points", self.max_spread_points)
            # Vérification points absolus (backward compat)
            spread_pts_ok = spread < max_sp * info.point * 1.05
            # Vérification ratio ATR (max 10% de l'ATR) pour les symboles à grand pip value
            atr_val = self._get_atr(symbol)
            atr_ok = True
            if atr_val and atr_val > 0:
                spread_atr_ratio = spread / atr_val
                if spread_atr_ratio > 0.10:  # max 10% de l'ATR
                    atr_ok = False
            if not spread_pts_ok and not atr_ok:
                return False, f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f}, ATR ratio={spread/atr_val:.1%})" if atr_val else \
                    f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f})"
        else:
            return False, f"Cannot get symbol info for {symbol}"

        # Price staleness check
        if signal is not None and not self.check_price_staleness(symbol):
            return False, "Stale price: tick > 60s"

        # 🔒 Global cooldown: pause 30min après AUTO_PAUSE_LOSSES pertes consécutives
        if self.global_cooldown_until is not None:
            now = datetime.utcnow()
            if now < self.global_cooldown_until:
                remaining = int((self.global_cooldown_until - now).total_seconds() // 60)
                return False, f"Global cooldown: {remaining}min (after {self.consecutive_losses} consecutive losses)"
            else:
                # Cooldown expired → reset counter et libérer
                logger.info(f"Global cooldown expired — reseting consecutive_losses from {self.consecutive_losses} to 0")
                self.consecutive_losses = 0
                self.global_cooldown_until = None

        max_trades = self.config.get("MAX_TRADES_PER_DAY", 5)
        # Vérifier trades FERMÉS (daily_stats) ET trades OUVERTS (_opened_today)
        if self.daily_stats["trades"] >= max_trades:
            return False, f"Daily trade limit (closed: {self.daily_stats['trades']}/{max_trades})"
        if self._opened_today >= max_trades:
            return False, f"Daily trade limit (opened: {self._opened_today}/{max_trades})"

        # Per-symbol daily limit: supprimé (l'utilisateur ne veut pas manquer d'opportunités)

        # Symbol-level direction restrictions
        if signal is not None:
            sym_cfg = self.symbol_limits.get(symbol, {})
            allow_shorts = sym_cfg.get("allow_shorts", True)
            allow_buys = sym_cfg.get("allow_buys", True)
            if not allow_shorts and signal.get("action") == "SELL":
                return False, f"Shorts not allowed on {symbol} (per-symbol config)"
            if not allow_buys and signal.get("action") == "BUY":
                return False, f"Buys not allowed on {symbol} (per-symbol config)"

        # Signal quality gate (with per-symbol override)
        if signal is not None:
            min_score = self.config.get("MIN_SIGNAL_SCORE", 0.55)
            sym_cfg = self.symbol_limits.get(symbol, {})
            sym_min_score = sym_cfg.get("min_score")
            if sym_min_score is not None:
                min_score = sym_min_score
            sig_score = signal.get("score", 0)
            if sig_score < min_score:
                return False, f"Signal score too low: {sig_score:.2f} < {min_score}"

        # FIX #3: SL OBLIGATOIRE — REFUSER tout trade sans Stop Loss défini
        # Les signaux contiennent sl_atr/tp_atr (multiplicateurs ATR) mais pas les prix absolus.
        # On calcule les prix absolus ici si nécessaire (plutôt que bloquer).
        if signal is not None:
            sl = signal.get("sl")
            tp = signal.get("tp")
            if sl is None or tp is None or sl == 0 or tp == 0:
                # Essayer de calculer SL/TP depuis les paramètres ATR du signal
                try:
                    atr = signal.get("atr")
                    sl_atr = signal.get("sl_atr", 2.0)
                    tp_atr = signal.get("tp_atr", 4.0)
                    entry = signal.get("entry_price")
                    action = signal.get("action")
                    if entry is None or entry == 0:
                        tick = self.mt5.get_tick(symbol)
                        if tick:
                            entry = tick.ask if action == "BUY" else tick.bid
                    if entry and entry > 0 and action:
                        direction = 0 if action == "BUY" else 1
                        new_sl, new_tp = self._calc_sl_tp(symbol, entry, direction, atr, sl_atr, tp_atr)
                        if new_sl is not None and new_tp is not None and new_sl > 0 and new_tp > 0:
                            signal["sl"] = new_sl
                            signal["tp"] = new_tp
                            sl, tp = new_sl, new_tp
                except Exception as exc:
                    logger.debug(f"  [SL CALC] {symbol}: echec calcul SL={sl} TP={tp}: {exc}")
                # Vérification finale : si toujours pas de SL/TP → bloquer
                if sl is None or tp is None or sl == 0 or tp == 0:
                    return False, f"SL/TP manquant — transaction BLOQUÉE (SL={sl}, TP={tp})"

        # Volatility circuit breaker: skip if ATR% > 3x its 14-period median
        atr_pct = signal.get("atr_pct", 0) if signal else 0
        if atr_pct > 0:
            atr_median = signal.get("atr_median_14", atr_pct)
            if atr_median > 0 and atr_pct / atr_median > 3.0:
                return False, f"Volatility spike: ATR%={atr_pct:.3f} vs median={atr_median:.3f} (>3x)"

        account = self.mt5.get_account_info()
        if account is None:
            return False, "Cannot get account info"
        current_equity = account.equity

        # Daily profit limit → risk reduction mode (equity-based)
        daily_equity_change = current_equity - self.daily_start_equity
        daily_pnl_pct = daily_equity_change / max(self.initial_balance, 1)
        profit_limit = self.config.get("DAILY_PROFIT_LIMIT_PCT", 0.003)
        if daily_pnl_pct >= profit_limit:
            self._daily_profit_reduced = True
            logger.info(f"  [PROFIT LIMIT] daily PnL ${self.daily_stats['pnl']:.0f} "
                f"({daily_pnl_pct:.3%}) >= {profit_limit:.3%} — risk reduit a 25%")
        else:
            self._daily_profit_reduced = False
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        dd_initial = (self.initial_balance - current_equity) / self.initial_balance
        dd_peak = (self.peak_equity - current_equity) / self.peak_equity
        if dd_initial >= self.max_dd_pct:
            self.challenge_status = "FAILED_DD"
            return False, f"FTMO max drawdown from initial: {dd_initial:.1%}"
        if dd_peak >= self.max_dd_pct:
            self.challenge_status = "FAILED_DD"
            return False, f"FTMO max drawdown from peak: {dd_peak:.1%}"

        # Circuit breaker: drawdown élevé interdit les shorts
        circuit_breaker_threshold = self.config.get("CIRCUIT_BREAKER_DD_PCT", 0.08)
        # Per-symbol override (ex: BTCUSD = 6% au lieu de 8%)
        sym_cfg_cb = self.symbol_limits.get(symbol, {})
        sym_circuit_breaker = sym_cfg_cb.get("circuit_breaker_dd_pct_override")
        if sym_circuit_breaker is not None:
            circuit_breaker_threshold = sym_circuit_breaker
        if dd_peak > circuit_breaker_threshold and signal and signal.get("action") == "SELL":
            return False, f"Circuit breaker: DD peak {dd_peak:.1%} > {circuit_breaker_threshold:.0%}, shorts disabled"

        daily_loss = max(0, -daily_equity_change) / self.initial_balance
        daily_loss_limit = self.max_daily_loss_pct
        # Per-symbol override (ex: BTCUSD = 1.5% au lieu de 2%)
        sym_daily_loss = sym_cfg_cb.get("max_daily_loss_pct_override")
        if sym_daily_loss is not None:
            daily_loss_limit = sym_daily_loss
        if daily_loss >= daily_loss_limit:
            self.challenge_status = "FAILED_DD"
            return False, f"FTMO daily loss limit: {daily_loss:.1%}"
        # Zone 3: >1.5% daily loss → stop trading (marge sous FTMO 5%)
        zone3 = self.config.get("ZONE3_LOSS_PCT", 0.015)
        if daily_loss >= zone3 and self.daily_stats["losses"] > 0:
            return False, f"Zone 3: daily DD {daily_loss:.1%} >= {zone3:.1%}, stop"

        # 🔒 Pause 30min (FIXE) après AUTO_PAUSE_LOSSES pertes consécutives
        # Note: utilise un temps fixe de 30min, PAS COOLDOWN_MINUTES
        # Dans prod, COOLDOWN_MINUTES=15 pour l'intervalle entre trades,
        # mais AUTO_PAUSE doit être plus long (30 min) pour protéger FTMO
        auto_pause = self.config.get("AUTO_PAUSE_LOSSES", 5)
        if self.consecutive_losses >= auto_pause:
            if self.global_cooldown_until is None:
                AUTO_PAUSE_COOLDOWN = 30  # minutes fixes pour l'auto-pause
                self.global_cooldown_until = datetime.utcnow() + timedelta(minutes=AUTO_PAUSE_COOLDOWN)
                logger.warning(
                    f"AUTO PAUSE: {self.consecutive_losses} consecutive losses >= {auto_pause}, "
                    f"global cooldown {AUTO_PAUSE_COOLDOWN}min jusqu'à {self.global_cooldown_until}"
                )
            # Bloquer immédiatement tant que cooldown actif
            remaining = int((self.global_cooldown_until - datetime.utcnow()).total_seconds() // 60)
            return False, f"Global cooldown: {remaining}min (after {self.consecutive_losses} consecutive losses)"

        if symbol in self.cooldowns and datetime.utcnow() < self.cooldowns[symbol]:
            remaining = (self.cooldowns[symbol] - datetime.utcnow()).seconds // 60
            return False, f"Cooldown: {remaining}min"

        if signal:
            our_pos = [p for p in (positions or self.mt5.get_positions()) if p.magic == cfg.ROBOT_MAGIC]
            
            # Correlation checks désactivés (mode MAX)

            # Institutional profile check
            profile = self._get_profile(symbol)
            if profile:
                # Per-symbol ranging restriction
                if signal.get("_is_ranging", True) != False:
                    sym_cfg = self.symbol_limits.get(symbol, {})
                    allow_ranging = sym_cfg.get("allow_ranging")
                    if allow_ranging == False and signal.get("_regime") == "RANGING":
                        return False, f"{symbol}: ranging trades not allowed (per-symbol config)"

                # DL required for specific symbols
                dl_required = sym_cfg.get("dl_required", False)
                if dl_required and signal.get("_ml_agrees") != True:
                    return False, f"{symbol}: DL agreement required but not confirmed"

                # Per-symbol max daily trades override
                max_daily = sym_cfg.get("max_daily_trades")
                if max_daily is not None:
                    sym_daily = self._daily_trades_per_symbol.get(symbol, 0)
                    if sym_daily >= max_daily:
                        return False, f"{symbol}: daily limit ({max_daily})"

                # Profile-based ATR validation
                atr_val = signal.get("atr", 0)
                if atr_val > 0:
                    from engine_simple.symbol_profile import get_atr_scaling
                    scaling = get_atr_scaling(symbol, atr_val * 10000)
                    if scaling < 0.75:
                        return False, f"{symbol}: ATR {atr_val:.5f} trop eleve pour profil"

        # Blocage weekend SUPPRIMÉ — mode 24/7
        # now = datetime.utcnow()
        # (Le weekend est tradable en crypto/indices 24/7)

        # News filter: block avant/après événements à haut-impact
        # Temps de blocage par symbole (XAUUSD=10min, BTCUSD=15min, US500.cash=20min)
        news_blocked, news_details = is_news_blocked(symbol=symbol)
        if news_blocked:
            news_name = news_details[0]['name'] if news_details else "high-impact"
            return False, f"News: {news_name}"

        # Danger hours: créneaux identifiés comme à risque par l'analyse live
        # (Excel Juin 2026: 12:00 UTC = 0% WR sur 6 trades)
        # Bypassé si check_danger_hours=False (pré-vérification sans signal dans main.py)
        # ou si signal score ≥ 0.80 (signaux très forts, bypass ADX également)
        utc_hour = datetime.utcnow().hour
        danger_hours = self.config.get("DANGER_HOURS", [])
        if utc_hour in danger_hours and check_danger_hours:
            if signal is not None and signal.get("score", 0) >= 0.80:
                logger.debug(f"  [DANGER] {symbol}: bypass DANGER_HOUR ({utc_hour}h UTC) "
                             f"pour signal fort (score={signal.get('score',0):.2f} ≥ 0.80)")
            else:
                return False, f"Danger hour: {utc_hour}h UTC (0% WR historique sur ce créneau)"

        # Session block: 24/5 (toutes les heures, weekend bloqué séparément)
        start_hour = self.config.get("TRADING_START_HOUR", 0)
        end_hour = self.config.get("TRADING_END_HOUR", 24)
        if not (start_hour <= utc_hour < end_hour):
            return False, f"Session block: {utc_hour}h UTC (trade only {start_hour}-{end_hour}h UTC)"

        # Per-symbol preferred hours (ex: XAUUSD = 13-22h, US500.cash = 13-21h)
        if signal is not None:
            sym_cfg_ph = self.symbol_limits.get(symbol, {})
            pref_hours = sym_cfg_ph.get("preferred_hours")
            if pref_hours is not None and len(pref_hours) > 0 and utc_hour not in pref_hours:
                return False, f"{symbol}: not in preferred hours {pref_hours}h UTC"

        # Challenge expiry: max trading days atteint
        if self.max_trading_days > 0 and len(self.trading_days) >= self.max_trading_days:
            self.challenge_status = "FAILED_EXPIRY"
            return False, f"FTMO: maximum trading days ({self.max_trading_days}) atteint — challenge expiré"

        # ✅ FTMO exige un profit target réalisé (closed trades), pas flottant
        current_pnl = sum(self.daily_pnl_by_date.values())
        profit_target_amount = self.initial_balance * self.profit_target_pct
        if current_pnl >= profit_target_amount:
            if self.consistency_violated:
                self.challenge_status = "FAILED_CONSISTENCY"
                return False, "FTMO FAILED: consistency violated (>30% daily)"
            if len(self.trading_days) < self.min_trading_days:
                return False, (
                    f"FTMO: target atteint (${current_pnl:.0f}) mais {len(self.trading_days)}/"
                    f"{self.min_trading_days} jours de trading"
                )
            self.challenge_status = "PASSED"
            return False, "FTMO PASSED: target + 10 days + consistency OK"

        # Consistency already violated: empecher d'approcher du target
        if self.consistency_violated and current_pnl >= profit_target_amount * 0.8:
            self.challenge_status = "FAILED_CONSISTENCY"
            return False, "FTMO FAILED: consistency violated — stop avant target"

        return True, "OK"

    def reset_challenge(self, new_initial_balance=None):
        """Reset l'état du challenge (utile pour comptes practice/Free Trial).
        Ne PAS appeler en cours de vrai challenge FTMO — destiné aux reset de
        comptes d'entraînement ou après réapprovisionnement.

        Args:
            new_initial_balance: nouvelle balance initiale (None = conserve l'actuelle)
        """
        self.challenge_status = "ACTIVE"
        self.consistency_violated = False
        self.consecutive_losses = 0
        self._symbol_consecutive_losses = {}
        self.global_cooldown_until = None
        self.cooldowns = {}
        self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": datetime.utcnow().date()}
        self._daily_trades_per_symbol = {}
        self._opened_today = 0
        self._trade_history = []
        self._symbol_trade_history = {}
        self.daily_pnl_by_date = {}
        self.trading_days = set()
        self.trading_days.add(datetime.utcnow().date())
        self._daily_profit_reduced = False
        if new_initial_balance is not None:
            self.initial_balance = new_initial_balance
        account = self.mt5.get_account_info()
        if account:
            self.peak_equity = account.equity
            self.daily_start_equity = account.equity
        logger.warning(f"[CHALLENGE RESET] Status={self.challenge_status}, "
                       f"balance=${self.initial_balance:.2f}, peak=${self.peak_equity:.2f}")

    def _adaptive_lot_mult(self):
        """Multiplicateur adaptatif (0.30-1.0) basé sur performance récente.
        Augmente les lots quand le robot performe bien, les réduit en cas de difficulté.
        """
        mult = 1.0

        # 1. Win rate récent (max 20 derniers trades)
        recent = self._trade_history[-20:] if len(self._trade_history) >= 20 else self._trade_history
        if len(recent) >= 5:
            wins = sum(1 for t in recent if t.get("profit", 0) > 0)
            wr = wins / len(recent)
            if wr > 0.65:
                mult *= 1.15  # Bon WR → lots +15%
            elif wr > 0.55:
                mult *= 1.05  # WR correct → lots +5%
            elif wr < 0.40:
                mult *= 0.70  # Mauvais WR → lots -30%
            elif wr < 0.50:
                mult *= 0.85  # WR médiocre → lots -15%

        # 2. Drawdown progressif
        account = self.mt5.get_account_info()
        if account:
            dd = (self.peak_equity - account.equity) / max(self.peak_equity, 1)
            if dd > 0.07:
                mult *= 0.60  # DD > 7% → -40%
            elif dd > 0.05:
                mult *= 0.75  # DD > 5% → -25%
            elif dd > 0.03:
                mult *= 0.90  # DD > 3% → -10%

        # 3. Pertes consécutives (utilise AUTO_PAUSE_LOSSES de la config)
        auto_pause = self.config.get("AUTO_PAUSE_LOSSES", 5)
        if self.consecutive_losses >= auto_pause:
            mult *= 0.30  # Pause imminente → risque fortement réduit
        elif self.consecutive_losses >= max(3, auto_pause - 2):
            mult *= 0.50  # Proche du seuil → risque réduit de moitié
        elif self.consecutive_losses >= 2:
            mult *= 0.75  # 2 pertes → pré-alerte, risque réduit

        # 4. Challenge progress (confiance croissante)
        report = self.get_progress_report()
        progress_str = report.get("profit_progress", "0%")
        try:
            progress = float(progress_str.strip().rstrip("%"))
        except (ValueError, AttributeError):
            progress = 0
        if progress > 70:
            mult *= 1.20
        elif progress > 40:
            mult *= 1.10

        return max(0.30, min(1.0, mult))

    def _adx_market_risk_mult(self) -> float:
        """🔒 ADX Market Filter : si >50% des symboles actifs ont ADX < 22,
        le marché est majoritairement rangeant → risque réduit de 50%.
        Le MOM20x3 whipsaw en ranging, c'est la protection la plus importante.
        Cache 15 min pour éviter les appels API excessifs."""
        now = time.time()
        if now - self._adx_cache_ts < self._adx_cache_ttl:
            return self._adx_cache_mult

        symbols = cfg.SYMBOLS
        low_adx_count = 0
        total_checked = 0

        for sym in symbols:
            try:
                rates = self.mt5.get_rates(sym, "H1", 30)
                if rates is None or len(rates) < 26:
                    continue
                total_checked += 1
                high = np.array([r[2] for r in rates[-26:]], dtype=np.float64)
                low = np.array([r[3] for r in rates[-26:]], dtype=np.float64)
                close = np.array([r[4] for r in rates[-26:]], dtype=np.float64)

                # ADX simplifié (DM-based)
                up_move = np.diff(high)
                down_move = -np.diff(low)
                plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
                minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
                tr = np.maximum(high[1:] - low[1:],
                                np.maximum(np.abs(high[1:] - close[:-1]),
                                           np.abs(low[1:] - close[:-1])))
                period = 14
                if len(tr) < period:
                    continue
                atr = np.mean(tr[-period:])
                if atr <= 0:
                    continue
                avg_plus = np.mean(plus_dm[-period:])
                avg_minus = np.mean(minus_dm[-period:])
                di_plus = 100.0 * avg_plus / atr if atr > 0 else 0
                di_minus = 100.0 * avg_minus / atr if atr > 0 else 0
                dx = 100.0 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0
                adx_val = dx  # simplified ADX

                if adx_val < 22:
                    low_adx_count += 1
                    logger.debug(f"  [ADX FILTER] {sym}: ADX={adx_val:.1f} < 22 → LOW")
                else:
                    logger.debug(f"  [ADX FILTER] {sym}: ADX={adx_val:.1f} >= 22 → OK")
            except Exception as e:
                logger.debug(f"  [ADX FILTER] {sym}: erreur {e}")
                continue

        if total_checked == 0:
            return 1.0

        ratio = low_adx_count / total_checked
        if ratio >= 0.50:
            self._adx_cache_mult = 0.50
            logger.warning(f"  [ADX FILTER] {low_adx_count}/{total_checked} symboles ADX<22 "
                           f"({ratio:.0%}) → RISK × 0.50 (marché RANGING)")
        else:
            self._adx_cache_mult = 1.0
            logger.info(f"  [ADX FILTER] {low_adx_count}/{total_checked} symboles ADX<22 "
                        f"({ratio:.0%}) → risque normal")

        self._adx_cache_ts = now
        return self._adx_cache_mult

    def _get_symbol_perf_risk_mult(self, symbol: str) -> float:
        """Multiplicateur de risque par symbole basé sur les 20 derniers trades.
        
        Principe : chaque symbole a sa propre dynamique. Au lieu d'un risk_mult
        fixe dans la config, on l'ajuste dynamiquement selon le WR récent.
        
        Fenêtre : 20 derniers trades du symbole (ou moins si pas assez de données).
        
        Règles :
          WR > 70% → x1.20 (le symbole performe, on augmente)
          WR > 60% → x1.10 (bon, léger boost)
          WR 50-60% → x1.00 (neutre)
          WR 40-50% → x0.80 (faible, on réduit)
          WR < 40% → x0.50 (mauvais, forte réduction)
          < 5 trades → x1.00 (pas assez de données, neutre)
        """
        sym_trades = self._symbol_trade_history.get(symbol, [])
        if len(sym_trades) < 5:
            return 1.0
        
        # Derniers 20 trades du symbole
        recent = sym_trades[-20:] if len(sym_trades) >= 20 else sym_trades
        wins = sum(1 for t in recent if t.get("profit", 0) > 0)
        wr = wins / len(recent)
        
        if wr > 0.70:
            mult = 1.20
        elif wr > 0.60:
            mult = 1.10
        elif wr > 0.50:
            mult = 1.00
        elif wr > 0.40:
            mult = 0.80
        else:
            mult = 0.50
        
        logger.debug(f"  [SYM-PERF] {symbol}: {wins}/{len(recent)} WR={wr:.0%} → risk_mult={mult:.2f}")
        return mult

    def calculate_lot(self, symbol, entry, sl, quality=1.0, direction=0):
        account = self.mt5.get_account_info()
        if account is None:
            return 0.01
        current_equity = account.equity

        # Base risk from RISK_PER_TRADE, ajusté par direction
        base_risk = self.config.get("RISK_PER_TRADE", 0.004)
        short_mult = self.config.get("RISK_SHORT_MULT", 1.0)
        dir_mult = 1.0 if direction == 0 else short_mult
        risk_amount = current_equity * base_risk * dir_mult
        dd_peak = (self.peak_equity - current_equity) / max(self.peak_equity, 1)
        if dd_peak > 0.05:
            risk_amount *= (1 - dd_peak)
        risk_amount *= quality

        # Friday risk reduction SUPPRIMÉE — mode 24/7
        risk_amount *= self.symbol_limits.get(symbol, {}).get("risk_mult", 1.0)

        # Per-symbol performance multiplier (rolling WR tracker)
        perf_mult = self._get_symbol_perf_risk_mult(symbol)
        risk_amount *= perf_mult

        # Absolute max risk cap (if configured)
        if self.max_risk_amount > 0:
            risk_amount = min(risk_amount, self.max_risk_amount)

        # Zone 2: >1% daily loss → risk × 0.5 (uniquement sur pertes réelles, pas sur gains)
        daily_loss_amt = max(0, -self.daily_stats["pnl"])
        zone2 = self.config.get("ZONE2_LOSS_PCT", 0.01)
        if daily_loss_amt > 0 and (daily_loss_amt / max(self.initial_balance, 1)) >= zone2:
            risk_amount *= 0.50
            logger.debug(f"  [ZONE 2] daily loss {daily_loss_amt/self.initial_balance:.2%} >= {zone2:.1%}, risk 50%")

        # ADX Market Filter: si >50% des symboles ont ADX<22, risk × 0.50
        adx_mult = self._adx_market_risk_mult()
        if adx_mult < 1.0:
            risk_amount *= adx_mult
            logger.debug(f"  [ADX FILTER] risk × {adx_mult:.2f} = ${risk_amount:.2f}")

        # Daily profit limit: risk reduit a 25%
        if self._daily_profit_reduced:
            risk_amount *= 0.25
            logger.debug("  [RISK REDUCED] daily profit limit atteint, risk_amount=25%")

        sym_cfg = self.symbol_limits.get(symbol, {})
        max_lot = sym_cfg.get("max_lot", 0.55)
        min_lot = sym_cfg.get("min_lot", 0.01)
        lot_size = self.config.get("LOT_SIZE", 0.1)

        order_type = self.mt5.ORDER_TYPE_BUY if direction == 0 else self.mt5.ORDER_TYPE_SELL
        sl_profit = self.mt5.calc_profit(order_type, symbol, 0.1, entry, sl)
        if sl_profit is not None and sl_profit < 0:
            risk_per_01 = abs(sl_profit)
            if risk_per_01 < 1.0:
                # SL trop serré ou marché fermé → lot safety = LOT_SIZE
                logger.warning(f"[LOT SAFETY] {symbol}: risk_per_01=${risk_per_01:.2f} anormal "
                               f"(marché fermé ou SL trop serré) → lot={lot_size}")
                lot = lot_size
            else:
                lot = (risk_amount / risk_per_01) * 0.1
        else:
            lot = lot_size

        # Adaptive lot multiplier (performance-based)
        adaptive_mult = self._adaptive_lot_mult()
        lot *= adaptive_mult
        logger.debug(f"  [ADAPTIVE LOT] {symbol}: lot pré-clamp={lot:.3f} (risk_per_01=${sl_profit if sl_profit else 0:.2f})")

        # Safety: si le lot calculé est absurde (> 3× max_lot), forcer LOT_SIZE
        if lot > max_lot * 3:
            logger.warning(f"[LOT SAFETY] {symbol}: lot={lot:.3f} > 3×max_lot={max_lot} → force lot={lot_size}")
            lot = lot_size

        # Clamp between min_lot and max_lot from symbol config
        lot = max(min_lot, min(lot, max_lot))

        # 🔒 SECOND CLAMP : re-vérifier avec la config module-level (fraîche)
        try:
            import config_simple as _cfg
            if hasattr(_cfg, 'SYMBOL_LIMITS'):
                _fresh = _cfg.SYMBOL_LIMITS.get(symbol, {})
                _max = _fresh.get("max_lot", max_lot)
                if _max < max_lot:
                    # La config fraîche a un max_lot plus bas → l'utiliser
                    max_lot = _max
                    lot = min(lot, max_lot)
                    logger.info(f"  [LOT CLAMP] {symbol}: second clamp {_max} (hot-reload override)")
        except (ImportError, AttributeError):
            pass

        if lot > max_lot:
            logger.warning(f"  [LOT CLAMP] {symbol}: lot {lot:.3f} > max_lot {max_lot}, clamp force")
            lot = max_lot
        return round(lot, 2)

    REGIME_FROM_COMMENT = {
        "TRE": "TREND_UP", "DOW": "TREND_DOWN", "RAN": "RANGING",
        "HIG": "HIGH_VOL", "LOW": "LOW_VOL",
    }

    def register_open_trade(self, symbol=None):
        """Enregistre un trade qui VIENT d'être ouvert.
        Permet au MAX_TRADES_PER_DAY de compter aussi les trades ouverts,
        pas seulement les fermés (était la cause des 222 trades/jour)."""
        self._opened_today += 1
        self._reset_daily()  # reset si jour a changé

    def refresh_symbol_limits(self):
        """Recharge les symbol_limits depuis la config globale.
        Appelé par main.py après un hot-reload pour propager les changements."""
        try:
            import config_simple as _cfg
            if hasattr(_cfg, 'SYMBOL_LIMITS'):
                self.symbol_limits = _cfg.SYMBOL_LIMITS
                logger.info(f"[CONFIG] symbol_limits rechargées: {len(self.symbol_limits)} symboles")
            if hasattr(_cfg, 'DANGER_HOURS'):
                self.config["DANGER_HOURS"] = _cfg.DANGER_HOURS
        except Exception as e:
            logger.warning(f"[CONFIG] refresh_symbol_limits failed: {e}")

    def check_invariants(self, position):
        ticket_key = str(position.ticket)
        if ticket_key not in self.position_open_times:
            open_time = getattr(position, 'time', None) or datetime.utcnow()
            self.position_open_times[ticket_key] = {
                "open_time": open_time, "symbol": position.symbol
            }
        if ticket_key not in self.position_regime:
            comment = getattr(position, 'comment', '') or ''
            self._parse_comment_regime(comment, ticket_key)
        self._prune_position_times()
        # Chaque sous-vérification est protégée individuellement :
        # le "readonly attribute" d'MT5 survient si la position a été modifiée
        # entre la lecture et l'envoi (ex: partial TP puis trailing dans le même cycle)
        subs = [
            ("time_stop", self._check_time_stop),
            ("partial_tp", self._check_partial_tp),
            ("step_trail", self._check_step_trailing),
            ("structure", self._check_structure_exit),
        ]
        for name, fn in subs:
            try:
                fn(position)
            except AttributeError as e:
                if "readonly" in str(e).lower() or "attribute" in str(e).lower():
                    logger.debug(f"[GUARD] {position.symbol} ticket={ticket_key}: {name} skip (position locked)")
                else:
                    logger.warning(f"[GUARD] {position.symbol} ticket={ticket_key}: {name} attr err: {e}")
            except Exception as e:
                logger.warning(f"[GUARD] {position.symbol} ticket={ticket_key}: {name} err: {e}")

    def set_position_regime(self, ticket, regime):
        self.position_regime[str(ticket)] = regime

    def _prune_position_times(self):
        if len(self.position_open_times) > 200:
            try:
                old = sorted(self.position_open_times.keys(),
                             key=lambda k: self.position_open_times[k]["open_time"])[:-150]
                for k in old:
                    del self.position_open_times[k]
            except Exception as e:
                logger.warning(f"Prune failed: {e}")
                self.position_open_times = dict(list(self.position_open_times.items())[-150:])

    def record_trade_result(self, symbol, profit, historical=False):
        """Enregistre le résultat d'un trade fermé.

        Args:
            symbol: le symbole tradé
            profit: profit en $ (positif=gain, négatif=perte)
            historical: True si appelé depuis import_history() au démarrage.
                        Ne compte PAS dans le daily_stats["trades"] pour
                        éviter de bloquer les trades live (MAX_TRADES_PER_DAY).
        """
        if not historical:
            self.daily_stats["trades"] += 1
            self._daily_trades_per_symbol[symbol] = self._daily_trades_per_symbol.get(symbol, 0) + 1
            self.daily_stats["pnl"] += profit
            today = datetime.utcnow().date()
            self.trading_days.add(today)
            self.daily_pnl_by_date[today] = self.daily_pnl_by_date.get(today, 0) + profit
            self._trade_history.append(dict(symbol=symbol, profit=profit, time=datetime.utcnow()))
            if len(self._trade_history) > 1000:
                self._trade_history = self._trade_history[-1000:]
            # Per-symbol trade history (rolling window 50 trades)
            if symbol not in self._symbol_trade_history:
                self._symbol_trade_history[symbol] = []
            self._symbol_trade_history[symbol].append(dict(profit=profit, time=datetime.utcnow()))
            if len(self._symbol_trade_history[symbol]) > 50:
                self._symbol_trade_history[symbol] = self._symbol_trade_history[symbol][-50:]

            if profit < 0:
                self.daily_stats["losses"] += 1
                self.consecutive_losses += 1
                # Cooldown progressif: 15 min pour 1 perte, 30 min pour 2+ consécutives
                sym_losses = self._symbol_consecutive_losses.get(symbol, 0) + 1
                self._symbol_consecutive_losses[symbol] = sym_losses
                cd_minutes = 15 if sym_losses <= 1 else 30
                self.cooldowns[symbol] = datetime.utcnow() + timedelta(minutes=cd_minutes)
                logger.info(f"  [COOLDOWN] {symbol}: {sym_losses} perte(s) consecutive(s) → {cd_minutes}min")
                # Pas de pause globale — le robot doit toujours être prêt à trader 24/5.
                # Le per-symbol cooldown et les limites FTMO (daily loss 2%, max DD 10%)
                # sont suffisants pour protéger le capital.
            elif profit > 0:
                self.consecutive_losses = 0
                self._symbol_consecutive_losses[symbol] = 0  # reset per-symbol sur gain
            # profit == 0 (BE) ne reset PAS consecutive_losses intentionnellement

            self._check_consistency()
            self._check_daily_loss_limit(symbol=symbol)
            self._check_drawdown_limit()

        self._prune_histories()

    def _check_consistency(self):
        """FTMO consistency rule: aucun jour ne doit dépasser 30% du profit total.
        Vérification EN CONTINU dès que le PnL total dépasse $500 (évite les faux
        positifs en début de challenge où 1-2 trades représentent >50%).
        total_pnl calculé depuis daily_pnl_by_date (intégral, jamais tronqué)."""
        total_pnl = sum(self.daily_pnl_by_date.values())
        # Seuil minimum de PnL pour éviter les faux positifs (1-2 premiers trades)
        if total_pnl < 500:
            return
        if total_pnl <= 0:
            return
        for day, day_pnl in sorted(self.daily_pnl_by_date.items()):
            if day_pnl <= 0:
                continue
            day_pct = day_pnl / total_pnl
            if day_pct > self.consistency_max_pct:
                self.consistency_violated = True
                logger.critical(
                    f"FTMO CONSISTENCY VIOLATED: {day} = ${day_pnl:.0f} ({day_pct:.1%}) "
                    f"> {self.consistency_max_pct:.0%} of ${total_pnl:.0f} total (realized PnL)"
                )

    def _check_daily_loss_limit(self, symbol=None):
        """H-05: Vérifie la daily loss avec coordination et caching.
        Met à jour _daily_loss_violated pour synchronisation avec can_trade().
        
        Args:
            symbol: si fourni, utilise le max_daily_loss_pct_override du symbole
        """
        try:
            account = self.mt5.get_account_info()
            equity_val = getattr(account, 'equity', None)
            if equity_val is not None and isinstance(equity_val, (int, float)):
                daily_equity_change = equity_val - self.daily_start_equity
            else:
                daily_equity_change = self.daily_stats["pnl"]
        except (AttributeError, RuntimeError, OSError):
            daily_equity_change = self.daily_stats["pnl"]
        daily_loss_pct = max(0, -daily_equity_change) / max(self.initial_balance, 1)
        daily_loss_limit = self.max_daily_loss_pct
        # Per-symbol override (ex: BTCUSD = 1.5%)
        if symbol:
            sym_cfg = self.symbol_limits.get(symbol, {})
            sym_daily_loss = sym_cfg.get("max_daily_loss_pct_override")
            if sym_daily_loss is not None:
                daily_loss_limit = sym_daily_loss
        self._daily_loss_violated = daily_loss_pct >= daily_loss_limit
        if self._daily_loss_violated:
            self.challenge_status = "FAILED_DD"
            logger.warning(f"DAILY LOSS LIMIT: {daily_loss_pct:.1%}")

    def current_dd_pct(self):
        try:
            account = self.mt5.get_account_info()
            if not account:
                return 0.0
            eq = account.equity
            peak = self.peak_equity
            return (peak - eq) / max(peak, 1) if peak > 0 else 0.0
        except Exception:
            return 0.0

    def _check_drawdown_limit(self):
        try:
            account = self.mt5.get_account_info()
            if account:
                dd_pct = (self.peak_equity - account.equity) / max(self.peak_equity, 1)
                if dd_pct >= self.max_dd_pct:
                    self.challenge_status = "FAILED_DD"
                    logger.warning(f"MAX DRAWDOWN: {dd_pct:.1%} - STOPPING")
        except Exception as e:
            logger.warning(f"Drawdown check failed: {e}")

    def _prune_histories(self):
        if len(self._trade_history) > 1000:
            self._trade_history = self._trade_history[-1000:]
        if len(self.partial_closed) > 500:
            self.partial_closed = set(list(self.partial_closed)[-300:])
        if len(self.peak_profit) > 500:
            old = sorted(self.peak_profit.keys(), key=lambda k: int(k))[:-300]
            for k in old:
                del self.peak_profit[k]
        if len(self.trailing_peaks) > 500:
            old = sorted(self.trailing_peaks.keys(), key=lambda k: int(k))[:-300]
            for k in old:
                del self.trailing_peaks[k]
        if len(self.position_regime) > 500:
            old = sorted(self.position_regime.keys(), key=lambda k: int(k))[:-300]
            for k in old:
                del self.position_regime[k]

    def get_progress_report(self):
        account = self.mt5.get_account_info()
        equity = account.equity if account else self.peak_equity
        balance = account.balance if account else self.initial_balance
        # PnL réalisé (trades fermés) pour consistency — PnL flottant (equity) pour DD
        realized_pnl = sum(self.daily_pnl_by_date.values()) if self.daily_pnl_by_date else 0
        current_pnl = equity - self.initial_balance  # inclut flottant pour profit_progress
        if realized_pnl == 0 and self._trade_history:
            realized_pnl = sum(t.get("profit", 0) for t in self._trade_history)
        profit_progress = current_pnl / max(self.initial_balance * self.profit_target_pct, 1e-6)
        dd_init = max(0, (self.initial_balance - equity) / self.initial_balance)
        dd_peak = max(0, (self.peak_equity - equity) / max(self.peak_equity, 1))
        winners = sum(1 for t in self._trade_history if t["profit"] > 0)
        wr = winners / max(len(self._trade_history), 1)
        best_day = max(self.daily_pnl_by_date.values()) if self.daily_pnl_by_date else 0
        # Si daily_pnl_by_date est vide mais qu'on a des trades ET un PnL positif,
        # reconstruire à partir de _trade_history (cas restart après perte du state)
        if best_day == 0 and self._trade_history and current_pnl > 0:
            temp_daily = {}
            for t in self._trade_history:
                d = t.get("time").date() if isinstance(t.get("time"), datetime) else None
                if d is None:
                    continue
                temp_daily[d] = temp_daily.get(d, 0) + t.get("profit", 0)
            if temp_daily:
                best_day = max(temp_daily.values())
        # best_day_pct = meilleur jour / profit total (pour règle consistance FTMO)
        if realized_pnl > 0 and best_day > 0:
            best_day_pct = best_day / realized_pnl
        else:
            best_day_pct = 0.0
        return dict(
            balance=balance, equity=equity, pnl=current_pnl,
            status=self.challenge_status,
            consistency_violated=self.consistency_violated,
            best_day_pct=f"{best_day_pct:.1%}",
            profit_progress=f"{profit_progress:.1%}",
            profit_remaining=f"${max(0, self.initial_balance * self.profit_target_pct - current_pnl):.0f}",
            dd_from_initial=f"{dd_init:.1%}", dd_from_peak=f"{dd_peak:.1%}",
            trading_days=len(self.trading_days),
            days_remaining=max(0, self.min_trading_days - len(self.trading_days)),
            total_trades=len(self._trade_history), win_rate=f"{wr:.0%}",
            daily_pnl=f"${self.daily_stats['pnl']:.0f}",
            daily_equity_pnl=f"${equity - self.daily_start_equity:.0f}",
            peak_equity=self.peak_equity, consecutive_losses=self.consecutive_losses,
        )

    def _pip_offset(self, symbol, pips=10):
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return 0.001
        point = info.point if info.point else 0.0001
        pip_size = point * (10 if info.digits >= 3 else 1)  # 1 pip = 10 points pour tous les forex
        return pips * pip_size

    def _check_partial_tp(self, position):
        ticket = str(position.ticket)
        if ticket in self.partial_closed:
            return
        entry = position.price_open
        if position.sl is None or position.tp is None or position.sl == position.tp:
            return
        # Only count progress moving TOWARD TP (not in the wrong direction)
        if position.type == 0:  # BUY: TP > entry
            if position.price_current <= entry:
                return
            progress = (position.price_current - entry) / max(position.tp - entry, 0.00001)
        else:  # SELL: TP < entry
            if position.price_current >= entry:
                return
            progress = (entry - position.price_current) / max(entry - position.tp, 0.00001)
        if progress < 0.60:
            return
        close_vol = position.volume / 2
        if close_vol < 0.01:
            return
        # Ajuster au volume_step du symbole (ex: 0.01 pour standard, 0.001 pour mini lots)
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return
        lot_step = getattr(info, 'volume_step', 0.01)
        if isinstance(lot_step, (int, float)) and lot_step > 0:
            close_vol = round(close_vol / lot_step) * lot_step
            close_vol = round(close_vol, 6)  # éviter les artefacts flottants
        tick = self.mt5.get_tick(position.symbol)
        if tick is None:
            return
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=position.symbol, volume=close_vol,
            type=ct, position=position.ticket, price=price, deviation=20,
            magic=cfg.ROBOT_MAGIC, comment="PARTIAL_TP")
        # info déjà récupéré pour volume_step (ligne ~673)
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"TP Partiel: {position.symbol} ferme "
                        f"{close_vol}/{position.volume} a {price:.5f} "
                        f"(profit={position.profit:.2f})")
            self.partial_closed.add(ticket)
            # Persister partial_closed pour survie aux redémarrages
            try:
                import json, pathlib as _pl
                _state_path = _pl.Path("runtime/robot_state.json")
                if _state_path.exists():
                    _d = json.loads(_state_path.read_text())
                    _d["partial_closed"] = list(self.partial_closed)
                    _atomic = _state_path.with_suffix(".tmp")
                    _atomic.write_text(json.dumps(_d, default=str))
                    _atomic.replace(_state_path)
            except Exception as _e:
                logger.debug(f"[PERSIST] partial_closed non persisté: {_e}")
            # Set BE for remaining position après partial TP réussi
            if info:
                atr_val = self._get_atr(position.symbol)
                if atr_val and atr_val > 0:
                    regime = self.position_regime.get(ticket, "RANGING")
                    be_mult = BE_BUFFER_BY_REGIME.get(regime, 0.50)
                    be_buffer = be_mult * atr_val
                else:
                    be_buffer = self._pip_offset(position.symbol, 10)
                be_sl = entry + be_buffer if position.type == 0 else entry - be_buffer
                be_sl = round(be_sl, info.digits)
                # Ne JAMAIS reculer le SL — ne set BE que si le SL actuel est plus faible
                is_buy = position.type == 0
                sl_improves = (position.sl is None) or (
                    (is_buy and be_sl > position.sl) or (not is_buy and be_sl < position.sl)
                )
                if sl_improves:
                    old_sl = position.sl
                    r = self.mt5.update_sl(position, be_sl)
                    if r and r.retcode == 10009:
                        try:
                            position.sl = be_sl  # ← SYNC critical: échoue si TradePosition est tuple immuable
                        except AttributeError:
                            logger.debug(f"  [SYNC] {position.symbol} SL local sync skipped (readonly)")
                    logger.info(f"  [AUDIT] {position.symbol} SL {old_sl}→{be_sl} (BE after partial TP, retcode={r.retcode if r else '?'})")
        elif result and result.retcode != 10009:
            logger.warning(f"PARTIAL TP FAILED {position.symbol}: "
                           f"retcode={result.retcode} {getattr(result, 'comment', '')}")
        else:
            logger.warning(f"PARTIAL TP FAILED {position.symbol}: no result from MT5")

    def _check_time_stop(self, position):
        ticket = str(position.ticket)
        if ticket not in self.position_open_times:
            return
        ot = self.position_open_times[ticket]["open_time"]
        if isinstance(ot, (int, float)):
            ot = datetime.utcfromtimestamp(ot)
        elapsed = datetime.utcnow() - ot
        hours = elapsed.total_seconds() / 3600

        # Time-stop basé sur le MEILLEUR PnL observé (pas le PnL actuel)
        # pour éviter de fermer prématurément une position qui retrace
        max_profit = self.position_meta.get(ticket, {}).get("max_profit", position.profit)
        if position.profit > max_profit:
            self.position_meta.setdefault(ticket, {})["max_profit"] = position.profit
            max_profit = position.profit
        max_hours = 12 if max_profit > 0 else 4.0
        if hours < max_hours:
            return

        # 🔧 Cooldown : éviter retcode 10018 (rate limit) en n'essayant qu'1x/heure
        last_try = self._time_stop_cooldown.get(ticket, 0)
        if time.time() - last_try < 3600:
            return
        self._time_stop_cooldown[ticket] = time.time()

        tick = self.mt5.get_tick(position.symbol)
        if tick is None:
            return
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=position.symbol, volume=position.volume,
            type=ct, position=position.ticket, price=price, deviation=20,
            magic=cfg.ROBOT_MAGIC, comment="TIME_STOP")
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"Time-stop: {position.symbol} ferme apres {hours:.1f}h (profit={position.profit:.2f})")
        elif result and result.retcode == 10018:
            logger.debug(f"  [TIME-STOP] {position.symbol}: rate limit (retcode=10018), reessai dans 1h")
        elif result and result.retcode != 10009:
            logger.warning(f"TIME STOP FAILED {position.symbol}: retcode={result.retcode}")

    def _get_atr(self, symbol, period=14):
        """Get current ATR in price units (True Range) for a symbol (cached TTL=60s).

        Utilise le True Range max(high-low, |high-prev_close|, |low-prev_close|)
        au lieu du simple high-low pour capturer les gaps d'ouverture.
        """
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached and (now - cached[1]) < ATR_CACHE_TTL:
            return cached[0]
        try:
            rates = self.mt5.get_rates(symbol, "H1", period + 5)
            if rates is None or len(rates) < period:
                return None
            # Utiliser l'indexation numérique r[2]/r[3]/r[4] comme dans _reconstruct_peak
            # et _check_structure_exit — compatible tuples ET numpy structured arrays
            high = np.array([r[2] for r in rates], dtype=float)
            low = np.array([r[3] for r in rates], dtype=float)
            close = np.array([r[4] for r in rates], dtype=float)
            # True Range: max(high-low, |high-prev_close|, |low-prev_close|)
            prev_close = np.roll(close, 1)
            prev_close[0] = close[0]  # first bar: prev = same
            tr = np.maximum(high - low,
                 np.maximum(np.abs(high - prev_close),
                            np.abs(low - prev_close)))
            val = float(tr[-period:].mean())
            self._atr_cache[symbol] = (val, now)
            return val
        except Exception as e:
            logger.warning(f"ATR calc (True Range) failed for {symbol}: {e}")
            return None

    def _check_step_trailing(self, position):
        """ATR-based trailing SL — niveaux progressifs adaptés au régime.
        Le trail distance se resserre à mesure que le profit augmente,
        et varie selon le régime (TREND large, RANGING serré, HIGH_VOL très large, etc.)
        """
        ticket = str(position.ticket)
        atr_val = self._get_atr(position.symbol)
        if atr_val is None or atr_val <= 0:
            logger.debug(f"  [TRAIL] {position.symbol} ticket={ticket} "
                         f"skip: ATR={atr_val}")
            return

        if ticket not in self.trailing_peaks:
            self.trailing_peaks[ticket] = self._reconstruct_peak(position)

        if position.type == 0:
            peak = max(self.trailing_peaks[ticket], position.price_current)
            profit_atr = (peak - position.price_open) / atr_val
        else:
            peak = min(self.trailing_peaks[ticket], position.price_current)
            profit_atr = (position.price_open - peak) / atr_val
        self.trailing_peaks[ticket] = peak

        regime = self.position_regime.get(ticket, "RANGING")
        # Check if symbol profile has custom trailing levels
        profile = self._get_profile(position.symbol)
        profile_levels = profile.trailing_profile.get(regime) if profile else None
        # Utilise les trailing calibrés par symbole si disponibles
        levels = profile_levels or get_trailing_for_symbol(position.symbol, regime)
        first_thresh = levels[0][0] if levels else 0.50
        logger.debug(f"  [TRAIL] {position.symbol} ticket={ticket} "
                     f"ATR={atr_val:.5f} peak={peak:.5f} "
                     f"entry={position.price_open:.5f} profit_atr={profit_atr:.2f} "
                     f"SL={position.sl:.5f} regime={regime} "
                     f"thresh={first_thresh}")
        if profit_atr <= first_thresh:
            logger.debug(f"  [TRAIL] {position.symbol} ticket={ticket} "
                         f"wait: profit_atr={profit_atr:.2f} <= {first_thresh}")
            return

        trail_dist = levels[-1][1]
        for thresh, dist in reversed(levels):
            if profit_atr > thresh:
                trail_dist = dist
                break

        # Weekend gap protection SUPPRIMÉE — mode 24/7

        # Randomisation ±10% du trail pour éviter le stop hunting prévisible
        jitter = 1.0 + random.uniform(-0.10, 0.10)
        trail_distance = trail_dist * atr_val * jitter
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return

        # ── Validation du SL peak-based ───────────────────────────────
        # Le trailing calcule SL = peak ± trail_distance. Mais si le prix a
        # fortement divergé du peak (ex: SELL remontée du trough vers l'entrée),
        # ce SL est invalide (sous le marché pour SELL, au-dessus pour BUY).
        # Dans ce cas, on NE MODIFIE PAS le SL — on laisse le niveau précédent
        # protéger le trade. Le trade se fermera naturellement quand le prix
        # traversera ce niveau.
        #
        # Si le SL est valide, on le plafonne entre le prix d'entrée (breakeven)
        # et le prix courant ± distance_minimale (contrainte MT5).
        current_price = position.price_current
        entry_price = position.price_open
        try:
            min_stop_points = int(info.trade_stops_level or 0)
        except (TypeError, ValueError):
            min_stop_points = 0
        min_gap = max(trail_distance, min_stop_points * info.point) if min_stop_points > 0 else trail_distance

        if position.type == 0:  # BUY
            trail_sl = peak - trail_distance
            # SL valide = sous le marché
            if trail_sl >= current_price:
                # ← FIX Juin 2026: reset peak si le prix a retracé >1.5×ATR du peak
                #   Évite le blocage permanent (peak figé à l'ancien sommet)
                retrace_atr = (peak - current_price) / max(atr_val, 1e-10)
                if retrace_atr > 1.5:
                    self.trailing_peaks[ticket] = current_price
                    logger.debug(f"  [TRAIL] {position.symbol}: retrace={retrace_atr:.1f}ATR "
                                 f"> 1.5 → peak reset to current={current_price:.5f}")
                else:
                    logger.debug(f"  [TRAIL] {position.symbol}: trail_sl={trail_sl:.5f} >= "
                                 f"current={current_price:.5f}, keep old SL (invalid for BUY)")
                return
            # Plage valide : [entry_price , current_price - min_gap]
            lower = entry_price
            upper = current_price - min_gap
            if lower > upper:
                logger.debug(f"  [TRAIL] {position.symbol}: no valid SL range "
                             f"[{lower:.5f}, {upper:.5f}], keep old SL")
                return
            new_sl = min(trail_sl, upper)
            new_sl = max(new_sl, lower)

        else:  # SELL
            trail_sl = peak + trail_distance
            # SL valide = au-dessus du marché
            if trail_sl <= current_price:
                # ← FIX Juin 2026: reset peak si le prix a retracé >1.5×ATR du trough
                retrace_atr = (current_price - peak) / max(atr_val, 1e-10)
                if retrace_atr > 1.5:
                    self.trailing_peaks[ticket] = current_price
                    logger.debug(f"  [TRAIL] {position.symbol}: retrace={retrace_atr:.1f}ATR "
                                 f"> 1.5 → trough reset to current={current_price:.5f}")
                else:
                    logger.debug(f"  [TRAIL] {position.symbol}: trail_sl={trail_sl:.5f} <= "
                                 f"current={current_price:.5f}, keep old SL (invalid for SELL)")
                return
            # Plage valide : [current_price + min_gap , entry_price]
            lower = current_price + min_gap
            upper = entry_price
            if lower > upper:
                logger.debug(f"  [TRAIL] {position.symbol}: no valid SL range "
                             f"[{lower:.5f}, {upper:.5f}], keep old SL")
                return
            new_sl = max(trail_sl, lower)
            new_sl = min(new_sl, upper)

        new_sl = round(new_sl, info.digits)
        if position.type == 0 and new_sl <= position.sl:
            return
        if position.type == 1 and new_sl >= position.sl:
            return

        old_sl = position.sl
        result = self.mt5.update_sl(position, new_sl)
        if result and result.retcode == 10009:
            try:
                position.sl = new_sl  # ← SYNC: échoue si TradePosition est tuple immuable
            except AttributeError:
                logger.debug(f"  [SYNC] {position.symbol} SL local sync skipped (readonly)")
            self.peak_profit[ticket] = profit_atr
            logger.info(f"TrailATR: {position.symbol} SL {old_sl}→{new_sl} "
                        f"(peak_profit={profit_atr:.1f}ATR, "
                        f"trail={trail_dist:.2f}ATR)")
            return
        rc = result.retcode if result else -1
        # Retry for retcode 10016 (invalid stops - too close to market)
        if rc == 10016:
            retry_gap = trail_distance + 2 * atr_val * jitter
            retry_sl = peak - retry_gap if position.type == 0 else peak + retry_gap
            retry_sl = round(retry_sl, info.digits)
            logger.debug(f"  [TRAIL] {position.symbol}: retcode=10016, retry with wider gap {retry_sl:.5f}")
            result2 = self.mt5.update_sl(position, retry_sl)
            if result2 and result2.retcode == 10009:
                try:
                    position.sl = retry_sl
                except AttributeError:
                    pass
                self.peak_profit[ticket] = profit_atr
                logger.info(f"TrailATR: {position.symbol} SL {old_sl}→{retry_sl} (retry OK, gap={retry_gap:.5f})")
                return
            else:
                rc2 = result2.retcode if result2 else -1
                logger.debug(f"  [TRAIL] {position.symbol}: retry aussi échoué retcode={rc2}")
        logger.debug(f"  [TRAIL] FAILED {position.symbol}: "
                     f"peak={peak:.5f} SL={position.sl:.5f} "
                     f"new_sl={new_sl:.5f} retcode={rc}")

    def _reconstruct_peak(self, position):
        """Trouve le vrai peak depuis l'ouverture du trade en scannant l'historique H1.
        Évite les peaks manqués quand le robot redémarre en milieu de trade.
        168 bougies H1 = 7 jours (couvre trades traversant le weekend).

        ⚠️ Filtre les bougies par date d'ouverture de la position : les bougies ANTÉRIEURES
        à l'ouverture du trade sont exclues. Sans ce filtre, une position ouverte à $4080
        verrait un « peak » à $4023 (plus bas des 7 jours) et le trailing penserait avoir
        1.7 ATR de profit immédiatement → SL serré à 0.03 du prix → fermeture instantanée.
        """
        try:
            rates = self.mt5.get_rates(position.symbol, "H1", count=168)
            if rates is not None and len(rates) > 5:
                # H-08: Filtrer les bougies ANTÉRIEURES à l'ouverture de la position
                # pour éviter qu'un pic historique (7 jours) ne fausse le trailing.
                pos_open_ts = None
                try:
                    pos_open_ts = position.time.timestamp()
                except (AttributeError, TypeError):
                    pass
                if pos_open_ts is not None:
                    # r[0] = timestamp d'ouverture de la bougie (int)
                    filtered = [r for r in rates if r[0] >= pos_open_ts]
                    if len(filtered) >= 2:
                        rates = filtered
                h = np.array([r[2] for r in rates], dtype=float)
                lo = np.array([r[3] for r in rates], dtype=float)
                if position.type == 0:  # BUY
                    return max(position.price_open, position.price_current, np.max(h))
                else:  # SELL
                    return min(position.price_open, position.price_current, np.min(lo))
        except Exception as e:
            logger.debug(f"Peak reconstruction failed for {position.symbol}: {e}")
        # Fallback: peak depuis prix courant seulement
        if position.type == 0:
            return max(position.price_open, position.price_current)
        else:
            return min(position.price_open, position.price_current)

    def _check_structure_exit(self, position):
        """Structure-based exit: ferme si BOS/CHoCH invalide la direction.
        Vérifie que la cassure de structure est POSTÉRIEURE à l'ouverture
        du trade (évite les faux départs sur structure déjà cassée)."""
        symbol = position.symbol
        # Cache TTL pour les rates H1 (rafraîchi toutes les 60s, mutualisé entre positions)
        now = time.time()
        cached = self._rates_cache.get(symbol)
        if cached and now - cached["time"] < 60:
            rates = cached["rates"]
        else:
            rates = self.mt5.get_rates(symbol, "H1", 30)
            if rates is not None and len(rates) >= 15:
                self._rates_cache[symbol] = {"rates": rates, "time": now}
        if rates is None or len(rates) < 15:
            return
        h1h = np.array([r[2] for r in rates], dtype=float)
        h1l = np.array([r[3] for r in rates], dtype=float)
        h1c = np.array([r[4] for r in rates], dtype=float)
        h1t = np.array([r[0] for r in rates], dtype=float)  # timestamps
        should_exit, reason, candle_idx = structure_exit_signal(
            position.type, h1h, h1l, h1c, window=5)
        if not should_exit or candle_idx is None:
            return
        # Vérifier que la cassure est POSTÉRIEURE à l'ouverture du trade
        try:
            pos_open_ts = position.time.timestamp()
            candle_ts = h1t[candle_idx]
            if candle_ts <= pos_open_ts:
                logger.info(f"Structure exit SKIP: {symbol} BOS @ candle #{candle_idx} "
                            f"({candle_ts}) <= open ({pos_open_ts}) — structure antérieure au trade")
                return
        except (AttributeError, IndexError, TypeError):
            # Si on ne peut pas déterminer, ne pas fermer (conservateur)
            return
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=position.volume,
            type=ct, position=position.ticket, price=price, deviation=20,
            magic=cfg.ROBOT_MAGIC, comment=f"STRUCT_{reason[:8]}")
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"Structure exit: {symbol} ({reason}) profit={position.profit:.2f}")
            # Le résultat sera enregistré par PositionTracker.check_closed au prochain cycle
            # (évite le double comptage si on enregistrait aussi ici)
        elif result and result.retcode != 10009:
            logger.warning(f"STRUCTURE EXIT FAILED {symbol}: retcode={result.retcode}")

    def _calc_sl_tp(self, symbol, entry, direction, atr_val=None, sl_mult=2.0, tp_mult=4.0):
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return None, None
        digits = info.digits
        if atr_val and atr_val > 0:
            min_dist = cfg.ATR_MULTIPLIER * atr_val
            # Randomisation ±5% pour éviter le stop clustering exploitable
            jitter_sl = 1.0 + random.uniform(-0.05, 0.05)
            jitter_tp = 1.0 + random.uniform(-0.05, 0.05)
            sl_dist = max(sl_mult * atr_val * jitter_sl, min_dist)
            tp_dist = max(tp_mult * atr_val * jitter_tp, min_dist)
        else:
            sl_dist = self.config.get("SL_PIPS", 15) * (0.0001 if "JPY" not in symbol else 0.01)
            tp_dist = sl_dist * self.config.get("TP_MULTIPLIER", 2.0)
        if direction == 0:
            return round(entry - sl_dist, digits), round(entry + tp_dist, digits)
        else:
            return round(entry + sl_dist, digits), round(entry - tp_dist, digits)

    def _reset_daily(self):
        now = datetime.utcnow()
        if now.date() != self.daily_stats.get("day"):
            self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": now.date()}
            self._daily_trades_per_symbol = {}
            self._opened_today = 0
            self._daily_profit_reduced = False
            account = self.mt5.get_account_info()
            if account:
                self.daily_start_equity = account.equity
            else:
                self.daily_start_equity = max(self.peak_equity, self.initial_balance)
