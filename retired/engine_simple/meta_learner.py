import json
import logging
from collections import defaultdict

logger = logging.getLogger("meta_learner")

REGIMES: list[str] = ["TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL"]


class ModelTracker:
    def __init__(self, model_name: str):
        self.model_name: str = model_name
        self.regime_stats: defaultdict = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        self.symbol_stats: defaultdict = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        self.global_stats: dict[str, int] = {"wins": 0, "losses": 0, "total": 0}
        self.regime_penalty: defaultdict = defaultdict(lambda: 1.0)

    def record(self, regime: str, symbol: str, correct: bool) -> None:
        for stats in [self.regime_stats[regime], self.symbol_stats[symbol], self.global_stats]:
            stats["total"] += 1
            if correct:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

    def win_rate(self, regime: str | None = None, symbol: str | None = None, min_trades: int = 3) -> float:
        # Priority: symbol > regime > global
        if symbol and symbol in self.symbol_stats and self.symbol_stats[symbol]["total"] >= min_trades:
            s = self.symbol_stats[symbol]
            return s["wins"] / s["total"]
        if regime and regime in self.regime_stats and self.regime_stats[regime]["total"] >= min_trades:
            s = self.regime_stats[regime]
            return s["wins"] / s["total"]
        if self.global_stats["total"] >= min_trades:
            return self.global_stats["wins"] / self.global_stats["total"]
        return 0.5

    def weight(self, regime: str, base_weight: float = 1.0, symbol: str | None = None) -> float:
        wr = self.win_rate(regime, symbol=symbol)
        penalty = self.regime_penalty.get(regime, 1.0)
        return base_weight * (0.5 + wr) / penalty


class MetaLearner:
    def __init__(self, recalibration_freq: int = 50):
        self.trackers: dict[str, ModelTracker] = {}
        self.regime_performance: defaultdict = defaultdict(lambda: {"win": 0, "loss": 0})
        self.recalibration_freq: int = recalibration_freq
        self.trades_since_recal: int = 0
        self.model_base_weights: dict[str, float] = {
            "MOM20x3": 1.0, "DL_LSTM": 1.0, "LGB": 0.0,
        }
        self._init_trackers()

    def _init_trackers(self) -> None:
        for name in ["DL_LSTM", "MOM20x3", "LGB"]:
            self.trackers[name] = ModelTracker(name)

    def get_model_names(self) -> list[str]:
        return list(self.trackers.keys())

    def record_trade(self, symbol: str, regime: str, predictions_outcomes: dict[str, bool]) -> None:
        for model_name, correct in predictions_outcomes.items():
            if model_name in self.trackers:
                self.trackers[model_name].record(regime, symbol, correct)
                if correct:
                    self.regime_performance[regime]["win"] += 1
                else:
                    self.regime_performance[regime]["loss"] += 1
        self.trades_since_recal += 1

    def get_weights(self, regime: str, symbol: str | None = None) -> dict[str, float]:
        weights: dict[str, float] = {}
        for name, tracker in self.trackers.items():
            bw = self.model_base_weights.get(name, 1.0)
            weights[name] = tracker.weight(regime, base_weight=bw, symbol=symbol)
        total = sum(weights.values()) or 1
        return {k: v / total for k, v in weights.items()}

    def get_ensemble_action(self, regime: str, predictions: dict, symbol: str | None = None) -> tuple[str, float, dict]:
        weights = self.get_weights(regime, symbol=symbol)
        buy_w, sell_w, hold_w = 0.0, 0.0, 0.0
        details: dict = {}
        for model_name, pred in predictions.items():
            w = weights.get(model_name, 1.0 / len(predictions))
            action = pred.get("action", "HOLD")
            score = pred.get("score", 0.5)
            details[model_name] = {"action": action, "weight": w, "score": score}
            if action == "BUY":
                buy_w += w * score
            elif action == "SELL":
                sell_w += w * score
            else:
                hold_w += w * 0.3
        total_w = buy_w + sell_w + hold_w
        if total_w == 0:
            return "HOLD", 0.5, details
        if buy_w > sell_w and buy_w / total_w >= 0.55:
            return "BUY", buy_w / total_w, details
        if sell_w > buy_w and sell_w / total_w >= 0.55:
            return "SELL", sell_w / total_w, details
        return "HOLD", max(buy_w, sell_w) / total_w if total_w > 0 else 0.5, details

    def devil_advocate_check(self, regime: str, predictions: dict,
                             signal_action: str, symbol: str | None = None) -> list:
        disagreement: list = []
        weights = self.get_weights(regime, symbol=symbol)
        for model_name, pred in predictions.items():
            action = pred.get("action", "HOLD")
            w = weights.get(model_name, 0)
            score = pred.get("score", 0)
            if action != "HOLD" and action != signal_action and w > 0.15:
                disagreement.append({"model": model_name, "weight": w, "score": score, "says": action})
        if disagreement and any(d["score"] > 0.65 for d in disagreement):
            models_str = [d["model"] for d in disagreement]
            logger.info(f"  [DEVIL] {len(disagreement)} model(s) disagree with "
                        f"{signal_action}: {models_str}")
            return disagreement
        return []

    def get_regime_win_rate(self, regime: str) -> float:
        s = self.regime_performance[regime]
        if s["win"] + s["loss"] >= 3:
            return s["win"] / (s["win"] + s["loss"])
        return 0.5

    def should_recalibrate(self) -> bool:
        return self.trades_since_recal >= self.recalibration_freq
    
    def initialize_from_history(self, trade_history: list[dict]) -> None:
        """PHASE 2.2: Initialiser les traces à partir des trades historiques.
        
        Simule des prédictions pour les modèles MOM20x3, DL_LSTM, LGB basées
        sur l'historique réel pour bootstrapper les poids du Meta-Learner.
        """
        if not trade_history or len(trade_history) == 0:
            logger.info("[META] Historique vide — initialisation par défaut")
            return
        
        # MOM20x3 a toujours généré le signal → on assume qu'il a raison 100%
        # DL_LSTM + LGB : on assume une performance moyenne (50-60%)
        logger.info(f"[META] Initialisation à partir de {len(trade_history)} trades")
        
        for trade in trade_history[-200:]:  # Derniers 200 trades seulement
            symbol = trade.get("symbol", "UNKNOWN")
            regime = trade.get("regime", "RANGING")
            profit = trade.get("profit", 0)
            correct = profit > 0
            
            # MOM20x3: assume tout le crédit (il a généré les signaux)
            self.trackers["MOM20x3"].record(regime, symbol, correct)
            
            # DL_LSTM: assume 55% de précision (légèrement meilleur que random)
            import random
            random.seed(hash((trade.get("ticket", 0), "DL")) % (2**31))
            dl_correct = random.random() < 0.55
            self.trackers["DL_LSTM"].record(regime, symbol, dl_correct)
            
            # LGB: assume 50% de précision (random)
            random.seed(hash((trade.get("ticket", 0), "LGB")) % (2**31))
            lgb_correct = random.random() < 0.50
            self.trackers["LGB"].record(regime, symbol, lgb_correct)
        
        logger.info(f"[META] Initialisation complétée — "
                   f"MOM20x3 {self.trackers['MOM20x3'].global_stats}, "
                   f"DL_LSTM {self.trackers['DL_LSTM'].global_stats}, "
                   f"LGB {self.trackers['LGB'].global_stats}")
    
    def get_calibration_status(self) -> dict:
        """Retourne l'état de calibration du Meta-Learner."""
        return {
            "trades_recorded": self.trades_since_recal,
            "models": {
                name: {
                    "total_trades": tracker.global_stats["total"],
                    "win_rate": tracker.win_rate(),
                    "weight": self.get_weights("RANGING").get(name, 0),  # Exemple avec RANGING
                }
                for name, tracker in self.trackers.items()
            }
        }

    def save_state(self, path: str = "runtime/meta_learner.json") -> None:
        try:
            data = {}
            for name, tracker in self.trackers.items():
                data[name] = {
                    "regime_penalty": dict(tracker.regime_penalty),
                    "global_stats": tracker.global_stats,
                }
            with open(path, "w") as f:
                json.dump(data, f)
        except (OSError, TypeError) as e:
            logger.warning(f"[META] Save state failed: {e}")

    def load_state(self, path: str = "runtime/meta_learner.json") -> None:
        try:
            with open(path) as f:
                data = json.load(f)
            for name, tracker_data in data.items():
                if name in self.trackers:
                    tracker = self.trackers[name]
                    # Restore global_stats (wins/losses/total) — critical pour le suivi
                    gs = tracker_data.get("global_stats", {})
                    if gs:
                        tracker.global_stats.update({
                            k: int(v) for k, v in gs.items()
                        })
                    # Restore regime_penalty
                    tracker.regime_penalty.update(
                        {k: float(v) for k, v in tracker_data.get("regime_penalty", {}).items()}
                    )
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"[META] Load state failed: {e}")

    def recalibrate(self) -> None:
        self.trades_since_recal = 0
        n_adjusted = 0
        for name, tracker in self.trackers.items():
            global_wr = tracker.win_rate()
            for regime in REGIMES:
                regime_wr = tracker.win_rate(regime=regime)
                if regime_wr < 0.45:
                    tracker.regime_penalty[regime] = min(tracker.regime_penalty.get(regime, 1.0) + 0.1, 1.5)
                    n_adjusted += 1
                elif regime_wr > 0.55:
                    tracker.regime_penalty[regime] = max(tracker.regime_penalty.get(regime, 1.0) - 0.05, 0.5)
                    n_adjusted += 1
            logger.info(f"  [META] {name}: WR={global_wr:.0%} | per regime: " +
                ", ".join(f"{r}={tracker.win_rate(regime=r):.0%}" for r in REGIMES))
        logger.info(f"  [META] Recalibration done: {n_adjusted} regime weights adjusted")
