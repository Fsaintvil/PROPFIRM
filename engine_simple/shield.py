"""Protection FTMO — FTMOAccount (état du challenge).

FTMOAccount : état du challenge (DD, daily loss, consistance, min jours).
PositionGuard SUPPRIMÉ (FIX #23 — hardcodait ATR=0.005 = stop-out garanti).
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("shield")


class FTMOAccount:
    """État du challenge FTMO : DD, daily loss, consistance, min jours."""

    STATE_PATH = Path("runtime/shield_state.json")

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
        # Écriture atomique : temp puis rename
        tmp = self.STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(self.STATE_PATH)
        # H-01: Synchronisation croisée avec robot_state.json
        try:
            _main_state_path = Path("runtime/robot_state.json")
            if _main_state_path.exists():
                main_data = json.loads(_main_state_path.read_text())
                main_data["status"] = self.status
                main_data["consecutive_losses"] = self.consecutive_losses
                main_data["total_profit"] = self.total_profit
                main_data["total_trades"] = self.total_trades
                main_data["trading_days"] = list(self.trading_days)
                main_data["daily_pnl"] = {str(k): v for k, v in self.daily_pnl.items()}
                main_tmp = _main_state_path.with_suffix(".tmp")
                main_tmp.write_text(json.dumps(main_data, default=str))
                main_tmp.replace(_main_state_path)
        except Exception as e:
            logger.debug(f"[SHIELD] Sync vers robot_state.json ignoré: {e}")

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



