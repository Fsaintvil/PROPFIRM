import json
import logging
import os
from collections import defaultdict, deque
from datetime import datetime

logger = logging.getLogger("validator")

REGIMES = ["TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL"]
MODEL_NAMES = ["DL_LSTM", "MOM20x3", "LGB"]
DEFAULT_PATH = "runtime/validation_history.json"


class WalkForwardValidator:
    def __init__(self, max_history: int = 200, snapshot_interval: int = 50,
                 path: str = DEFAULT_PATH):
        self.max_history = max_history
        self.snapshot_interval = snapshot_interval
        self.path = path
        self.trades_since_snapshot: int = 0
        self.recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self.recent_regime: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=max_history)))
        self.history: dict[str, list] = defaultdict(list)
        self._load()

    def record(self, model_name: str, correct: bool, regime: str = "",
               symbol: str = "") -> None:
        self.recent[model_name].append(correct)
        if regime:
            self.recent_regime[model_name][regime].append(correct)
        self.trades_since_snapshot += 1
        if self.trades_since_snapshot >= self.snapshot_interval:
            self._snapshot()

    def get_accuracy(self, model_name: str, window: int = 100) -> float | None:
        d = list(self.recent.get(model_name, []))
        d = d[-window:]
        if len(d) < 10:
            return None
        return sum(d) / len(d)

    def get_regime_accuracy(self, model_name: str, regime: str,
                            window: int = 100) -> float | None:
        d = list(self.recent_regime.get(model_name, {}).get(regime, []))
        d = d[-window:]
        if len(d) < 5:
            return None
        return sum(d) / len(d)

    def detect_drift(self, model_name: str, threshold: float = 0.50,
                     window: int = 50) -> bool:
        acc = self.get_accuracy(model_name, window)
        if acc is None:
            return False
        return acc < threshold

    def get_report(self) -> dict:
        report: dict = {}
        for m in MODEL_NAMES:
            acc = self.get_accuracy(m)
            drift = self.detect_drift(m)
            regime_acc = {}
            for r in REGIMES:
                ra = self.get_regime_accuracy(m, r)
                if ra is not None:
                    regime_acc[r] = round(ra, 4)
            report[m] = {
                "accuracy": round(acc, 4) if acc is not None else None,
                "n": len(self.recent.get(m, [])),
                "drift": drift,
                "regime": regime_acc,
            }
        report["accumulated_trades"] = sum(len(v) for v in self.recent.values())
        report["last_snapshot"] = (
            self.history.get(MODEL_NAMES[0], [{}])[-1].get("ts", "")
            if self.history.get(MODEL_NAMES[0]) else "")
        return report

    def _snapshot(self) -> None:
        self.trades_since_snapshot = 0
        ts = datetime.utcnow().isoformat()
        for m in MODEL_NAMES:
            acc = self.get_accuracy(m)
            if acc is not None:
                entry = {"ts": ts, "n": len(self.recent.get(m, [])),
                         "acc": round(acc, 4)}
                self.history[m].append(entry)
        acc_strs = []
        for m in MODEL_NAMES:
            a = self.get_accuracy(m)
            if a is not None:
                acc_strs.append(f"{m}={a:.1%}")
        logger.info(f"[VALIDATOR] Snapshot: {', '.join(acc_strs)}")
        self._save()

    def _save(self) -> None:
        try:
            data = {
                "history": {k: v[-200:] for k, v in self.history.items()},
                "recent_counts": {k: len(v) for k, v in self.recent.items()},
                "recent_regime": {
                    m: {r: list(dq) for r, dq in reg.items()}
                    for m, reg in self.recent_regime.items()
                },
            }
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"[VALIDATOR] Save failed: {e}")

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            for m, entries in data.get("history", {}).items():
                self.history[m] = entries[-200:]
            for m, regimes in data.get("recent_regime", {}).items():
                for r, entries in regimes.items():
                    self.recent_regime[m][r] = deque(entries[-self.max_history:], maxlen=self.max_history)
        except Exception as e:
            logger.warning(f"[VALIDATOR] Load failed: {e}")
