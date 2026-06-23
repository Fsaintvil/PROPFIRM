"""VWAP Analyzer — Volume Weighted Average Price avec Premium/Discount Zones.

Calcule le VWAP standard, l'Anchored VWAP (session), et classifie la position
du prix en zone premium (au-dessus) ou discount (en dessous) avec un ratio ATR.

Usage:
    vwap = VWAPAnalyzer()
    result = vwap.analyze(df)  # DataFrame avec OHLCV
    if result["zone"] == "discount":
        # zone d'achat favorable
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("vwap")

PREMIUM_THRESHOLD_ATR = 1.5  # ×ATR au-dessus du VWAP = zone premium
DISCOUNT_THRESHOLD_ATR = 1.5  # ×ATR en dessous du VWAP = zone discount
DISTANCE_CAP_ATR = 3.0  # plafond pour la force du signal


def compute_vwap(high, low, close, volume):
    """Volume Weighted Average Price."""
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    typical = (h + lo + c) / 3
    cum_vp = np.cumsum(typical * v)
    cum_v = np.cumsum(v)
    return cum_vp / np.maximum(cum_v, 1e-10)


def compute_anchored_vwap(high, low, close, volume, anchor_idx=0):
    """Anchored VWAP depuis un point d'ancrage (ex: début de session)."""
    h, lo, c, v = [np.asarray(x, dtype=float) for x in (high, low, close, volume)]
    if anchor_idx >= len(c) or len(c) < 10:
        return compute_vwap(h, lo, c, v)
    typical = (h + lo + c) / 3
    cum_vp = np.cumsum(typical * v)
    cum_v = np.cumsum(v)
    anchored_vp = cum_vp[-1] - cum_vp[anchor_idx] if anchor_idx > 0 else cum_vp[-1]
    anchored_v = cum_v[-1] - cum_v[anchor_idx] if anchor_idx > 0 else cum_v[-1]
    return anchored_vp / max(anchored_v, 1e-10)


def classify_zone(price, vwap_val, atr_val=None):
    """Classifie la position du prix par rapport au VWAP.

    Returns:
        dict avec zone, distance_pct, atr_distance, force (0-1)
    """
    if vwap_val is None or vwap_val <= 0:
        return {"zone": "unknown", "distance_pct": 0.0, "atr_distance": 0.0, "force": 0.5}

    distance_pct = (price - vwap_val) / vwap_val * 100

    # Distance en ATR
    atr_distance = 0.0
    if atr_val and atr_val > 0:
        atr_distance = abs(price - vwap_val) / atr_val

    if price > vwap_val:
        # Zone premium (au-dessus du VWAP)
        if atr_val and atr_distance >= PREMIUM_THRESHOLD_ATR:
            zone = "premium"
        else:
            zone = "premium_light"
        force = min(1.0, atr_distance / DISTANCE_CAP_ATR) if atr_val else 0.3
    elif price < vwap_val:
        # Zone discount (en dessous du VWAP)
        if atr_val and atr_distance >= DISCOUNT_THRESHOLD_ATR:
            zone = "discount"
        else:
            zone = "discount_light"
        force = min(1.0, atr_distance / DISTANCE_CAP_ATR) if atr_val else 0.3
    else:
        zone = "at_vwap"
        force = 0.0

    return {
        "zone": zone,
        "distance_pct": round(distance_pct, 3),
        "atr_distance": round(atr_distance, 3),
        "force": round(force, 3),
        "vwap": round(vwap_val, 5),
    }


class VWAPAnalyzer:
    """Analyse VWAP multi-cadre."""

    def __init__(self, atr_period=14):
        self.atr_period = atr_period

    def analyze(self, df: pd.DataFrame) -> dict:
        """Analyse complète VWAP sur un DataFrame OHLCV.

        Returns:
            dict avec vwap, anchored_vwap, zone, signal_score_adjustment
        """
        if df is None or len(df) < 20:
            return {"vwap": None, "zone": "unknown", "score_adj": 1.0}

        data = df.tail(200).copy()
        high = data["high"].values
        low = data["low"].values
        close = data["close"].values
        volume = data["volume"].values
        current_price = close[-1]

        # VWAP standard (rolling)
        vwap_arr = compute_vwap(high, low, close, volume)
        current_vwap = vwap_arr[-1] if not np.isnan(vwap_arr[-1]) else None

        # Anchored VWAP depuis le début de la session (dernier changement de jour notable)
        # On cherche la session actuelle: dernière séquence de 24h
        session_start = max(0, len(close) - 48)  # ~48 barres H1 = 2 jours
        awap_arr = compute_anchored_vwap(high, low, close, volume, session_start)
        current_awap = awap_arr if not np.isnan(awap_arr) else None

        # ATR pour la classification
        atr_val = None
        try:
            from engine_simple.indicators import atr

            atr_arr = atr(high, low, close, self.atr_period)
            if atr_arr is not None and len(atr_arr) > 0 and not np.isnan(atr_arr[-1]):
                atr_val = float(atr_arr[-1])
        except Exception:
            pass

        # Classification zone VWAP standard
        vwap_zone = classify_zone(current_price, current_vwap, atr_val)
        awap_zone = classify_zone(current_price, current_awap, atr_val)

        # Ajustement score : bonus en discount, pénalité en premium
        score_adj = 1.0
        reasons = []

        # VWAP standard
        if vwap_zone["zone"] == "discount":
            score_adj = min(1.15, 1.0 + vwap_zone["force"] * 0.12)
            reasons.append(f"DISCOUNT ({vwap_zone['atr_distance']:.1f}×ATR)")
        elif vwap_zone["zone"] == "premium":
            score_adj = max(0.75, 1.0 - vwap_zone["force"] * 0.20)
            reasons.append(f"PREMIUM ({vwap_zone['atr_distance']:.1f}×ATR)")

        # Anchored VWAP renforce si les deux sont en discount/premium
        if awap_zone["zone"] == vwap_zone["zone"] and "light" not in vwap_zone["zone"]:
            if "discount" in vwap_zone["zone"]:
                score_adj = min(1.20, score_adj * 1.05)
                reasons.append("AVWAP confirm")
            elif "premium" in vwap_zone["zone"]:
                score_adj = max(0.70, score_adj * 0.95)
                reasons.append("AVWAP confirm")

        return {
            "vwap": current_vwap,
            "anchored_vwap": current_awap,
            "zone": vwap_zone["zone"],
            "zone_detail": vwap_zone,
            "awap_zone": awap_zone,
            "score_adj": round(score_adj, 4),
            "reason": " + ".join(reasons) if reasons else None,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_vwap = VWAPAnalyzer()


def analyze(df: pd.DataFrame) -> dict:
    """Analyse VWAP (fonction convenience)."""
    return _default_vwap.analyze(df)
