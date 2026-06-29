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
# 5s = très permissif, permet plusieurs trades par cycle si le signal est fort.
# ⚠️ La protection FTMO (DD 10%, daily loss) est la vraie barrière, pas le rate limiter.
MIN_SYMBOL_INTERVAL_S = 5  # 5s (↓ 30→5 le 26 Juin: laisser passer tous les signaux valides)

# Intervalle minimum entre deux trades HIGH CONFIDENCE (>90%) sur le même symbole
# 300s = 5 min — le tradeur veut un rythme soutenu mais pas du scalping
HIGH_CONFIDENCE_INTERVAL_S = 120  # 2 min (↓ 300→120 le 29 Juin: débloquer les signaux haute confiance)


class PerSymbolRateLimiter:
    """Rate limiter par symbole : max 1 trade/min/symbole, min 5 min entre deux trades.

    Remplace l'ancien RateLimiter global qui était contourné facilement.
    min_interval_s peut être mis à 0 pour les tests.

    ATTENTION: la fenêtre de nettoyage doit être >= min_interval, sinon
    les entrées sont vidées avant le check d'intervalle et le rate limit
    est contourné (cf bug doublons Juin 2026).
    """

    def __init__(self, max_per_minute: int = 1, window_seconds: int = 60, min_interval_s: int | None = None):
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
            self._symbol_timestamps[symbol] = [t for t in self._symbol_timestamps[symbol] if now - t < cleanup_window]
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

    def __init__(self, min_interval_s: int = 3):  # ↓ 10→3 le 29 Juin: débloquer trades sur différents symboles
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
    def validate(symbol: str, action: str, lot: float, price: float, sl: float, tp: float, symbol_info) -> str | None:
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
        # Rate limiter par symbole: max 6 trades/min/symbole + 5s intervalle
        # 26 Juin: ↑ 2→6 trades/min pour laisser passer tous les signaux valides
        self.rate_limiter = PerSymbolRateLimiter(
            max_per_minute=6, window_seconds=60
        )  # Mode agressif: 6 trades/min/symbole (était 2)
        # Rate limiter HIGH CONFIDENCE : 1 trade/5min/symbole, aucune limite de positions
        self.high_conf_rate_limiter = PerSymbolRateLimiter(
            max_per_minute=1, window_seconds=HIGH_CONFIDENCE_INTERVAL_S, min_interval_s=HIGH_CONFIDENCE_INTERVAL_S
        )
        # Rate limiter global : max 1 ordre toutes les 3s (↓ 10→3 le 29 Juin: débloquer trades multi-symboles)
        self.global_rate_limiter = GlobalRateLimiter(min_interval_s=3)
        # 🐛 FIX 19 Juin: Market-closed cooldown — évite flood WARNING XAUUSD
        # Quand MT5 retourne retcode=10018 (Market closed), on bloque le symbole
        # pendant MARKET_CLOSED_COOLDOWN_S secondes avant de réessayer.
        self._market_closed_cooldowns: dict[str, float] = {}
        self.MARKET_CLOSED_COOLDOWN_S = 120  # 2 min de pause après marché fermé

    def _get_signal_value(self, signal, key, default=None):
        if isinstance(signal, dict):
            return signal.get(key, default)
        return getattr(signal, key, default)

    def execute(self, symbol, signal):
        action = self._get_signal_value(signal, "action")
        high_confidence = self._get_signal_value(signal, "high_confidence", False)

        # Vérification doublon — permet jusqu'à N positions selon la confidence
        # max_per_symbol = 3 si conf>85%, 2 si conf>70%, 1 sinon (défini dans main.py)
        # HIGH CONFIDENCE (>90%) : aucun limite de positions
        max_per_symbol = self._get_signal_value(signal, "max_per_symbol", 1)
        all_positions = self.mt5.get_positions()
        existing = [p for p in all_positions if p.symbol == symbol] if all_positions else []
        if existing and not high_confidence:
            sig_type = 0 if action == "BUY" else 1  # POSITION_TYPE_BUY=0, SELL=1
            same_dir_count = sum(1 for p in existing if p.type == sig_type)
            if same_dir_count >= max_per_symbol:
                logger.debug(
                    f"[DOUBLON] {symbol}: déjà {same_dir_count} position(s) {action} "
                    f"(max={max_per_symbol}, ticket={existing[0].ticket}) — skip"
                )
                return None

            # 🔧 18 Juin 2026: Vérifier entrée à prix identique (vrai doublon)
            # Des positions au même prix ±0.01% ouvertes à <60s d'intervalle sont des doublons
            # 🔧 FIX C3 19 Juin: seuil resserré 0.01%→0.005% pour catch les entrées quasi-identiques
            price = self._get_signal_value(signal, "entry_price")
            if price is not None and price > 0:
                for pos in existing:
                    if pos.type == sig_type:
                        price_diff_pct = abs(pos.price_open - price) / max(price, 0.0001) * 100
                        if price_diff_pct < 0.005:  # moins de 0.005% d'écart = même niveau
                            logger.warning(
                                f"[DOUBLON] {symbol}: entrée {price:.5f} identique "
                                f"à pos #{pos.ticket} ({pos.price_open:.5f}, diff={price_diff_pct:.3f}%) → skip"
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
        # FIX C1: passer le risk_mult du signal au calculate_lot
        signal_rm = self._get_signal_value(signal, "risk_mult")
        lot = self._calc_lot(symbol, price, sl, quality=lot_quality, signal_risk_mult=signal_rm)

        # Validation avant envoi (avec symbol_info pour vérifier volume_max broker)
        info = self.mt5.get_symbol_info(symbol)
        err = OrderValidator.validate(symbol, action, lot, price, sl, tp, info)
        if err:
            logger.warning(f"{symbol}: validation echouee: {err}")
            return None

        # 🐛 FIX 19 Juin: Market-closed cooldown — pas de flood si marché fermé
        if self._is_market_closed(symbol):
            logger.debug(f"[MARKET CLOSED] {symbol}: en cooldown (retcode=10018 récent), skip")
            return None

        # Rate limiter GLOBAL en premier — évite de consommer un slot per-symbol pour rien
        if not self.global_rate_limiter.allow():
            logger.warning(f"[RATE LIMIT] Global: trop d'ordres simultanés, skip {symbol}")
            return None

        # Rate limiter par SYMBOLE — high confidence ou normal
        if high_confidence:
            # 🔥 HIGH CONFIDENCE: rate limiter 5 min, pas de limite de positions
            if not self.high_conf_rate_limiter.allow(symbol):
                logger.info(f"[RATE LIMIT] {symbol}: high confidence, attendre 5 min, skip")
                return None
        else:
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
            if result is None or (hasattr(result, "retcode") and result.retcode != 10009):
                if high_confidence:
                    self.high_conf_rate_limiter.release(symbol)
                else:
                    self.rate_limiter.release(symbol)
                self.global_rate_limiter.release()

        return result

    def _calc_lot(self, symbol, entry, sl, quality=1.0, signal_risk_mult=None):
        lot = self.ftmo.calculate_lot(symbol, entry, sl, quality=quality, signal_risk_mult=signal_risk_mult)
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
        "TREND_UP": "TRE",
        "TREND_DOWN": "DOW",
        "RANGING": "RAN",
        "HIGH_VOL": "HIG",
        "LOW_VOL": "LOW",
    }

    def _place_order(self, symbol, action, lot, price, sl, tp, regime="RANGING"):
        import MetaTrader5 as mt5

        # 🐛 FIX 19 Juin: Double-check market closed — même si execute() a raté le check
        if self._is_market_closed(symbol):
            logger.debug(f"[MARKET CLOSED] {symbol}: cooldown actif dans _place_order, pas d'envoi")
            return None

        # Ensure symbol is in Market Watch (crypto non-standard symbols need this)
        try:
            mt5.symbol_select(symbol, True)
        except Exception as e:
            logger.warning(f"[SYMBOL_SELECT] {symbol}: activation Market Watch échouée: {e}")
        # Get symbol info for slippage calculation
        info = self.mt5.get_symbol_info(symbol)
        direction = 0 if action == "BUY" else 1
        regime_short = self.REGIME_TO_SHORT.get(regime.upper(), "RAN")
        comment = f"ADAPT_{regime_short}"
        req = dict(
            action=mt5.TRADE_ACTION_DEAL,
            symbol=symbol,
            volume=lot,
            type=direction,
            price=price,
            sl=sl,
            tp=tp,
            deviation=20,
            magic=cfg.ROBOT_MAGIC,
            type_filling=mt5.ORDER_FILLING_IOC,
            type_time=mt5.ORDER_TIME_DAY,
            comment=comment,
        )
        logger.debug(
            f"[ORDER REQ] {symbol} {action} lot={lot:.3f} price={price:.5f} "
            f"SL={sl:.5f} TP={tp:.5f} dev=20 fill=IOC digits={info.digits if info else '?'} "
            f"point={info.point if info else '?'}"
        )
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"PlaceOrder OK: {symbol} {action} {lot}@{price} SL={sl} TP={tp}")
        elif result and result.retcode in (10006, 10018, 10025):
            # 🐛 FIX 19 Juin: Si marché fermé (10018), activer le cooldown
            if result.retcode == 10018:
                self._set_market_closed(symbol)
                comment = getattr(result, "comment", "?") or "?"
                logger.warning(
                    f"[MARKET CLOSED] {symbol}: retcode=10018 ({comment}) — "
                    f"cooldown {self.MARKET_CLOSED_COOLDOWN_S}s activé, pas de retry"
                )
                return result  # Pas de retry — le marché est fermé

            # Retry with RETURN filling on requote/too many requests/connection lost
            # M20: limiter le slippage — vérifier le prix de fill avant d'accepter
            comment = getattr(result, "comment", "?") or "?"
            logger.warning(
                f"PlaceOrder: {symbol} retcode={result.retcode} comment={comment}, retry with RETURN filling"
            )
            req["type_filling"] = mt5.ORDER_FILLING_RETURN
            result = self.mt5.order_send(req)
            if result and result.retcode == 10009:
                # Vérifier slippage: prix de fill vs prix demandé
                fill_price = getattr(result, "price", price)
                slippage_pts = abs(fill_price - price) / info.point if info and info.point else 0
                max_slippage_pts = 20  # max 20 points de slippage sur le retry
                if slippage_pts > max_slippage_pts:
                    logger.warning(f"PlaceOrder RETRY SLIPPAGE {symbol}: {slippage_pts:.0f}pts > {max_slippage_pts}max")
                else:
                    logger.info(
                        f"PlaceOrder RETRY OK: {symbol} {action} {lot}@{fill_price} (slip={slippage_pts:.0f}pts)"
                    )
            elif result:
                comment = getattr(result, "comment", "?") or "?"
                logger.warning(f"PlaceOrder RETRY FAILED {symbol}: retcode={result.retcode} comment={comment}")
        elif result:
            comment = getattr(result, "comment", "?") or "?"
            logger.warning(f"PlaceOrder FAILED {symbol}: retcode={result.retcode} comment={comment}")
        else:
            logger.warning(f"PlaceOrder FAILED {symbol}: no result")
        return result

    # 🐛 FIX 19 Juin: Market-closed cooldown — arrête le flood XAUUSD
    def _is_market_closed(self, symbol: str) -> bool:
        """Vérifie si le symbole est en cooldown pour marché fermé."""
        import time

        if symbol in self._market_closed_cooldowns:
            remaining = time.time() - self._market_closed_cooldowns[symbol]
            if remaining < self.MARKET_CLOSED_COOLDOWN_S:
                return True
            else:
                # Cooldown expiré, nettoyer
                del self._market_closed_cooldowns[symbol]
        return False

    def _set_market_closed(self, symbol: str) -> None:
        """Active le cooldown pour marché fermé sur ce symbole."""
        import time

        self._market_closed_cooldowns[symbol] = time.time()
