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
# 🔧 FIX 21 Juillet 2026: ↑ 5→30s — les doublons massifs (9 trades XAUUSD en 30s,
# 8 trades XAGUSD en 90s) ont causé -$604 de pertes évitables.
# Chaque doublon triple les pertes sans augmenter les gains (même SL/TP).
MIN_SYMBOL_INTERVAL_S = 30  # 30s entre deux trades sur le même symbole

# Intervalle minimum entre deux trades HIGH CONFIDENCE (>90%) sur le même symbole
HIGH_CONFIDENCE_INTERVAL_S = 300  # 5 min (↑ 120→300 le 21 Juillet: anti-doublon haute confiance aussi)


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
        """Annule le dernier timestamp si l'ordre a échoué.

        🔧 FIX AUDIT H9: Ne PAS reset à 0.0 (bypass rate limit MT5).
        Si l'échec était dû au rate limit MT5 (10018/10025), un reset à 0
        permet un retry immédiat → storm contre MT5. On garde le timestamp
        précédent pour que le cooldown min_interval_s s'applique au retry.
        """
        pass  # Ne rien faire — le prochain allow() respectera le cooldown


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
    MIN_LOT = 0.01  # 🔧 FIX 10 Juillet 2026: 0.05→0.01 (data collection mode, global_max_lot=0.02)
    MAX_LOT = 10.0
    MIN_RR = cfg.MIN_RR_RATIO  # de la config (1.95); ±5% jitter SL/TP est incorporé

    @staticmethod
    def validate(
        symbol: str, action: str, lot: float, price: float, sl: float, tp: float, symbol_info, min_rr: float = None
    ) -> str | None:
        # REFUS ABSOLU si SL ou TP est None ou 0
        if sl is None or tp is None:
            return "SL ou TP non défini — REFUSÉ"
        if sl == 0 or tp == 0:
            return "SL ou TP = 0 — REFUSÉ (FTMO VIOLATION: trade sans stop loss)"
        if price == 0:
            return "Price = 0 — REFUSÉ"
        # 🐛 FIX 16 Août 2026 (Audit M-EX2): vérification du SENS des SL/TP.
        # L'ancien code utilisait abs() partout → un BUY avec SL au-dessus du prix
        # et TP en-dessous passait la validation RR tant que les distances étaient
        # bonnes. Un tel ordre serait rejeté par le broker (invalid stops) — ou
        # pire, exécuté avec un SL du mauvais côté en cas de retry RETURN.
        if action == "BUY":
            if not (sl < price < tp):
                return f"BUY invalide: SL={sl} doit être < price={price} < TP={tp}"
        elif action == "SELL":
            if not (sl > price > tp):
                return f"SELL invalide: SL={sl} doit être > price={price} > TP={tp}"
        else:
            return f"Action inconnue: {action}"
        # 🔧 FIX 21 Juillet 2026: SL trop proche du prix = pas de protection réelle
        # Certains trades avaient SL=entry_price±0 (rows 115-117, 126-128 du log)
        if abs(sl - price) / max(abs(price), 0.0001) < 0.0001:
            return f"SL identique au prix ({sl} ≈ {price}) — PAS DE PROTECTION, BLOQUÉ"

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
        # 🐛 FIX #13 (3 Juillet): Utiliser min_rr du signal (per-symbol) si disponible,
        # sinon fallback sur la config globale. Les SYMBOL_CONFIG dans strategy.py ont
        # min_rr individualisé (XAUUSD=1.5, NZDUSD=1.3, BTCUSD=1.8...), mais le
        # trade_executor utilisait cfg.MIN_RR_RATIO=2.5 pour TOUS les symboles.
        # Conséquence: NZDUSD (min_rr=1.3, RR réel=1.6) passait ftmo mais était
        # rejeté à l'exécution car 1.6 < 2.5.
        effective_min_rr = min_rr if min_rr is not None else OrderValidator.MIN_RR
        if rr < effective_min_rr - 0.001:
            return (
                f"RR {rr:.2f} < {effective_min_rr} (symbole min_rr={min_rr or 'global:' + str(OrderValidator.MIN_RR)})"
            )
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
        # Rate limiter par symbole: max 1 trade/30s/symbole
        # 🔧 FIX 21 Juillet 2026: ↓ 6→2 trades/min — les doublons massifs causaient
        # des pertes ×3 sur le même signal. 2/min = 1/30s = pas de doublon intra-cycle.
        self.rate_limiter = PerSymbolRateLimiter(
            max_per_minute=2, window_seconds=60
        )  # Mode sécurisé: 2 trades/min/symbole max
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
        # 🔧 FIX 28 Juillet 2026: Signal fingerprint — dernière ligne anti-doublon
        # Clé: (symbol, action, price_rounded) → timestamp du dernier envoi
        # Vérifié juste avant _place_order() pour bloquer un même signal relancé
        # dans les 60s (même fingerprint = même trade).
        self._recent_trades: dict[tuple, float] = {}
        # 🔧 FIX 28 Août 2026: Initialiser _last_signal_per_symbol dans __init__
        # au lieu du lazy init (hasattr) qui n'est pas thread-safe.
        self._last_signal_per_symbol: dict[tuple, float] = {}
        self._last_cleanup_time: float = 0.0  # pour cleanup périodique

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
        # Limite de positions par direction — uniquement pour signaux normaux (pas high_confidence)
        if existing and not high_confidence:
            sig_type = 0 if action == "BUY" else 1  # POSITION_TYPE_BUY=0, SELL=1
            same_dir_count = sum(1 for p in existing if p.type == sig_type)
            if same_dir_count >= max_per_symbol:
                logger.debug(
                    f"[DOUBLON] {symbol}: déjà {same_dir_count} position(s) {action} "
                    f"(max={max_per_symbol}, ticket={existing[0].ticket}) — skip"
                )
                return None

        # 🔧 FIX 28 Juillet 2026: Price-dedup pour TOUS les signaux (y compris high_confidence)
        # Vérifie si une position existe avec un prix d'entrée quasi-identique (<0.05%, age<600s).
        # Les doublons XAUUSD (9 trades en 30s) avaient des prix à ±0.01-0.02%.
        # 🔧 FIX 20 Août 2026 (Auto-Fixer): fenêtre élargie 0.03%→0.05% / 120s→600s.
        # Les signaux MOM20x3 PERSISTENT tant qu'ils sont actifs (rejoués chaque cycle 15s) :
        # XAUUSD BUY 02:02 + 02:07 (prix 4515.49/4515.12, diff 0.008%) → 2 SL = -338$.
        # À 5 min d'écart, l'ancienne fenêtre 120s ne bloquait pas → 600s couvre le replay
        # d'un même signal (le MOM20x3 reste valide jusqu'à la fermeture de la bougie).
        price = self._get_signal_value(signal, "entry_price")
        if price is not None and price > 0 and existing:
            sig_type = 0 if action == "BUY" else 1
            import time as _time

            _now = _time.time()
            for pos in existing:
                if pos.type == sig_type:
                    price_diff_pct = abs(pos.price_open - price) / max(price, 0.0001) * 100
                    if price_diff_pct < 0.05:  # 0.05% d'écart max (FIX 20/08: était 0.03%)
                        # Vérifier aussi l'age de la position (ouverte < 600s = doublon probable)
                        # 🐛 FIX 10 Août 2026: pos.time (API MT5) est en TEMPS SERVEUR (FTMO-Demo
                        # décalé de ~3h vs time.time() local) → age NÉGATIF permanent → le
                        # price-dedup bloquait TOUS les signaux high_confidence du symbole
                        # (65 faux rejets USDJPY en 15 min le 10/08). On n'applique le price-dedup
                        # QUE si l'age est cohérent.
                        # 🔧🔧 FIX 20 Août 2026 (2nd, Robot Manager): la garde 0 < age < 600
                        # ajoutée le 10/08 a rendu le price-dedup TOTALEMENT INERTE : pos.time
                        # étant en temps serveur (+3h), pos_age = now - pos.time ≈ -10800s →
                        # 0 < age TOUJOURS FAUX → JAMAIS de blocage (0 occurrence "DOUBLON"
                        # dans les logs malgré les doublons XAUUSD 04:53 et 08:28 du 20/08,
                        # chacun -338$ / -113$). Fix : on soustrait l'offset serveur mesuré
                        # (_server_offset_s, fix time-stop du 19/08) AVANT de calculer l'age.
                        server_offset = getattr(getattr(self, "ftmo", None), "_server_offset_s", None)
                        if not isinstance(server_offset, (int, float)) or server_offset == 0:
                            # 🔧 FIX 28 Août 2026: si offset=0 (non mesuré), logger un warning
                            # et skip le price-dedup proprement au lieu de le rendre inerte silencieusement.
                            logger.debug(
                                f"[DOUBLON] {symbol}: server_offset non mesuré (={server_offset}), "
                                f"price-dedup désactivé pour cette itération"
                            )
                        else:
                            pos_age = _now - (getattr(pos, "time", 0) - server_offset)
                            if 0 < pos_age < 600:
                                logger.warning(
                                    f"[DOUBLON] {symbol}: entrée {price:.5f} identique "
                                    f"à pos #{pos.ticket} ({pos.price_open:.5f}, diff={price_diff_pct:.3f}%, "
                                    f"age={pos_age:.0f}s) → skip (high_confidence={high_confidence})"
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

        # Extraire les valeurs ATR du signal pour le logging post-trade
        _atr_val = self._get_signal_value(signal, "atr") or 0
        _sl_atr_val = self._get_signal_value(signal, "sl_atr") or None
        _tp_atr_val = self._get_signal_value(signal, "tp_atr") or None

        if sl is None or tp is None or sl == 0 or tp == 0:
            atr = _atr_val or self._get_signal_value(signal, "atr")
            sl_atr = _sl_atr_val or self._get_signal_value(signal, "sl_atr")
            tp_atr = _tp_atr_val or self._get_signal_value(signal, "tp_atr")
            if None not in (price, atr, sl_atr, tp_atr):
                direction = 0 if action == "BUY" else 1
                # 🔧 FIX #2: _calc_sl_tp n'existe pas sur FTMOProtector.
                # Utiliser ftmo.trailer.calc_sl_tp (la bonne méthode).
                logger.debug(
                    f"[FIX#2] {symbol}: recalcul SL/TP via trailer.calc_sl_tp "
                    f"entry={price:.5f} dir={direction} atr={atr:.5f} sl_atr={sl_atr} tp_atr={tp_atr}"
                )
                sl, tp = self.ftmo.trailer.calc_sl_tp(symbol, price, direction, atr, sl_atr, tp_atr)
                logger.debug(f"[FIX#2] {symbol}: SL={sl} TP={tp} après recalcul")

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
        # 🐛 FIX #13: Utiliser min_rr du SYMBOL_CONFIG (per-symbol) pour la validation RR
        # Le global cfg.MIN_RR_RATIO=2.5 est trop strict pour des symboles comme
        # NZDUSD (min_rr=1.3) ou XAUUSD (min_rr=1.5). On lit depuis la config
        # du symbole directement, comme le fait ftmo_protector.py.
        from engine_simple.strategy import SYMBOL_CONFIG, DEFAULT_SYMBOL_CONFIG

        sym_cfg = SYMBOL_CONFIG.get(symbol, DEFAULT_SYMBOL_CONFIG)
        symbol_min_rr = sym_cfg.get("min_rr", OrderValidator.MIN_RR)
        info = self.mt5.get_symbol_info(symbol)
        err = OrderValidator.validate(symbol, action, lot, price, sl, tp, info, min_rr=symbol_min_rr)
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

        # 🔧 FIX 28 Juillet 2026 + FIX 27 Août 2026: Signal fingerprint — anti-doublon
        # Vérifie si le même (symbol, action, price_arrondi) a déjà été exécuté dans les 120s.
        # 🔧 FIX 27 Août: arrondi 5→2 décimales. Les doublons XAUUSD avaient des prix
        # à 0.001%-0.12% d'écart — le signal cooldown 120s (ci-dessous) est la vraie
        # protection. Le fingerprint est un filet de sécurité secondaire.
        fingerprint = (symbol, action, round(price, 2))
        _now = time.time()
        last_fp_time = self._recent_trades.get(fingerprint)
        if last_fp_time is not None and (_now - last_fp_time) < 120:
            logger.warning(
                f"[FINGERPRINT] {symbol} {action}: signal similaire déjà exécuté il y a "
                f"{_now - last_fp_time:.0f}s (price={price:.2f}) — skip"
            )
            return None
        # 🔧 FIX 27 Août 2026: Cooldown par symbole — 2ème couche anti-doublon
        # Même si le fingerprint échoue (prix légèrement différents), ce cooldown
        # bloque tout trade sur le même symbole dans les 120s.
        # Protège contre les signaux MOM20x3 rejoués chaque cycle 15s.
        _sig_key = (symbol, action)
        _last_sig_time = self._last_signal_per_symbol.get(_sig_key, 0)
        if (_now - _last_sig_time) < 120:
            logger.warning(
                f"[SIGNAL COOLDOWN] {symbol} {action}: dernier signal il y a "
                f"{_now - _last_sig_time:.0f}s (< 120s) — skip"
            )
            return None
        self._last_signal_per_symbol[_sig_key] = _now

        # 🔧 FIX 28 Août 2026: Nettoyage périodique basé sur le TEMPS, pas la taille.
        # L'ancien code (len>100) ne nettoyait jamais en trading lent → memory leak.
        # Nouveau : nettoyage toutes les 60s, supprime les entrées > 300s.
        if _now - self._last_cleanup_time > 60:
            stale = [k for k, ts in self._recent_trades.items() if _now - ts > 300]
            for k in stale:
                del self._recent_trades[k]
            # Nettoyer aussi les signal cooldowns > 300s
            stale_sig = [k for k, ts in self._last_signal_per_symbol.items() if _now - ts > 300]
            for k in stale_sig:
                del self._last_signal_per_symbol[k]
            self._last_cleanup_time = _now
            stale = [k for k, ts in self._recent_trades.items() if _now - ts > 300]
            for k in stale:
                del self._recent_trades[k]

        # try/finally pour libérer les rate limiters en cas d'exception
        result = None
        try:
            regime = self._get_signal_value(signal, "regime") or self._get_signal_value(signal, "_regime") or "RANGING"
            result = self._place_order(symbol, action, lot, price, sl, tp, regime)
            # Enregistrer le fingerprint si l'ordre a réussi
            if result is not None and getattr(result, "retcode", None) == 10009:
                # 🔧 FIX C4: Vérifier partial fill — result.volume doit correspondre
                requested_vol = lot
                filled_vol = getattr(result, "volume", requested_vol)
                if isinstance(filled_vol, (int, float)) and abs(filled_vol - requested_vol) > 0.001:
                    logger.warning(
                        f"[PARTIAL FILL] {symbol}: demandé {requested_vol} lot, "
                        f"fillé {filled_vol} lot (retcode=10009). Risque réel < calculé."
                    )
                self._recent_trades[fingerprint] = time.time()
            # Stocker sl_atr/tp_atr/atr dans le meta pour le logging post-trade
            if (
                result is not None
                and hasattr(result, "retcode")
                and result.retcode == 10009
                and hasattr(result, "order")
                and result.order
            ):
                _atr_meta = {
                    "sl_atr": _sl_atr_val if _sl_atr_val is not None else "",
                    "tp_atr": _tp_atr_val if _tp_atr_val is not None else "",
                    "atr": _atr_val or 0.0,
                }
                self.tracker.add_meta(result.order, _atr_meta)
                logger.debug(
                    f"[ATR-META] {symbol} #{result.order}: "
                    f"sl_atr={_atr_meta['sl_atr']} tp_atr={_atr_meta['tp_atr']} atr={_atr_meta['atr']}"
                )
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
        # 🐛 FIX 28 Juillet 2026: lot=0.0 = gel intentionnel (risk_mult=0.0)
        # Ne pas remplacer par 0.01 — `calculate_lot` retourne 0.0 uniquement
        # quand le symbole est gelé via risk_mult=0.0.
        if lot is None:
            return 0.01  # Fallback sécurisé : lot minimum en cas d'erreur
        if lot <= 0:
            logger.info(f"  [GEL] {symbol}: calculate_lot retourné {lot} → trade refusé (symbole gelé)")
            return 0.0
        # lot > 0: clamping sécurité
        try:
            # 🔧 FIX 28 Août 2026: import supprimé — config_simple déjà importé en haut du fichier
            # (line 13). L'import dans la méthode créait un overhead à chaque appel.

            # 🔧 FIX 10 Juillet 2026: global_max_lot appliqué comme plafond absolu
            _max = min(getattr(cfg, "GLOBAL_MAX_LOT", 10.0), 10.0)
            _min = 0.01  # minimum absolu (lots min pour data collection)
            if lot > _max:
                logger.warning(f"[LOT SAFETY] {symbol}: lot={lot:.3f} > {_max} (global_max_lot clamp)")
                lot = _max
            if lot < _min:
                lot = _min
        except (AttributeError, Exception) as _e:
            logger.debug(f"[LOT SAFETY] config_simple non disponible: {_e}")
        return lot

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
        # 🔧 FIX 6 Juillet 2026: Utilise self.mt5.symbol_select (timeout) au lieu de mt5.symbol_select direct
        try:
            self.mt5.symbol_select(symbol, True)
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
            f"point={info.point if info else '?'} comment={comment} regime={regime}"
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
            # 🐛 FIX 16 Août 2026 (Audit A1): timeout order_send (None) — l'ordre a pu
            # être rempli côté serveur. Vérifier positions_get pour détecter un fill
            # effectif avant de déclarer l'échec. Sinon le cycle suivant ré-ouvrirait
            # le même signal (doublon) — le fingerprint n'est jamais enregistré sur None.
            logger.warning(f"PlaceOrder FAILED {symbol}: no result (timeout ou erreur) — vérification fill effectif...")
            try:
                our_positions = self.mt5.get_positions() or []
                # Cherche une position récente sur ce symbole (moins de 60s)
                for p in our_positions:
                    if getattr(p, "symbol", None) == symbol:
                        pos_time = getattr(p, "time", 0) or 0
                        if pos_time and (time.time() - pos_time) < 60:
                            logger.info(
                                f"  [POST-TIMEOUT] {symbol}: position #{getattr(p, 'ticket', '?')} "
                                f"ouverte récemment (time={pos_time}) — ordre probablement rempli, "
                                f"considéré succès pour éviter doublon"
                            )
                            result = p  # traité comme succès par l'appelant (fingerprint enregistré)
                            return result
                logger.warning(f"  [POST-TIMEOUT] {symbol}: aucune position récente trouvée — échec réel")
            except Exception as e:
                logger.warning(f"  [POST-TIMEOUT] {symbol}: vérification positions impossible: {e}")
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
