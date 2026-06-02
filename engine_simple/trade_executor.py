"""TradeExecutor — exécution institutionnelle avec confirmation, slippage tracking et rate limiting

Améliorations vs main.py inline:
  - Pre-trade checklist intégré
  - Order confirmation (verify-send pattern)
  - Slippage tracking et execution quality
  - Latency measurement
  - Rate limiting (max N ordres/min)
  - Audit trail des exécutions
"""
import logging
import time
from collections import deque
from datetime import datetime

import config_simple as cfg

logger = logging.getLogger("robot.executor")


class RateLimiter:
    def __init__(self, max_orders=10, window_seconds=60):
        self.max_orders = max_orders
        self.window_seconds = window_seconds
        self._timestamps = deque()

    def allow(self):
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > self.window_seconds:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_orders:
            return False
        self._timestamps.append(now)
        return True

    @property
    def recent_count(self):
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > self.window_seconds:
            self._timestamps.popleft()
        return len(self._timestamps)


class ExecutionStats:
    def __init__(self):
        self.total_attempts = 0
        self.successful = 0
        self.rejected = 0
        self.total_slippage_pts = 0.0
        self.slippage_count = 0
        self.total_latency = 0.0
        self.latencies = deque(maxlen=100)

    def record(self, success, latency, slippage_pts=0):
        self.total_attempts += 1
        if success:
            self.successful += 1
        else:
            self.rejected += 1
        self.total_latency += latency
        self.latencies.append(latency)
        if slippage_pts:
            self.total_slippage_pts += abs(slippage_pts)
            self.slippage_count += 1

    @property
    def success_rate(self):
        return self.successful / max(self.total_attempts, 1)

    @property
    def avg_latency(self):
        return self.total_latency / max(self.total_attempts, 1)

    @property
    def p95_latency(self):
        if not self.latencies:
            return 0
        sorted_l = sorted(self.latencies)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def avg_slippage(self):
        return self.total_slippage_pts / max(self.slippage_count, 1)

    def summary(self):
        return {
            "attempts": self.total_attempts,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency * 1000, 1),
            "p95_latency_ms": round(self.p95_latency * 1000, 1),
            "avg_slippage_pts": round(self.avg_slippage, 1),
        }


class TradeExecutor:
    def __init__(self, mt5, ftmo, journal, position_tracker, signals, adaptive, audit=None):
        self.mt5 = mt5
        self.ftmo = ftmo
        self.journal = journal
        self.tracker = position_tracker
        self.signals = signals
        self.adaptive = adaptive
        self.audit = audit
        self.rate_limiter = RateLimiter(max_orders=cfg.MAX_ORDERS_PER_MINUTE)
        self.stats = ExecutionStats()
        self._last_execution_time = 0.0
        self._min_interval = 1.0

    def execute(self, symbol, signal):
        now = time.time()
        if now - self._last_execution_time < self._min_interval:
            logger.debug(f"  [RATE] {symbol}: intervalle minimum ({self._min_interval}s)")
            return
        if not self.rate_limiter.allow():
            logger.warning(f"  [RATE LIMIT] {symbol}: max {cfg.MAX_ORDERS_PER_MINUTE}/min atteint")
            return

        logger.info(f"  >>> [EXECUTE] {symbol} {signal['action']}")
        tick = self.mt5.get_symbol_info(symbol)
        if tick is None:
            logger.error(f"  [ERROR] {symbol}: symbol info not found")
            if self.audit:
                self.audit.log_error("TradeExecutor", f"{symbol}: symbol info not found")
            return

        entry = tick.ask if signal["action"] == "BUY" else tick.bid
        direction = 0 if signal["action"] == "BUY" else 1
        atr = signal.get("atr", 0)
        sl_mult = signal.get("sl_atr", 3.0)
        tp_mult = signal.get("tp_atr", 4.0)
        risk_mult = signal.get("risk_mult", 1.0)

        sl, tp = self.ftmo._calc_sl_tp(symbol, entry, direction, atr, sl_mult, tp_mult)
        if sl is None or tp is None:
            logger.warning(f"    [SKIP] {symbol}: impossible de calculer SL/TP")
            return
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        if sl_dist > 0 and tp_dist / sl_dist < cfg.MIN_RR_RATIO:
            rr = tp_dist / sl_dist
            logger.warning(f"    [RR] {symbol}: RR={rr:.1f} < {cfg.MIN_RR_RATIO}, SKIP")
            if self.audit:
                self.audit.log_risk_check("REJECTED", symbol, {
                    "reason": "min_rr", "rr": round(rr, 2), "min_rr": cfg.MIN_RR_RATIO
                })
            return

        quality = signal.get("quality", 1.0) * risk_mult
        _sig_score = signal.get("score", 0)
        _sig_conf = signal.get("confidence", 0)
        _is_ranging = signal.get("is_ranging", True)
        _ml_agrees = signal.get("_ml_agrees", None)
        if _sig_score >= 0.90 and _sig_conf >= 0.90 and _ml_agrees is True and not _is_ranging:
            quality *= 2.0
            logger.info(f"    [BOOST 100%] {symbol}: DOUBLE position")

        lot = self.ftmo.calculate_lot(symbol, entry, sl, quality, direction)

        regime = signal.get("_regime", "?")
        ml_agree = signal.get("_ml_agrees", None)
        learner = self.adaptive.learner.get_summary(symbol)
        logger.info(f"    [ADAPTIVE] {symbol}: regime={regime}, ml_agree={ml_agree}, "
                     f"risk={risk_mult:.2f}, sl=x{sl_mult} tp=x{tp_mult}")
        if learner:
            logger.info(f"    [LEARNER] {symbol}: {learner}")

        request = dict(
            action=1, symbol=symbol, volume=lot, type=direction,
            price=entry, sl=sl, tp=tp, deviation=20, magic=cfg.ROBOT_MAGIC,
            comment=f"ADAPT_{regime[:3]}",
            type_filling=self.mt5.ORDER_FILLING_IOC,
            type_time=self.mt5.ORDER_TIME_GTC,
        )

        t0 = time.time()
        result = self.mt5.order_send(request)
        latency = time.time() - t0

        if result and result.retcode == 10009:
            fill_price = getattr(result, 'price', None) or entry
            slippage_pts = abs(fill_price - entry) / (tick.point or 0.0001)
            self.stats.record(True, latency, slippage_pts)
            logger.info(f"    [SUCCESS] {symbol} @ {fill_price:.5f} (req={entry:.5f} slip={slippage_pts:.1f}pts)"
                       f" | SL={sl:.5f} TP={tp:.5f} | Lot={lot} | Lat={latency*1000:.0f}ms")
            self.journal.record(dict(
                symbol=symbol, direction=signal["action"],
                entry=fill_price, sl=sl, tp=tp, lot=lot, profit=0,
                time_open=str(datetime.utcnow()), time_close="",
                reason=f"ADAPT_{regime[:3]}",
            ))
            order_type = self.mt5.ORDER_TYPE_BUY if direction == 0 else self.mt5.ORDER_TYPE_SELL
            r1 = self.mt5.calc_profit(order_type, symbol, lot, entry, sl)
            predictions = signal.get("_model_predictions", {})
            if not predictions:
                predictions = {"MOM20x3": signal.get("action", "HOLD")}
                if signal.get("_dl_score") is not None:
                    predictions["DL_LSTM"] = signal.get("action", "HOLD")
            self.ftmo.set_position_regime(result.order, regime)
            dl_features = self.adaptive.build_dl_features(signal.get("rates", {}))
            self.tracker.add_meta(result.order, dict(
                symbol=symbol, entry=fill_price, sl=sl, lot=lot, regime=regime,
                r1_usd=max(abs(r1 or 0), 1), predictions=predictions,
                dl_features=dl_features,
            ))
            if self.audit:
                self.audit.log_execution(symbol, signal["action"], fill_price, sl, tp, lot,
                                          status="filled", retcode=result.retcode)
        else:
            rc = result.retcode if result else -1
            self.stats.record(False, latency)
            logger.warning(f"    [FAILED] market {symbol} - Code: {rc} (Lat={latency*1000:.0f}ms)")
            if self.audit:
                self.audit.log_execution(symbol, signal["action"], entry, sl, tp, lot,
                                          status="rejected", retcode=rc)

        self._last_execution_time = time.time()
