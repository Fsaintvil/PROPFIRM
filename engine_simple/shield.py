"""Protection FTMO — moteur de risque challenge

FTMOAccount : état du challenge (DD, daily loss, consistance, min jours)
PositionGuard : trailing ATR, partial TP, time-stop
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("shield")

TRAILING_BY_REGIME = {
    "TREND_UP":    [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "TREND_DOWN":  [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "RANGING":     [(1.00, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
    "HIGH_VOL":    [(1.00, 1.00), (2.00, 0.70), (3.00, 0.50), (5.00, 0.25)],
    "LOW_VOL":     [(1.00, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
}

BE_BUFFER_BY_REGIME = {
    "TREND_UP": 0.60, "TREND_DOWN": 0.60,
    "RANGING": 0.80, "HIGH_VOL": 1.00, "LOW_VOL": 0.50,
}

FIRST_LOCK_ATR = 1.0


class FTMOAccount:
    """État du challenge FTMO : DD, daily loss, consistance, min jours."""

    STATE_PATH = Path("runtime/robot_state.json")

    def __init__(self, initial_balance: float, peak_equity: float, current_balance: float):
        self.initial_balance = initial_balance
        self.peak_equity = peak_equity
        self.current_balance = current_balance
        self.total_trades = 0
        self.total_profit = 0.0
        self.consecutive_losses = 0
        self.global_cooldown_until = 0.0
        self.trading_days: set[str] = set()
        self.daily_pnl: dict[str, float] = {}
        self.status = "ACTIVE"
        self.daily_start_equity = current_balance

    @property
    def drawdown_pct(self) -> float:
        return (self.peak_equity - self.current_balance) / max(self.peak_equity, 1)

    @property
    def profit_pct(self) -> float:
        return (self.current_balance - self.initial_balance) / max(self.initial_balance, 1)

    def record_trade(self, pnl: float, date: str | None = None) -> None:
        self.total_trades += 1
        self.total_profit += pnl
        self.current_balance += pnl
        if pnl > 0:
            self.consecutive_losses = 0
            if self.current_balance > self.peak_equity:
                self.peak_equity = self.current_balance
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                self.global_cooldown_until = time.time() + 1800
                logger.warning("3 pertes consecutives → pause globale 30min")
        date = date or str(datetime.utcnow().date())
        self.trading_days.add(date)
        self.daily_pnl[date] = self.daily_pnl.get(date, 0) + pnl

    def can_trade(self, max_daily_loss_pct: float = 0.02, max_dd_pct: float = 0.10) -> bool:
        if self.status != "ACTIVE":
            return False
        if self.global_cooldown_until > time.time():
            return False
        if self.drawdown_pct >= max_dd_pct:
            return False
        today = str(datetime.utcnow().date())
        daily_loss = self.daily_pnl.get(today, 0)
        if daily_loss < 0 and abs(daily_loss) / max(self.initial_balance, 1) >= max_daily_loss_pct:
            return False
        return True

    def check_failure(self, max_dd_pct: float = 0.10, max_daily_loss_pct: float = 0.02) -> str | None:
        if self.drawdown_pct >= max_dd_pct:
            self.status = "FAILED_DD"
            return "FAILED_DD"
        today = str(datetime.utcnow().date())
        daily_loss = self.daily_pnl.get(today, 0)
        if daily_loss < 0 and abs(daily_loss) / max(self.initial_balance, 1) >= max_daily_loss_pct:
            self.status = "FAILED_DAILY_LOSS"
            return "FAILED_DAILY_LOSS"
        return None

    def check_pass(self, profit_target_pct: float = 0.10,
                   consistency_max_pct: float = 0.30,
                   min_trading_days: int = 10,
                   max_trading_days: int = 0) -> bool:
        if self.profit_pct < profit_target_pct:
            return False
        if len(self.trading_days) < min_trading_days:
            return False
        if self._consistency_violated(consistency_max_pct):
            return False
        if max_trading_days > 0 and len(self.trading_days) > max_trading_days:
            self.status = "FAILED_MAX_DAYS"
            return False
        self.status = "PASSED"
        return True

    def _consistency_violated(self, consistency_max_pct: float) -> bool:
        if self.total_profit <= 0:
            return False
        for day_pnl in self.daily_pnl.values():
            if day_pnl > 0 and (day_pnl / self.total_profit) > consistency_max_pct:
                return True
        return False

    def save(self) -> None:
        data = {
            "initial_balance": self.initial_balance,
            "peak_equity": self.peak_equity,
            "current_balance": self.current_balance,
            "total_trades": self.total_trades,
            "total_profit": self.total_profit,
            "consecutive_losses": self.consecutive_losses,
            "global_cooldown_until": self.global_cooldown_until,
            "trading_days": list(self.trading_days),
            "daily_pnl": self.daily_pnl,
            "status": self.status,
            "daily_start_equity": self.daily_start_equity,
        }
        self.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.STATE_PATH.write_text(json.dumps(data, default=str))

    @classmethod
    def load(cls) -> "FTMOAccount | None":
        try:
            data = json.loads(cls.STATE_PATH.read_text())
            acc = cls(
                initial_balance=data["initial_balance"],
                peak_equity=data["peak_equity"],
                current_balance=data["current_balance"],
            )
            acc.total_trades = data.get("total_trades", 0)
            acc.total_profit = data.get("total_profit", 0)
            acc.consecutive_losses = data.get("consecutive_losses", 0)
            acc.global_cooldown_until = data.get("global_cooldown_until", 0)
            acc.trading_days = set(data.get("trading_days", []))
            acc.daily_pnl = data.get("daily_pnl", {})
            acc.status = data.get("status", "ACTIVE")
            acc.daily_start_equity = data.get("daily_start_equity", data["current_balance"])
            return acc
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Impossible de charger l'etat FTMO: {e}")
            return None


class PositionGuard:
    """Surveillance des positions : trailing ATR, partial TP, time-stop."""

    MAX_POSITION_HOURS = 48

    def __init__(self):
        self.open_times: dict[str, datetime] = {}
        self.peak_prices: dict[str, float] = {}
        self.partial_closed: set[str] = set()
        self.regimes: dict[str, str] = {}
        self.max_profit_mult: dict[str, float] = {}

    def track(self, ticket: str, regime: str, entry_price: float) -> None:
        self.open_times[ticket] = datetime.utcnow()
        self.peak_prices[ticket] = entry_price
        self.partial_closed.discard(ticket)
        self.regimes[ticket] = regime
        self.max_profit_mult[ticket] = 0.0

    def reconcile(self, tickets: list[str]) -> None:
        known = set(tickets)
        for t in list(self.open_times.keys()):
            if t not in known:
                del self.open_times[t]
                self.peak_prices.pop(t, None)
                self.partial_closed.discard(t)
                self.regimes.pop(t, None)
                self.max_profit_mult.pop(t, None)

    def check(self, ticket: str, price: float, age_minutes: float,
              atr_val: float, entry_price: float, sl_price: float,
              tp_price: float | None = None) -> dict:
        regime = self.regimes.get(ticket, "RANGING")
        peak = self.peak_prices.get(ticket, entry_price)
        if price > peak:
            self.peak_prices[ticket] = price
            peak = price

        profit_dist = abs(peak - entry_price) if entry_price > 0 else 0
        profit_atr = profit_dist / max(atr_val, 1e-10)
        self.max_profit_mult[ticket] = max(self.max_profit_mult.get(ticket, 0), profit_atr)
        dist_from_peak = abs(peak - price)

        # Time-stop
        if age_minutes > self.MAX_POSITION_HOURS * 60:
            return {"action": "close", "reason": "time_stop", "sl": sl_price}

        # Partial TP (50% à 60% du TP)
        if ticket not in self.partial_closed and tp_price:
            progress = (price - entry_price) / (tp_price - entry_price) if abs(tp_price - entry_price) > 1e-10 else 0
            if abs(progress) > 0.60:
                be_buffer = BE_BUFFER_BY_REGIME.get(regime, 0.80)
                be_sl = entry_price + be_buffer * atr_val * (1 if price > entry_price else -1)
                return {"action": "partial", "reason": "partial_tp_60", "sl": be_sl}

        # Trailing stop
        if profit_atr >= FIRST_LOCK_ATR:
            levels = TRAILING_BY_REGIME.get(regime, TRAILING_BY_REGIME["RANGING"])
            trail_mult = levels[-1][1]
            for threshold, mult in levels:
                if profit_atr >= threshold:
                    trail_mult = mult
            new_sl = peak - trail_mult * atr_val
            if dist_from_peak > trail_mult * atr_val:
                if new_sl > sl_price:
                    return {"action": "trail", "reason": "trailing", "sl": new_sl}

        return {"action": "hold", "reason": "", "sl": sl_price}
