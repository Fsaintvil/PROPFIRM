"""
CostModel — Modèle de coûts réaliste pour backtest institutionnel.

Supporte :
  - Spread : historique (parquet) → statique (YAML) → widening news/volatilité
  - Commission : $3.5/lot forex, 0.05-0.10% crypto, $5/lot métaux
  - Swap : overnight par symbole (long/short)
  - Slippage : stochastique selon régime de volatilité

Usage :
    cm = CostModel(config)
    costs = cm.get_total_cost("EURUSD", "BUY", 0.1, 1.1050, 1.1100,
                              days_held=2, volatility="normal", timestamp=dt)
    # -> {"spread": 1.5, "commission": 0.70, "swap": -0.15, "slippage": 0.3, "total": 2.35}
"""

import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional
import yaml

import numpy as np

logger = logging.getLogger("backtest_core.costs")

# ─── Config par défaut (peut être surchargée par YAML) ─────────────────────

DEFAULT_SPREAD_PIPS: dict[str, float] = {
    # Forex majeurs
    "EURUSD": 1.5,
    "GBPUSD": 1.5,
    "USDJPY": 1.5,
    "USDCHF": 1.5,
    "USDCAD": 1.5,
    "AUDUSD": 1.5,
    "NZDUSD": 1.5,
    # Forex cross/volatiles
    "EURJPY": 2.5,
    "GBPJPY": 3.0,
    "AUDJPY": 2.5,
    # Métaux
    "XAUUSD": 5.0,
    "XAGUSD": 6.0,
    # Indices
    "US500.cash": 2.0,
    "US100.cash": 2.0,
    "NAS100.cash": 2.0,
    "JP225.cash": 2.0,
    # Commodités
    "USOIL.cash": 5.0,
    "UKOIL.cash": 5.0,
    "NATGAS": 7.0,
    "NATGAS.cash": 7.0,
    # Crypto
    "BTCUSD": 10.0,
    "ETHUSD": 10.0,
}

DEFAULT_COMMISSION: dict[str, tuple[float, str]] = {
    # (montant, type) — type = "per_lot" | "pct_notional"
    "forex": (3.5, "per_lot"),
    "metals": (5.0, "per_lot"),
    "indices": (2.0, "per_lot"),
    "energy": (5.0, "per_lot"),
    "crypto": (0.0007, "pct_notional"),  # 0.07%
}

DEFAULT_SWAP: dict[str, tuple[float, float]] = {
    # (swap_long_pips, swap_short_pips) — pips par lot par nuit
    "EURUSD": (-0.5, 0.2),
    "GBPUSD": (-0.8, 0.4),
    "USDJPY": (0.3, -0.6),
    "USDCHF": (0.2, -0.5),
    "USDCAD": (0.1, -0.4),
    "AUDUSD": (0.5, -1.0),
    "NZDUSD": (0.6, -1.1),
    "EURJPY": (-0.7, 0.3),
    "GBPJPY": (-1.0, 0.5),
    "AUDJPY": (0.4, -0.8),
    "XAUUSD": (-2.0, 1.0),
    "XAGUSD": (-1.5, 0.8),
    "US500.cash": (-1.0, 0.5),
    "US100.cash": (-1.2, 0.6),
    "NAS100.cash": (-1.2, 0.6),
    "JP225.cash": (-0.8, 0.4),
    "USOIL.cash": (-2.5, 1.5),
    "UKOIL.cash": (-2.5, 1.5),
    "NATGAS": (-3.0, 2.0),
    "NATGAS.cash": (-3.0, 2.0),
    "BTCUSD": (0.0, 0.0),  # Pas de swap crypto (marché 24/7)
    "ETHUSD": (0.0, 0.0),
}

# ─── Catégories de symboles ────────────────────────────────────────────────

SYMBOL_CATEGORY: dict[str, str] = {
    "EURUSD": "forex",
    "GBPUSD": "forex",
    "USDJPY": "forex",
    "USDCHF": "forex",
    "USDCAD": "forex",
    "AUDUSD": "forex",
    "NZDUSD": "forex",
    "EURJPY": "forex",
    "GBPJPY": "forex",
    "AUDJPY": "forex",
    "XAUUSD": "metals",
    "XAGUSD": "metals",
    "US500.cash": "indices",
    "US100.cash": "indices",
    "NAS100.cash": "indices",
    "JP225.cash": "indices",
    "USOIL.cash": "energy",
    "UKOIL.cash": "energy",
    "NATGAS": "energy",
    "NATGAS.cash": "energy",
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",
}


# ─── Pip utilities ─────────────────────────────────────────────────────────


def get_pip_info(symbol: str) -> tuple[float, float]:
    """Retourne (pip_size, pip_value_per_lot) pour un symbole.

    La pip_value est la valeur en USD d'1 pip (i.e. d'1 mouvement de pip_size)
    pour 1 lot standard.

    Logique par catégorie d'instrument :
      - Forex (EURUSD, etc.) : 1 pip = 0.0001, pip_value = $10/lot (100K unités × 0.0001)
      - Métaux (XAUUSD) : 1 pip = $0.01, pip_value = $10/lot (100 oz × $0.01)
      - Indices USD (US500, US100, US30) : 1 pip = 1 index point, pip_value = $1/lot
      - Indices JPY (JP225) : 1 pip = 1 index point, pip_value = $0.0091/lot (≈ 1/110 USDJPY)
      - Indices UK (UK100) : 1 pip = 1 index point, pip_value = $0.80/lot (≈ 1/1.25 GBPUSD)
      - Énergie (USOIL) : 1 pip = $0.01, pip_value = $10/lot (100 barils × $0.01)
      - Crypto (BTCUSD) : 1 pip = $0.01, pip_value = $1/lot (1 coin × $0.01)
    """
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 10.0  # 10$ par pip pour 1 lot standard (100oz)
    if symbol in ("US500.cash", "US100.cash", "NAS100.cash", "US30.cash"):
        return 1.0, 1.0  # 1 index point = $1/lot (USD-denominated)
    if symbol == "JP225.cash":
        return 1.0, 0.0091  # 1 index point = ¥1 ≈ $0.0091/lot (JPY→USD à 110)
    if symbol == "UK100.cash":
        return 1.0, 0.80  # 1 index point = £1 ≈ $0.80/lot (GBP→USD à 1.25)
    if symbol in ("USOIL.cash", "UKOIL.cash", "NATGAS", "NATGAS.cash"):
        return 0.01, 10.0  # 10$ par pip pour 1 lot (100 barils)
    if symbol in ("BTCUSD", "ETHUSD"):
        return 0.01, 1.0  # 1$ par pip pour 1 lot (1 coin)
    return 0.0001, 10.0  # Forex standard : 10$ par pip pour 1 lot (100K)


def get_contract_size(symbol: str) -> float:
    """Taille du contrat standard."""
    if symbol in ("XAUUSD", "XAGUSD"):
        return 100.0
    if symbol in ("US500.cash", "US100.cash", "NAS100.cash", "JP225.cash", "US30.cash", "UK100.cash"):
        return 1.0
    if symbol in ("USOIL.cash", "UKOIL.cash", "NATGAS", "NATGAS.cash"):
        return 100.0
    if symbol in ("BTCUSD", "ETHUSD"):
        return 1.0
    return 100_000.0


# ─── Périodes de liquidité / news ──────────────────────────────────────────

# Heures de faible liquidité (UTC) : after-hours, weekend
LOW_LIQUIDITY_SESSIONS = [
    (time(22, 0), time(2, 0)),  # 22h-2h UTC : transition NY→Asia
]

NEWS_WIDENING_FACTOR = 2.0  # Le spread double pendant les news
VOLATILE_WIDENING_FACTOR = 1.5  # Multiplié par 1.5 en volatilité

# ─── Constantes pour le slippage ───────────────────────────────────────────

SLIPPAGE_CONFIG = {
    "normal": {"mean": 0.3, "std": 0.2},  # pips
    "high_vol": {"mean": 2.0, "std": 1.0},
    "news": {"mean": 10.0, "std": 5.0},
    "crypto": {"mean": 0.0007, "std": 0.0003, "unit": "pct"},
}

ROLLOVER_TIME = time(21, 0)  # Heure de rollover MT5 (~21h UTC)
ROLLOVER_3DAY = time(23, 59)  # Mercredi → triple swap


# ═══════════════════════════════════════════════════════════════════════════
# CostModel
# ═══════════════════════════════════════════════════════════════════════════


class CostModel:
    """Modèle de coûts réaliste pour backtest multi-actifs."""

    def __init__(self, config: Optional[dict] = None):
        """
        Args:
            config: Dict issu de backtest/config/*.yaml.
                    Peut contenir les clés 'spread', 'commission', 'slippage', 'swap'.
                    Si None, utilise les valeurs par défaut.
        """
        self.config = config or {}
        self._spread_config = self.config.get("spread", {})
        self._commission_config = self.config.get("commission", {})
        self._slippage_config = self.config.get("slippage", {})
        self._swap_config = self.config.get("swap", {})

        # Swap rates surchargés depuis YAML si présent
        self._swap_rates: dict[str, tuple[float, float]] = dict(DEFAULT_SWAP)
        swap_source = self._swap_config.get("source")
        if swap_source:
            self._load_swap_rates(swap_source)

        # Spread widening multiplicateurs
        self._news_mult = NEWS_WIDENING_FACTOR
        self._vol_mult = VOLATILE_WIDENING_FACTOR

    # ─── Spread ────────────────────────────────────────────────────────────

    def get_spread(
        self,
        symbol: str,
        timestamp: Optional[datetime] = None,
        historical_spread: Optional[float] = None,
        is_news: bool = False,
        volatility: str = "normal",
    ) -> float:
        """
        Calcule le spread en pips.

        Priorité :
          1. Spread historique du parquet (si fourni et > 0)
          2. Spread statique par symbole
          3. Si news → ×2
          4. Si faible liquidité → ×1.5
          5. Si haute volatilité → ×1.5
        """
        # Base : historique ou statique
        if historical_spread is not None and historical_spread > 0:
            # Convertir les points MT5 en pips
            # MT5 rapporte le spread en points (plus petite unité de prix).
            # Pour chaque type d'instrument, le facteur de conversion est
            #   point_size / pip_size.
            #
            #   Instrument  | DIGITS | point | pip_size | facteur
            #   ------------+--------+-------+----------+--------
            #   Forex       |   5    | 1e-5  | 0.0001   | ÷10
            #   Or/Crypto   |   2    | 0.01  | 0.01     | ×1
            #   Indices     |   2    | 0.01  | 1.0      | ÷100
            pip_size = get_pip_info(symbol)[0]
            if pip_size == 0.0001:
                base = historical_spread / 10.0
            elif pip_size == 1.0:
                # Indices : 1 pip = 1 index point = 100 points MT5
                base = historical_spread / 100.0
            else:
                # pip_size=0.01 (XAU, crypto, oil) : 1 pip = 1 point MT5
                base = historical_spread
        else:
            base = DEFAULT_SPREAD_PIPS.get(symbol, 2.0)

        # Widening
        mult = 1.0
        if is_news:
            mult *= self._news_mult
            logger.debug(f"  [SPREAD] {symbol} news widening x{self._news_mult}")
        if self._is_low_liquidity(timestamp):
            mult *= self._vol_mult
            logger.debug(f"  [SPREAD] {symbol} low liq widening x{self._vol_mult}")
        if volatility == "high":
            mult *= self._vol_mult

        return round(base * mult, 2)

    def _is_low_liquidity(self, timestamp: Optional[datetime]) -> bool:
        """Vérifie si le timestamp tombe dans une période de faible liquidité."""
        if timestamp is None:
            return False
        t = timestamp.time()
        for start, end in LOW_LIQUIDITY_SESSIONS:
            if start <= t or t < end:
                return True
        # Weekend
        if timestamp.weekday() >= 5:
            return True
        return False

    # ─── Commission ───────────────────────────────────────────────────────

    def get_commission(self, symbol: str, lot: float, price: float) -> float:
        """
        Calcule la commission en USD.

        Types :
          - forex : $3.5/lot/side (soit ×2 round-trip)
          - metals : $5/lot/side
          - indices : $2/lot/side
          - energy : $5/lot/side
          - crypto : 0.07% du notionnel (×2 round-trip)
        """
        category = SYMBOL_CATEGORY.get(symbol, "forex")
        comm_config = self._commission_config.get(category, DEFAULT_COMMISSION.get(category, (3.5, "per_lot")))

        if isinstance(comm_config, (list, tuple)):
            amount, comm_type = comm_config
        elif isinstance(comm_config, dict):
            amount = comm_config.get("amount", 3.5)
            comm_type = comm_config.get("type", "per_lot")
        else:
            amount, comm_type = 3.5, "per_lot"

        if comm_type == "per_lot":
            # Round-trip : entrée + sortie
            return round(amount * lot * 2, 2)
        elif comm_type == "pct_notional":
            notional = lot * get_contract_size(symbol) * price
            return round(notional * amount * 2, 2)
        else:
            return 0.0

    # ─── Swap ──────────────────────────────────────────────────────────────

    def get_swap(self, symbol: str, direction: int, entry_time: datetime, exit_time: datetime) -> float:
        """
        Calcule le swap total pour la période de détention.

        Args:
            direction: 0 = BUY, 1 = SELL
            entry_time: datetime d'entrée
            exit_time: datetime de sortie

        Retourne:
            Swap total en USD (négatif = coût, positif = gain).
        """
        if symbol not in self._swap_rates:
            return 0.0

        swap_long, swap_short = self._swap_rates[symbol]
        swap_rate = swap_long if direction == 0 else swap_short
        if swap_rate == 0.0:
            return 0.0

        # Compter les nuits de détention
        days_held = 0
        current = entry_time
        while current < exit_time:
            next_day = current.replace(hour=23, minute=59, second=59)
            if next_day > exit_time:
                next_day = exit_time
            # Le rollover s'applique si on passe 21h UTC
            if current.time() < ROLLOVER_TIME and next_day.time() >= ROLLOVER_TIME:
                swap_mult = 1.0
                # Mercredi = triple swap
                if current.weekday() == 2:  # Wednesday
                    swap_mult = 3.0
                days_held += swap_mult
            current += __import__("datetime").timedelta(days=1)
            current = current.replace(hour=0, minute=0, second=0)

        if days_held == 0:
            return 0.0

        # Swap en pips → USD
        pip_value = get_pip_info(symbol)[1]
        # swap_rate est en pips par lot par nuit
        swap_usd = swap_rate * pip_value * days_held
        return round(swap_usd, 2)

    def _load_swap_rates(self, path: str):
        """Charge les taux swap depuis un fichier YAML."""
        p = Path(path)
        if not p.exists():
            logger.warning(f"Fichier swap {path} introuvable, utilisation des défauts")
            return
        try:
            with open(p) as f:
                data = yaml.safe_load(f)
            if data and "swap_rates" in data:
                for sym, rates in data["swap_rates"].items():
                    self._swap_rates[sym] = (rates.get("long", 0), rates.get("short", 0))
                logger.info(f"Swap rates chargés depuis {path} ({len(data['swap_rates'])} symboles)")
        except Exception as e:
            logger.warning(f"Erreur chargement swap {path}: {e}")

    # ─── Slippage ──────────────────────────────────────────────────────────

    def get_slippage(self, symbol: str, price: float, volatility: str = "normal", is_news: bool = False) -> float:
        """
        Calcule le slippage stochastique en pips (ou % pour crypto).

        Régimes :
          - normal    : N(0.3, 0.2) pips
          - high_vol  : N(2.0, 1.0) pips
          - news      : N(10.0, 5.0) pips
          - crypto    : N(0.07%, 0.03%) du prix

        Args:
            symbol: Le symbole tradé
            price: Prix d'entrée/sortie
            volatility: "normal" | "high" | "low"
            is_news: S'applique-t-il pendant une news ?

        Returns:
            Slippage en USD (toujours positif = coût additionnel)
        """
        # Déterminer le régime de slippage
        if is_news:
            regime = "news"
        elif SYMBOL_CATEGORY.get(symbol) == "crypto":
            regime = "crypto"
        elif volatility == "high":
            regime = "high_vol"
        else:
            regime = "normal"

        cfg = SLIPPAGE_CONFIG.get(regime, SLIPPAGE_CONFIG["normal"])

        mean = cfg["mean"]
        std = cfg["std"]
        unit = cfg.get("unit", "pips")

        # Tirage aléatoire (valeur absolue, slippage est toujours un coût)
        slippage_val = abs(float(np.random.normal(mean, std)))

        if slippage_val < 0:
            slippage_val = 0.0

        if unit == "pct":
            # En fraction du prix
            return round(slippage_val * price, 2)
        else:
            # En pips → USD
            pip_value = get_pip_info(symbol)[1]
            return round(slippage_val * pip_value, 2)

    # ─── Total cost ────────────────────────────────────────────────────────

    def get_total_cost(
        self,
        symbol: str,
        direction: int,
        lot: float,
        entry_price: float,
        exit_price: float,
        entry_time: datetime,
        exit_time: datetime,
        volatility: str = "normal",
        is_news: bool = False,
        historical_spread: Optional[float] = None,
    ) -> dict:
        """
        Calcule le coût total d'un trade en USD.

        Tous les montants retournés sont en USD, corrigés par la taille du lot.

        Returns:
            dict avec clés : spread_cost, commission, swap, slippage_entry,
                             slippage_exit, total (tous en USD)
        """
        pip_size, pip_value = get_pip_info(symbol)

        # Spread : retourné en pips → conversion en USD
        spread_pips = self.get_spread(symbol, entry_time, historical_spread, is_news, volatility)
        spread_cost = round(spread_pips * pip_value * lot, 2)

        # Commission : déjà en USD, lot déjà appliqué dans get_commission
        commission = self.get_commission(symbol, lot, entry_price)

        # Swap : retourné en USD (via pip_value × days_held)
        swap = self.get_swap(symbol, direction, entry_time, exit_time)

        # Slippage : doit être corrigé par le lot
        slippage_entry = self.get_slippage(symbol, entry_price, volatility, is_news)
        slippage_exit = self.get_slippage(symbol, exit_price, volatility, is_news)

        # Ajuster le slippage par le lot (get_slippage retourne pour 1 lot)
        # Note : pour le mode 'pct', get_slippage retourne % du prix × price → on scaling par lot × contract_size
        category = SYMBOL_CATEGORY.get(symbol, "forex")
        if category == "crypto":
            # Pour crypto, slippage_val est en % du prix : appliquer au notionnel complet
            contract_size = get_contract_size(symbol)
            slippage_entry = round(slippage_entry * lot * contract_size, 2) if lot > 0 else 0.0
            slippage_exit = round(slippage_exit * lot * contract_size, 2) if lot > 0 else 0.0
        else:
            # Pour les autres (pip mode) : get_slippage retourne pips × pip_value (pour 1 lot)
            slippage_entry = round(slippage_entry * lot, 2) if lot > 0 else 0.0
            slippage_exit = round(slippage_exit * lot, 2) if lot > 0 else 0.0

        total = round(spread_cost + commission + swap + slippage_entry + slippage_exit, 2)

        return {
            "spread_cost": spread_cost,
            "commission": commission,
            "swap": swap,
            "slippage_entry": slippage_entry,
            "slippage_exit": slippage_exit,
            "total": total,
        }

    def to_dict(self) -> dict:
        """Export de la configuration pour le rapport."""
        return {
            "spread": self._spread_config,
            "commission": self._commission_config,
            "slippage": self._slippage_config,
            "swap": {"n_symbols": len(self._swap_rates)},
        }
