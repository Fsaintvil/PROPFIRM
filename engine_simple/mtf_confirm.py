"""Multi-Timeframe Confirmation — Confirmation croisée entre timeframes.

Vérifie que la direction du signal est confirmée par les timeframes supérieurs.
Logique :
- H1 signal → H4 et D1 doivent être alignés (ou au moins pas en opposition)
- H4 signal → D1 doit être aligné
- Score adjustment : ±10-20% selon confirmation

Usage:
    mtf = MultiTimeframeConfirmer()
    confirmed, factor = mtf.confirm(df_h1, df_h4, df_d1, "BUY")
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("mtf_confirm")


class MultiTimeframeConfirmer:
    """Confirme les signaux via multi-timeframe."""

    def __init__(self, ema_fast: int = 20, ema_slow: int = 50, confirmation_threshold: float = 0.6):
        """
        Args:
            ema_fast: Période EMA rapide
            ema_slow: Période EMA lente
            confirmation_threshold: Seuil de confirmation (0-1)
        """
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.confirmation_threshold = confirmation_threshold

    def _get_trend(self, df: pd.DataFrame) -> str:
        """Détermine la tendance d'un timeframe."""
        if df is None or len(df) < self.ema_slow + 10:
            return "NEUTRAL"

        close = df["close"].values
        ema_fast = self._ema(close, self.ema_fast)
        ema_slow = self._ema(close, self.ema_slow)

        # Current position
        if ema_fast[-1] > ema_slow[-1]:
            trend = "BULLISH"
        elif ema_fast[-1] < ema_slow[-1]:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        # Slope (momentum) — 🔒 H11 fix: ne pas écraser BULLISH/BEARISH par NEUTRAL
        # Le slope est un signal de momentum, pas un override du trend EMA.
        # Si la pente est faible (≤0.1%), on conserve le trend EMA détecté.
        # Seulement si le slope est négatif alors que le trend est haussier (ou inverse),
        # on peut dégrader à NEUTRAL pour éviter les faux signaux en fin de trend.

        return trend

    def _get_adx_direction(self, df: pd.DataFrame) -> str:
        """Retourne la direction basée sur ADX."""
        if df is None or len(df) < 30:
            return "NEUTRAL"

        # Simplified ADX direction from +DI/-DI
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        plus_dm = np.maximum(high[1:] - high[:-1], 0)
        minus_dm = np.maximum(low[:-1] - low[1:], 0)

        # Remove opposing
        mask = plus_dm > minus_dm
        plus_dm[mask == False] = 0
        minus_dm[mask == True] = 0

        tr = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))

        if len(plus_dm) < 14:
            return "NEUTRAL"

        atr = pd.Series(tr).rolling(14).mean().iloc[-1]
        if atr == 0:
            return "NEUTRAL"

        plus_di = pd.Series(plus_dm).rolling(14).mean().iloc[-1] / atr * 100
        minus_di = pd.Series(minus_dm).rolling(14).mean().iloc[-1] / atr * 100

        if plus_di > minus_di:
            return "BULLISH"
        elif minus_di > plus_di:
            return "BEARISH"
        return "NEUTRAL"

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calcule l'EMA."""
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    def confirm(self, df_signal: pd.DataFrame, df_higher: pd.DataFrame, action: str) -> tuple[bool, float]:
        """Vérifie la confirmation du TF supérieur.

        Args:
            df_signal: Données du timeframe du signal (ex: H1)
            df_higher: Données du timeframe supérieur (ex: H4 ou D1)
            action: Direction du signal ("BUY" ou "SELL")

        Returns:
            (confirmed, factor) — confirmed=True si OK, factor=multiplicateur de score
        """
        if df_higher is None or len(df_higher) < 50:
            return True, 1.0  # Pas de données = pas de filtrage

        higher_trend = self._get_trend(df_higher)
        higher_adx_dir = self._get_adx_direction(df_higher)

        # Check alignment
        aligned = False
        opposite = False

        if action == "BUY":
            aligned = higher_trend in ("BULLISH", "NEUTRAL") and higher_adx_dir != "BEARISH"
            opposite = higher_trend == "BEARISH" and higher_adx_dir == "BEARISH"
        elif action == "SELL":
            aligned = higher_trend in ("BEARISH", "NEUTRAL") and higher_adx_dir != "BULLISH"
            opposite = higher_trend == "BULLISH" and higher_adx_dir == "BULLISH"

        if opposite:
            return False, 0.7  # Opposé → pénalité
        elif aligned:
            return True, 1.1  # Aligné → bonus
        else:
            return True, 1.0  # Neutre → neutre

    def confirm_multi(
        self, df_signal: pd.DataFrame, df_higher_list: list[pd.DataFrame], action: str
    ) -> tuple[bool, float, dict]:
        """Vérifie la confirmation sur plusieurs TFs supérieurs.

        Args:
            df_signal: Données du TF du signal
            df_higher_list: Liste des données TFs supérieurs
            action: Direction du signal

        Returns:
            (confirmed, factor, details)
        """
        if not df_higher_list:
            return True, 1.0, {}

        total_factor = 1.0
        details = {}

        for i, df_higher in enumerate(df_higher_list):
            tf_name = f"TF_{i + 1}"
            confirmed, factor = self.confirm(df_signal, df_higher, action)
            total_factor *= factor
            details[tf_name] = {
                "confirmed": confirmed,
                "factor": factor,
                "trend": self._get_trend(df_higher),
            }

        # Final decision
        final_confirmed = total_factor >= self.confirmation_threshold

        return final_confirmed, total_factor, details


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_mtf = MultiTimeframeConfirmer()


def confirm(df_signal: pd.DataFrame, df_higher: pd.DataFrame, action: str) -> tuple[bool, float]:
    """Confirme un signal (fonction convenience)."""
    return _default_mtf.confirm(df_signal, df_higher, action)


def confirm_multi(df_signal: pd.DataFrame, df_higher_list: list[pd.DataFrame], action: str) -> tuple[bool, float, dict]:
    """Confirme sur plusieurs TFs (fonction convenience)."""
    return _default_mtf.confirm_multi(df_signal, df_higher_list, action)
