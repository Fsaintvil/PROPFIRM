"""TrendFollow — stratégie de suivi de tendance pour actifs à fort momentum.

Utilisation:
    Utilisée via le Strategy Registry (strategy_registry.py) pour les symboles
    configurés avec "TrendFollow".

Principe:
    - N'entre QUE si ADX ≥ 22 (marché en tendance)
    - Direction déterminée par EMA50 + pente EMA50 + croisement +DI/-DI
    - SL large (2.5×ATR) pour ne pas être secoué
    - TP large (6.0×ATR) pour laisser courir les gains
    - Pas de trades en RANGING (ADX < 22)

Conçue pour XAUUSD H4 (or) où MOM20x3 échouait (RR 0.44).
"""

import logging
from typing import Any

import numpy as np

from engine_simple.indicators import ema, atr, adx, rsi as ind_rsi

logger = logging.getLogger("strategy_trend_follow")

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration par défaut — peut être surchargée par symbole
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    # ADX
    "adx_threshold": 22,  # ADX min pour considérer une tendance
    "adx_strong": 28,  # ADX pour breakout confirmation
    # EMA
    "ema_fast": 20,  # EMA rapide
    "ema_slow": 50,  # EMA lente
    "ema_slope_period": 5,  # Période pour calculer la pente EMA50
    "ema_slope_min": 0.0002,  # Pente minimum (0.02%) pour confirmer direction
    # SL/TP (×ATR)
    "sl_atr_trending": 2.5,  # SL en mode tendance
    "tp_atr_trending": 6.0,  # TP en mode tendance
    "sl_atr_breakout": 1.5,  # SL serré en breakout (volonté forte)
    "tp_atr_breakout": 5.0,  # TP en breakout
    # Pullback
    "pullback_band": 0.5,  # Bande autour d'EMA50 pour pullback (×ATR)
    "pullback_score_bonus": 0.05,  # Bonus de score si pullback
    "breakout_score_bonus": 0.10,  # Bonus de score si breakout fort
    # Score
    "base_score": 0.70,  # Score de base
    "score_min": 0.60,  # Score minimum pour passer les filtres
    # Risque
    "risk_mult": 0.70,  # Multiplicateur risque (conservatif)
    "cooldown_minutes": 20,  # Pause après perte
    "auto_pause_losses": 4,  # Pertes consécutives avant pause
}

# Configuration par symbole (surcharges)
SYMBOL_CONFIG: dict[str, dict[str, Any]] = {
    "XAUUSD": {
        "adx_threshold": 22,
        "adx_strong": 26,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.0,
        "pullback_band": 0.5,
        "risk_mult": 0.70,
    },
    # Futures surcharges possibles :
    # "BTCUSD": { ... }
}


def _get_config(symbol: str | None = None) -> dict[str, Any]:
    """Retourne la config complète pour un symbole."""
    cfg = dict(DEFAULT_CONFIG)
    if symbol and symbol in SYMBOL_CONFIG:
        cfg.update(SYMBOL_CONFIG[symbol])
    return cfg


def trend_follow_signal(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray | None = None,
    symbol: str | None = None,
    custom_thresh_trending: float | None = None,
    custom_thresh_ranging: float | None = None,
) -> dict[str, Any] | None:
    """Génère un signal TrendFollow.

    Args:
        close: Prix de clôture
        high: Prix haut
        low: Prix bas
        open_: Prix d'ouverture (optionnel)
        symbol: Symbole pour config spécifique
        custom_thresh_trending: Ignoré (pour compatibilité API)
        custom_thresh_ranging: Ignoré (pour compatibilité API)

    Returns:
        Dict signal (même format que MOM20x3) ou None si pas de signal
    """
    cfg = _get_config(symbol)
    length = len(close)

    # Vérifier données suffisantes
    min_bars = cfg["ema_slow"] + cfg["ema_slope_period"] + 14 + 5
    if length < min_bars:
        logger.debug(f"  [TF] {symbol}: données insuffisantes ({length} < {min_bars})")
        return None

    # ── Indicateurs ────────────────────────────────────────────────────────
    ema_slow_arr = ema(close, cfg["ema_slow"])
    ema_fast_arr = ema(close, cfg["ema_fast"])

    # ADX, +DI, -DI
    try:
        adx_val, plus_di, minus_di = adx(high, low, close, period=14)
    except Exception as e:
        logger.debug(f"  [TF] {symbol}: ADX error: {e}")
        return None

    # ATR
    atr_arr = atr(high, low, close, period=14)
    current_atr = float(atr_arr[-1]) if len(atr_arr) > 0 and not np.isnan(atr_arr[-1]) else 0

    # Vérifier validité des données
    if any(np.isnan([adx_val, plus_di, minus_di, current_atr])) or current_atr <= 0:
        logger.debug(f"  [TF] {symbol}: indicateurs NaN ou ATR nul")
        return None

    # Prix courant
    current_close = float(close[-1])
    current_high = float(high[-1])
    current_low = float(low[-1])

    # EMA50 et EMA20 actuelles
    ema50 = float(ema_slow_arr[-1]) if len(ema_slow_arr) > 0 and not np.isnan(ema_slow_arr[-1]) else 0
    ema20 = float(ema_fast_arr[-1]) if len(ema_fast_arr) > 0 and not np.isnan(ema_fast_arr[-1]) else 0

    if ema50 <= 0 or ema20 <= 0:
        logger.debug(f"  [TF] {symbol}: EMA non valide")
        return None

    # ── Pente EMA50 ─────────────────────────────────────────────────────────
    slope_period = cfg["ema_slope_period"]
    if len(ema_slow_arr) > slope_period and not np.isnan(ema_slow_arr[-slope_period - 1]):
        ema50_prev = float(ema_slow_arr[-slope_period - 1])
        ema50_slope = (ema50 - ema50_prev) / max(ema50_prev, 0.0001)
    else:
        ema50_slope = 0.0

    # ── Détection de régime ─────────────────────────────────────────────────
    is_trending = adx_val >= cfg["adx_threshold"]
    is_strong = adx_val >= cfg["adx_strong"]

    # Régime déduit
    if is_trending:
        if ema50_slope > cfg["ema_slope_min"] and plus_di > minus_di:
            regime = "TREND_UP"
        elif ema50_slope < -cfg["ema_slope_min"] and minus_di > plus_di:
            regime = "TREND_DOWN"
        elif plus_di > minus_di:
            regime = "TREND_UP"  # DI gagne sur la pente
        elif minus_di > plus_di:
            regime = "TREND_DOWN"
        else:
            regime = "HIGH_VOL" if current_atr / current_close > 0.01 else "RANGING"
    else:
        # Pas de signal en ranging pour TrendFollow
        logger.debug(f"  [TF] {symbol}: ADX={adx_val:.1f} < {cfg['adx_threshold']} → pas de signal (ranging)")
        return None

    # ── Décision d'achat/vente ─────────────────────────────────────────────

    # Distance par rapport à EMA50 (en % et en ATR)
    dist_to_ema50_pct = (current_close - ema50) / max(ema50, 0.0001)
    dist_to_ema50_atr = (current_close - ema50) / max(current_atr, 0.0001)
    pullback_band_atr = cfg["pullback_band"] * current_atr / max(ema50, 0.0001)  # bande en %

    action = None
    score = cfg["base_score"]
    confidence = 0.50

    # Condition BUY : uptrend
    if regime in ("TREND_UP",) and plus_di > minus_di:
        # Prix au-dessus d'EMA50 ? (tendance haussière confirmée)
        if current_close > ema50:
            # Vérifier pente EMA50
            if ema50_slope > cfg["ema_slope_min"]:
                action = "BUY"
                score = cfg["base_score"]  # 0.70

                # Pullback vers EMA50 (retracement dans la tendance) → confirmation
                if abs(dist_to_ema50_pct) < pullback_band_atr:
                    score += cfg["pullback_score_bonus"]
                    logger.debug(f"  [TF] {symbol}: BUY pullback EMA50 (dist={dist_to_ema50_pct:.4%})")
                else:
                    logger.debug(f"  [TF] {symbol}: BUY trend (dist_ema50={dist_to_ema50_pct:.4%})")

                # Breakout fort → score plus haut
                if is_strong and dist_to_ema50_atr > 1.5:
                    score += cfg["breakout_score_bonus"]
                    logger.debug(f"  [TF] {symbol}: BUY breakout (ATR={dist_to_ema50_atr:.2f})")

    # Condition SELL : downtrend
    elif regime in ("TREND_DOWN",) and minus_di > plus_di:
        if current_close < ema50:
            if ema50_slope < -cfg["ema_slope_min"]:
                action = "SELL"
                score = cfg["base_score"]

                # Pullback vers EMA50
                if abs(dist_to_ema50_pct) < pullback_band_atr:
                    score += cfg["pullback_score_bonus"]
                    logger.debug(f"  [TF] {symbol}: SELL pullback EMA50 (dist={dist_to_ema50_pct:.4%})")
                else:
                    logger.debug(f"  [TF] {symbol}: SELL trend (dist_ema50={dist_to_ema50_pct:.4%})")

                # Breakdown fort
                if is_strong and abs(dist_to_ema50_atr) > 1.5:
                    score += cfg["breakout_score_bonus"]
                    logger.debug(f"  [TF] {symbol}: SELL breakdown (ATR={abs(dist_to_ema50_atr):.2f})")

    if action is None:
        logger.debug(
            f"  [TF] {symbol}: pas de signal (regime={regime}, ema_slope={ema50_slope:.4%}, "
            f"+DI={plus_di:.1f}, -DI={minus_di:.1f})"
        )
        return None

    # ── SL/TP ──────────────────────────────────────────────────────────────
    # Mode breakout : SL plus serré (volonté forte)
    if is_strong and abs(dist_to_ema50_atr) > 1.5:
        sl_atr = cfg["sl_atr_breakout"]
        tp_atr = cfg["tp_atr_breakout"]
    else:
        sl_atr = cfg["sl_atr_trending"]
        tp_atr = cfg["tp_atr_trending"]

    # ── Score et confiance ──────────────────────────────────────────────────
    score = min(0.99, max(cfg["score_min"], score))
    confidence = min(0.95, 0.40 + (score - 0.50) / 0.45 * 0.50)
    confidence = max(0.40, min(0.95, confidence))

    # ── RSI (information) ──────────────────────────────────────────────────
    try:
        rsi_arr = ind_rsi(close, period=14)
        rsi_val = round(float(rsi_arr[-1]), 1) if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50.0
    except Exception:
        rsi_val = 50.0

    # ── Structure (compatibilité MOM20x3) ───────────────────────────────────
    structure_trend = "bullish" if action == "BUY" else "bearish"

    # ── Construction du signal (même format que MOM20x3) ────────────────────
    slope_val = round(ema50_slope * 100, 3)  # en %

    signal = {
        # Core
        "action": action,
        "score": score,
        "confidence": confidence,
        # Indicateurs
        "atr": round(current_atr, 5),
        "adx": round(adx_val, 1),
        "plus_di": round(plus_di, 1),
        "minus_di": round(minus_di, 1),
        "adx_slope": slope_val,  # pente EMA50 utilisée comme proxy ADX slope
        # SL/TP
        "sl_atr": sl_atr,
        "tp_atr": tp_atr,
        # Pullback
        "pullback_active": abs(dist_to_ema50_pct) < pullback_band_atr,
        "pullback_dist": round(dist_to_ema50_pct, 4),
        "pullback_band": round(pullback_band_atr, 4),
        # Seuils utilisés
        "thresh_used": round(abs(dist_to_ema50_atr), 2),  # distance à EMA50 en ATR
        "ol_thresh_applied": False,
        "mom_abs": round(abs(dist_to_ema50_pct), 5),
        "threshold_value": round(current_atr * sl_atr, 5),
        "momentum_period": cfg["ema_slow"],  # 50 (pour compatibilité)
        "is_trending": is_trending,
        # Métadonnées
        "_regime": regime,
        "_ml_agrees": None,
        "_model_predictions": {"TrendFollow": action},
        "_dl_score": None,
        # Structure
        "structure_trend": structure_trend,
        "structure_score": 0.0,
        "unmitigated_obs": 0,
        "unmitigated_fvgs": 0,
        "_structure_obs": [],
        # Meta
        "strategy": "TrendFollow",
        "rsi": rsi_val,
        # Données EMA pour diagnostic
        "ema50": round(ema50, 5),
        "ema20": round(ema20, 5),
        "ema50_slope": slope_val,
        "distance_to_ema50_pct": round(dist_to_ema50_pct * 100, 2),
    }

    logger.debug(
        f"  [TF] {action} {symbol} | "
        f"ADX={adx_val:.1f} +DI={plus_di:.1f} -DI={minus_di:.1f} "
        f"EMA50={ema50:.2f} slope={ema50_slope:.4%} "
        f"dist={dist_to_ema50_pct:.4%} score={score:.2f}"
    )

    return signal


class TrendFollow:
    """Wrapper de la stratégie TrendFollow — interface compatible MOM20x3."""

    def __init__(self, rates: list, symbol: str, period: int | None = None):
        """Initialise avec les rates MT5 et le symbole.

        Args:
            rates: Liste de rates MT5 [(time, open, high, low, close, volume, spread, real_volume)]
            symbol: Nom du symbole (ex: "XAUUSD")
            period: Ignoré (pour compatibilité MOM20x3)
        """
        self.rates = rates
        self.symbol = symbol
        self._parse_rates()

    def _parse_rates(self):
        """Extrait les tableaux numpy des rates MT5."""
        min_bars = 70  # EMA50 + pente + ATR + marge
        if self.rates is None or len(self.rates) < min_bars:
            self._close = None
            self._high = None
            self._low = None
            self._open = None
            return
        self._close = np.array([r[4] for r in self.rates], dtype=float)
        self._high = np.array([r[2] for r in self.rates], dtype=float)
        self._low = np.array([r[3] for r in self.rates], dtype=float)
        self._open = np.array([r[1] for r in self.rates], dtype=float)

    def analyze(
        self,
        custom_thresh_trending: float | None = None,
        custom_thresh_ranging: float | None = None,
    ) -> dict[str, Any] | None:
        """Analyse et retourne un signal TrendFollow.

        Args:
            custom_thresh_trending: Ignoré (pour compatibilité API MOM20x3)
            custom_thresh_ranging: Ignoré (pour compatibilité API MOM20x3)

        Returns:
            Dict signal ou None
        """
        if self._close is None:
            logger.debug(f"  [TF] {self.symbol}: rates insuffisantes pour TrendFollow")
            return None
        return trend_follow_signal(
            self._close,
            self._high,
            self._low,
            open_=self._open,
            symbol=self.symbol,
        )

    def __call__(self) -> dict[str, Any] | None:
        return self.analyze()
