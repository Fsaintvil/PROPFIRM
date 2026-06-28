"""
ExecutionEngine — Simulation réaliste de l'exécution des ordres.

Supporte :
  - Calcul Bid/Ask depuis le Close + spread
  - Latence configurable (50/100/250ms)
  - Requotes (probabilité paramétrable)
  - Partial fills (fractionnement des lots)
  - Slippage adverse et favorable

Usage :
    ee = ExecutionEngine(latency_ms=100, requote_prob=0.02)
    fill = ee.execute_market_order(signal, bar, spread_pips, volatility)
    # fill.price, fill.filled_lot, fill.slippage_usd, fill.requoted
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger("backtest_core.execution")


# ─── Order types ──────────────────────────────────────────────────────────

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1


# ─── FillResult ────────────────────────────────────────────────────────────


@dataclass
class FillResult:
    """Résultat de l'exécution d'un ordre."""

    order_type: int  # 0 = BUY, 1 = SELL
    requested_lot: float  # Taille demandée
    filled_lot: float  # Taille réellement remplie
    requested_price: float  # Prix au moment de la demande
    fill_price: float  # Prix d'exécution réel
    slippage_usd: float  # Coût du slippage (toujours ≥ 0)
    requoted: bool  # True si l'ordre a subi un requote
    partial_fill: bool  # True si remplissage partiel
    latency_ms: float  # Latence simulée
    timestamp: datetime  # Timestamp d'exécution
    bid: float  # Prix bid au moment de l'exécution
    ask: float  # Prix ask au moment de l'exécution

    def is_buy(self) -> bool:
        return self.order_type == ORDER_TYPE_BUY

    def is_sell(self) -> bool:
        return self.order_type == ORDER_TYPE_SELL


# ─── Market conditions ────────────────────────────────────────────────────


@dataclass
class MarketCond:
    """Conditions de marché au moment de l'exécution."""

    spread_pips: float = 1.5
    volatility: str = "normal"  # "normal" | "high" | "low" | "extreme"
    is_news: bool = False
    is_low_liquidity: bool = False
    bid: float = 0.0
    ask: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionEngine
# ═══════════════════════════════════════════════════════════════════════════


class ExecutionEngine:
    """
    Simule l'exécution d'ordres avec des conditions de marché réalistes.

    Caractéristiques :
      - Latence : délai entre signal et exécution (ms)
      - Requotes : probabilité que le prix bouge avant exécution
      - Partial fills : fractionnement pour liquidité faible
      - Slippage adverse : toujours appliqué (coût additionnel)
    """

    # Taux de requote par liquidité
    REQUOTE_PROB = {
        "normal": 0.01,  # 1% en liquidité normale
        "low": 0.05,  # 5% en liquidité faible
        "news": 0.10,  # 10% pendant les news
        "crypto": 0.03,  # 3% sur crypto (volatil mais liquide)
    }

    # Taux de partial fill par liquidité
    PARTIAL_FILL_PROB = {
        "normal": 0.0,  # 0% en liquidité normale (remplissage total)
        "low": 0.10,  # 10% en liquidité faible
        "news": 0.05,  # 5% pendant les news
        "crypto": 0.02,  # 2% sur crypto
    }

    # Fraction moyenne de remplissage en cas de partial fill
    PARTIAL_FILL_RATIO = {
        "normal": 1.0,
        "low": 0.7,  # 70% du lot demandé
        "news": 0.8,
        "crypto": 0.85,
    }

    def __init__(
        self,
        latency_ms: float = 100,
        requote_prob: Optional[float] = None,
        enable_partial_fill: bool = True,
        rng_seed: Optional[int] = None,
    ):
        """
        Args:
            latency_ms: Latence moyenne en ms (50/100/250).
            requote_prob: Probabilité de requote (None = auto selon liquidité).
            enable_partial_fill: Activer les partial fills.
            rng_seed: Seed pour reproductibilité.
        """
        self.latency_ms = latency_ms
        self.requote_prob_override = requote_prob
        self.enable_partial_fill = enable_partial_fill
        self.rng = np.random.default_rng(rng_seed)

    # ─── Calcul bid/ask ───────────────────────────────────────────────────

    @staticmethod
    def calculate_bid_ask(close: float, spread_pips: float, pip_size: float) -> tuple[float, float]:
        """
        Calcule les prix bid et ask à partir du close et du spread.

        Args:
            close: Prix de clôture de la bougie
            spread_pips: Spread en pips
            pip_size: Taille d'un pip (ex: 0.0001 pour EURUSD)

        Returns:
            (bid, ask)
        """
        half_spread = (spread_pips * pip_size) / 2.0
        bid = close - half_spread
        ask = close + half_spread
        return round(bid, 5), round(ask, 5)

    # ─── Liquidité ▸ probabilité de requote ───────────────────────────────

    def _get_requote_prob(self, cond: MarketCond) -> float:
        """Détermine la probabilité de requote selon les conditions."""
        if self.requote_prob_override is not None:
            return self.requote_prob_override

        if cond.is_news:
            return self.REQUOTE_PROB["news"]
        if cond.is_low_liquidity:
            return self.REQUOTE_PROB["low"]
        if cond.volatility == "extreme":
            return self.REQUOTE_PROB["news"]
        return self.REQUOTE_PROB["normal"]

    def _get_partial_fill_ratio(self, cond: MarketCond) -> float:
        """Détermine le ratio de remplissage."""
        if not self.enable_partial_fill:
            return 1.0

        prob = self.PARTIAL_FILL_PROB.get("normal", 0.0)
        if cond.is_news:
            prob = self.PARTIAL_FILL_PROB.get("news", 0.05)
        elif cond.is_low_liquidity:
            prob = self.PARTIAL_FILL_PROB.get("low", 0.10)
        elif cond.volatility == "extreme":
            prob = self.PARTIAL_FILL_PROB.get("news", 0.05)

        if self.rng.random() < prob:
            ratio = self.PARTIAL_FILL_RATIO.get("low", 0.7)
            if cond.is_news:
                ratio = self.PARTIAL_FILL_RATIO.get("news", 0.8)
            return ratio
        return 1.0

    def _simulate_latency(self) -> float:
        """
        Simule la latence d'exécution.
        Distribution : normale centrée sur latency_ms, écart-type = 30% de la moyenne.
        Returns: Latence en ms.
        """
        std = self.latency_ms * 0.3
        lat = float(self.rng.normal(self.latency_ms, std))
        return max(5.0, lat)  # Minimum 5ms (physique)

    def _apply_requote(self, price: float, cond: MarketCond) -> tuple[float, bool]:
        """
        Simule un requote : le prix bouge avant exécution.
        Mouvement : normal avec sigma = spread * 0.5
        Returns: (nouveau_prix, requoted)
        """
        pip_size = 0.0001 if cond.ask - cond.bid > 0.0005 else 0.0001
        spread_val = cond.spread_pips * pip_size
        price_move = float(self.rng.normal(0, spread_val * 0.5))
        new_price = price + price_move
        return new_price, True

    # ─── Exécution principale ─────────────────────────────────────────────

    def execute_market_order(
        self, order_type: int, lot: float, close_price: float, cond: MarketCond, timestamp: Optional[datetime] = None
    ) -> FillResult:
        """
        Exécute un ordre au marché.

        Args:
            order_type: 0 = BUY, 1 = SELL
            lot: Taille du lot demandé
            close_price: Prix de clôture de la bougie courante
            cond: Conditions de marché
            timestamp: Timestamp de l'exécution

        Returns:
            FillResult avec toutes les informations d'exécution.
        """
        pip_size = 0.0001 if cond.ask - cond.bid > 0.0005 else 0.0001
        if cond.ask == 0 or cond.bid == 0:
            cond.bid, cond.ask = self.calculate_bid_ask(close_price, cond.spread_pips, pip_size)

        # 1. Simuler la latence
        latency = self._simulate_latency()

        # 2. Prix d'entrée théorique
        if order_type == ORDER_TYPE_BUY:
            theoretical_price = cond.ask
        else:
            theoretical_price = cond.bid

        # 3. Simuler le requote
        requoted = False
        fill_price = theoretical_price
        req_prob = self._get_requote_prob(cond)
        if self.rng.random() < req_prob:
            fill_price, requoted = self._apply_requote(theoretical_price, cond)
            logger.debug(f"  [EXEC] Requote! Prix: {theoretical_price:.5f} → {fill_price:.5f}")

        # 4. Simuler le partial fill
        fill_ratio = self._get_partial_fill_ratio(cond)
        filled_lot = round(lot * fill_ratio, 2)
        partial_fill = fill_ratio < 1.0

        # 5. Calculer le slippage en USD
        if order_type == ORDER_TYPE_BUY:
            # BUY : slippage adverse = fill_price > ask (payé plus cher)
            slippage_pips = abs(fill_price - cond.ask) / pip_size
        else:
            # SELL : slippage adverse = fill_price < bid (vendu moins cher)
            slippage_pips = abs(cond.bid - fill_price) / pip_size

        pip_value = 10.0  # Standard $10/pip pour forex
        if pip_size > 0.001:  # Crypto, métaux, indices
            pip_value = 1.0
        slippage_usd = round(slippage_pips * pip_value * filled_lot, 2)

        # 6. Ajuster le prix réel (slippage adverse toujours)
        if order_type == ORDER_TYPE_BUY and fill_price < theoretical_price:
            fill_price = theoretical_price  # Jamais favorable
        elif order_type == ORDER_TYPE_SELL and fill_price > theoretical_price:
            fill_price = theoretical_price

        # Arrondi
        fill_price = round(fill_price, 5)

        return FillResult(
            order_type=order_type,
            requested_lot=lot,
            filled_lot=filled_lot,
            requested_price=theoretical_price,
            fill_price=fill_price,
            slippage_usd=slippage_usd,
            requoted=requoted,
            partial_fill=partial_fill,
            latency_ms=round(latency, 1),
            timestamp=timestamp or datetime.utcnow(),
            bid=cond.bid,
            ask=cond.ask,
        )

    def execute_buy(
        self, lot: float, close_price: float, cond: MarketCond, timestamp: Optional[datetime] = None
    ) -> FillResult:
        """Raccourci pour un ordre BUY."""
        return self.execute_market_order(ORDER_TYPE_BUY, lot, close_price, cond, timestamp)

    def execute_sell(
        self, lot: float, close_price: float, cond: MarketCond, timestamp: Optional[datetime] = None
    ) -> FillResult:
        """Raccourci pour un ordre SELL."""
        return self.execute_market_order(ORDER_TYPE_SELL, lot, close_price, cond, timestamp)
