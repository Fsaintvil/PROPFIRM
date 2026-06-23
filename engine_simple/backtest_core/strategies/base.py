"""
Strategy ABC — Interface commune pour toutes les stratégies de trading.

Chaque stratégie implémente generate() qui retourne un Signal ou None.
Le Signal contient toutes les informations nécessaires à l'exécution d'un trade.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Signal:
    """Signal de trading généré par une stratégie."""

    symbol: str
    action: str  # "BUY" | "SELL"
    score: float  # Confiance du signal (0.0 - 1.0)
    entry_price: float  # Prix d'entrée
    sl: float  # Stop Loss
    tp: float  # Take Profit
    regime: str  # Régime de marché détecté
    timestamp: datetime  # Timestamp du signal

    # Métadonnées optionnelles
    strategy: str = ""  # Nom de la stratégie
    timeframe: str = "H1"
    metadata: dict = field(default_factory=dict)  # Indicateurs, raisons, etc.
    risk_mult: float = 1.0  # Multiplicateur de risque (0.0 - 2.0)
    enforce_rr: bool = True  # Forcer le respect du RR minimum

    def is_buy(self) -> bool:
        return self.action == "BUY"

    def is_sell(self) -> bool:
        return self.action == "SELL"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "score": round(self.score, 4),
            "entry_price": round(self.entry_price, 5),
            "sl": round(self.sl, 5),
            "tp": round(self.tp, 5),
            "regime": self.regime,
            "timestamp": str(self.timestamp),
            "strategy": self.strategy,
            "timeframe": self.timeframe,
        }


class Strategy(ABC):
    """Classe de base pour toutes les stratégies de trading."""

    @abstractmethod
    def generate(
        self, bar_idx: int, data: dict, regime: str, open_positions: list, timestamp: Optional[datetime] = None
    ) -> Optional[Signal]:
        """
        Génère un signal de trading à l'index de barre donné.

        Args:
            bar_idx: Index de la barre courante dans les arrays
            data: Dict avec clés "open", "high", "low", "close", "volume", "spread"
                  Chaque valeur est un np.ndarray
            regime: Régime de marché détecté
            open_positions: Liste des positions ouvertes (SimTrade)
            timestamp: Timestamp de la barre courante

        Returns:
            Signal ou None si pas de trade
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Nom lisible de la stratégie."""
        ...

    def get_config(self) -> dict:
        """Retourne la configuration courante."""
        return {}
