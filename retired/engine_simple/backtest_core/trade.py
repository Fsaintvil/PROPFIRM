"""
SimTrade — Simulation d'une position de trading avec gestion complète.

Supporte :
  - SL/TP basés sur ATR (multiples par régime)
  - Trailing stop à 4 niveaux (peak-based, régime-dépendant)
  - Partial TP (ferme 50% à 60% du TP, set BE à 0.8×ATR)
  - Time-stop (sortie après N barres)
  - Gap handling (SL sauté le weekend)
  - Tracking du PnL clean + coûts (via CostModel)

Usage :
    trade = SimTrade(symbol="EURUSD", action="BUY", entry=1.1050,
                     sl=1.1000, tp=1.1150, atr_val=0.0025,
                     regime="RANGING", bar_idx=100, bar_time=dt,
                     entry_cost={"spread": 1.5, ...})
    trade.check_sl_tp(high=1.1120, low=1.1040, close=1.1100, ...)
    trade.update_trailing(atr_val=0.0025)
    trade.check_partial_tp(atr_val=0.0025)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("backtest_core.trade")

# ─── Types de résultat ────────────────────────────────────────────────────

SL = "SL"
TP = "TP"
TIMEOUT = "TIMEOUT"
STOP = "STOP"  # Arrêt manuel (FTMO stop, circuit breaker)

# ─── Niveaux de trailing par régime (défaut) ──────────────────────────────
# Format : [(profit_atr_threshold, trailing_distance_atr), ...]
# Utilisé comme fallback quand SimTrade est créé sans trailing_levels explicite.
# Les valeurs en production sont définies dans BacktestConfig / backtest.yaml.

DEFAULT_TRAILING_LEVELS: dict[str, list[tuple[float, float]]] = {
    "RANGING": [(0.5, 0.25), (1.0, 0.15), (2.0, 0.10), (4.0, 0.05)],
    "TREND_UP": [(0.5, 0.30), (1.0, 0.20), (2.0, 0.12), (4.0, 0.06)],
    "TREND_DOWN": [(0.5, 0.30), (1.0, 0.20), (2.0, 0.12), (4.0, 0.06)],
    "HIGH_VOL": [(0.6, 0.40), (1.2, 0.28), (2.5, 0.18), (5.0, 0.10)],
    "LOW_VOL": [(0.4, 0.18), (0.8, 0.10), (1.5, 0.06), (3.0, 0.03)],
}

# Buffer BE après partial TP (défaut)
DEFAULT_BE_BUFFER_ATR = 0.35  # 0.35×ATR au-dessus du prix d'entrée

# Alias rétrocompatibles (dépréciés — utiliser DEFAULT_TRAILING_LEVELS)
TRAILING_LEVELS: dict[str, list[tuple[float, float]]] = DEFAULT_TRAILING_LEVELS
BE_BUFFER_ATR: float = DEFAULT_BE_BUFFER_ATR


# ═══════════════════════════════════════════════════════════════════════════
# SimTrade
# ═══════════════════════════════════════════════════════════════════════════


class SimTrade:
    """Simule un trade individuel avec gestion complète des risques."""

    __slots__ = (
        "symbol",
        "timeframe",
        "action",
        "direction",
        "entry",
        "sl",
        "tp",
        "atr_val",
        "regime",
        "open_bar",
        "open_time",
        "close_bar",
        "close_time",
        "close_price",
        "lot",
        "partial_lot",
        "result",
        "closed",
        "profit_usd",
        "profit_pct",
        "profit_usd_cost",
        "profit_pct_cost",
        "costs",
        "peak_price",
        "trailing_sl",
        "trailing_tp",
        "partial_closed",
        "bars_held",
        "entry_cost",
        "exit_cost",
        "trailing_levels",
        "be_buffer_atr",
    )

    def __init__(
        self,
        symbol: str,
        action: str,
        entry: float,
        sl: float,
        tp: float,
        atr_val: float,
        regime: str,
        bar_idx: int,
        bar_time: datetime,
        lot: float = 0.0,
        timeframe: str = "H1",
        entry_cost: Optional[dict] = None,
        trailing_levels: Optional[dict] = None,
        be_buffer_atr: Optional[float] = None,
    ):
        """
        Args:
            symbol: Symbole tradé
            action: "BUY" ou "SELL"
            entry: Prix d'entrée
            sl: Prix du Stop Loss initial
            tp: Prix du Take Profit initial
            atr_val: Valeur ATR au moment de l'entrée
            regime: Régime de marché ("RANGING", "TREND_UP", etc.)
            bar_idx: Index de la barre d'entrée
            bar_time: Timestamp de l'entrée
            lot: Taille du lot (0.0 = calculée automatiquement)
            timeframe: TF de la barre d'entrée
            entry_cost: Coûts d'entrée (depuis CostModel)
            trailing_levels: Niveaux de trailing par régime (défaut=module)
            be_buffer_atr: Buffer BE après partial TP (défaut=module)
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.action = action
        self.direction = 0 if action == "BUY" else 1  # 0=BUY, 1=SELL

        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.atr_val = atr_val
        self.regime = regime

        self.open_bar = bar_idx
        self.open_time = bar_time
        self.close_bar = 0
        self.close_time = bar_time
        self.close_price = entry

        self.lot = lot
        self.partial_lot = lot * 0.5  # 50% fermé au partial TP

        self.result: Optional[str] = None
        self.closed = False

        # PnL (sera calculé à la fermeture)
        self.profit_usd = 0.0
        self.profit_pct = 0.0
        self.profit_usd_cost = 0.0  # Après coûts
        self.profit_pct_cost = 0.0

        # Coûts totaux (cumulés)
        self.costs: dict = {
            "spread_cost": 0.0,
            "commission": 0.0,
            "swap": 0.0,
            "slippage_entry": 0.0,
            "slippage_exit": 0.0,
            "total": 0.0,
        }

        # Trailing
        self.peak_price = entry
        self.trailing_sl = sl
        self.trailing_tp = tp

        # Configuration trailing (depuis BacktestConfig ou défaut module)
        self.trailing_levels: dict = trailing_levels or DEFAULT_TRAILING_LEVELS
        self.be_buffer_atr: float = be_buffer_atr if be_buffer_atr is not None else DEFAULT_BE_BUFFER_ATR

        # Partial TP
        self.partial_closed = False

        self.bars_held = 0

        # Coûts d'entrée/sortie
        self.entry_cost: dict = entry_cost or {}
        self.exit_cost: Optional[dict] = None

        # Appliquer coûts d'entrée
        self._apply_entry_costs()

    # ─── Propriétés ───────────────────────────────────────────────────────

    @property
    def is_buy(self) -> bool:
        return self.direction == 0

    @property
    def is_sell(self) -> bool:
        return self.direction == 1

    @property
    def gross_pnl(self) -> float:
        """PnL brut (sans coûts)."""
        return self.profit_usd

    @property
    def net_pnl(self) -> float:
        """PnL net (après coûts)."""
        return self.profit_usd_cost

    @property
    def total_cost(self) -> float:
        return self.costs.get("total", 0.0)

    # ─── Application des coûts ────────────────────────────────────────────

    def _apply_entry_costs(self):
        """Ajoute les coûts d'entrée (spread + commission entrée + slippage entrée)."""
        if not self.entry_cost:
            return
        for key in ("spread_cost", "commission", "slippage_entry"):
            val = self.entry_cost.get(key, 0.0)
            # Le spread et le slippage réduisent le PnL
            if key == "spread_cost":
                self.costs["spread_cost"] += val
            elif key == "commission":
                self.costs["commission"] += val * 0.5  # Moitié à l'entrée
            elif key == "slippage_entry":
                self.costs["slippage_entry"] += val
        self._update_total_cost()

    def apply_exit_costs(self, exit_cost: dict):
        """Ajoute les coûts de sortie (commission sortie + slippage sortie + swap)."""
        self.exit_cost = exit_cost
        if not exit_cost:
            return
        for key in ("commission", "slippage_exit", "swap"):
            val = exit_cost.get(key, 0.0)
            if key == "commission":
                self.costs["commission"] += val * 0.5  # Moitié à la sortie
            elif key == "slippage_exit":
                self.costs["slippage_exit"] += val
            elif key == "swap":
                self.costs["swap"] += val
        self._update_total_cost()

    def _update_total_cost(self):
        self.costs["total"] = round(sum(self.costs.values()), 2)

    # ─── Calcul PnL ──────────────────────────────────────────────────────

    def _calc_pnl(self):
        """Calcule le PnL brut et net à la fermeture."""
        pip_size, pip_value = self._get_pip_info()
        usd_per_pip = self.lot * pip_value

        # Pips gagnés/perdus
        if self.is_buy:
            pips = (self.close_price - self.entry) / pip_size
        else:
            pips = (self.entry - self.close_price) / pip_size

        # PnL brut
        self.profit_usd = round(pips * usd_per_pip, 2)
        self.profit_pct = round(pips * pip_size / self.entry * 100, 4) if self.entry > 0 else 0.0

        # PnL net (après coûts)
        self.profit_usd_cost = round(self.profit_usd - self.costs["total"], 2)
        entry_notional = self.lot * self._get_contract_size() * self.entry
        self.profit_pct_cost = round(self.profit_usd_cost / entry_notional * 100, 4) if entry_notional > 0 else 0.0

    def get_floating_pnl(self, current_price: float) -> float:
        """Calcule le PnL flottant à un prix donné (pour equity curve)."""
        from engine_simple.backtest_core.costs import get_pip_info

        pip_size, pip_value = get_pip_info(self.symbol)
        usd_per_pip = self.lot * pip_value
        if self.is_buy:
            pips = (current_price - self.entry) / pip_size if pip_size > 0 else 0
        else:
            pips = (self.entry - current_price) / pip_size if pip_size > 0 else 0
        return round(pips * usd_per_pip, 2)

    def _get_pip_info(self):
        """Retourne (pip_size, pip_value) pour le symbole."""
        from engine_simple.backtest_core.costs import get_pip_info

        return get_pip_info(self.symbol)

    def _get_contract_size(self):
        from engine_simple.backtest_core.costs import get_contract_size

        return get_contract_size(self.symbol)

    # ─── Vérification SL/TP ──────────────────────────────────────────────

    def check_sl_tp(
        self, high: float, low: float, close: float, bar_idx: int, bar_time: datetime, gap_open: Optional[float] = None
    ):
        """
        Vérifie si le SL ou le TP a été touché sur cette barre.

        Args:
            high: Plus haut de la barre
            low: Plus bas de la barre
            close: Clôture de la barre
            bar_idx: Index de la barre
            bar_time: Timestamp de la barre
            gap_open: Prix d'ouverture après gap (weekend/news)
        """
        if self.closed:
            return

        # Gap handling : si gap_open traverse SL/TP
        if gap_open is not None:
            self._check_gap(gap_open, bar_idx, bar_time)
            if self.closed:
                return

        if self.is_buy:
            # BUY : SL en dessous, TP au-dessus
            if low <= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = SL
                self.closed = True
            elif high >= self.trailing_tp:
                self.close_price = self.trailing_tp
                self.result = TP
                self.closed = True
        else:
            # SELL : SL au-dessus, TP en dessous
            if high >= self.trailing_sl:
                self.close_price = self.trailing_sl
                self.result = SL
                self.closed = True
            elif low <= self.trailing_tp:
                self.close_price = self.trailing_tp
                self.result = TP
                self.closed = True

        if self.closed:
            self.close_bar = bar_idx
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            self._calc_pnl()
            logger.debug(
                f"  [TRADE] {self.symbol} {self.action} {self.result} "
                f"à {self.close_price:.5f} | PnL: ${self.profit_usd:.2f}"
            )

    def _check_gap(self, gap_open: float, bar_idx: int, bar_time: datetime):
        """Vérifie si le gap d'ouverture traverse le SL ou TP."""
        if self.is_buy:
            # Gap baissier : ouverture < SL → SL sauté au gap_open
            if gap_open < self.trailing_sl:
                self.close_price = gap_open
                self.result = SL
                self.closed = True
                logger.warning(
                    f"  [GAP] {self.symbol} {self.action} gap baissier {self.entry:.5f}→{gap_open:.5f}, SL sauté"
                )
            # Gap haussier : ouverture > TP → TP sauté au gap_open
            elif gap_open > self.trailing_tp:
                self.close_price = gap_open
                self.result = TP
                self.closed = True
        else:
            # Gap haussier : ouverture > SL → SL sauté
            if gap_open > self.trailing_sl:
                self.close_price = gap_open
                self.result = SL
                self.closed = True
            # Gap baissier : ouverture < TP → TP sauté
            elif gap_open < self.trailing_tp:
                self.close_price = gap_open
                self.result = TP
                self.closed = True

        if self.closed:
            self.close_bar = bar_idx
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            self._calc_pnl()

    # ─── Time-stop ────────────────────────────────────────────────────────

    def check_timeout(self, current_bar: int, current_time: datetime, max_bars: int = 120):
        """Ferme la position si elle dure trop longtemps."""
        if self.closed:
            return
        if (current_bar - self.open_bar) >= max_bars:
            self.closed = True
            self.close_bar = current_bar
            self.close_time = current_time
            self.result = TIMEOUT
            self.bars_held = current_bar - self.open_bar
            # Timeout : on ferme au close de la bougie
            self._calc_pnl()
            logger.debug(f"  [TIMEOUT] {self.symbol} {self.action} fermé après {self.bars_held} barres")

    # ─── Mise à jour du peak ─────────────────────────────────────────────

    def update_peak(self, high: float, low: float):
        """Met à jour le prix peak (plus haut pour BUY, plus bas pour SELL)."""
        if self.closed:
            return
        if self.is_buy:
            if high > self.peak_price:
                self.peak_price = high
        else:
            if low < self.peak_price:
                self.peak_price = low

    # ─── Trailing stop ───────────────────────────────────────────────────

    def update_trailing(self, atr_v: float):
        """
        Met à jour le trailing stop selon les 4 niveaux ATR.

        Trailing activé seulement quand le profit dépasse le premier seuil.
        Les distances sont définies par self.trailing_levels[regime].
        """
        if self.closed or atr_v <= 0:
            return

        # Profit en multiple d'ATR
        if self.is_buy:
            profit_atr = (self.peak_price - self.entry) / atr_v
        else:
            profit_atr = (self.entry - self.peak_price) / atr_v

        # Récupérer les niveaux pour ce régime
        levels = self.trailing_levels.get(
            self.regime,
            self.trailing_levels.get("RANGING", DEFAULT_TRAILING_LEVELS["RANGING"]),
        )

        # 🔴 FIX BUG : Si profit < premier seuil, ne pas traîler du tout
        first_thresh = levels[0][0]
        if profit_atr <= first_thresh:
            return

        # Trouver le niveau de trailing approprié (du plus haut au plus bas)
        trail_dist = levels[-1][1]  # Dernier niveau (le plus serré) par défaut
        for thresh, dist in reversed(levels):
            if profit_atr > thresh:
                trail_dist = dist
                break

        dist = trail_dist * atr_v

        if self.is_buy:
            new_sl = self.peak_price - dist
            if new_sl > self.trailing_sl:
                self.trailing_sl = new_sl
        else:
            new_sl = self.peak_price + dist
            if new_sl < self.trailing_sl:
                self.trailing_sl = new_sl

    # ─── Partial TP ──────────────────────────────────────────────────────

    def check_partial_tp(self, atr_v: float):
        """
        Vérifie si le trade a atteint 60% du TP pour déclencher le partial TP.
        Si oui, ferme 50% de la position et rapproche le SL au BE buffer.
        """
        if self.closed or self.partial_closed or atr_v <= 0:
            return

        # Progression vers le TP
        if self.is_buy:
            progress = (self.peak_price - self.entry) / max(self.tp - self.entry, 1e-10)
        else:
            progress = (self.entry - self.peak_price) / max(self.entry - self.tp, 1e-10)

        if progress < 0.60:
            return

        # Déclencher le partial TP
        self.partial_closed = True

        # Rapprocher le SL au BE + buffer (utilise self.be_buffer_atr)
        be_buffer = self.be_buffer_atr * atr_v
        if self.is_buy:
            be_sl = self.entry + be_buffer
            if be_sl > self.trailing_sl:
                self.trailing_sl = be_sl
                logger.debug(f"  [PARTIAL] {self.symbol} {self.action} SL→BE+{be_buffer:.5f} ({self.trailing_sl:.5f})")
        else:
            be_sl = self.entry - be_buffer
            if be_sl < self.trailing_sl:
                self.trailing_sl = be_sl

    # ─── Arrêt manuel ─────────────────────────────────────────────────────

    def force_close(self, close_price: float, bar_idx: int, bar_time: datetime, reason: str = STOP):
        """Ferme manuellement la position."""
        if self.closed:
            return
        self.closed = True
        self.close_price = close_price
        self.close_bar = bar_idx
        self.close_time = bar_time
        self.result = reason
        self.bars_held = bar_idx - self.open_bar
        self._calc_pnl()

    # ─── Export ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Exporte le trade en dictionnaire pour analyse/sérialisation."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "action": self.action,
            "regime": self.regime,
            "entry": round(self.entry, 5),
            "sl": round(self.sl, 5),
            "tp": round(self.tp, 5),
            "close_price": round(self.close_price, 5),
            "lot": round(self.lot, 4),
            "result": self.result,
            "closed": self.closed,
            "partial_closed": self.partial_closed,
            # PnL
            "profit_usd": round(self.profit_usd, 2),
            "profit_pct": round(self.profit_pct, 4),
            "profit_usd_cost": round(self.profit_usd_cost, 2),
            "profit_pct_cost": round(self.profit_pct_cost, 4),
            # Coûts
            "spread_cost": self.costs.get("spread_cost", 0.0),
            "commission": self.costs.get("commission", 0.0),
            "swap": self.costs.get("swap", 0.0),
            "slippage_entry": self.costs.get("slippage_entry", 0.0),
            "slippage_exit": self.costs.get("slippage_exit", 0.0),
            "total_cost": self.costs.get("total", 0.0),
            # Timing
            "open_bar": self.open_bar,
            "close_bar": self.close_bar,
            "bars_held": self.bars_held,
            "open_time": str(self.open_time) if self.open_time else "",
            "close_time": str(self.close_time) if self.close_time else "",
        }
