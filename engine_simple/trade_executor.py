"""RateLimiter, OrderValidator, TradeExecutor — exécution."""
import logging
import time

import numpy as np

logger = logging.getLogger("executor")


class RateLimiter:
    def __init__(self, max_per_minute: int = 5, max_orders: int | None = None, window_seconds: int = 60):
        if max_orders is not None:
            max_per_minute = max_orders
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self.timestamps: list[float] = []

    @property
    def remaining(self) -> int:
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t < self.window_seconds]
        return max(0, self.max_per_minute - len(self.timestamps))

    @property
    def recent_count(self) -> int:
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t < self.window_seconds]
        return len(self.timestamps)

    def allow(self) -> bool:
        if self.remaining <= 0:
            return False
        self.timestamps.append(time.time())
        return True


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

    @staticmethod
    def validate(symbol: str, action: str, lot: float,
                 price: float, sl: float, tp: float,
                 symbol_info) -> str | None:
        if lot < OrderValidator.MIN_LOT:
            return "Lot en dessous du minimum"
        if lot > OrderValidator.MAX_LOT:
            return "Lot au dessus du maximum"
        if symbol_info:
            if lot > getattr(symbol_info, "volume_max", 10):
                return "Lot > volume_max broker"
        risk = abs(price - sl) * lot
        reward = abs(tp - price) * lot
        if risk <= 0 or reward <= 0:
            return "SL ou TP invalide"
        rr = reward / risk
        if rr < 2.0:
            return f"RR {rr:.1f} < 2.0"
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
        self.rate_limiter = RateLimiter(max_per_minute=5)

    def _get_signal_value(self, signal, key, default=None):
        if isinstance(signal, dict):
            return signal.get(key, default)
        return getattr(signal, key, default)

    def execute(self, symbol, signal):
        if not self.rate_limiter.allow():
            logger.warning(f"{symbol}: rate limit atteint, skip")
            return None
        action = self._get_signal_value(signal, "action")
        price = self._get_signal_value(signal, "entry_price")
        if price is None:
            tick = self.mt5.get_tick(symbol)
            price = tick.ask if tick else None
        sl = self._get_signal_value(signal, "sl")
        tp = self._get_signal_value(signal, "tp")
        if None in (sl, tp):
            atr = self._get_signal_value(signal, "atr")
            sl_atr = self._get_signal_value(signal, "sl_atr")
            tp_atr = self._get_signal_value(signal, "tp_atr")
            if None not in (price, atr, sl_atr, tp_atr):
                direction = 0 if action == "BUY" else 1
                sl, tp = self.ftmo._calc_sl_tp(symbol, price, direction, atr, sl_atr, tp_atr)
            else:
                sl, tp = None, None
        if None in (price, sl, tp):
            logger.warning(f"{symbol}: SL/TP manquant, skip")
            return None
        lot = self._calc_lot(symbol, price, sl)
        err = OrderValidator.validate(symbol, action, lot, price, sl, tp, None)
        if err:
            logger.warning(f"{symbol}: validation echouee: {err}")
            return None
        return self._place_order(symbol, action, lot, price, sl, tp)

    def _calc_lot(self, symbol, entry, sl):
        lot = self.ftmo.calculate_lot(symbol, entry, sl)
        if lot is not None and lot > 0:
            return lot
        risk_per_trade = 0.004
        account = self.mt5.get_account_info()
        balance = account.balance if account else 100000
        risk_amount = balance * risk_per_trade
        price_risk = abs(entry - sl)
        if price_risk <= 0:
            return 0.01
        return round(risk_amount / (price_risk * 100000), 2)

    def _place_order(self, symbol, action, lot, price, sl, tp):
        import MetaTrader5 as mt5
        direction = 0 if action == "BUY" else 1
        order_type = self.mt5.ORDER_TYPE_BUY if direction == 0 else self.mt5.ORDER_TYPE_SELL
        req = dict(
            action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=lot,
            type=direction, price=price, sl=sl, tp=tp,
            deviation=20, magic=999001,
            comment="ADAPT_RAN",
        )
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"PlaceOrder OK: {symbol} {action} {lot}@{price} SL={sl} TP={tp}")
        elif result:
            logger.warning(f"PlaceOrder FAILED {symbol}: retcode={result.retcode}")
        else:
            logger.warning(f"PlaceOrder FAILED {symbol}: no result")
        return result
