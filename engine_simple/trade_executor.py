"""RateLimiter, OrderValidator, TradeExecutor — exécution avec sécurité renforcée.

CORRECTIFS HAUTE COUR D'AUDIT (Juin 2026):
  - FIX #3: SL obligatoire — aucun trade sans Stop Loss
  - FIX #2: Intervalle minimum 5 min entre trades sur le même symbole
  - FIX #5: Circuit breaker de fréquence — max 1 trade/min/symbole
  - FIX #8: Rate limiter par symbole (plus de scalping en rafale)
"""
import logging
import time

import config_simple as cfg

logger = logging.getLogger("executor")

# Intervalle minimum entre deux trades sur le MÊME symbole (secondes)
MIN_SYMBOL_INTERVAL_S = 300  # 5 minutes


class PerSymbolRateLimiter:
    """Rate limiter par symbole : max 1 trade/min/symbole, min 5 min entre deux trades.

    Remplace l'ancien RateLimiter global qui était contourné facilement.
    min_interval_s peut être mis à 0 pour les tests.

    ATTENTION: la fenêtre de nettoyage doit être >= min_interval, sinon
    les entrées sont vidées avant le check d'intervalle et le rate limit
    est contourné (cf bug doublons Juin 2026).
    """

    def __init__(self, max_per_minute: int = 1, window_seconds: int = 60,
                 min_interval_s: int | None = None):
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self._min_interval = MIN_SYMBOL_INTERVAL_S if min_interval_s is None else min_interval_s
        self._symbol_timestamps: dict[str, list[float]] = {}

    def allow(self, symbol: str) -> bool:
        now = time.time()

        # Nettoyage des entrées périmées
        # Utilise MAX(window_seconds, _min_interval) pour éviter que le
        # nettoyage ne vide la liste avant le check d'intervalle minimum.
        cleanup_window = max(self.window_seconds, self._min_interval)
        if symbol in self._symbol_timestamps:
            self._symbol_timestamps[symbol] = [
                t for t in self._symbol_timestamps[symbol]
                if now - t < cleanup_window
            ]
        else:
            self._symbol_timestamps[symbol] = []

        # Vérification du nombre dans la fenêtre
        if len(self._symbol_timestamps[symbol]) >= self.max_per_minute:
            return False

        # Vérification de l'intervalle minimum depuis le dernier trade
        if self._symbol_timestamps[symbol] and self._min_interval > 0:
            last_trade = self._symbol_timestamps[symbol][-1]
            if now - last_trade < self._min_interval:
                remaining = int(self._min_interval - (now - last_trade))
                logger.warning(
                    f"[RATE LIMIT] {symbol}: dernier trade il y a "
                    f"{now - last_trade:.0f}s (< {MIN_SYMBOL_INTERVAL_S}s), "
                    f"attendre {remaining}s"
                )
                return False

        self._symbol_timestamps[symbol].append(now)
        return True

    def release(self, symbol: str) -> None:
        """Annule le dernier timestamp (si l'ordre a échoué après allow())."""
        if symbol in self._symbol_timestamps and self._symbol_timestamps[symbol]:
            self._symbol_timestamps[symbol].pop()
            if not self._symbol_timestamps[symbol]:
                del self._symbol_timestamps[symbol]


class GlobalRateLimiter:
    """Rate limiter GLOBAL : max 1 ordre toutes les N secondes sur TOUS les symboles.

    Évite le retcode 10018 (TRADE_RETCODE_TOO_MANY_REQUESTS) de MT5 quand
    on envoie trop d'ordres en rafale (ex: 7 symboles dans le même cycle 15s).
    """

    def __init__(self, min_interval_s: int = 10):
        self.min_interval_s = min_interval_s
        self._last_order_time: float = 0.0

    def allow(self) -> bool:
        now = time.time()
        if now - self._last_order_time < self.min_interval_s:
            return False
        self._last_order_time = now
        return True

    def release(self) -> None:
        """Annule le dernier timestamp si l'ordre a échoué."""
        self._last_order_time = 0.0


class ExecutionStats:
    """Statistiques d'exécution : taux de succès, latence, slippage."""
    def __init__(self):
        self.records: list[dict] = []
        self.total_attempts = 0

    def record(self, success: bool, latency: float = 0, slippage_pts: int | None = None):
        entry = {"success": success, "latency": latency}
        if slippage_pts is not None:
            entry["slippage"] = slippage_pts
        self.records.append(entry)
        self.total_attempts += 1

    @property
    def successful(self) -> int:
        return sum(1 for r in self.records if r["success"])

    @property
    def rejected(self) -> int:
        return sum(1 for r in self.records if not r["success"])

    @property
    def success_rate(self) -> float:
        if not self.records:
            return 1.0
        return sum(1 for r in self.records if r["success"]) / len(self.records)

    @property
    def avg_slippage(self) -> float:
        slippages = [r["slippage"] for r in self.records if "slippage" in r]
        return sum(slippages) / len(slippages) if slippages else 0.0

    @property
    def p95_latency(self) -> float:
        if not self.records:
            return 0.0
        latencies = sorted(r["latency"] for r in self.records)
        idx = int(len(latencies) * 0.95)
        return latencies[idx] if idx < len(latencies) else latencies[-1]

    def summary(self) -> dict:
        latencies = [r["latency"] for r in self.records] if self.records else [0]
        slippages = [r["slippage"] for r in self.records if "slippage" in r] or [0]
        return {
            "total": len(self.records),
            "success_rate": self.success_rate,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
            "avg_slippage_pts": round(sum(slippages) / len(slippages), 1),
        }


class OrderValidator:
    MIN_LOT = 0.01
    MAX_LOT = 10.0
    MIN_RR = cfg.MIN_RR_RATIO  # de la config (1.95); ±5% jitter SL/TP est incorporé

    @staticmethod
    def validate(symbol: str, action: str, lot: float,
                 price: float, sl: float, tp: float,
                 symbol_info) -> str | None:
        # REFUS ABSOLU si SL ou TP est None ou 0
        if sl is None or tp is None:
            return "SL ou TP non défini — REFUSÉ"
        if sl == 0 or tp == 0:
            return "SL ou TP = 0 — REFUSÉ"
        if price == 0:
            return "Price = 0 — REFUSÉ"

        if lot < OrderValidator.MIN_LOT:
            return f"Lot {lot} < min {OrderValidator.MIN_LOT}"
        if lot > OrderValidator.MAX_LOT:
            return f"Lot {lot} > max {OrderValidator.MAX_LOT}"
        if symbol_info:
            vol_max = getattr(symbol_info, "volume_max", None)
            if vol_max is not None and isinstance(vol_max, (int, float)):
                if lot > vol_max:
                    return f"Lot {lot} > volume_max broker {vol_max}"

        risk = abs(price - sl) * lot
        reward = abs(tp - price) * lot
        if risk <= 0:
            return f"Risque nul (price={price}, sl={sl}) — SL trop proche"
        if reward <= 0:
            return f"Récompense nulle (price={price}, tp={tp}) — TP trop proche"
        rr = reward / risk
        if rr < OrderValidator.MIN_RR:
            return f"RR {rr:.2f} < {OrderValidator.MIN_RR}"
        return None


class TradeExecutor:
    def __init__(self, mt5, ftmo, journal, position_tracker, signals, adaptive, audit=None):
        self.mt5 = mt5
        self.ftmo = ftmo
        self.journal = journal
        self.tracker = position_tracker
        self.signals = signals
        self.adaptive = adaptive
        self.audit = audit
        # Rate limiter par symbole: max 1 trade/min/symbole + 5min intervalle
        self.rate_limiter = PerSymbolRateLimiter(max_per_minute=1, window_seconds=60)  # 1 trade/min/symbole max
        # Rate limiter global : max 1 ordre toutes les 10s (évite retcode 10018)
        self.global_rate_limiter = GlobalRateLimiter(min_interval_s=10)

    def _get_signal_value(self, signal, key, default=None):
        if isinstance(signal, dict):
            return signal.get(key, default)
        return getattr(signal, key, default)

    def _confirm_position(self, symbol, action, max_retries=3):
        """Vérifie qu'une position a bien été créée après order_send retcode=10009."""
        for i in range(max_retries):
            import time
            time.sleep(0.2)
            positions = self.mt5.get_positions()
            if positions:
                for p in positions:
                    if p.symbol == symbol and p.magic == 999001:
                        if (action == "BUY" and p.type == 0) or (action == "SELL" and p.type == 1):
                            logger.debug(f"  [CONFIRM] {symbol} {action}: position {p.ticket} confirmée (tentative {i+1})")
                            return True
            logger.debug(f"  [CONFIRM] {symbol} {action}: position pas encore visible (tentative {i+1})")
        logger.warning(f"  [CONFIRM] {symbol} {action}: POSITION NON CONFIRMÉE après {max_retries} tentatives!")
        return False

    def execute(self, symbol, signal):
        action = self._get_signal_value(signal, "action")

        # Vérification doublon en PREMIER (ne consomme PAS le rate limiter)
        all_positions = self.mt5.get_positions()
        existing = [p for p in all_positions if p.symbol == symbol] if all_positions else []
        if existing:
            sig_type = 0 if action == "BUY" else 1  # POSITION_TYPE_BUY=0, SELL=1
            for pos in existing:
                if pos.type == sig_type:
                    logger.debug(
                        f"[DOUBLON] {symbol}: position {action} déjà ouverte "
                        f"(ticket={pos.ticket}) — skip"
                    )
                    return None

        price = self._get_signal_value(signal, "entry_price")
        if price is None or price == 0:
            tick = self.mt5.get_tick(symbol)
            if tick is None:
                logger.warning(f"{symbol}: impossible d'obtenir le tick, skip")
                return None
            price = tick.ask if action == "BUY" else tick.bid

        # FIX #3: SL/TP obligatoires — calcul depuis ATR si manquants
        sl = self._get_signal_value(signal, "sl")
        tp = self._get_signal_value(signal, "tp")

        if sl is None or tp is None or sl == 0 or tp == 0:
            atr = self._get_signal_value(signal, "atr")
            sl_atr = self._get_signal_value(signal, "sl_atr")
            tp_atr = self._get_signal_value(signal, "tp_atr")
            if None not in (price, atr, sl_atr, tp_atr):
                direction = 0 if action == "BUY" else 1
                sl, tp = self.ftmo._calc_sl_tp(symbol, price, direction, atr, sl_atr, tp_atr)

        # REFUS catégorique si SL ou TP est encore None
        if sl is None or tp is None:
            logger.error(f"[SL REFUS] {symbol}: aucun SL/TP calculable — transaction BLOQUÉE")
            return None
        if sl == 0 or tp == 0:
            logger.error(f"[SL REFUS] {symbol}: SL/TP = 0 — transaction BLOQUÉE")
            return None

        # Mode dégradé (WR < 40%) : lot minimum = 0.01
        is_degraded = self._get_signal_value(signal, "_degraded", False)
        lot_quality = 0.01 if is_degraded else 1.0
        lot = self._calc_lot(symbol, price, sl, quality=lot_quality)

        # Validation avant envoi (avec symbol_info pour vérifier volume_max broker)
        info = self.mt5.get_symbol_info(symbol)
        err = OrderValidator.validate(symbol, action, lot, price, sl, tp, info)
        if err:
            logger.warning(f"{symbol}: validation echouee: {err}")
            return None

        # Rate limiter GLOBAL en premier — évite de consommer un slot per-symbol pour rien
        if not self.global_rate_limiter.allow():
            logger.warning(f"[RATE LIMIT] Global: trop d'ordres simultanés, skip {symbol}")
            return None

        # Rate limiter par SYMBOLE — max 1 trade/min/symbole
        if not self.rate_limiter.allow(symbol):
            logger.warning(f"[RATE LIMIT] {symbol}: fréquence max atteinte, skip")
            return None

        # try/finally pour libérer les rate limiters en cas d'exception
        result = None
        try:
            regime = self._get_signal_value(signal, "regime") or self._get_signal_value(signal, "_regime") or "RANGING"
            result = self._place_order(symbol, action, lot, price, sl, tp, regime)
        finally:
            # Si l'ordre a échoué, on libère les rate limiters
            if result is None or (hasattr(result, 'retcode') and result.retcode != 10009):
                self.rate_limiter.release(symbol)
                self.global_rate_limiter.release()

        return result

    def _calc_lot(self, symbol, entry, sl, quality=1.0):
        lot = self.ftmo.calculate_lot(symbol, entry, sl, quality=quality)
        if lot is not None and lot > 0:
            # 🔒 Sûreté redondante : clamp à max_lot depuis la config module-level
            try:
                import config_simple as _cfg
                _sym_cfg = _cfg.SYMBOL_LIMITS.get(symbol, {})
                _max = _sym_cfg.get("max_lot", 10.0)
                _min = _sym_cfg.get("min_lot", 0.01)
                if lot > _max:
                    logger.warning(f"[LOT CLAMP] {symbol}: lot={lot:.3f} > max_lot={_max} (clamp redondant)")
                    lot = _max
                if lot < _min:
                    lot = _min
            except (ImportError, AttributeError, Exception) as _e:
                logger.debug(f"[LOT CLAMP] config_simple non disponible: {_e}")
            return lot
        # Fallback sécurisé : jamais plus que 0.01 en cas d'erreur
        return 0.01

    REGIME_TO_SHORT = {
        "TREND_UP": "TRE", "TREND_DOWN": "DOW", "RANGING": "RAN",
        "HIGH_VOL": "HIG", "LOW_VOL": "LOW",
    }

    def _place_order(self, symbol, action, lot, price, sl, tp, regime="RANGING"):
        import MetaTrader5 as mt5
        # Ensure symbol is in Market Watch (crypto non-standard symbols need this)
        try:
            mt5.symbol_select(symbol, True)
        except Exception:
            pass
        direction = 0 if action == "BUY" else 1
        regime_short = self.REGIME_TO_SHORT.get(regime.upper(), "RAN")
        comment = f"ADAPT_{regime_short}"
        req = dict(
            action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=lot,
            type=direction, price=price, sl=sl, tp=tp,
            deviation=20, magic=999001,
            type_filling=mt5.ORDER_FILLING_IOC,
            type_time=mt5.ORDER_TIME_DAY,
            comment=comment,
        )
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"PlaceOrder OK: {symbol} {action} {lot}@{price} SL={sl} TP={tp}")
        elif result and result.retcode in (10006, 10018, 10025):
            # Retry with RETURN filling on requote/too many requests/connection lost
            logger.warning(f"PlaceOrder: {symbol} retcode={result.retcode}, retry with RETURN filling")
            req["type_filling"] = mt5.ORDER_FILLING_RETURN
            result = self.mt5.order_send(req)
            if result and result.retcode == 10009:
                logger.info(f"PlaceOrder RETRY OK: {symbol} {action} {lot}@{price}")
            elif result:
                logger.warning(f"PlaceOrder RETRY FAILED {symbol}: retcode={result.retcode}")
        elif result:
            logger.warning(f"PlaceOrder FAILED {symbol}: retcode={result.retcode}")
        else:
            logger.warning(f"PlaceOrder FAILED {symbol}: no result")
        return result
