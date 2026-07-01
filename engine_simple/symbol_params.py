"""SymbolParamManager — Paramètres unifiés par symbole.

Source de vérité unique pour TOUS les paramètres du robot.
Combine config statique (strategy.py), paramètres adaptés (OnlineLearner),
et métriques calculées (WR, PnL, PF) en un seul dict par symbole.

Chaque paramètre est variable par symbole avec fallback global.

Utilisation:
    from engine_simple.symbol_params import get_symbol_params
    params = get_symbol_params("EURUSD")
    # params = {
    #     "risk_mult": 1.0, "min_score": 0.70, "min_rr": 1.5,
    #     "WR_all": 0.47, "WR_50": 0.45, "Trades": 55, "PnL": 35.90, "PF": 1.35,
    #     "cfg_score": 0.70, "dyn_score": 0.80, "conf": 0.85,
    #     "lots": {"base": 0.01, "max": 0.10, "current": 0.01},
    #     "cooldown_minutes": 15, "auto_pause_losses": 5,
    #     "adx_thresh": 22, "max_spread_points": 40,
    #     "max_positions_per_symbol": 6,
    #     "lot_progression": [{"wr_min": 0.70, "lot": 0.02}, ...],
    #     "trailing_levels": [...], "be_buffer": 0.80, ...
    # }
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

# Lazy imports pour éviter les circular imports
# strategy.SYMBOL_CONFIG, position_tracker, ftmo_protector, adaptive_intelligence
import config_simple as cfg

logger = logging.getLogger("robot.symbol_params")

# ═════════════════════════════════════════════════════════════════════════════
# Configuration de lot progressif — règles globales
# ═════════════════════════════════════════════════════════════════════════════

LOT_PROGRESSION_RULES = [
    # (win_rate_min, win_rate_max, lot_size)
    (0.00, 0.60, 0.01),  # WR < 60%  → lot minimum
    (0.60, 0.65, 0.02),  # WR 60-65% → lot 0.02
    (0.65, 0.70, 0.03),  # WR 65-70% → lot 0.03
    (0.70, 0.75, 0.05),  # WR 70-75% → lot 0.05
    (0.75, 0.80, 0.07),  # WR 75-80% → lot 0.07
    (0.80, 1.01, 0.10),  # WR ≥ 80%  → lot 0.10
]


def get_lot_for_wr(wr: float, lot_base: float = 0.01, lot_max: float = 0.10) -> float:
    """Calcule la taille de lot selon le win rate."""
    if wr <= 0:
        return lot_base
    for lo, hi, lot in LOT_PROGRESSION_RULES:
        if lo <= wr < hi:
            return min(max(lot, lot_base), lot_max)
    return lot_base


# ═════════════════════════════════════════════════════════════════════════════
# SymbolParamManager
# ═════════════════════════════════════════════════════════════════════════════


class SymbolParamManager:
    """Gestionnaire centralisé des paramètres par symbole.

    Agrège les paramètres de :
    - strategy.py SYMBOL_CONFIG (config statique, source de vérité)
    - OnlineLearner (paramètres adaptés)
    - PositionTracker (métriques calculées)
    - FTMO Protector (dyn_score, cooldowns)
    """

    def __init__(self):
        self._tracker = None  # PositionTracker (injecté après init)
        self._ftmo = None  # FTMOProtector (injecté après init)
        self._learner = None  # OnlineLearner (injecté après init)
        self._dyn_scores: dict[str, float] = {}  # per-symbol dynamic min_score
        self._cfg: dict | None = None  # config_simple flat dict

    @property
    def cfg(self):
        if self._cfg is None:
            self._cfg = cfg.__dict__
        return self._cfg

    # ── Injection des dépendances ───────────────────────────────────────

    def set_tracker(self, tracker):
        """Injecte le PositionTracker."""
        self._tracker = tracker

    def set_ftmo(self, ftmo):
        """Injecte le FTMOProtector."""
        self._ftmo = ftmo

    def set_learner(self, learner):
        """Injecte l'OnlineLearner."""
        self._learner = learner

    # ── Dynamic min_score ───────────────────────────────────────────────

    def update_dyn_score(self, symbol: str, score: float):
        """Met à jour le dynamic min_score pour un symbole (appelé par ftmo_protector)."""
        self._dyn_scores[symbol] = score

    def get_dyn_score(self, symbol: str) -> float | None:
        """Retourne le dynamic min_score ou None si pas encore calculé."""
        return self._dyn_scores.get(symbol)

    # ── OnlineLearner params ────────────────────────────────────────────

    def _get_ol_params(self, symbol: str) -> dict:
        """Récupère les paramètres adaptés par l'OnlineLearner."""
        if self._learner is None:
            return {}
        try:
            ol = self._learner.get_params(symbol, base_thresh=2.5)
            return {
                "ol_thresh": ol.get("thresh"),
                "ol_risk_mult": ol.get("risk_mult"),
                "ol_sl_mult": ol.get("sl_mult"),
                "ol_tp_mult": ol.get("tp_mult"),
                "ol_sample_size": ol.get("sample_size", 0),
            }
        except Exception as e:
            logger.debug(f"  [PARAMS] OL indisponible pour {symbol}: {e}")
            return {}

    # ── PositionTracker metrics ─────────────────────────────────────────

    def _get_tracker_metrics(self, symbol: str) -> dict:
        """Récupère les métriques calculées depuis le PositionTracker."""
        if self._tracker is None:
            return {}
        try:
            perf = self._tracker.get_symbol_performance(symbol)
            if perf is None:
                return {}
            return {
                "Trades": perf.trades,
                "Wins": perf.wins,
                "Losses": perf.losses,
                "WR_all": round(perf.win_rate, 4),
                "PnL": round(perf.total_profit, 2),
                "PF": round(perf.profit_factor, 4),
                "avg_rr": round(perf.avg_r_multiple, 4),
                "gross_profit": round(perf.gross_profit, 2),
                "gross_loss": round(perf.gross_loss, 2),
                "consecutive_wins": perf.consecutive_wins,
                "consecutive_losses": perf.consecutive_losses,
                "max_consecutive_wins": perf.max_consecutive_wins,
                "max_consecutive_losses": perf.max_consecutive_losses,
            }
        except Exception as e:
            logger.debug(f"  [PARAMS] Tracker indisponible pour {symbol}: {e}")
            return {}

    # ── WR sur fenêtre glissante (50 derniers trades) ───────────────────

    def _get_wr_50(self, symbol: str) -> dict:
        """Win rate sur les 50 derniers trades depuis le FTMO Protector."""
        if self._ftmo is None:
            return {"WR_50": None, "WR_50_trades": 0}
        try:
            history = self._ftmo._symbol_trade_history.get(symbol, [])
            if len(history) < 5:
                return {"WR_50": None, "WR_50_trades": len(history)}
            recent = history[-50:]
            wins = sum(1 for t in recent if t.get("profit", 0) > 0)
            return {
                "WR_50": round(wins / len(recent), 4),
                "WR_50_trades": len(recent),
                "WR_50_wins": wins,
            }
        except Exception as e:
            logger.debug(f"  [PARAMS] WR_50 indisponible pour {symbol}: {e}")
            return {"WR_50": None, "WR_50_trades": 0}

    # ── Config statique (strategy.py) ───────────────────────────────────

    def _get_static_config(self, symbol: str) -> dict:
        """Récupère la config statique depuis strategy.py SYMBOL_CONFIG.
        Tous les paramètres ont un fallback dans DEFAULT_SYMBOL_CONFIG."""
        from engine_simple.strategy import get_symbol_full_config

        return get_symbol_full_config(symbol)

    # ── Paramètres additionnels depuis config_simple ────────────────────

    def _get_global_fallbacks(self, symbol: str) -> dict:
        """Paramètres globaux avec override possible par symbole."""
        c = self.cfg
        strat_cfg = self._get_static_config(symbol)
        return {
            "max_spread_points": strat_cfg.get("max_spread_points", c.get("MAX_SPREAD_POINTS", 120)),
            "max_positions_per_symbol": strat_cfg.get("max_positions_per_symbol", c.get("MAX_POSITIONS_PER_SYMBOL", 6)),
            "max_positions": c.get("MAX_POSITIONS", 20),
            "max_trades_per_day": c.get("MAX_TRADES_PER_DAY", 200),
            "risk_per_trade": strat_cfg.get("risk_per_trade", c.get("RISK_PER_TRADE", 0.004)),
            "daily_loss_limit_pct": strat_cfg.get("daily_loss_limit_pct", c.get("MAX_DAILY_LOSS_PCT", 0.02)),
            "max_dd_pct": strat_cfg.get("max_dd_pct", c.get("MAX_DD_PCT", 0.10)),
            "consistency_max_pct": c.get("CONSISTENCY_MAX_PCT", 0.30),
            "min_trading_days": c.get("MIN_TRADING_DAYS", 10),
            "circuit_breaker_dd_pct": c.get("CIRCUIT_BREAKER_DD_PCT", 0.08),
        }

    # ── Lot sizing ──────────────────────────────────────────────────────

    def _get_lot_params(self, symbol: str, wr_all: float | None = None) -> dict:
        """Calcule les paramètres de taille de lot."""
        strat_cfg = self._get_static_config(symbol)
        lot_base = strat_cfg.get("lot_base", 0.01)
        lot_max = strat_cfg.get("lot_max", 0.10)
        if wr_all is None:
            metrics = self._get_tracker_metrics(symbol)
            wr_all = metrics.get("WR_all", 0)
        current_lot = get_lot_for_wr(wr_all or 0, lot_base, lot_max)
        return {
            "lot_base": lot_base,
            "lot_max": lot_max,
            "lot_current": current_lot,
            "lot_progression_rules": LOT_PROGRESSION_RULES,
        }

    # ── Trailing levels ─────────────────────────────────────────────────

    def _get_trailing_params(self, symbol: str) -> dict:
        """Récupère les niveaux de trailing par régime pour ce symbole."""
        from engine_simple.ftmo_config import get_trailing_for_symbol, TRAILING_BY_REGIME

        regimes = ["TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL"]
        trailing = {}
        for regime in regimes:
            trailing[regime] = get_trailing_for_symbol(symbol, regime)
        return {
            "trailing_levels": trailing,
            "first_lock_atr": 1.5,  # standardisé par le fix du 1er Juillet
        }

    # ── Méthode principale ──────────────────────────────────────────────

    def get_all_params(self, symbol: str) -> dict[str, Any]:
        """Retourne TOUS les paramètres pour un symbole.

        C'est la méthode principale à utiliser partout dans le pipeline.
        """
        # 1. Config statique (source de vérité)
        static = self._get_static_config(symbol)

        # 2. Global fallbacks
        globals_ = self._get_global_fallbacks(symbol)

        # 3. OnlineLearner adapted params
        ol_params = self._get_ol_params(symbol)

        # 4. Tracker metrics
        tracker_metrics = self._get_tracker_metrics(symbol)
        wr_all = tracker_metrics.get("WR_all")

        # 5. WR_50
        wr_50_data = self._get_wr_50(symbol)

        # 6. Lot sizing
        lot_params = self._get_lot_params(symbol, wr_all)

        # 7. Trailing
        trailing = self._get_trailing_params(symbol)

        # 8. Dynamic min_score
        cfg_score = static.get("min_score", 0.70)
        dyn_score = self.get_dyn_score(symbol)
        effective_min_score = max(dyn_score, cfg_score) if dyn_score else cfg_score

        # 9. Confidence threshold
        conf = static.get("conf", max(cfg_score, 0.85))

        # ── Assemblage final ────────────────────────────────────────────
        params = {
            # Source de vérité
            "source": "strategy.py SYMBOL_CONFIG",
            # ── Config trading (per-symbol) ──
            "timeframe": static.get("timeframe", "H1"),
            "momentum_period": static.get("momentum_period", 20),
            "sl_atr_trending": static.get("sl_atr_trending", 2.0),
            "tp_atr_trending": static.get("tp_atr_trending", 5.0),
            "sl_atr_ranging": static.get("sl_atr_ranging", 1.5),
            "tp_atr_ranging": static.get("tp_atr_ranging", 5.0),
            "threshold_trending": static.get("threshold_trending", 2.5),
            "threshold_ranging": static.get("threshold_ranging", 2.0),
            "adx_slope_threshold": static.get("adx_slope_threshold", -6.0),
            "adx_slope_threshold_strong": static.get("adx_slope_threshold_strong", -10.0),
            "pullback_band_trending": static.get("pullback_band_trending", 0.5),
            "pullback_band_ranging": static.get("pullback_band_ranging", 0.3),
            # ── Risque & Filtres (per-symbol) ──
            "risk_mult": static.get("risk_mult", 1.0),
            "min_score": cfg_score,
            "dyn_score": dyn_score,
            "effective_min_score": effective_min_score,
            "conf": conf,
            "min_rr": static.get("min_rr", 1.5),
            "adx_thresh": static.get("adx_thresh", 22),
            "cooldown_minutes": static.get("cooldown_minutes", 15),
            "auto_pause_losses": static.get("auto_pause_losses", 5),
            # ── Volume indicators (per-symbol) ──
            "cmf_threshold": static.get("cmf_threshold", 0.10),
            "obv_div_penalty_high": static.get("obv_div_penalty_high", 0.70),
            "obv_div_penalty_low": static.get("obv_div_penalty_low", 0.85),
            # ── Créneaux horaires (per-symbol) ──
            "preferred_hours": static.get("preferred_hours", list(range(24))),
            "news_minutes_before": static.get("news_minutes_before", 5),
            "news_minutes_after": static.get("news_minutes_after", 5),
            # ── OnlineLearner adapted ──
            **ol_params,
            # ── Métriques calculées ──
            **tracker_metrics,
            **wr_50_data,
            # ── Lot sizing ──
            **lot_params,
            # ── Trailing ──
            **trailing,
            # ── Global fallbacks ──
            **globals_,
            # ── Signal score breakdown ──
            "cfg_score": cfg_score,
        }

        # Nettoyer les None
        params = {k: v for k, v in params.items() if v is not None}

        return params


# ═════════════════════════════════════════════════════════════════════════════
# Instance singleton et fonction d'accès rapide
# ═════════════════════════════════════════════════════════════════════════════

_manager: SymbolParamManager | None = None


def get_manager() -> SymbolParamManager:
    """Retourne l'instance singleton du SymbolParamManager."""
    global _manager
    if _manager is None:
        _manager = SymbolParamManager()
    return _manager


def get_symbol_params(symbol: str) -> dict[str, Any]:
    """Fonction d'accès rapide — retourne tous les paramètres d'un symbole.

    Utilisation:
        params = get_symbol_params("EURUSD")
        risk_mult = params["risk_mult"]
        wr = params["WR_all"]
    """
    return get_manager().get_all_params(symbol)


def get_symbol_param(symbol: str, param: str, default: Any = None) -> Any:
    """Retourne un paramètre spécifique pour un symbole."""
    return get_symbol_params(symbol).get(param, default)


def update_dyn_score(symbol: str, score: float):
    """Met à jour le dynamic min_score (appelé par ftmo_protector)."""
    get_manager().update_dyn_score(symbol, score)


def configure(
    tracker=None,
    ftmo=None,
    learner=None,
):
    """Configure le manager avec les dépendances (appelé au démarrage)."""
    m = get_manager()
    if tracker:
        m.set_tracker(tracker)
    if ftmo:
        m.set_ftmo(ftmo)
    if learner:
        m.set_learner(learner)
