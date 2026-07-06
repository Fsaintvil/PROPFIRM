"""RiskManager — gestion des risques institutionnelle

Composants :
  - PreTradeChecklist : vérifie toutes les règles de risque avant chaque trade
  - KellySizing : position sizing selon critère de Kelly
  - VaREstimator : Value at Risk (variance-covariance + historique)
  - StressTester : scénarios de stress (3-sigma, gap moves)
  - CircuitBreaker : arrêt automatique si pertes trop rapides
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Optional

import numpy as np

import config_simple as cfg

logger = logging.getLogger("robot.risk")


class PreTradeChecklist:
    """Checklist pré-trade unifiée — évalue TOUTES les règles de risque"""

    def __init__(self, ftmo: Any, audit: Optional[Any] = None) -> None:
        self.ftmo = ftmo
        self.audit = audit

    def check(
        self, symbol: str, signal: Optional[dict[str, Any]] = None, positions: Optional[list[Any]] = None
    ) -> tuple[bool, list[dict[str, Any]]]:
        checks: list[dict[str, Any]] = []
        all_pass = True

        # Pré-vérification SANS signal : skip DANGER_HOURS (vérifié plus tard avec le signal)
        can_trade, reason = self.ftmo.can_trade(symbol, check_danger_hours=False)
        checks.append(
            {
                "rule": "can_trade",
                "pass": can_trade,
                "reason": reason,
                "severity": "critical",
            }
        )
        if not can_trade:
            all_pass = False

        if self.ftmo.challenge_status in ("FAILED_DD", "FAILED_CONSISTENCY", "PASSED"):
            checks.append(
                {
                    "rule": "challenge_status",
                    "pass": False,
                    "reason": f"Challenge status: {self.ftmo.challenge_status}",
                    "severity": "critical",
                }
            )
            all_pass = False

        dd_pct = self.ftmo.current_dd_pct()
        if dd_pct >= self.ftmo.max_dd_pct * 0.8:
            checks.append(
                {
                    "rule": "dd_warning",
                    "pass": False,
                    "reason": f"DD {dd_pct:.1f}% proche de max {self.ftmo.max_dd_pct:.0%}",
                    "severity": "warning",
                }
            )

        if signal:
            rr = signal.get("rr", 0)
            if rr < cfg.MIN_RR_RATIO:
                checks.append(
                    {
                        "rule": "min_rr",
                        "pass": False,
                        "reason": f"RR {rr:.1f} < {cfg.MIN_RR_RATIO}",
                        "severity": "critical",
                    }
                )
                all_pass = False

        if self.audit:
            self.audit.log_risk_check(
                "PASS" if all_pass else "FAIL",
                symbol,
                {c["rule"]: c["pass"] for c in checks},
            )

        return all_pass, checks

    def summary(self, checks: list[dict[str, Any]]) -> str:
        passed = sum(1 for c in checks if c["pass"])
        failed = sum(1 for c in checks if not c["pass"])
        return f"{passed}/{len(checks)} passed, {failed} failed"


class KellySizing:
    """Position sizing selon le critère de Kelly fractionnel

    Kelly optimal = WR - (1-WR) / RR
    Fractionnel : utilise f = Kelly * fraction (ex: 0.25 pour 25%)
    """

    def __init__(self, fraction: float = 0.25, max_risk_pct: float = 0.01) -> None:
        self.fraction = fraction
        self.max_risk_pct = max_risk_pct

    def calculate(self, symbol_perf: Any, rr: float, base_risk: float = cfg.RISK_PER_TRADE) -> float:
        wr = symbol_perf.win_rate if symbol_perf.trades > 0 else 0.5
        rr_avg = symbol_perf.avg_r_multiple if symbol_perf.trades > 0 else rr
        if rr_avg <= 0:
            return base_risk
        kelly = wr - (1 - wr) / rr_avg
        kelly = max(0, min(kelly, 0.5))
        kelly_fractional = kelly * self.fraction
        risk = base_risk * (1 + kelly_fractional)
        risk = min(risk, self.max_risk_pct)
        return risk


class VaREstimator:
    """Value at Risk — estimation paramétrique et historique

    VaR(95%) = -1.645 * sigma * position_value
    CVaR = perte moyenne au-delà de VaR
    """

    def __init__(self, confidence: float = 0.95, lookback: int = 100) -> None:
        self.confidence = confidence
        self.lookback = lookback
        self._returns: deque = deque(maxlen=lookback)

    def add_return(self, r: float) -> None:
        self._returns.append(r)

    def parametric_var(self, position_value: float) -> float:
        if len(self._returns) < 30:
            return position_value * 0.02
        sigma = float(np.std(list(self._returns)))
        z = {0.95: 1.645, 0.99: 2.326}.get(self.confidence, 1.645)
        return z * sigma * position_value

    def historical_var(self, position_value: float) -> float:
        if len(self._returns) < self.lookback // 2:
            return position_value * 0.02
        sorted_ret = sorted(self._returns)
        idx = int(len(sorted_ret) * (1 - self.confidence))
        var_pct = abs(sorted_ret[min(idx, len(sorted_ret) - 1)])
        return var_pct * position_value

    def cvar(self, position_value: float) -> float:
        if len(self._returns) < self.lookback // 2:
            return position_value * 0.03
        sorted_ret = sorted(self._returns)
        idx = int(len(sorted_ret) * (1 - self.confidence))
        tail = [abs(r) for r in sorted_ret[: max(idx, 1)]]
        return float(np.mean(tail)) * position_value


class StressTester:
    """Scénarios de stress — what-if analysis"""

    def __init__(self) -> None:
        self.scenarios: dict[str, dict[str, Any]] = {
            "3sigma_down": {"move_sigma": -3, "label": "3σ baisse"},
            "3sigma_up": {"move_sigma": 3, "label": "3σ hausse"},
            "gap_down_1pct": {"move_pct": -0.01, "label": "Gap -1%"},
            "gap_up_1pct": {"move_pct": 0.01, "label": "Gap +1%"},
            "flash_crash_2pct": {"move_pct": -0.02, "label": "Flash crash -2%"},
        }

    def run(
        self, symbol: str, entry: float, sl: float, lot: float, atr: float, price: float, action: str = "BUY"
    ) -> dict[str, dict[str, Any]]:
        direction = 1 if action.upper() == "BUY" else -1
        results: dict[str, dict[str, Any]] = {}
        for name, scenario in self.scenarios.items():
            move = scenario["move_sigma"] * atr if "move_sigma" in scenario else price * scenario["move_pct"]
            new_price = price + move
            pnl = (new_price - entry) * direction * lot * 100000
            hits_sl = (move < 0 and new_price <= sl) or (move > 0 and new_price >= sl)
            results[name] = {
                "pnl_estimate": round(pnl, 2),
                "hits_sl": hits_sl,
                "worst_case": (sl - entry) * direction * lot * 100000,
            }
        return results


class CircuitBreaker:
    """Coupe-circuit automatique — arrête le trading si conditions anormales

    Déclenché si :
      - perte > X% en N minutes
      - N pertes consécutives
      - drawdown > seuil
      - volatilité extrême
    """

    def __init__(self, max_loss_pct: float = 0.03, window_minutes: int = 30, max_consecutive: int = 5) -> None:
        self.max_loss_pct = max_loss_pct
        self.window_minutes = window_minutes
        self.max_consecutive = max_consecutive
        self._pnl_snapshots: deque = deque()
        self._tripped = False
        self._trip_time: float = 0
        self._cooldown_seconds: int = 1800

    def update(self, current_equity: float, reference_equity: float) -> None:
        now = time.time()
        self._pnl_snapshots.append((now, current_equity))
        while self._pnl_snapshots and now - self._pnl_snapshots[0][0] > self.window_minutes * 60:
            self._pnl_snapshots.popleft()

    def check(
        self, current_equity: float, reference_equity: float, consecutive_losses: int, ftmo: Optional[Any] = None
    ) -> bool:
        if self._tripped:
            if time.time() - self._trip_time > self._cooldown_seconds:
                self._tripped = False
                # NE PAS reset ftmo.consecutive_losses — le FTMO Protector a son propre
                # mécanisme d'auto-pause (AUTO_PAUSE_LOSSES). Si le circuit breaker
                # reset ce compteur, les pertes historiques sont perdues et le premier
                # trade après cooldown repart sans protection.
                logger.info("[CIRCUIT BREAKER] Reset apres cooldown")
                return False
            return True

        loss_pct = (reference_equity - current_equity) / max(reference_equity, 1)
        if loss_pct >= self.max_loss_pct:
            logger.warning(
                f"[CIRCUIT BREAKER] Perte {loss_pct:.1%} > {self.max_loss_pct:.1%} en {self.window_minutes}min"
            )
            self._trip(loss_pct, "loss_threshold")
            return True

        if consecutive_losses >= self.max_consecutive:
            logger.warning(f"[CIRCUIT BREAKER] {consecutive_losses} pertes consecutives >= {self.max_consecutive}")
            self._trip(consecutive_losses, "consecutive_losses")
            return True

        return False

    def _trip(self, value: float, reason: str) -> None:
        self._tripped = True
        self._trip_time = time.time()
        logger.critical(
            f"[CIRCUIT BREAKER] TRIP: {reason}={value} — trading suspendu {self._cooldown_seconds // 60}min"
        )

    @property
    def is_tripped(self) -> bool:
        return self._tripped


class RiskManager:
    """Gestionnaire de risques unifié — point d'entrée unique pour tous les contrôles"""

    def __init__(self, ftmo: Any, audit: Optional[Any] = None) -> None:
        self.ftmo = ftmo
        self.audit = audit
        self.checklist = PreTradeChecklist(ftmo, audit)
        self.kelly = KellySizing()
        self.var_estimator = VaREstimator()
        self.stress_tester = StressTester()
        self.circuit_breaker = CircuitBreaker()
        self._last_circuit_check: float = 0

    def pre_trade(
        self, symbol: str, signal: Optional[dict[str, Any]] = None, positions: Optional[list[Any]] = None
    ) -> tuple[bool, list[dict[str, Any]]]:
        return self.checklist.check(symbol, signal, positions)

    def calculate_position_risk(self, symbol_perf: Any, rr: float) -> float:
        return self.kelly.calculate(symbol_perf, rr)

    def check_circuit(
        self, equity: float, reference: float, consecutive_losses: int, ftmo: Optional[Any] = None
    ) -> bool:
        return self.circuit_breaker.check(equity, reference, consecutive_losses, ftmo=ftmo)

    def estimate_var(self, position_value: float) -> float:
        return self.var_estimator.parametric_var(position_value)

    def stress_test(
        self, symbol: str, entry: float, sl: float, lot: float, atr: float, price: float, action: str = "BUY"
    ) -> dict[str, dict[str, Any]]:
        return self.stress_tester.run(symbol, entry, sl, lot, atr, price, action=action)

    def update(self, equity: float, reference: float) -> None:
        self.circuit_breaker.update(equity, reference)

    def summary(self) -> dict[str, Any]:
        return {
            "circuit_tripped": self.circuit_breaker.is_tripped,
            "var_95": round(self.var_estimator.parametric_var(100000), 2),
            "var_samples": len(self.var_estimator._returns),
        }
