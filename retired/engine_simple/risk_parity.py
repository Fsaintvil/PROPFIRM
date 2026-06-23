"""Risk Parity — Allocation basée sur le risque égal par position.

Chaque position contribue au risque total du portfolio de manière égale.
Le sizing est ajusté en fonction de la volatilité (ATR) de chaque symbole.

Usage:
    rp = RiskParitySizer()
    lot = rp.calculate_lot("BTCUSD", atr=500, account_balance=200000)
"""

import logging
import numpy as np

logger = logging.getLogger("risk_parity")


class RiskParitySizer:
    """Calcule la taille des positions selon le risk parity."""

    # Default volatilities (ATR% approximate)
    DEFAULT_VOLATILITY = {
        "XAUUSD": 0.8,
        "BTCUSD": 2.5,
        "ETHUSD": 3.0,
        "US500.cash": 0.6,
    }

    # Contract sizes (lots to notional) — Juin 2026: purgé EURUSD
    CONTRACT_SIZE = {
        "XAUUSD": 100,  # 1 lot = 100 oz
        "BTCUSD": 1,  # 1 lot = 1 BTC
        "ETHUSD": 1,  # 1 lot = 1 ETH
        "US500.cash": 1,  # 1 lot = 1 index
    }

    def __init__(
        self, total_capital: float = 200000, risk_per_trade_pct: float = 0.004, max_risk_budget_pct: float = 0.02
    ):
        """
        Args:
            total_capital: Capital total du compte
            risk_per_trade_pct: Risque par trade en % du capital (0.4%)
            max_risk_budget_pct: Budget de risque max par symbole (2%)
        """
        self.total_capital = total_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_risk_budget_pct = max_risk_budget_pct

    def get_volatility(self, symbol: str, atr: float = None, price: float = None) -> float:
        """Retourne la volatilité du symbole (ATR%)."""
        if atr is not None and price is not None and price > 0:
            return atr / price * 100

        return self.DEFAULT_VOLATILITY.get(symbol, 1.0)

    def calculate_lot(
        self, symbol: str, atr: float = None, price: float = None, sl_atr_mult: float = 2.0, risk_mult: float = 1.0
    ) -> float:
        """Calcule la taille du lot en risk parity.

        Args:
            symbol: Symbole
            atr: ATR actuel
            price: Prix actuel
            sl_atr_mult: Multiplicateur SL (ex: 2.0 ATR)
            risk_mult: Multiplicateur de risque (ex: 0.8 pour HIGH_VOL)

        Returns:
            Taille du lot (arrondie au lot minimum)
        """
        # Volatility
        vol = self.get_volatility(symbol, atr, price)

        # Risk budget in $
        risk_budget = self.total_capital * self.risk_per_trade_pct * risk_mult

        # SL distance in $
        if atr is not None and price is not None:
            sl_distance = atr * sl_atr_mult
        else:
            # Estimate from volatility
            sl_distance = price * vol / 100 * sl_atr_mult if price else 100

        # Contract size
        contract_size = self.CONTRACT_SIZE.get(symbol, 100)

        # Lot size = risk_budget / (sl_distance * contract_size)
        if sl_distance > 0 and contract_size > 0:
            lot = risk_budget / (sl_distance * contract_size)
        else:
            lot = 0.01

        # Apply volatility scaling
        # Higher vol → smaller position
        vol_factor = 1.0
        if vol > 2.0:
            vol_factor = 0.7
        elif vol > 1.5:
            vol_factor = 0.8
        elif vol < 0.3:
            vol_factor = 1.2

        lot *= vol_factor

        # Round to lot step
        lot = self._round_lot(symbol, lot)

        return lot

    def _round_lot(self, symbol: str, lot: float) -> float:
        """Arrondit le lot au pas minimum."""
        if symbol in ("BTCUSD", "ETHUSD"):
            # Crypto: 0.01 lot step
            return round(lot, 2)
        elif symbol == "XAUUSD":
            # Gold: 0.01 lot step
            return round(lot, 2)
        elif symbol == "US500.cash":
            # Index: 0.01 lot step
            return round(lot, 2)
        else:
            # Forex: 0.01 lot step
            return round(lot, 2)

    def calculate_risk_contribution(self, symbol: str, lot: float, atr: float, price: float) -> float:
        """Calcule la contribution au risque total."""
        if atr is None or price is None or price == 0:
            return 0.0

        # Risk in $
        sl_distance = atr * 2.0  # Default 2 ATR SL
        contract_size = self.CONTRACT_SIZE.get(symbol, 100)
        risk = lot * sl_distance * contract_size

        # As % of capital
        risk_pct = risk / self.total_capital * 100

        return risk_pct

    def get_equal_risk_allocation(
        self, symbols: list[str], atrs: dict[str, float], prices: dict[str, float]
    ) -> dict[str, float]:
        """Calcule l'allocation risk-parity pour tous les symboles.

        Returns:
            {symbol: lot_size}
        """
        if not symbols:
            return {}

        # Target risk per symbol
        target_risk_pct = self.risk_per_trade_pct * 100 / len(symbols)

        allocations = {}
        for sym in symbols:
            atr = atrs.get(sym)
            price = prices.get(sym)

            lot = self.calculate_lot(sym, atr, price)
            allocations[sym] = lot

        return allocations

    def get_status(self) -> dict:
        """Retourne le statut du risk parity."""
        return {
            "total_capital": self.total_capital,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_risk_budget_pct": self.max_risk_budget_pct,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_sizer = RiskParitySizer()


def calculate_lot(
    symbol: str, atr: float = None, price: float = None, sl_atr_mult: float = 2.0, risk_mult: float = 1.0
) -> float:
    """Calcule le lot (fonction convenience)."""
    return _default_sizer.calculate_lot(symbol, atr, price, sl_atr_mult, risk_mult)


def get_equal_risk_allocation(symbols: list[str], atrs: dict[str, float], prices: dict[str, float]) -> dict[str, float]:
    """Retourne l'allocation risk-parity (fonction convenience)."""
    return _default_sizer.get_equal_risk_allocation(symbols, atrs, prices)
