"""Position Manager — extraction de la gestion des positions depuis main.py

Responsabilités:
- _manage_positions : surveillance MT5 (time-stop, trailing, partial TP)
- _vigilance_scan : analyse parallèle de tous les symboles (régime, DL, structure)
"""

import logging
import time
from datetime import datetime

import numpy as np

import config_simple as cfg

logger = logging.getLogger("position.mgr")


class PositionManager:
    """Gère la surveillance des positions ouvertes + scan de vigilance.

    Délégation de main.py : appelé cycle par cycle.
    """

    def __init__(self, mt5, ftmo, adaptive, signal_gen, regime_detector, pos_cache):
        self.mt5 = mt5
        self.ftmo = ftmo
        self.adaptive = adaptive
        self.signals = signal_gen
        self._regime_detector = regime_detector
        self._pos_cache = pos_cache
        # Cache TTL pour les rates de vigilance (partagé entre symboles)
        self._vigilance_rate_cache = {}

    # ── Position management ─────────────────────────────────────────

    def manage_positions(self):
        """Surveille les positions ouvertes (time-stop, trailing, partial TP)."""
        our = [p for p in self._pos_cache.get() if p.magic == cfg.ROBOT_MAGIC]
        self.ftmo._reconcile_positions(our)
        pos_summary = []
        total_fl = 0.0
        for pos in our:
            self.ftmo.check_invariants(pos)
            total_fl += pos.profit
            pos_summary.append(f"{pos.symbol}={pos.profit:+.0f}")
        if pos_summary:
            logger.info(f"  Positions: {' | '.join(pos_summary)}  →  Total: {total_fl:+.2f}")

    # ── Vigilance scan ──────────────────────────────────────────────

    def vigilance_scan(self):
        """DL/regime pipeline pour TOUS les symboles chaque cycle."""
        positions = {p.symbol: p for p in self._pos_cache.get() if p.magic == cfg.ROBOT_MAGIC}
        for symbol in cfg.SYMBOLS:
            try:
                rates = self._get_rates_for_vigilance(symbol)
                if rates is None:
                    continue
                result = self.adaptive.vigilance(symbol, rates)
                if result is None:
                    continue
                # RegimeDetector: comparaison parallèle
                h1 = rates.get("H1")
                if h1 is not None and len(h1) >= 30:
                    hh = np.array([r[2] for r in h1], dtype=float)
                    ll = np.array([r[3] for r in h1], dtype=float)
                    cc = np.array([r[4] for r in h1], dtype=float)
                    new_regime, new_meta = self._regime_detector.detect(hh, ll, cc)
                    if new_regime != result.get("regime", ""):
                        logger.debug(
                            f"  [REGIME COMPARE] {symbol}: "
                            f"old={result.get('regime', '?')} new={new_regime} "
                            f"(adx={new_meta.get('adx', 0):.0f})"
                        )
                # MOM20x3: signal parallèle (analyse seule, pas de décision)
                from engine_simple.strategy import MOM20x3

                h1_rates = rates.get("H1", []) if isinstance(rates, dict) else rates
                mom = MOM20x3(h1_rates, symbol)
                mom_signal = mom.analyze()
                if mom_signal and mom_signal.get("action") not in (None, "HOLD"):
                    regime_meta = result.get("regime_meta", {})
                    old_action = result.get("dl_action", "HOLD")
                    new_action = mom_signal["action"]
                    if new_action != old_action and old_action is not None:
                        logger.debug(
                            f"  [STRAT COMPARE] {symbol}: "
                            f"DL={old_action} MOM20x3={new_action} "
                            f"score={mom_signal.get('score', 0):.2f} "
                            f"ADX={regime_meta.get('adx', 0):.0f}"
                        )
                # Position existante → comparer le régime d'entrée
                pos = positions.get(symbol)
                if pos:
                    comment = pos.comment or ""
                    entry_regime = comment.replace("ADAPT_", "")[:12] if comment.startswith("ADAPT_") else "?"
                    if entry_regime not in ("?", "LIMIT") and result["regime"] not in ("?", "LIMIT"):
                        if result["regime"] != entry_regime:
                            logger.info(
                                f"  [REGIME SHIFT] {symbol}: {entry_regime} → {result['regime']} (position ouverte)"
                            )
                    if pos.sl > 0:
                        dist = abs(pos.price_open - pos.sl)
                        logger.debug(f"  [POS] {symbol}: SL={pos.sl:.5f} dist={dist:.5f} profit={pos.profit:+.2f}")
            except Exception as e:
                logger.warning(f"  [VIGIL] {symbol}: error: {e}")

    def _get_rates_for_vigilance(self, symbol):
        """Rates mises en cache pour le scan de vigilance (TTL 60s)."""
        now = time.time()
        cached = self._vigilance_rate_cache.get(symbol)
        if cached and now - cached["time"] < 60:
            return cached["rates"]
        rates = self.mt5.get_rates_multi_tf(symbol, ["H1", "M15", "M5"], count=100)
        if not rates:
            return None
        self._vigilance_rate_cache[symbol] = {"rates": rates, "time": now}
        return rates
