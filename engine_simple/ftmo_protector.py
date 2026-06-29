import logging
import random
import re
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import numpy as np

import config_simple as cfg
from engine_simple.challenge import ChallengeTracker
from engine_simple.ftmo_config import (
    ATR_CACHE_TTL,
    BE_BUFFER_BY_REGIME,
    FIRST_LOCK_ATR,
    TRAILING_BY_REGIME,
    RISK_MULT_CAP,
    DD_REDUCE_THRESHOLD,
    DD_CRITICAL_THRESHOLD,
    DD_AUTODISABLE_THRESHOLD,
    get_trailing_for_symbol,
    get_be_buffer_for_symbol,
)
from engine_simple.news_filter import is_news_blocked
from engine_simple.structure_analyzer import structure_exit_signal
from engine_simple.trailer import Trailer

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
        self._symbol_auto_disable_wr_threshold = 0.20

        # ── ChallengeTracker (P2.3) ──────────────────────────────────
        self.challenge = ChallengeTracker(mt5, config)
        # Expose state aliases for backward compat with Trailer / tests
        self.peak_equity = self.challenge.peak_equity
        self.daily_start_equity = self.challenge.daily_start_equity
        self.daily_stats = self.challenge.daily_stats
        self.consecutive_losses = self.challenge.consecutive_losses
        self.cooldowns = self.challenge.cooldowns
        self._symbol_consecutive_losses = self.challenge._symbol_consecutive_losses
        self._trade_history = self.challenge._trade_history
        self.trading_days = self.challenge.trading_days
        self.daily_pnl_by_date = self.challenge.daily_pnl_by_date
        self.consistency_violated = self.challenge.consistency_violated
        self.challenge_status = self.challenge.challenge_status
        self._daily_loss_violated = self.challenge._daily_loss_violated
        self._daily_trades_per_symbol = self.challenge._daily_trades_per_symbol
        self._opened_today = self.challenge._opened_today
        self._daily_profit_reduced = self.challenge._daily_profit_reduced
        self._symbol_trade_history = self.challenge._symbol_trade_history
        self.global_cooldown_until = None  # trading control, not challenge tracking

        # ── Position tracking (still in FTMOProtector) ───────────────
        self.position_open_times = {}
        self.partial_closed = set()
        self.peak_profit = {}
        self.trailing_peaks = {}
        self.position_regime = {}
        self.position_meta = {}
        self._time_stop_cooldown = {}
        self._atr_cache = {}
        self._rates_cache = {}

        # ── Correlation ──────────────────────────────────────────────
        self._position_cache_ttl = 60
        self._last_position_fetch = 0.0

        # ── ADX market filter ────────────────────────────────────────
        self._adx_cache_ts = 0.0
        self._adx_cache_mult = 1.0
        self._adx_cache_ttl = 900

        # ── Profile cache (for _get_profile) ─────────────────────────
        self._profile_cache = {}

        # ── Auto-stop (ranging market) ──────────────────────────────
        self._auto_stop_paused = False
        self._auto_stop_until = None

        # ── Trailer (delegated) ──────────────────────────────────────
        self.trailer = Trailer(mt5, config)
        self.trailer.partial_closed = self.partial_closed
        self.trailer.trailing_peaks = self.trailing_peaks
        self.trailer.position_regime = self.position_regime
        self.trailer.position_meta = self.position_meta
        self.trailer.position_open_times = self.position_open_times
        self.trailer.peak_profit = self.peak_profit

    def check_price_staleness(self, symbol, max_age=60):
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return False
        tick_time = getattr(tick, "time", None)
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
                raw = getattr(p, "time", None)
                if raw is None:
                    open_time = datetime.utcnow()
                elif isinstance(raw, (int, float)):
                    open_time = datetime.utcfromtimestamp(raw)
                else:
                    open_time = raw
                self.position_open_times[ticket_key] = {"open_time": open_time, "symbol": p.symbol}
            if ticket_key not in self.position_regime:
                comment = getattr(p, "comment", "") or ""
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

    # Corrélations RÉACTIVÉES — 25 Juin 2026 (Risk & Compliance Officer)
    # Vérifications de corrélation via portfolio_controller.py (groupes de corrélation).
    # Max 3 trades/groupe, max 2/direction. Limite les pertes simultanées.

    def _get_profile(self, symbol):
        """Retourne le profil institutionnel du symbole (caché)."""
        if symbol not in self._profile_cache:
            try:
                from engine_simple.symbol_profile import get_profile

                self._profile_cache[symbol] = get_profile(symbol)
            except (ImportError, RuntimeError, KeyError):
                self._profile_cache[symbol] = None
        return self._profile_cache[symbol]

    def can_trade(self, symbol, signal=None, positions=None, check_danger_hours=True):
        """Vérifie si un trade est autorisé pour le symbole.

        Pipeline de vérification (chaque étape peut bloquer) :
          0. _check_auto_stop         — pause auto-ranging (MarketMemory)
          1. _check_symbol_health    — auto-disable si WR < 20%
          2. _check_spread           — spread > max ou > 10% ATR
          3. _check_global_cooldown  — pause 30min après N pertes consécutives
          4. _check_daily_limits     — max trades/jour (ouverts + fermés)
          5. _check_signal_valid     — direction, corrélation, score, SL/TP
          6. _check_risk_state       — volatility spike, DD, daily loss zones, auto-pause
          7. _check_profile          — ranging restriction, DL required, ATR scaling
          8. _check_session          — news, danger hours, session, preferred hours, weekend
          9. _check_ftmo_status      — expiry, profit target, consistency

        Args:
            symbol: symbole à trader
            signal: dict du signal MOM20x3 (optionnel, requis pour bypass DANGER_HOURS)
            positions: liste des positions actuelles (optionnel)
            check_danger_hours: False pour la pré-vérification sans signal (main.py première passe)
        """
        # _reset_daily() est appelé dans _scan_signals() (main.py) — une fois par cycle

        for check in (
            lambda: self._check_auto_stop(),
            lambda: self._check_symbol_health(symbol, signal),
            lambda: self._check_spread(symbol),
            lambda: self._check_global_cooldown(),
            lambda: self._check_daily_limits(),
            lambda: self._check_signal_valid(symbol, signal, positions),
            lambda: self._check_risk_state(symbol, signal),
            lambda: self._check_profile(symbol, signal),
            lambda: self._check_session(symbol, signal, check_danger_hours),
            lambda: self._check_ftmo_status(),
        ):
            ok, reason = check()
            if not ok:
                return ok, reason

        return True, "OK"

    # ── Sub-checks (extraites de can_trade pour lisibilité) ─────────

    def _check_auto_stop(self):
        """🔒 AUTO-STOP : pause auto-ranging basée sur ADX moyen des symboles."""
        try:
            from engine_simple.auto_stop import decision

            # 🐛 FIX 26 Juin 2026: passer self.mt5 au lieu de laisser auto_stop
            # créer sa propre connexion (MT5Connector() sans arguments plantait)
            verdict, state = decision(mt5_connector=self.mt5)
            if verdict == "STOP":
                self._auto_stop_paused = True
                self._auto_stop_until = state.get("auto_paused_until")
                return False, f"AUTO_STOP: Trading paused (ranging market, until {self._auto_stop_until})"
            elif verdict == "RESUME":
                self._auto_stop_paused = False
                self._auto_stop_until = None
            elif verdict == "WAIT" and self._auto_stop_paused:
                # 🐛 FIX 26 Juin 2026: utiliser state.get() au lieu de self._auto_stop_until
                # car ce dernier n'est pas mis à jour par les prolongations de pause (15min).
                actual_until = state.get("auto_paused_until", self._auto_stop_until)
                self._auto_stop_until = actual_until  # sync pour prochains cycles
                return False, f"AUTO_STOP: Still paused until {actual_until}"
        except ImportError:
            pass  # auto_stop module non disponible
        except Exception as e:
            logger.debug(f"  [AUTO-STOP] erreur: {e}")
        return True, None

    def _check_symbol_health(self, symbol, signal):
        """🔒 AUTO-DISABLE : symbole avec WR < 20% sur les 20 derniers trades."""
        if signal is None:
            return True, None
        sym_history = self._symbol_trade_history.get(symbol, [])
        last20 = [t for t in sym_history[-20:] if t.get("profit", 0) != 0]
        if len(last20) >= 10:
            wins = sum(1 for t in last20 if t["profit"] > 0)
            wr = wins / len(last20)
            if wr < DD_AUTODISABLE_THRESHOLD:
                return False, (
                    f"[AUTO-DISABLE] {symbol} WR={wr:.0%} sur {len(last20)} trades "
                    f"< {self._symbol_auto_disable_wr_threshold:.0%}"
                )
        return True, None

    def _check_spread(self, symbol):
        """Spread check — points absolus + ratio ATR (max 10% de l'ATR)."""
        info = self.mt5.get_symbol_info(symbol)
        if not (info and hasattr(info, "point") and info.point > 0):
            return False, f"Cannot get symbol info for {symbol}"
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return False, f"No tick data for {symbol} — spread check impossible"
        spread = tick.ask - tick.bid
        sym_cfg = self.symbol_limits.get(symbol, {})
        max_sp = sym_cfg.get("max_spread_points", self.max_spread_points)
        spread_pts_ok = spread < max_sp * info.point * 1.05
        atr_val = self.trailer._get_atr(symbol)
        atr_ok = True
        if atr_val and atr_val > 0:
            if spread / atr_val > 0.10:
                atr_ok = False
        if not spread_pts_ok and not atr_ok:
            return False, (
                f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f}, ATR ratio={spread / atr_val:.1%})"
                if atr_val
                else f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f})"
            )
        return True, None

    def _check_global_cooldown(self):
        """🔒 Global cooldown: pause après AUTO_PAUSE_LOSSES pertes consécutives."""
        if self.global_cooldown_until is None:
            return True, None
        now = datetime.utcnow()
        if now < self.global_cooldown_until:
            remaining = int((self.global_cooldown_until - now).total_seconds() // 60)
            return False, f"Global cooldown: {remaining}min (after {self.consecutive_losses} consecutive losses)"
        # Cooldown expired → reset
        logger.info(f"Global cooldown expired — reseting consecutive_losses from {self.consecutive_losses} to 0")
        self.consecutive_losses = 0
        self.global_cooldown_until = None
        return True, None

    def _check_daily_limits(self):
        """Max trades/jour (fermés + ouverts)."""
        max_trades = self.config.get("MAX_TRADES_PER_DAY", 5)
        if self.daily_stats["trades"] >= max_trades:
            return False, f"Daily trade limit (closed: {self.daily_stats['trades']}/{max_trades})"
        if self._opened_today >= max_trades:
            return False, f"Daily trade limit (opened: {self._opened_today}/{max_trades})"
        return True, None

    def _check_signal_valid(self, symbol, signal, positions):
        """Direction restrictions, corrélation, score minimum, SL/TP obligatoire."""
        if signal is None:
            return True, None

        # Direction restrictions
        sym_cfg = self.symbol_limits.get(symbol, {})
        if not sym_cfg.get("allow_shorts", True) and signal.get("action") == "SELL":
            return False, f"Shorts not allowed on {symbol} (per-symbol config)"
        if not sym_cfg.get("allow_buys", True) and signal.get("action") == "BUY":
            return False, f"Buys not allowed on {symbol} (per-symbol config)"

        # P4: Correlation groups — supprimé Juin 2026
        # La corrélation est centralisée dans portfolio_controller.py
        # (CORR_GROUPS avec FOREX_MAJORS 7 sym, CRYPTO, COMMODITIES, INDICES)
        # Ce bloc était incomplet (crypto seulement) → délégué à portfolio_controller

        # Signal quality gate (per-symbol override)
        min_score = self.config.get("MIN_SIGNAL_SCORE", 0.55)
        sym_min_score = sym_cfg.get("min_score")
        if sym_min_score is not None:
            min_score = sym_min_score
        sig_score = signal.get("score", 0)
        if sig_score < min_score:
            return False, f"Signal score too low: {sig_score:.2f} < {min_score}"

        # 🔥 SYMBOL_CONFIDENCE_GATES — gate de confiance par symbole (29 Juin 2026)
        # Chaque symbole a son propre seuil minimum de confiance :
        #   CORE (XAUUSD, BTCUSD, US30.cash) : pas de gate (trading normal)
        #   TARGET_80 (ETHUSD, US100.cash, US500.cash, XAGUSD) : gate 0.80
        #   REACTIVATED (forex majeurs) : gate 0.90
        # Le signal doit avoir confidence >= gate pour être tradé.
        # Le flag high_confidence (calculé par signal_pipeline.py) est utilisé
        # pour le bypass des limites de positions (pas pour le gate lui-même).
        _gates = self.config.get("SYMBOL_CONFIDENCE_GATES", {})
        conf_gate = _gates.get(symbol, 0.0)
        if conf_gate > 0:
            sig_confidence = signal.get("confidence", 0.0)
            if sig_confidence < conf_gate - 0.0001:  # floating point guard
                return False, (f"Confidence gate {symbol}: conf={sig_confidence:.2f} < gate={conf_gate:.2f}")

        # FIX #3: SL OBLIGATOIRE — calcul auto si ATR disponible, sinon blocage
        sl = signal.get("sl")
        tp = signal.get("tp")
        if sl is None or tp is None or sl == 0 or tp == 0:
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
                    logger.debug(
                        f"  [SL_CALC] {symbol}: entry={entry:.2f} atr={atr:.4f} "
                        f"sl_atr={sl_atr:.2f} tp_atr={tp_atr:.2f} dir={direction}"
                    )
                    new_sl, new_tp = self.trailer.calc_sl_tp(symbol, entry, direction, atr, sl_atr, tp_atr)
                    if new_sl is not None and new_tp is not None and new_sl > 0 and new_tp > 0:
                        signal["sl"] = new_sl
                        signal["tp"] = new_tp
                        sl, tp = new_sl, new_tp
            except Exception as exc:
                logger.debug(f"  [SL CALC] {symbol}: echec calcul SL={sl} TP={tp}: {exc}")
            if sl is None or tp is None or sl == 0 or tp == 0:
                return False, f"SL/TP manquant — transaction BLOQUÉE (SL={sl}, TP={tp})"

        # Ajuster SL si order block non mitigé proche
        obs = signal.get("_structure_obs", [])
        if obs and sl and entry:
            for ob in obs:
                if not ob.get("is_mitigated"):
                    ob_high = ob.get("high", 0)
                    ob_low = ob.get("low", 0)
                    ob_type = ob.get("type", "")
                    # BUY: SL ne doit pas être dans un OB haussier non mitigé (support faible)
                    if action == "BUY" and ob_type == "bullish" and ob_low > 0:
                        if sl < ob_high and sl > ob_low * 0.99:
                            # SL est dans la zone de l'OB → ajuster en dessous
                            new_sl = ob_low - (ob_high - ob_low) * 0.1
                            if new_sl > 0:
                                logger.debug(
                                    f"  [SL OB] {symbol}: SL ajusté {sl:.5f} → {new_sl:.5f} (sous OB haussier)"
                                )
                                sl = new_sl
                                signal["sl"] = sl
                    # SELL: SL ne doit pas être dans un OB baissier non mitigé (résistance forte)
                    elif action == "SELL" and ob_type == "bearish" and ob_high > 0:
                        if sl > ob_low and sl < ob_high * 1.01:
                            new_sl = ob_high + (ob_high - ob_low) * 0.1
                            if new_sl > 0:
                                logger.debug(
                                    f"  [SL OB] {symbol}: SL ajusté {sl:.5f} → {new_sl:.5f} (dessus OB baissier)"
                                )
                                sl = new_sl
                                signal["sl"] = sl

        # Price staleness
        if not self.check_price_staleness(symbol):
            return False, "Stale price: tick > 60s"

        return True, None

    def _check_risk_state(self, symbol, signal):
        """Volatility spike, DD circuit breaker, daily loss zones, auto-pause, cooldown."""
        # Volatility spike
        atr_pct = signal.get("atr_pct", 0) if signal else 0
        if atr_pct > 0:
            atr_median = signal.get("atr_median_14", atr_pct)
            if atr_median > 0 and atr_pct / atr_median > 3.0:
                return False, f"Volatility spike: ATR%={atr_pct:.3f} vs median={atr_median:.3f} (>3x)"

        # Account info
        account = self.mt5.get_account_info()
        if account is None:
            return False, "Cannot get account info"
        current_equity = account.equity

        # Daily profit limit → risk reduction mode
        daily_equity_change = current_equity - self.daily_start_equity
        daily_pnl_pct = daily_equity_change / max(self.initial_balance, 1)
        profit_limit = self.config.get("DAILY_PROFIT_LIMIT_PCT", 0.003)
        if daily_pnl_pct >= profit_limit:
            self._daily_profit_reduced = True
            logger.info(
                f"  [PROFIT LIMIT] daily PnL ${self.daily_stats['pnl']:.0f} "
                f"({daily_pnl_pct:.3%}) >= {profit_limit:.3%} — risk reduit a 25%"
            )
        else:
            self._daily_profit_reduced = False
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self.challenge.peak_equity = current_equity  # Sync challenge tracker

        # Daily loss flag
        self._check_daily_loss_limit(symbol=symbol)

        # DD from peak
        dd_peak = (self.peak_equity - current_equity) / max(self.peak_equity, 1)

        # Circuit breaker progressif (3 niveaux)
        cb_threshold = self.config.get("CIRCUIT_BREAKER_DD_PCT", 0.08)
        sym_cfg_cb = self.symbol_limits.get(symbol, {})
        sym_cb_override = sym_cfg_cb.get("circuit_breaker_dd_pct_override")
        if sym_cb_override is not None:
            cb_threshold = sym_cb_override
        # Niveau 1: DD > 6% → shorts interdits (avertissement)
        dd_warn = 0.06
        if dd_peak > dd_warn and dd_peak <= cb_threshold and signal and signal.get("action") == "SELL":
            return False, f"DD warning {dd_peak:.1%} > {dd_warn:.0%}: shorts disabled"
        # Niveau 2: DD > seuil cb → TOUS les trades bloqués
        if dd_peak > cb_threshold:
            return False, f"Circuit breaker: DD {dd_peak:.1%} > {cb_threshold:.0%}, all trades blocked"

        # FTMO daily loss limit
        daily_loss = max(0, -daily_equity_change) / self.initial_balance
        if self._daily_loss_violated:
            return False, f"FTMO daily loss limit: {daily_loss:.1%}"

        # Zone 3: >1.5% daily loss → stop
        zone3 = self.config.get("ZONE3_LOSS_PCT", 0.015)
        if daily_loss >= zone3 and self.daily_stats["losses"] > 0:
            return False, f"Zone 3: daily DD {daily_loss:.1%} >= {zone3:.1%}, stop"

        # Auto-pause après N pertes consécutives
        auto_pause = self.config.get("AUTO_PAUSE_LOSSES", 6)
        if self.consecutive_losses >= auto_pause:
            if self.global_cooldown_until is None:
                auto_pause_cooldown = self.config.get("COOLDOWN_MINUTES", 15)
                self.global_cooldown_until = datetime.utcnow() + timedelta(minutes=auto_pause_cooldown)
                logger.warning(
                    f"AUTO PAUSE: {self.consecutive_losses} consecutive losses >= {auto_pause}, "
                    f"global cooldown {auto_pause_cooldown}min jusqu'à {self.global_cooldown_until}"
                )
                return (
                    False,
                    f"Global cooldown: {auto_pause_cooldown}min (after {self.consecutive_losses} consecutive losses)",
                )
            # Cooldown déjà actif — vérifier expiration
            now = datetime.utcnow()
            if now < self.global_cooldown_until:
                remaining = int((self.global_cooldown_until - now).total_seconds() // 60)
                return False, f"Global cooldown: {remaining}min (after {self.consecutive_losses} consecutive losses)"
            # Cooldown expiré → reset comme _check_global_cooldown()
            logger.info(f"Global cooldown expired — reseting consecutive_losses from {self.consecutive_losses} to 0")
            self.consecutive_losses = 0
            self.global_cooldown_until = None

        # Per-symbol cooldown
        if symbol in self.cooldowns and datetime.utcnow() < self.cooldowns[symbol]:
            remaining = (self.cooldowns[symbol] - datetime.utcnow()).seconds // 60
            return False, f"Cooldown: {remaining}min"

        return True, None

    def _check_profile(self, symbol, signal):
        """Institutional profile: ranging restriction, DL required, ATR scaling."""
        if signal is None:
            return True, None
        profile = self._get_profile(symbol)
        if not profile:
            return True, None
        sym_cfg = self.symbol_limits.get(symbol, {})

        # Ranging restriction
        if signal.get("_is_ranging", True) is not False:
            allow_ranging = sym_cfg.get("allow_ranging")
            if allow_ranging is False and signal.get("_regime") == "RANGING":
                return False, f"{symbol}: ranging trades not allowed (per-symbol config)"

        # DL required
        if sym_cfg.get("dl_required", False) and signal.get("_ml_agrees") is not True:
            return False, f"{symbol}: DL agreement required but not confirmed"

        # Per-symbol max daily trades
        max_daily = sym_cfg.get("max_daily_trades")
        if max_daily is not None:
            sym_daily = self._daily_trades_per_symbol.get(symbol, 0)
            if sym_daily >= max_daily:
                return False, f"{symbol}: daily limit ({max_daily})"

        # Profile-based ATR validation
        atr_val = signal.get("atr", 0)
        if atr_val > 0:
            from engine_simple.symbol_profile import get_atr_scaling

            scaling = get_atr_scaling(symbol, atr_val)
            if scaling < 0.75:
                return False, f"{symbol}: ATR {atr_val:.5f} trop eleve pour profil"

        return True, None

    def _check_session(self, symbol, signal, check_danger_hours):
        """News, danger hours, session block, preferred hours, weekend block."""
        # News filter — returns (blocked: bool, reason: str)
        news_blocked, news_reason = is_news_blocked(symbol=symbol)
        if news_blocked:
            return False, f"News: {news_reason}"

        utc_hour = datetime.utcnow().hour

        # Danger hours (bypass si signal fort: score ≥ 0.80, ADX ≥ 15)
        danger_hours = self.config.get("DANGER_HOURS", [])
        if utc_hour in danger_hours and check_danger_hours:
            if signal is not None and signal.get("score", 0) >= 0.80 and signal.get("adx", 0) >= 15:
                logger.debug(
                    f"  [DANGER] {symbol}: bypass DANGER_HOUR ({utc_hour}h UTC) "
                    f"pour signal fort (score={signal.get('score', 0):.2f}, ADX={signal.get('adx', 0):.1f})"
                )
            else:
                return False, f"Danger hour: {utc_hour}h UTC (0% WR historique sur ce créneau)"

        # Session block
        start_hour = self.config.get("TRADING_START_HOUR", 0)
        end_hour = self.config.get("TRADING_END_HOUR", 24)
        if not (start_hour <= utc_hour < end_hour):
            return False, f"Session block: {utc_hour}h UTC (trade only {start_hour}-{end_hour}h UTC)"

        # Per-symbol preferred hours
        if signal is not None:
            pref_hours = self.symbol_limits.get(symbol, {}).get("preferred_hours")
            if pref_hours is not None and len(pref_hours) > 0 and utc_hour not in pref_hours:
                return False, f"{symbol}: not in preferred hours {pref_hours}h UTC"

        # Per-symbol weekend block (XAUUSD = 24/5, BTC/ETH = 24/7)
        weekend_ok = self.symbol_limits.get(symbol, {}).get("weekend_trading", True)
        if not weekend_ok and datetime.utcnow().weekday() >= 5:
            return False, f"{symbol}: weekend block (24/5 — pas de trading samedi/dimanche)"

        return True, None

    def _check_ftmo_status(self):
        """Challenge expiry, profit target, consistency."""
        # Max trading days
        if self.max_trading_days > 0 and len(self.trading_days) >= self.max_trading_days:
            self.challenge_status = "FAILED_EXPIRY"
            return False, f"FTMO: maximum trading days ({self.max_trading_days}) atteint — challenge expiré"

        # Profit target (realized PnL only)
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

        # Consistency violated → stop avant target (80% seuil de sécurité)
        # Permet la dilution : le PnL total continue de croître, ce qui réduit
        # progressivement best_day_pct jusqu'à repasser sous 30%.
        if self.consistency_violated and current_pnl >= profit_target_amount * 0.8:
            self.challenge_status = "FAILED_CONSISTENCY"
            return False, "FTMO FAILED: consistency violated — stop avant target"

        return True, None

    def reset_challenge(self, new_initial_balance=None):
        """Reset challenge state. Delegates to ChallengeTracker."""
        self.challenge.reset_challenge(new_initial_balance)
        # Sync aliases
        self.peak_equity = self.challenge.peak_equity
        self.daily_start_equity = self.challenge.daily_start_equity
        self.consecutive_losses = self.challenge.consecutive_losses
        self.challenge_status = self.challenge.challenge_status

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
                mult *= 1.25  # Bon WR → lots +25%
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
                mult *= 0.80  # DD > 7% → -20%
            elif dd > 0.05:
                mult *= 0.75  # DD > 5% → -25%
            elif dd > 0.03:
                mult *= 0.90  # DD > 3% → -10%

        # 3. Pertes consécutives (utilise AUTO_PAUSE_LOSSES de la config)
        auto_pause = self.config.get("AUTO_PAUSE_LOSSES", 6)
        if self.consecutive_losses >= auto_pause:
            mult *= 0.50  # Pause imminente → risque réduit de moitié
        elif self.consecutive_losses >= max(3, auto_pause - 2):
            mult *= 0.65  # Proche du seuil → risque réduit
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
            mult *= 1.30
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
                tr = np.maximum(
                    high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
                )
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
            logger.warning(
                f"  [ADX FILTER] {low_adx_count}/{total_checked} symboles ADX<22 "
                f"({ratio:.0%}) → RISK × 0.50 (marché RANGING)"
            )
        else:
            self._adx_cache_mult = 1.0
            logger.info(f"  [ADX FILTER] {low_adx_count}/{total_checked} symboles ADX<22 ({ratio:.0%}) → risque normal")

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
            mult = 1.35
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

    def calculate_lot(self, symbol, entry, sl, quality=1.0, direction=0, signal_risk_mult=None):
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
        if dd_peak > DD_REDUCE_THRESHOLD:
            risk_amount *= 1 - dd_peak
        # 🔒 CRITIQUE: réduction agressive au-delà de 7% DD (proche du max 10% FTMO)
        if dd_peak > DD_CRITICAL_THRESHOLD:
            risk_amount *= 0.20  # ×0.20 au lieu de ×0.93 → 80% de réduction
            logger.warning(f"  [DD CRITICAL] {symbol}: DD peak {dd_peak:.1%} > 7% → risk ×0.20")
        risk_amount *= quality

        # Friday risk reduction SUPPRIMÉE — mode 24/7

        # 🔒 FIX C1: Utiliser le risk_mult du signal (OL→Adaptive→Anticipation→Kelly)
        # au lieu de la config statique. Cap par symbole pour éviter le sure-sizing.
        # Per-symbol risk_mult cap: XAUUSD=1.25, BTCUSD=1.00, US500.cash=1.15, ETHUSD=1.00
        if signal_risk_mult is not None and signal_risk_mult > 0:
            # Utiliser le risk_mult du signal, capé par symbole
            sym_cap = RISK_MULT_CAP.get(symbol, 1.0)
            final_rm = max(0.1, min(signal_risk_mult, sym_cap))
            if final_rm != signal_risk_mult:
                logger.debug(
                    f"  [RISK] {symbol}: signal_risk_mult={signal_risk_mult:.3f} capé à {sym_cap} → {final_rm:.3f}"
                )
            risk_amount *= final_rm
            logger.debug(
                f"  [RISK] {symbol}: base=${risk_amount / final_rm:.2f} × risk_mult={final_rm:.3f} = ${risk_amount:.2f}"
            )
        else:
            # Fallback: config statique si signal_risk_mult non fourni
            risk_amount *= self.symbol_limits.get(symbol, {}).get("risk_mult", 1.0)

        # Per-symbol performance multiplier (rolling WR tracker)
        perf_mult = self._get_symbol_perf_risk_mult(symbol)
        risk_amount *= perf_mult

        # Absolute max risk cap (if configured)
        if self.max_risk_amount > 0:
            risk_amount = min(risk_amount, self.max_risk_amount)

        # Zone 2: >1% daily loss → risk × 0.75 (uniquement sur pertes réelles, pas sur gains)
        daily_loss_amt = max(0, -self.daily_stats["pnl"])
        zone2 = self.config.get("ZONE2_LOSS_PCT", 0.01)
        if daily_loss_amt > 0 and (daily_loss_amt / max(self.initial_balance, 1)) >= zone2:
            risk_amount *= 0.75
            logger.debug(f"  [ZONE 2] daily loss {daily_loss_amt / self.initial_balance:.2%} >= {zone2:.1%}, risk 75%")

        sym_cfg = self.symbol_limits.get(symbol, {})
        max_lot = sym_cfg.get("max_lot", 0.55)
        min_lot = sym_cfg.get("min_lot", 0.01)
        lot_size = self.config.get("LOT_SIZE", 0.1)

        order_type = self.mt5.ORDER_TYPE_BUY if direction == 0 else self.mt5.ORDER_TYPE_SELL
        sl_profit = self.mt5.calc_profit(order_type, symbol, 0.1, entry, sl)
        if sl_profit is not None and sl_profit < 0:
            risk_per_01 = abs(sl_profit)
            if risk_per_01 < 1.0:
                lot = lot_size  # fallback silencieux (marché fermé ou SL trop serré)
            else:
                lot = (risk_amount / risk_per_01) * 0.1
        else:
            lot = lot_size

        # Adaptive lot multiplier (performance-based)
        adaptive_mult = self._adaptive_lot_mult()
        lot *= adaptive_mult
        logger.debug(
            f"  [ADAPTIVE LOT] {symbol}: lot pré-clamp={lot:.3f} (risk_per_01=${sl_profit if sl_profit else 0:.2f})"
        )

        # Safety: si le lot calculé est absurde (> 3× max_lot), forcer LOT_SIZE
        if lot > max_lot * 3:
            logger.warning(f"[LOT SAFETY] {symbol}: lot={lot:.3f} > 3×max_lot={max_lot} → force lot={lot_size}")
            lot = lot_size

        # Clamp between min_lot and max_lot from symbol config
        lot = max(min_lot, min(lot, max_lot))

        # 🔒 SECOND CLAMP : re-vérifier avec la config module-level (fraîche)
        try:
            import config_simple as _cfg

            if hasattr(_cfg, "SYMBOL_LIMITS"):
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
        "TRE": "TREND_UP",
        "DOW": "TREND_DOWN",
        "RAN": "RANGING",
        "HIG": "HIGH_VOL",
        "LOW": "LOW_VOL",
    }

    def register_open_trade(self, symbol=None):
        """Enregistre un trade qui VIENT d'être ouvert.
        Permet au MAX_TRADES_PER_DAY de compter aussi les trades ouverts,
        pas seulement les fermés (était la cause des 222 trades/jour)."""
        self._opened_today += 1
        self._reset_daily()  # reset si jour a changé

    def refresh_symbol_limits(self):
        """Recharge les symbol_limits depuis la config globale.
        Charge DIRECTEMENT depuis les YAML (contourne le mtime check buggé)."""
        try:
            # Charge frais depuis YAML en contournant le cache/hot-reload
            from config.schema import _load_yaml, _interpolate, _deep_merge, ConfigSchema
            from pathlib import Path

            # Vide le cache LRU de _load_yaml pour garantir des données fraîches
            _load_yaml.cache_clear()
            logger.debug("[CONFIG] cache YAML vidé pour rechargement frais")

            config_dir = Path(__file__).parent.parent / "config"
            default_path = config_dir / "default.yaml"
            env_path = config_dir / "production.yaml"

            raw = _load_yaml(default_path)
            raw = _interpolate(raw)
            if env_path.exists():
                raw = _deep_merge(raw, _interpolate(_load_yaml(env_path)))
            cfg = ConfigSchema(**raw)

            # Met à jour symbol_limits depuis le frais
            fresh_limits = {sym: lim.model_dump(exclude_none=True) for sym, lim in cfg.symbol_limits.items()}
            self.symbol_limits = fresh_limits
            logger.info(f"[CONFIG] symbol_limits rechargées (frais): {len(fresh_limits)} symboles")

            # Met à jour DANGER_HOURS depuis cfg
            self.config["DANGER_HOURS"] = cfg.trading.danger_hours
        except Exception as e:
            logger.warning(f"[CONFIG] refresh_symbol_limits failed: {e}")
            import traceback

            logger.warning(traceback.format_exc())

    def check_invariants(self, position):
        ticket_key = str(position.ticket)
        if ticket_key not in self.position_open_times:
            open_time = getattr(position, "time", None) or datetime.utcnow()
            self.position_open_times[ticket_key] = {"open_time": open_time, "symbol": position.symbol}
        if ticket_key not in self.position_regime:
            comment = getattr(position, "comment", "") or ""
            self._parse_comment_regime(comment, ticket_key)
        self._prune_position_times()
        # Chaque sous-vérification est protégée individuellement :
        # le "readonly attribute" d'MT5 survient si la position a été modifiée
        # entre la lecture et l'envoi (ex: partial TP puis trailing dans le même cycle)
        subs = [
            ("time_stop", self.trailer._check_time_stop),
            ("partial_tp", self.trailer._check_partial_tp),
            ("step_trail", self.trailer._check_step_trailing),
            ("structure", self.trailer._check_structure_exit),
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
                old = sorted(self.position_open_times.keys(), key=lambda k: self.position_open_times[k]["open_time"])[
                    :-150
                ]
                for k in old:
                    del self.position_open_times[k]
            except Exception as e:
                logger.warning(f"Prune failed: {e}")
                self.position_open_times = dict(list(self.position_open_times.items())[-150:])

    def record_trade_result(self, symbol, profit, historical=False, trade_time=None):
        """Enregistre le résultat d'un trade fermé. Delegates to ChallengeTracker."""
        self.challenge.record_trade_result(symbol, profit, historical, trade_time=trade_time)
        # Sync aliases
        self.consecutive_losses = self.challenge.consecutive_losses
        self.challenge_status = self.challenge.challenge_status

    def _check_consistency(self):
        """FTMO consistency rule. Delegates to ChallengeTracker."""
        # Sync state in case tests/code reassigned aliases
        self.challenge.daily_pnl_by_date = self.daily_pnl_by_date
        self.challenge._check_consistency()
        self.consistency_violated = self.challenge.consistency_violated

    def _check_daily_loss_limit(self, symbol=None):
        """Daily loss limit check. Delegates to ChallengeTracker."""
        self.challenge._check_daily_loss_limit(symbol)
        self._daily_loss_violated = self.challenge._daily_loss_violated
        self.challenge_status = self.challenge.challenge_status

    def current_dd_pct(self):
        """Current drawdown %. Delegates to ChallengeTracker."""
        return self.challenge.current_dd_pct()

    def _check_drawdown_limit(self):
        """Drawdown limit check. Delegates to ChallengeTracker."""
        self.challenge._check_drawdown_limit()
        self.challenge_status = self.challenge.challenge_status

    def _prune_histories(self):
        """Prune both challenge and position histories."""
        # Sync in case alias was broken by reassignment
        self.challenge._trade_history = self._trade_history
        self.challenge._prune_histories()
        # Re-sync after pruning (may have been replaced)
        self._trade_history = self.challenge._trade_history
        # Position-level pruning (stays in FTMOProtector)
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
        """Progress report. Delegates to ChallengeTracker."""
        return self.challenge.get_progress_report()

    # ── Trailing & Exit — delegated to Trailer (shared state) ─────────

    def _pip_offset(self, symbol, pips=10):
        return self.trailer._pip_offset(symbol, pips)

    def _check_partial_tp(self, position):
        return self.trailer._check_partial_tp(position)

    def _check_time_stop(self, position):
        return self.trailer._check_time_stop(position)

    def _get_atr(self, symbol, period=14):
        return self.trailer._get_atr(symbol, period)

    def _check_step_trailing(self, position):
        return self.trailer._check_step_trailing(position)

    def _reconstruct_peak(self, position):
        return self.trailer._reconstruct_peak(position)

    def _check_structure_exit(self, position):
        return self.trailer._check_structure_exit(position)

    def _calc_sl_tp(self, symbol, entry, direction, atr_val=None, sl_mult=2.0, tp_mult=4.0):
        return self.trailer.calc_sl_tp(symbol, entry, direction, atr_val, sl_mult, tp_mult)

    def _reset_daily(self):
        """Reset daily stats. Delegates to ChallengeTracker."""
        self.challenge._reset_daily()
        self.daily_stats = self.challenge.daily_stats
        self.daily_start_equity = self.challenge.daily_start_equity
