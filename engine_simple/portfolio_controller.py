"""Portfolio Controller — Gestion de portefeuille multi-symboles.

Gère l'exposition totale, la corrélation entre symboles, et les limites FTMO.

Features:
- Max positions total et par symbole
- Corrélation inter-symboles (correlation groups)
- Exposure management (long/short net)
- DD tracking par symbole
- Risk allocation dynamique

Usage:
    pc = PortfolioController()
    can, reason = pc.can_open_position("BTCUSD", "BUY", current_positions)
    allocation = pc.get_risk_allocation("BTCUSD", current_dd=0.05)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("portfolio_controller")


@dataclass
class Position:
    """Position ouverte."""

    symbol: str
    direction: str  # "BUY" ou "SELL"
    lot: float
    entry_price: float
    current_price: float = 0.0
    pnl: float = 0.0
    open_time: str = ""
    regime: str = "RANGING"


@dataclass
class PortfolioState:
    """État actuel du portefeuille."""

    total_positions: int
    long_positions: int
    short_positions: int
    net_exposure: float  # long - short (normalisé)
    symbols_exposure: dict[str, float] = field(default_factory=dict)


# ============================================================================
# GROUPES DE CORRÉLATION — réactivé 25 Juin 2026 (Risk & Compliance)
# Limite à max 3 trades total par groupe, max 2 dans la même direction.
# Empêche les pertes simultanées sur symboles corrélés (Pearson > 0.70 H1).
# ============================================================================
POSITION_GROUPS: dict[str, list[str]] = {
    "FOREX_MAJORS": ["EURUSD", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "USDJPY"],
    "FOREX_CROSSES": ["EURJPY", "GBPJPY", "EURGBP", "AUDJPY"],
    "CRYPTO": ["BTCUSD", "ETHUSD", "SOLUSD", "LNKUSD", "BNBUSD"],
    "INDICES": ["US500.cash", "US30.cash", "US100.cash", "JP225.cash"],
    "EUROPE_INDICES": ["GER40.cash", "UK100.cash"],
    "COMMODITIES": ["XAUUSD", "XAGUSD", "USOIL.cash", "UKOIL.cash", "NATGAS.cash"],
}

# Limites d'exposition 🔧 FIX #7 (2 Juillet 2026) — capacité augmentée
# Ces constantes doivent correspondre à config/default.yaml + config/production.yaml.
# Le pipeline filtre d'abord (par confiance), portfolio_controller = backup sécurité.
# ⚠️ NE PAS modifier sans synchroniser default.yaml et production.yaml.
MAX_POSITIONS_TOTAL = 18  # 🔧 ×1.5 (3 Juillet): 12→18 (11 symboles actifs)
MAX_POSITIONS_PER_SYMBOL = 6  # 🔧 ×1.5: 4→6 (plus de marge par symbole)
MAX_POSITIONS_PER_DIRECTION = 9  # 🔧 ×1.5: 6→9
MAX_TRADES_PER_GROUP = 4  # 🔧 ×1.5: 3→4 (arrondi inférieur)
MAX_TRADES_PER_DIRECTION_IN_GROUP = 3  # 🔧 ×1.5: 2→3


class PortfolioController:
    """Contrôleur de portefeuille pour le trading multi-symboles."""

    def __init__(self):
        self._positions: list[Position] = []
        self._symbol_dd: dict[str, float] = {}
        self._daily_pnl: float = 0.0
        self._initial_balance: float = 200_000.0

    @staticmethod
    def _get_group_for_symbol(symbol: str) -> str | None:
        """Retourne le groupe de corrélation d'un symbole, ou None."""
        for group_name, symbols in POSITION_GROUPS.items():
            if symbol in symbols:
                return group_name
        return None

    @staticmethod
    def _get_dir(p) -> str:
        """Extrait la direction d'une position (Position ou MT5 TradePosition)."""
        return p.direction if hasattr(p, "direction") else ("BUY" if getattr(p, "type", -1) == 0 else "SELL")

    def set_positions(self, positions: list[Position]):
        """Met à jour les positions actuelles."""
        self._positions = positions

    def can_open_position(
        self,
        symbol: str,
        direction: str,
        positions: list[Position] | None = None,
        high_confidence: bool = False,
    ) -> tuple[bool, str]:
        """Vérifie si on peut ouvrir une position.

        Args:
            symbol: Symbole (ex: "BTCUSD")
            direction: "BUY" ou "SELL"
            positions: Liste des positions actuelles (optionnel)
            high_confidence: Si True, bypass les limites de positions (conf>90%)

        Returns:
            (can_open, reason)
        """
        if positions is None:
            positions = self._positions

        # 🔥 HIGH CONFIDENCE (>90%) : relaxe les limites par symbole/direction
        # mais PRÉSERVE les limites de corrélation (protection FTMO)
        # 🐛 FIX 26 Juin 2026: ajout des limites per-symbol et per-direction manquantes
        if high_confidence:
            # Vérifier le DD et daily loss en premier (protection FTMO)
            sym_dd = self._symbol_dd.get(symbol, 0.0)
            if sym_dd > 0.08:
                return False, f"DD {symbol} trop élevé ({sym_dd * 100:.1f}%)"
            if self._daily_pnl < -self._initial_balance * 0.02:
                return False, f"Daily loss limit atteint ({self._daily_pnl:.2f})"
            # ✅ Limite max positions par symbole (doublée pour high confidence)
            sym_positions = [p for p in positions if p.symbol == symbol]
            sym_max = MAX_POSITIONS_PER_SYMBOL * 2  # 8 au lieu de 4 pour high conf
            if len(sym_positions) >= sym_max:
                return False, f"Max positions {symbol} atteint ({len(sym_positions)}/{sym_max})"
            # ✅ Limite max positions par direction (doublée)
            dir_positions = [p for p in positions if self._get_dir(p) == direction]
            dir_max = MAX_POSITIONS_PER_DIRECTION * 2  # 16 au lieu de 8
            if len(dir_positions) >= dir_max:
                return False, f"Max positions {direction} atteint ({len(dir_positions)}/{dir_max})"
            # ✅ Correlation group limits TOUJOURS actives (même en high confidence)
            group = self._get_group_for_symbol(symbol)
            if group:
                group_positions = [p for p in positions if self._get_group_for_symbol(p.symbol) == group]
                group_dir_positions = [p for p in group_positions if self._get_dir(p) == direction]
                if len(group_positions) >= MAX_TRADES_PER_GROUP:
                    return False, f"Groupe {group}: déjà {len(group_positions)} positions (max {MAX_TRADES_PER_GROUP})"
                if len(group_dir_positions) >= MAX_TRADES_PER_DIRECTION_IN_GROUP:
                    return (
                        False,
                        f"Groupe {group}: déjà {len(group_dir_positions)} positions {direction} (max {MAX_TRADES_PER_DIRECTION_IN_GROUP})",
                    )
            # ✅ Limite max positions total TOUJOURS active
            if len(positions) >= MAX_POSITIONS_TOTAL:
                return False, f"Max positions total atteint ({MAX_POSITIONS_TOTAL})"
            return True, "HIGH CONFIDENCE bypass (corrélation protégée)"

        # 1. Max positions total
        if len(positions) >= MAX_POSITIONS_TOTAL:
            return False, f"Max positions total atteint ({MAX_POSITIONS_TOTAL})"

        # 2. Max positions par symbole
        sym_positions = [p for p in positions if p.symbol == symbol]
        if len(sym_positions) >= MAX_POSITIONS_PER_SYMBOL:
            return False, f"Max positions {symbol} atteint ({MAX_POSITIONS_PER_SYMBOL})"

        # 3. Max positions par direction
        dir_positions = [p for p in positions if self._get_dir(p) == direction]
        if len(dir_positions) >= MAX_POSITIONS_PER_DIRECTION:
            return False, f"Max positions {direction} atteint ({MAX_POSITIONS_PER_DIRECTION})"

        # 4. Correlation group check — réactivé 25 Juin 2026
        group = self._get_group_for_symbol(symbol)
        if group:
            group_positions = [p for p in positions if self._get_group_for_symbol(p.symbol) == group]
            group_dir_positions = [p for p in group_positions if self._get_dir(p) == direction]

            # Max 3 trades TOTAL dans un groupe corrélé
            if len(group_positions) >= MAX_TRADES_PER_GROUP:
                return False, f"Groupe {group}: déjà {len(group_positions)} positions (max {MAX_TRADES_PER_GROUP})"

            # Max 2 trades dans la MÊME direction dans un groupe
            if len(group_dir_positions) >= MAX_TRADES_PER_DIRECTION_IN_GROUP:
                return (
                    False,
                    f"Groupe {group}: déjà {len(group_dir_positions)} positions {direction} (max {MAX_TRADES_PER_DIRECTION_IN_GROUP})",
                )

        # 5. DD check per symbol
        sym_dd = self._symbol_dd.get(symbol, 0.0)
        if sym_dd > 0.08:  # 8% DD
            return False, f"DD {symbol} trop élevé ({sym_dd * 100:.1f}%)"

        # 6. Daily loss check
        if self._daily_pnl < -self._initial_balance * 0.02:  # -2% daily
            return False, f"Daily loss limit atteint ({self._daily_pnl:.2f})"

        return True, "OK"

    def get_risk_allocation(self, symbol: str, current_dd: float = 0.0) -> float:
        """Retourne le multiplicateur de risque pour un symbole.

        Args:
            symbol: Symbole
            current_dd: Drawdown actuel (0-1)

        Returns:
            Multiplicateur de risque [0.2-1.0]
        """
        # Base allocation
        base = 1.0

        # DD reduction
        if current_dd > 0.07:  # 7% DD
            base = 0.20
        elif current_dd > 0.05:  # 5% DD
            base = 0.50
        elif current_dd > 0.03:  # 3% DD
            base = 0.75

        # Symbol-specific adjustment
        symbol_weights = {
            "XAUUSD": 1.0,
            "BTCUSD": 0.8,
            "US500.cash": 0.7,
        }
        weight = symbol_weights.get(symbol, 0.7)

        return round(base * weight, 3)

    def get_portfolio_state(self, positions: list[Position] | None = None) -> PortfolioState:
        """Retourne l'état actuel du portefeuille."""
        if positions is None:
            positions = self._positions

        long_pos = [p for p in positions if p.direction == "BUY"]
        short_pos = [p for p in positions if p.direction == "SELL"]

        # Net exposure (normalisé)
        total_lot = sum(p.lot for p in positions) or 1.0
        net = (sum(p.lot for p in long_pos) - sum(p.lot for p in short_pos)) / total_lot

        # Per-symbol exposure
        sym_exposure = {}
        for p in positions:
            sym_exposure[p.symbol] = sym_exposure.get(p.symbol, 0) + p.lot

        return PortfolioState(
            total_positions=len(positions),
            long_positions=len(long_pos),
            short_positions=len(short_pos),
            net_exposure=round(net, 3),
            symbols_exposure=sym_exposure,
        )

    def update_symbol_dd(self, symbol: str, dd: float):
        """Met à jour le DD d'un symbole."""
        self._symbol_dd[symbol] = dd

    def update_daily_pnl(self, pnl: float):
        """Met à jour le PnL journalier."""
        self._daily_pnl = pnl

    def reset_daily(self):
        """Reset le PnL journalier (nouveau jour)."""
        self._daily_pnl = 0.0


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_controller = PortfolioController()


def can_open_position(symbol: str, direction: str, positions: list[Position] | None = None) -> tuple[bool, str]:
    """Vérifie si on peut ouvrir une position (fonction convenience)."""
    return _default_controller.can_open_position(symbol, direction, positions)


def get_risk_allocation(symbol: str, current_dd: float = 0.0) -> float:
    """Retourne l'allocation de risque (fonction convenience)."""
    return _default_controller.get_risk_allocation(symbol, current_dd)
