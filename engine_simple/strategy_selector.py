"""Strategy Selector — Adaptation dynamique des paramètres par régime de marché.

Sélectionne et ajuste les paramètres de trading en fonction du régime détecté :
- STRONG_UPTREND/DOWNTREND: paramètres agressifs
- RANGING: paramètres conservateurs
- HIGH_VOL: réduction du risque
- LOW_VOL: optimisation des entrées

Usage:
    selector = StrategySelector()
    params = selector.get_params("BTCUSD", "STRONG_UPTREND", adx=30)
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("strategy_selector")


@dataclass
class StrategyParams:
    """Paramètres de trading adaptés au régime."""

    # Thresholds
    threshold_mult: float = 1.0
    # SL/TP
    sl_mult: float = 1.0
    tp_mult: float = 1.0
    # Risk
    risk_mult: float = 1.0
    # Trailing
    trailing_first_lock: float = 1.0
    trailing_n1: float = 0.50
    # Position sizing
    max_positions: int = 2
    # Confidence
    min_score: float = 0.60
    # Description
    description: str = ""


# ============================================================================
# PARAMETER PRESETS BY REGIME
# ============================================================================
REGIME_PARAMS = {
    "STRONG_UPTREND": StrategyParams(
        threshold_mult=0.85,
        sl_mult=1.0,
        tp_mult=1.2,
        risk_mult=1.0,
        trailing_first_lock=1.0,
        trailing_n1=0.80,
        max_positions=2,
        min_score=0.55,
        description="Fort uptrend — SL serré, TP large, risque maximum",
    ),
    "WEAK_UPTREND": StrategyParams(
        threshold_mult=0.95,
        sl_mult=1.0,
        tp_mult=1.1,
        risk_mult=0.9,
        trailing_first_lock=1.0,
        trailing_n1=0.70,
        max_positions=2,
        min_score=0.60,
        description="Faible uptrend — paramètres modérés",
    ),
    "RANGING": StrategyParams(
        threshold_mult=1.1,
        sl_mult=1.2,
        tp_mult=0.9,
        risk_mult=1.0,
        trailing_first_lock=1.0,
        trailing_n1=0.50,
        max_positions=2,
        min_score=0.55,  # Lowered from 0.65 — crypto trades 24/7, need more signals
        description="Range — SL large, TP modéré, entrée sélective",
    ),
    "WEAK_DOWNTREND": StrategyParams(
        threshold_mult=0.95,
        sl_mult=1.0,
        tp_mult=1.1,
        risk_mult=0.9,
        trailing_first_lock=1.0,
        trailing_n1=0.70,
        max_positions=2,
        min_score=0.60,
        description="Faible downtrend — paramètres modérés, direction short",
    ),
    "STRONG_DOWNTREND": StrategyParams(
        threshold_mult=0.85,
        sl_mult=1.0,
        tp_mult=1.2,
        risk_mult=1.0,
        trailing_first_lock=1.0,
        trailing_n1=0.80,
        max_positions=2,
        min_score=0.55,
        description="Fort downtrend — SL serré, TP large, direction short",
    ),
    "HIGH_VOL": StrategyParams(
        threshold_mult=1.2,
        sl_mult=1.3,
        tp_mult=1.3,
        risk_mult=0.7,
        trailing_first_lock=1.2,
        trailing_n1=1.00,
        max_positions=1,
        min_score=0.60,  # Lowered from 0.70 — crypto is always volatile
        description="Haute volatilité — SL large, risque réduit, 1 seul trade",
    ),
    "LOW_VOL": StrategyParams(
        threshold_mult=0.9,
        sl_mult=0.9,
        tp_mult=0.9,
        risk_mult=1.0,
        trailing_first_lock=0.8,
        trailing_n1=0.40,
        max_positions=2,
        min_score=0.60,
        description="Basse volatilité — SL/TP serrés, entrées fréquentes",
    ),
}

# ============================================================================
# SYMBOL-SPECIFIC ADJUSTMENTS
# ============================================================================
SYMBOL_ADJUSTMENTS = {
    "XAUUSD": {
        "HIGH_VOL": {"risk_mult": 0.6, "sl_mult": 1.5},  # Or très volatil
        "STRONG_UPTREND": {"tp_mult": 1.3},  # Or = trends longues
    },
    "BTCUSD": {
        "HIGH_VOL": {"risk_mult": 0.5, "sl_mult": 1.5},  # Crypto = extrême
        "LOW_VOL": {"threshold_mult": 1.1},  # BTC range souvent
    },
    "US500.cash": {
        "HIGH_VOL": {"risk_mult": 0.6},  # Indices = crashs rapides
        "STRONG_UPTREND": {"tp_mult": 1.2},
    },
}


class StrategySelector:
    """Sélectionne et adapte les paramètres de trading par régime."""

    def __init__(self):
        self._cache: dict[str, StrategyParams] = {}

    def get_params(self, symbol: str, regime: str, adx: float = 22, atr_pct: float = 0.5) -> StrategyParams:
        """Retourne les paramètres adaptés au régime et au symbole.

        Args:
            symbol: Symbole (ex: "BTCUSD")
            regime: Régime détecté (ex: "STRONG_UPTREND")
            adx: Valeur ADX (pour ajustement fin)
            atr_pct: ATR% (pour ajustement volatilité)

        Returns:
            StrategyParams avec tous les multiplicateurs
        """
        # Base params from regime
        base = REGIME_PARAMS.get(regime, REGIME_PARAMS["RANGING"])

        # Symbol-specific adjustments
        sym_adj = SYMBOL_ADJUSTMENTS.get(symbol, {})
        regime_adj = sym_adj.get(regime, {})

        # Merge: base + symbol adjustments
        params = StrategyParams(
            threshold_mult=regime_adj.get("threshold_mult", base.threshold_mult),
            sl_mult=regime_adj.get("sl_mult", base.sl_mult),
            tp_mult=regime_adj.get("tp_mult", base.tp_mult),
            risk_mult=regime_adj.get("risk_mult", base.risk_mult),
            trailing_first_lock=regime_adj.get("trailing_first_lock", base.trailing_first_lock),
            trailing_n1=regime_adj.get("trailing_n1", base.trailing_n1),
            max_positions=regime_adj.get("max_positions", base.max_positions),
            min_score=regime_adj.get("min_score", base.min_score),
            description=base.description,
        )

        # ADX-based fine tuning
        if adx > 30:
            # Strong trend → more aggressive
            params.risk_mult *= 1.1
            params.tp_mult *= 1.1
        elif adx < 18:
            # Weak trend → more conservative
            params.risk_mult *= 0.8
            params.min_score += 0.05

        # ATR% based fine tuning
        if atr_pct > 1.0:
            # High volatility → reduce risk
            params.risk_mult *= 0.8
            params.sl_mult *= 1.2

        return params

    def get_regime_for_signal(self, regime: str, action: str) -> str:
        """Ajuste le régime en fonction de la direction du signal.

        Contre-tendance : un signal BUY en TREND_DOWN ou SELL en TREND_UP
        est ramené à RANGING pour utiliser des paramètres plus conservateurs
        (SL 1.5×ATR, TP 4.0×ATR au lieu de 2.0/5.0).
        """
        if action == "BUY":
            if regime == "TREND_DOWN":
                return "RANGING"  # Contre-tendance : plus conservateur
        elif action == "SELL":
            if regime == "TREND_UP":
                return "RANGING"  # Contre-tendance : plus conservateur
        return regime

    def should_trade(self, symbol: str, regime: str, score: float, adx: float = 22) -> tuple[bool, str]:
        """Détermine si on doit trader selon le régime et le score.

        Returns:
            (should_trade, reason)
        """
        params = self.get_params(symbol, regime, adx)

        if score < params.min_score:
            return False, f"Score {score:.2f} < min {params.min_score:.2f}"

        if regime == "HIGH_VOL" and adx > 35:
            return False, f"ADX {adx:.1f} > 35 en HIGH_VOL → trop risqué"

        return True, "OK"


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_selector = StrategySelector()


def get_strategy_params(symbol: str, regime: str, adx: float = 22, atr_pct: float = 0.5) -> StrategyParams:
    """Retourne les paramètres adaptés (fonction convenience)."""
    return _default_selector.get_params(symbol, regime, adx, atr_pct)


def should_trade(symbol: str, regime: str, score: float, adx: float = 22) -> tuple[bool, str]:
    """Vérifie si on doit trader (fonction convenience)."""
    return _default_selector.should_trade(symbol, regime, score, adx)
