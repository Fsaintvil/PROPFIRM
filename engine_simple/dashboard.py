"""Dashboard — Tableau de bord en temps réel du robot.

Génère un rapport complet de l'état du robot incluant :
- Positions ouvertes
- PnL réalisé et non-réalisé
- Métriques par symbole (WR, PF, Sharpe)
- État des modules (Regime, Session, Portfolio, etc.)
- Alertes et recommandations

Usage:
    dashboard = Dashboard()
    report = dashboard.generate_report()
    dashboard.print_report(report)
"""

import json
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger("dashboard")


@dataclass
class PositionInfo:
    """Informations sur une position."""

    symbol: str
    ticket: int
    direction: str  # BUY/SELL
    entry_price: float
    current_price: float
    volume: float
    pnl: float
    pnl_pct: float
    duration_min: float
    regime: str = ""
    session_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ticket": self.ticket,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "volume": self.volume,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "duration_min": self.duration_min,
            "regime": self.regime,
            "session_score": self.session_score,
        }


@dataclass
class SymbolMetrics:
    """Métriques de performance par symbole."""

    symbol: str
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    sharpe: float = 0.0
    avg_trade: float = 0.0
    max_dd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "trades": self.trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_pnl": self.total_pnl,
            "sharpe": self.sharpe,
            "avg_trade": self.avg_trade,
            "max_dd": self.max_dd,
        }


@dataclass
class RobotStatus:
    """État complet du robot."""

    timestamp: str = ""
    uptime_min: float = 0.0
    pid: int = 0
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0

    # Performance
    total_trades: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Drawdown
    current_dd: float = 0.0
    max_dd: float = 0.0
    daily_pnl: float = 0.0
    daily_loss_limit: float = 0.0

    # Positions
    open_positions: int = 0
    positions: list[PositionInfo] = field(default_factory=list)

    # By symbol
    symbol_metrics: dict[str, SymbolMetrics] = field(default_factory=dict)

    # Modules
    regime: str = "UNKNOWN"
    session_active: bool = True
    portfolio_ok: bool = True
    news_blocked: bool = False

    # Alerts
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "uptime_min": self.uptime_min,
            "pid": self.pid,
            "balance": self.balance,
            "equity": self.equity,
            "margin": self.margin,
            "free_margin": self.free_margin,
            "total_trades": self.total_trades,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "current_dd": self.current_dd,
            "max_dd": self.max_dd,
            "daily_pnl": self.daily_pnl,
            "daily_loss_limit": self.daily_loss_limit,
            "open_positions": self.open_positions,
            "positions": [p.to_dict() for p in self.positions],
            "symbol_metrics": {s: m.to_dict() for s, m in self.symbol_metrics.items()},
            "regime": self.regime,
            "session_active": self.session_active,
            "portfolio_ok": self.portfolio_ok,
            "news_blocked": self.news_blocked,
            "alerts": self.alerts,
        }


class Dashboard:
    """Génère le tableau de bord du robot."""

    def __init__(self):
        self._start_time = time.time()

    def generate_report(
        self, robot_state: dict = None, positions: list[dict] = None, metrics: dict = None
    ) -> RobotStatus:
        """Génère un rapport complet.

        Args:
            robot_state: État du robot depuis robot_state.json
            positions: Positions ouvertes depuis MT5
            metrics: Métriques depuis performance_history.json

        Returns:
            RobotStatus complet
        """
        status = RobotStatus()
        status.timestamp = datetime.now(timezone.utc).isoformat()
        status.uptime_min = (time.time() - self._start_time) / 60

        # Parse robot state
        if robot_state:
            status.balance = robot_state.get("balance", 0)
            status.equity = robot_state.get("equity", 0)
            status.total_trades = robot_state.get("total_trades", 0)
            status.total_pnl = robot_state.get("total_profit", 0)
            status.win_rate = robot_state.get("win_rate", 0)
            status.profit_factor = robot_state.get("profit_factor", 0)
            status.current_dd = robot_state.get("current_dd", 0)
            status.max_dd = robot_state.get("max_dd", 0)
            status.daily_pnl = robot_state.get("daily_pnl", 0)
            status.daily_loss_limit = robot_state.get("daily_loss_limit", 4000)

        # Parse positions
        if positions:
            status.open_positions = len(positions)
            for pos in positions:
                pnl = pos.get("profit", 0)
                entry = pos.get("price_open", 0)
                current = pos.get("price_current", 0)
                volume = pos.get("volume", 0)

                pnl_pct = 0
                if entry > 0 and volume > 0:
                    pnl_pct = pnl / (entry * volume) * 100

                duration = 0
                if "time" in pos:
                    duration = (time.time() - pos["time"]) / 60

                status.positions.append(
                    PositionInfo(
                        symbol=pos.get("symbol", ""),
                        ticket=pos.get("ticket", 0),
                        direction="BUY" if pos.get("type", 0) == 0 else "SELL",
                        entry_price=entry,
                        current_price=current,
                        volume=volume,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        duration_min=duration,
                    )
                )

        # Parse metrics
        if metrics:
            for sym, data in metrics.items():
                status.symbol_metrics[sym] = SymbolMetrics(
                    symbol=sym,
                    trades=data.get("trades", 0),
                    win_rate=data.get("win_rate", 0),
                    profit_factor=data.get("profit_factor", 0),
                    total_pnl=data.get("total_pnl", 0),
                    sharpe=data.get("sharpe", 0),
                    avg_trade=data.get("avg_trade", 0),
                    max_dd=data.get("max_dd", 0),
                )

        # Generate alerts
        status.alerts = self._generate_alerts(status)

        return status

    def _generate_alerts(self, status: RobotStatus) -> list[str]:
        """Génère les alertes basées sur l'état actuel."""
        alerts = []

        # DD alerts
        if status.current_dd > 0.07:
            alerts.append(f"🔴 CRITIQUE: DD {status.current_dd:.1%} > 7%")
        elif status.current_dd > 0.05:
            alerts.append(f"🟠 WARNING: DD {status.current_dd:.1%} > 5%")
        elif status.current_dd > 0.03:
            alerts.append(f"🟡 INFO: DD {status.current_dd:.1%} > 3%")

        # Daily loss
        if status.daily_loss_limit > 0:
            daily_loss_pct = abs(status.daily_pnl) / status.daily_loss_limit if status.daily_loss_limit > 0 else 0
            if daily_loss_pct > 0.8:
                alerts.append(f"🔴 WARNING: Daily loss {daily_loss_pct:.0%} de la limite")

        # Win rate
        if status.total_trades > 20:
            if status.win_rate < 0.45:
                alerts.append(f"🟠 WARNING: WR global {status.win_rate:.1%} < 45%")
            elif status.win_rate < 0.50:
                alerts.append(f"🟡 INFO: WR global {status.win_rate:.1%} < 50%")

        # Per-symbol alerts
        for sym, metrics in status.symbol_metrics.items():
            if metrics.trades > 20:
                if metrics.win_rate < 0.40:
                    alerts.append(f"🟠 {sym}: WR {metrics.win_rate:.1%} < 40%")
                if metrics.profit_factor < 1.0:
                    alerts.append(f"🔴 {sym}: PF {metrics.profit_factor:.2f} < 1.0")

        # Position alerts
        for pos in status.positions:
            if pos.pnl < -200:
                alerts.append(f"🟠 {pos.symbol}: PnL non-réalisé {pos.pnl:+.0f}$")
            if pos.duration_min > 480:  # 8 hours
                alerts.append(f"🟡 {pos.symbol}: Position ouverte depuis {pos.duration_min:.0f}min")

        if not alerts:
            alerts.append("✅ Aucune alerte")

        return alerts

    def print_report(self, status: RobotStatus):
        """Affiche le rapport dans le logger."""
        logger.info("=" * 60)
        logger.info("📊 DASHBOARD — ÉTAT DU ROBOT")
        logger.info("=" * 60)

        logger.info(f"  PID: {status.pid} | Uptime: {status.uptime_min:.0f}min")
        logger.info(f"  Balance: ${status.balance:,.0f} | Equity: ${status.equity:,.0f}")
        logger.info(f"  DD: {status.current_dd:.1%} | Max DD: {status.max_dd:.1%}")
        logger.info(f"  Trades: {status.total_trades} | PnL: ${status.total_pnl:+,.0f}")
        logger.info(f"  WR: {status.win_rate:.1%} | PF: {status.profit_factor:.2f}")

        if status.open_positions > 0:
            logger.info(f"\n  📈 POSITIONS ({status.open_positions}):")
            for pos in status.positions:
                logger.info(
                    f"    {pos.symbol} {pos.direction} | "
                    f"Entry={pos.entry_price:.2f} | "
                    f"PnL=${pos.pnl:+.0f} | "
                    f"Durée={pos.duration_min:.0f}min"
                )

        if status.symbol_metrics:
            logger.info(f"\n  📊 PAR SYMBOLE:")
            for sym, m in status.symbol_metrics.items():
                logger.info(
                    f"    {sym}: {m.trades} trades | WR={m.win_rate:.1%} | "
                    f"PF={m.profit_factor:.2f} | PnL=${m.total_pnl:+,.0f}"
                )

        logger.info(f"\n  🚦 MODULES:")
        logger.info(f"    Regime: {status.regime}")
        logger.info(f"    Session: {'✅' if status.session_active else '❌'}")
        logger.info(f"    Portfolio: {'✅' if status.portfolio_ok else '❌'}")
        logger.info(f"    News: {'🔴' if status.news_blocked else '✅'}")

        logger.info(f"\n  ⚠️ ALERTES:")
        for alert in status.alerts:
            logger.info(f"    {alert}")

        logger.info("=" * 60)

    def save_report(self, status: RobotStatus, path: str = "runtime/dashboard.json"):
        """Sauvegarde le rapport en JSON."""
        try:
            with open(path, "w") as f:
                json.dump(status.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"Dashboard save failed: {e}")


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_dashboard = Dashboard()


def generate_report(robot_state: dict = None, positions: list[dict] = None, metrics: dict = None) -> RobotStatus:
    """Génère un rapport (fonction convenience)."""
    return _default_dashboard.generate_report(robot_state, positions, metrics)


def print_report(status: RobotStatus):
    """Affiche le rapport (fonction convenience)."""
    _default_dashboard.print_report(status)
