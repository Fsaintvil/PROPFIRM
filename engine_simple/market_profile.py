"""Market Profile — Analyse du profil de marché.

Analyse la structure du marché basée sur :
- Initial Balance (IB): range des N premières bougies du jour/session
- TAP (Time at Price): temps passé à chaque niveau de prix
- Session Type: trending (breakout IB) ou ranging (dans IB)
- Value Area: zone de 70% du volume (complète VolumeProfile)
- POC (Point of Control) temporel

Usage:
    mp = MarketProfile()
    result = mp.analyze(df)
    if result["session_type"] == "trending":
        logger.info(f"Breakout IB: {result['ib_direction']}")
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass

logger = logging.getLogger("market_profile")

IB_BARS = 4  # Nombre de bougies pour l'Initial Balance (H1=4h, H4=16h)
TAP_BINS = 24  # Nombre de niveaux de prix pour TAP

# Seuils pour classification de session
IB_BREAKOUT_MIN_ATR = 0.5  # Breakout IB significatif si > 0.5×ATR
IB_BREAKOUT_CONFIRM = 0.3  # Ratio de la bougie close en dehors de IB


@dataclass
class MarketProfileResult:
    """Résultat de l'analyse Market Profile."""

    ib_high: float | None = None  # Plus haut de l'Initial Balance
    ib_low: float | None = None  # Plus bas de l'Initial Balance
    ib_range: float | None = None  # Taille de IB en ATR
    session_type: str = "unknown"  # trending, ranging, neutral
    ib_direction: str = "NONE"  # BUY/SELL si breakout
    current_price: float | None = None
    price_in_ib: bool = True  # Prix dans la zone IB ?
    tap_poc: float | None = None  # POC temporel
    value_area_high: float | None = None
    value_area_low: float | None = None

    def to_dict(self, signal_action: str | None = None) -> dict:
        return {
            "ib_high": self.ib_high,
            "ib_low": self.ib_low,
            "ib_range": self.ib_range,
            "session_type": self.session_type,
            "ib_direction": self.ib_direction,
            "current_price": self.current_price,
            "price_in_ib": self.price_in_ib,
            "tap_poc": self.tap_poc,
            "value_area_high": self.value_area_high,
            "value_area_low": self.value_area_low,
            "score_adj": self._compute_score_adj(signal_action),
        }

    def _compute_score_adj(self, signal_action: str | None = None) -> float:
        """Calcule l'ajustement de score basé sur Market Profile.

        Règles:
          - Breakout IB dans la direction du signal → strong confirmation
          - Prix dans IB → neutral (pas de signal fort)
          - Breakout IB contre le signal → strong rejection
          - Près du POC temporel → support/résistance dynamique
        """
        adj = 1.0

        if self.session_type == "trending" and self.ib_direction != "NONE":
            # Breakout IB : comparer avec la direction du signal
            if signal_action and signal_action == self.ib_direction:
                adj = 1.12  # +12% : IB confirme le signal
            elif signal_action and signal_action != self.ib_direction:
                adj = 0.88  # -12% : IB contredit le signal
            else:
                adj = 1.0  # pas de signal → neutre
        elif self.session_type == "ranging":
            # Range IB : pas de direction claire, signaux moins fiables
            adj = 0.95  # -5%

        if self.tap_poc and self.current_price:
            # Près du POC temporel = zone de value, signal plus faible
            dist_pct = abs(self.current_price - self.tap_poc) / self.current_price * 100
            if dist_pct < 0.15:
                adj *= 0.95  # -5% near TAP POC

        return round(adj, 4)


class MarketProfile:
    """Analyse le profil de marché (Market Profile)."""

    def __init__(self, ib_bars: int = IB_BARS, tap_bins: int = TAP_BINS):
        self.ib_bars = ib_bars
        self.tap_bins = tap_bins

    def analyze(self, df: pd.DataFrame, signal_action: str | None = None) -> dict:
        """Analyse complète Market Profile.

        Args:
            df: DataFrame OHLCV (time, open, high, low, close, volume)
            signal_action: Direction du signal ('BUY' ou 'SELL') pour ajustement directionnel

        Returns:
            dict avec les résultats MarketProfileResult.to_dict(signal_action)
        """
        if df is None or len(df) < self.ib_bars + 5:
            return MarketProfileResult().to_dict()

        data = df.tail(200).copy()

        # 1. Initial Balance (premières N bougies de la session)
        ib_start = min(self.ib_bars, len(data) - 1)
        ib_data = data.iloc[:ib_start]
        ib_high = ib_data["high"].max()
        ib_low = ib_data["low"].min()
        ib_range = ib_high - ib_low

        current_price = data["close"].iloc[-1]

        # 2. Session type (breakout IB ou range IB)
        price_in_ib = ib_low <= current_price <= ib_high
        ib_direction = "NONE"
        session_type = "ranging" if price_in_ib else "trending"

        ib_mid = (ib_high + ib_low) / 2
        if not price_in_ib:
            # Breakout: regarder la direction
            if current_price > ib_high:
                recent_close = data["close"].iloc[-1]
                if recent_close > ib_mid:
                    ib_direction = "BUY"
                else:
                    ib_direction = "SELL"
            else:
                if data["close"].iloc[-1] < ib_mid:
                    ib_direction = "SELL"
                else:
                    ib_direction = "BUY"
        else:
            # Dans IB: regarder si on est en haut ou en bas de la zone
            ib_pos = (current_price - ib_low) / max(ib_range, 1e-10)
            if ib_pos > 0.7:
                session_type = "testing_high"
            elif ib_pos < 0.3:
                session_type = "testing_low"
            else:
                session_type = "ranging"

        # 3. TAP (Time at Price) — combien de bougies à chaque niveau
        price_bins = np.linspace(data["low"].min(), data["high"].max(), self.tap_bins)
        bin_centers = (price_bins[:-1] + price_bins[1:]) / 2
        time_profile = np.zeros(self.tap_bins - 1)

        for _, row in data.iterrows():
            bar_high = row["high"]
            bar_low = row["low"]
            mask = (price_bins[:-1] <= bar_high) & (price_bins[1:] >= bar_low)
            if mask.sum() > 0:
                time_profile[mask] += 1

        if time_profile.sum() > 0:
            tap_poc_idx = np.argmax(time_profile)
            tap_poc = bin_centers[tap_poc_idx]

            # Value Area (70% du temps)
            total_time = time_profile.sum()
            target_time = total_time * 0.70
            accumulated = time_profile[tap_poc_idx]

            va_low_idx = va_high_idx = tap_poc_idx
            while accumulated < target_time:
                low_vol = time_profile[va_low_idx - 1] if va_low_idx > 0 else 0
                high_vol = time_profile[va_high_idx + 1] if va_high_idx < len(time_profile) - 1 else 0
                if low_vol == 0 and high_vol == 0:
                    break
                if low_vol >= high_vol:
                    va_low_idx -= 1
                    accumulated += time_profile[va_low_idx]
                else:
                    va_high_idx += 1
                    accumulated += time_profile[va_high_idx]

            val = bin_centers[va_low_idx]
            vah = bin_centers[va_high_idx]
        else:
            tap_poc = None
            val = vah = None

        # 4. Calcul de IB range en ATR
        ib_range_atr = None
        try:
            from engine_simple.indicators import atr

            atr_arr = atr(data["high"].values, data["low"].values, data["close"].values, 14)
            if atr_arr is not None and len(atr_arr) > 0 and not np.isnan(atr_arr[-1]):
                atr_val = float(atr_arr[-1])
                ib_range_atr = round(ib_range / max(atr_val, 1e-10), 2)
        except Exception as e:
            logger.warning(f"  [MARKET_PROFILE] analyze ib_atr: {e}")
            pass

        result = MarketProfileResult(
            ib_high=round(ib_high, 5),
            ib_low=round(ib_low, 5),
            ib_range=ib_range_atr,
            session_type=session_type,
            ib_direction=ib_direction,
            current_price=round(current_price, 5),
            price_in_ib=price_in_ib,
            tap_poc=round(tap_poc, 5) if tap_poc else None,
            value_area_high=round(vah, 5) if vah else None,
            value_area_low=round(val, 5) if val else None,
        )

        return result.to_dict(signal_action)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_mp = MarketProfile()


def analyze(df: pd.DataFrame) -> dict:
    """Analyse Market Profile (fonction convenience)."""
    return _default_mp.analyze(df)
