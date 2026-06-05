import logging
import re
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import numpy as np

import config_simple as cfg
from engine_simple.news_filter import is_news_blocked
from engine_simple.structure_analyzer import structure_exit_signal

logger = logging.getLogger("ftmo")

# Niveaux de trailing par régime : liste de (profit_atr_seuil, trail_distance_mult)
# Format : seuil_profit_ATR -> multiplier ATR pour la distance du trailing SL
TRAILING_BY_REGIME = {
    "TREND_UP":    [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "TREND_DOWN":  [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "RANGING":     [(1.00, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
    "HIGH_VOL":    [(1.00, 1.00), (2.00, 0.70), (3.00, 0.50), (5.00, 0.25)],
    "LOW_VOL":     [(1.00, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
}

BE_BUFFER_BY_REGIME = {
    "TREND_UP":    0.60,
    "TREND_DOWN":  0.60,
    "RANGING":     0.80,
    "HIGH_VOL":    1.00,
    "LOW_VOL":     0.50,
}

ATR_CACHE_TTL = 60  # secondes avant de rafraîchir l'ATR en cache


class FTMOProtector:
    def __init__(self, mt5, config):
        self.mt5 = mt5
        self.config = config

        self.initial_balance = config.get("INITIAL_BALANCE", 200000)
        self.max_dd_pct = config.get("MAX_DD_PCT", 0.10)
        self.max_daily_loss_pct = config.get("MAX_DAILY_LOSS_PCT", 0.05)
        self.profit_target_pct = config.get("PROFIT_TARGET_PCT", 0.10)
        self.consistency_max_pct = config.get("CONSISTENCY_MAX_PCT", 0.30)
        self.min_trading_days = config.get("MIN_TRADING_DAYS", 10)
        self.max_trading_days = config.get("MAX_TRADING_DAYS", 0)
        self.max_spread_points = config.get("MAX_SPREAD_POINTS", 30)
        self.cooldown_minutes = config.get("COOLDOWN_MINUTES", 15)
        self.symbol_limits = config.get("SYMBOL_LIMITS", {})
        self.max_risk_amount = config.get("MAX_RISK_AMOUNT", 0)

        self.peak_equity = self.initial_balance
        self.daily_start_equity = self.initial_balance
        self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": datetime.utcnow().date()}
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
        self._atr_cache = {}  # symbol -> (value, timestamp) pour TTL cache
        self._rates_cache = {}  # symbol -> (rates_array, timestamp) pour structure exit cache

        self.daily_pnl_by_date = {}  # date -> total PnL for consistency
        self._daily_trades_per_symbol = {}  # symbol -> count pour limite quotidienne par symbole
        self.consistency_violated = False
        self.challenge_status = "ACTIVE"  # ACTIVE | PASSED | FAILED_CONSISTENCY | FAILED_DD
        self._daily_profit_reduced = False  # True quand daily profit > DAILY_PROFIT_LIMIT_PCT

        self._corr_matrix = {}
        self._profile_cache = {}
        self._corr_timestamp = 0
        self._corr_ttl = 3600  # recalc toutes les 1h
        self._corr_max_threshold = 0.70  # Pearson max entre deux positions same-direction

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
            return True  # can't determine staleness, assume fresh
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
            self._corr_matrix = {}
            self._corr_timestamp = now
            return {}
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
        """Correlation-aware position sizing — passage progressif continu.
        
        Réduction continue de la taille au lieu d'un blocage binaire :
          risk_mult = max(0.30, 1.0 - |correlation|)
        
        Exemples :
          corr 0.00 → 100%   corr 0.50 → 50%
          corr 0.30 → 70%    corr 0.73 → 27%
          corr 0.90 → 30% (plancher)
        """
        matrix = self._fetch_correlation_matrix()
        if not matrix or symbol not in matrix:
            return True, None, 1.0
        sig_dir = 0 if action == "BUY" else 1
        min_risk_mult = 1.0
        for pos in our_positions:
            if pos.symbol == symbol or pos.symbol not in matrix:
                continue
            if pos.type != sig_dir:
                continue  # direction opposée = pas de risque corrélatif
            corr = abs(matrix[symbol].get(pos.symbol, 0))
            if corr < 0.001:
                continue
            risk_mult = max(0.30, 1.0 - corr)
            min_risk_mult = min(min_risk_mult, risk_mult)
            logger.info(f"  [CORR] {symbol} vs {pos.symbol}: corr={corr:.2f} → risk_mult={risk_mult:.2f}")
        return True, None, min_risk_mult

    def can_trade(self, symbol, signal=None, positions=None):
        # _reset_daily() est appelé dans _scan_signals() (main.py) — une fois par cycle

        # Spread check FIRST — fast reject
        info = self.mt5.get_symbol_info(symbol)
        if info and hasattr(info, 'point') and info.point > 0:
            tick = self.mt5.get_tick(symbol)
            if tick:
                spread = tick.ask - tick.bid
                sym_cfg = self.symbol_limits.get(symbol, {})
                max_sp = sym_cfg.get("max_spread_points", self.max_spread_points)
                if spread >= max_sp * info.point * 1.05:
                    return False, f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f})"
        else:
            return False, f"Cannot get symbol info for {symbol}"

        # Price staleness check
        if signal is not None and not self.check_price_staleness(symbol):
            return False, "Stale price: tick > 60s"

        if self.daily_stats["trades"] >= self.config.get("MAX_TRADES_PER_DAY", 5):
            return False, "Daily trade limit"

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

        # Circuit breaker: drawdown > 8% interdit les shorts
        if dd_initial > 0.08 and signal and signal.get("action") == "SELL":
            return False, f"Circuit breaker: DD {dd_initial:.1%} > 8%, shorts disabled"

        daily_loss = max(0, -daily_equity_change) / self.initial_balance
        if daily_loss >= self.max_daily_loss_pct:
            self.challenge_status = "FAILED_DD"
            return False, f"FTMO daily loss limit: {daily_loss:.1%}"
        # Zone 3: >1.5% daily loss → stop trading (marge sous FTMO 5%)
        zone3 = self.config.get("ZONE3_LOSS_PCT", 0.015)
        if daily_loss >= zone3 and self.daily_stats["losses"] > 0:
            return False, f"Zone 3: daily DD {daily_loss:.1%} >= {zone3:.1%}, stop"

        # Auto-reset: si le global cooldown a expiré (ou n'existe pas après restart), on débloque
        if self.consecutive_losses >= self.config.get("AUTO_PAUSE_LOSSES", 3):
            if self.global_cooldown_until is None or datetime.utcnow() >= self.global_cooldown_until:
                logger.info(f"  [GLOBAL PAUSE] Cooldown terminé, reset des {self.consecutive_losses} pertes consécutives")
                self.consecutive_losses = 0
                self.global_cooldown_until = None

        # Global pause après 3 pertes consécutives (tous symboles, 30min)
        if self.global_cooldown_until is not None and datetime.utcnow() < self.global_cooldown_until:
            remaining = (self.global_cooldown_until - datetime.utcnow()).seconds // 60
            return False, f"Global pause: {remaining}min restantes"

        max_consec = self.config.get("AUTO_PAUSE_LOSSES", 3)
        if self.consecutive_losses >= max_consec:
            return False, "Consecutive losses pause"

        if symbol in self.cooldowns and datetime.utcnow() < self.cooldowns[symbol]:
            remaining = (self.cooldowns[symbol] - datetime.utcnow()).seconds // 60
            return False, f"Cooldown: {remaining}min"

        if signal:
            our_pos = [p for p in (positions or self.mt5.get_positions()) if p.magic == cfg.ROBOT_MAGIC]
            _, _, corr_risk_mult = self._check_correlation(symbol, signal.get("action"), our_pos)
            if corr_risk_mult < 1.0:
                cur_mult = signal.get("risk_mult", 1.0)
                signal["risk_mult"] = cur_mult * corr_risk_mult
                logger.info(f"  [CORR] {symbol}: risk_mult ajusté {cur_mult:.2f} → {signal['risk_mult']:.2f}")

            # Institutional profile check
            profile = self._get_profile(symbol)
            if profile:
                # Per-symbol ranging restriction
                if signal.get("_is_ranging", True) is not False:
                    sym_cfg = self.symbol_limits.get(symbol, {})
                    allow_ranging = sym_cfg.get("allow_ranging")
                    if allow_ranging is False and signal.get("_regime") == "RANGING":
                        return False, f"{symbol}: ranging trades not allowed (per-symbol config)"

                # DL required for specific symbols (EURUSD)
                dl_required = sym_cfg.get("dl_required", False)
                if dl_required and signal.get("_ml_agrees") is not True:
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

        now = datetime.utcnow()
        # Forex hours: opens Sun 22:00 UTC → closes Fri 22:00 UTC
        if now.weekday() == 4 and now.hour >= 22:   # Friday 22h+
            return False, "Weekend: Friday close"
        if now.weekday() == 5:                        # Saturday
            return False, "Weekend: Saturday"
        if now.weekday() == 6 and now.hour < 22:     # Sunday before 22h
            return False, "Weekend: Sunday before market open"
        # Sunday 22h+ = market open → autorisé

        # News filter: block 30min before/after high-impact events
        news_blocked, news_details = is_news_blocked()
        if news_blocked:
            news_name = news_details[0]['name'] if news_details else "high-impact"
            return False, f"News: {news_name}"

        # Session block: 5-18h UTC (Asie → NY close)
        utc_now = datetime.utcnow()
        start_hour = self.config.get("TRADING_START_HOUR", 5)
        end_hour = self.config.get("TRADING_END_HOUR", 18)
        if not (start_hour <= utc_now.hour < end_hour):
            return False, f"Session block: {utc_now.hour}h UTC (trade only {start_hour}-{end_hour}h UTC)"

        # Challenge expiry: max trading days atteint
        if self.max_trading_days > 0 and len(self.trading_days) >= self.max_trading_days:
            self.challenge_status = "FAILED_EXPIRY"
            return False, f"FTMO: maximum trading days ({self.max_trading_days}) atteint — challenge expiré"

        current_pnl = current_equity - self.initial_balance
        # FTMO challenge rules lorsque le profit target est proche/atteint
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

        # 3. Pertes consécutives
        if self.consecutive_losses >= 3:
            mult *= 0.50
        elif self.consecutive_losses >= 2:
            mult *= 0.75

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

        # Friday: reduce risk by 25% due to weekend gap risk
        if datetime.utcnow().weekday() == 4:
            risk_amount *= 0.75

        sym_cfg = self.symbol_limits.get(symbol, {})
        risk_amount *= sym_cfg.get("risk_mult", 1.0)

        # Absolute max risk cap (if configured)
        if self.max_risk_amount > 0:
            risk_amount = min(risk_amount, self.max_risk_amount)

        # Zone 2: >1% daily loss → risk × 0.5 (uniquement sur pertes réelles, pas sur gains)
        daily_loss_amt = max(0, -self.daily_stats["pnl"])
        zone2 = self.config.get("ZONE2_LOSS_PCT", 0.01)
        if daily_loss_amt > 0 and (daily_loss_amt / max(self.initial_balance, 1)) >= zone2:
            risk_amount *= 0.50
            logger.debug(f"  [ZONE 2] daily loss {daily_loss_amt/self.initial_balance:.2%} >= {zone2:.1%}, risk 50%")

        # Daily profit limit: risk reduit a 25%
        if self._daily_profit_reduced:
            risk_amount *= 0.25
            logger.debug("  [RISK REDUCED] daily profit limit atteint, risk_amount=25%")

        order_type = self.mt5.ORDER_TYPE_BUY if direction == 0 else self.mt5.ORDER_TYPE_SELL
        sl_profit = self.mt5.calc_profit(order_type, symbol, 0.1, entry, sl)
        if sl_profit is not None and sl_profit < 0:
            risk_per_01 = abs(sl_profit)
            lot = (risk_amount / risk_per_01) * 0.1
        else:
            lot = self.config.get("LOT_SIZE", 0.1)

        # Adaptive lot multiplier (performance-based)
        adaptive_mult = self._adaptive_lot_mult()
        lot *= adaptive_mult
        logger.debug(f"  [ADAPTIVE LOT] {symbol}: base_lot ajusté x{adaptive_mult:.2f} = {lot:.3f}")

        # Clamp between min_lot and max_lot from symbol config
        max_lot = sym_cfg.get("max_lot", 0.55)
        min_lot = sym_cfg.get("min_lot", 0.01)
        lot = max(min_lot, min(lot, max_lot))
        return round(lot, 2)

    REGIME_FROM_COMMENT = {
        "TRE": "TREND_UP", "DOW": "TREND_DOWN", "RAN": "RANGING",
        "HIG": "HIGH_VOL", "LOW": "LOW_VOL",
    }

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
        self._check_time_stop(position)
        self._check_partial_tp(position)
        self._check_step_trailing(position)
        self._check_structure_exit(position)

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

    def record_trade_result(self, symbol, profit):
        self.daily_stats["trades"] += 1
        self._daily_trades_per_symbol[symbol] = self._daily_trades_per_symbol.get(symbol, 0) + 1
        self.daily_stats["pnl"] += profit
        today = datetime.utcnow().date()
        self.trading_days.add(today)
        self.daily_pnl_by_date[today] = self.daily_pnl_by_date.get(today, 0) + profit
        self._trade_history.append(dict(symbol=symbol, profit=profit, time=datetime.utcnow()))
        if len(self._trade_history) > 1000:
            self._trade_history = self._trade_history[-1000:]

        if profit < 0:
            self.daily_stats["losses"] += 1
            self.consecutive_losses += 1
            # Cooldown progressif: 15 min pour 1 perte, 30 min pour 2+ consécutives
            sym_losses = self._symbol_consecutive_losses.get(symbol, 0) + 1
            self._symbol_consecutive_losses[symbol] = sym_losses
            cd_minutes = 15 if sym_losses <= 1 else 30
            self.cooldowns[symbol] = datetime.utcnow() + timedelta(minutes=cd_minutes)
            logger.info(f"  [COOLDOWN] {symbol}: {sym_losses} perte(s) consecutive(s) → {cd_minutes}min")
            # Pause globale après 3 pertes consécutives (30min, tous symboles)
            if self.consecutive_losses >= 3:
                self.global_cooldown_until = datetime.utcnow() + timedelta(minutes=30)
                logger.warning(f"  [GLOBAL PAUSE] {self.consecutive_losses} pertes consecutives, "
                              f"pause 30min jusqu'a {self.global_cooldown_until}")
        elif profit > 0:
            self.consecutive_losses = 0
            self._symbol_consecutive_losses[symbol] = 0  # reset per-symbol sur gain
            self.global_cooldown_until = None
        # profit == 0 (BE) ne reset PAS consecutive_losses intentionnellement

        self._check_consistency()
        self._check_daily_loss_limit()
        self._check_drawdown_limit()
        self._prune_histories()

    def _check_consistency(self):
        """FTMO consistency rule: aucun jour ne doit dépasser 30% du profit total.
        Ne check que quand on est proche du target (pnl > 80%) pour éviter les faux
        positifs pendant le drawdown recovery.
        total_pnl calculé depuis daily_pnl_by_date (intégral, jamais tronqué)."""
        total_pnl = sum(self.daily_pnl_by_date.values())
        profit_target_amount = self.initial_balance * self.profit_target_pct
        if total_pnl < profit_target_amount * 0.8:
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

    def _check_daily_loss_limit(self):
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
        if daily_loss_pct >= self.max_daily_loss_pct:
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
        current_pnl = equity - self.initial_balance
        profit_progress = current_pnl / (self.initial_balance * self.profit_target_pct)
        dd_init = max(0, (self.initial_balance - equity) / self.initial_balance)
        dd_peak = max(0, (self.peak_equity - equity) / max(self.peak_equity, 1))
        winners = sum(1 for t in self._trade_history if t["profit"] > 0)
        wr = winners / max(len(self._trade_history), 1)
        best_day = max(self.daily_pnl_by_date.values()) if self.daily_pnl_by_date else 0
        # best_day_pct = meilleur jour / profit total (pour règle consistance FTMO)
        # Si profit total <= 0, le ratio n'a pas de sens
        if current_pnl > 0 and best_day > 0:
            best_day_pct = best_day / current_pnl
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
                        position.sl = be_sl  # ← SYNC critical: met à jour l'objet Python
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
        max_hours = 12 if position.profit > 0 else 4.0
        if hours < max_hours:
            return
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
        elif result and result.retcode != 10009:
            logger.warning(f"TIME STOP FAILED {position.symbol}: retcode={result.retcode}")
        elif result:
            logger.warning(f"Time-stop failed: {position.symbol} retcode={result.retcode}")

    def _get_atr(self, symbol, period=14):
        """Get current ATR in price units for a symbol (cached TTL=60s)"""
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached and (now - cached[1]) < ATR_CACHE_TTL:
            return cached[0]
        try:
            rates = self.mt5.get_rates(symbol, "H1", period + 5)
            if rates is None or len(rates) < period:
                return None
            tr = rates['high'] - rates['low']
            val = float(tr[-period:].mean())
            self._atr_cache[symbol] = (val, now)
            return val
        except Exception as e:
            logger.debug(f"ATR calc failed for {symbol}: {e}")
            return None

    def _check_step_trailing(self, position):
        """ATR-based trailing SL — niveaux progressifs adaptés au régime.
        Le trail distance se resserre à mesure que le profit augmente,
        et varie selon le régime (TREND large, RANGING serré, HIGH_VOL très large, etc.)
        """
        ticket = str(position.ticket)
        atr_val = self._get_atr(position.symbol)
        if atr_val is None or atr_val <= 0:
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
        levels = profile_levels or TRAILING_BY_REGIME.get(regime, TRAILING_BY_REGIME["RANGING"])
        first_thresh = levels[0][0] if levels else 0.50
        if profit_atr <= first_thresh:
            return

        trail_dist = levels[-1][1]
        for thresh, dist in reversed(levels):
            if profit_atr > thresh:
                trail_dist = dist
                break

        trail_distance = trail_dist * atr_val
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return

        new_sl = peak - trail_distance if position.type == 0 else peak + trail_distance
        new_sl = round(new_sl, info.digits)

        if position.type == 0 and new_sl <= position.sl:
            return
        if position.type == 1 and new_sl >= position.sl:
            return

        old_sl = position.sl
        result = self.mt5.update_sl(position, new_sl)
        if result and result.retcode == 10009:
            position.sl = new_sl  # ← SYNC: évite les décisions basées sur un SL obsolète
            self.peak_profit[ticket] = profit_atr
            logger.info(f"TrailATR: {position.symbol} SL {old_sl}→{new_sl} "
                        f"(peak_profit={profit_atr:.1f}ATR, "
                        f"trail={trail_dist:.2f}ATR)")
            return
        rc = result.retcode if result else -1
        logger.warning(f"TrailATR FAILED {position.symbol}: retcode={rc}")

    def _reconstruct_peak(self, position):
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
            sl_dist = max(sl_mult * atr_val, min_dist)
            tp_dist = max(tp_mult * atr_val, min_dist)
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
            self._daily_profit_reduced = False
            account = self.mt5.get_account_info()
            if account:
                self.daily_start_equity = account.equity
            else:
                self.daily_start_equity = max(self.peak_equity, self.initial_balance)
