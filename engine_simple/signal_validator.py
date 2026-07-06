"""SignalValidator — validation des signaux avant exécution.

Extrait de FTMOProtector._check_signal_valid pour réduire la taille
de ftmo_protector.py (~190 lignes → module dédié).

Responsabilités:
- Direction restrictions (allow_shorts, allow_buys)
- Dynamic min_score basé sur le WR réel
- SL/TP calculation automatique si manquant
- Order block SL adjustment
- RR check par symbole
- Price staleness
"""

import logging
from typing import Any, Optional

from engine_simple.portfolio_controller import (
    MAX_TRADES_PER_DIRECTION_IN_GROUP,
    MAX_TRADES_PER_GROUP,
    POSITION_GROUPS,
)
from engine_simple.symbol_params import get_symbol_params, update_dyn_score

logger = logging.getLogger("ftmo.signal_validator")


class SignalValidator:
    """Valide un signal selon les règles de risque et de configuration.

    Usage:
        validator = SignalValidator(mt5, trailer, symbol_limits, symbol_trade_history)
        ok, reason = validator.check(symbol, signal, positions)
    """

    def __init__(
        self,
        mt5: Any,
        trailer: Any,
        symbol_limits: dict[str, Any],
        symbol_trade_history: dict[str, list[dict]],
        staleness_check_fn,
    ) -> None:
        self.mt5 = mt5
        self.trailer = trailer
        self.symbol_limits = symbol_limits
        self._symbol_trade_history = symbol_trade_history
        self._check_price_staleness = staleness_check_fn

    def check(self, symbol: str, signal: Optional[dict], positions: list) -> tuple[bool, Optional[str]]:
        """Valide un signal. Retourne (valid, reason).

        Returns:
            (True, None) si le signal est valide
            (False, "raison") si le signal est rejeté
        """
        if signal is None:
            return True, None

        # ── 1. Direction restrictions ──────────────────────────────────
        sym_cfg = self.symbol_limits.get(symbol, {})
        if not sym_cfg.get("allow_shorts", True) and signal.get("action") == "SELL":
            return False, f"Shorts not allowed on {symbol} (per-symbol config)"
        if not sym_cfg.get("allow_buys", True) and signal.get("action") == "BUY":
            return False, f"Buys not allowed on {symbol} (per-symbol config)"

        # ── 1b. Corrélation multi-symboles ─────────────────────────────
        sym_group = self._get_group_for_symbol(symbol)
        if sym_group and positions:
            group_positions = [p for p in positions if self._get_group_for_symbol(p.symbol) == sym_group]
            if len(group_positions) >= MAX_TRADES_PER_GROUP:
                return False, (
                    f"Groupe {sym_group}: déjà {len(group_positions)} positions "
                    f"(max {MAX_TRADES_PER_GROUP}) — corrélation bloquée"
                )
            direction = signal.get("action", "BUY")
            group_dir_positions = [
                p
                for p in group_positions
                if (direction == "BUY" and getattr(p, "type", None) in (0, "BUY"))
                or (direction == "SELL" and getattr(p, "type", None) in (1, "SELL"))
            ]
            if len(group_dir_positions) >= MAX_TRADES_PER_DIRECTION_IN_GROUP:
                return False, (
                    f"Groupe {sym_group}: déjà {len(group_dir_positions)} positions {direction} "
                    f"(max {MAX_TRADES_PER_DIRECTION_IN_GROUP}) — corrélation bloquée"
                )

        # ── 2. Signal quality gate (dynamic min_score) ─────────────────
        sym_params = get_symbol_params(symbol)
        cfg_score = sym_params.get("cfg_score", 0.60)

        # Dynamic min_score basé sur WR réel (50 derniers trades)
        sym_trades = self._symbol_trade_history.get(symbol, [])
        dyn_score: Optional[float] = None
        if len(sym_trades) >= 15:
            wins = sum(1 for t in sym_trades if t.get("profit", 0) > 0)
            wr = wins / len(sym_trades)
            if wr < 0.50:
                dyn_score = max(cfg_score, 0.60)
                if dyn_score != cfg_score:
                    logger.info(
                        f"  [DYNAMIC SCORE] {symbol}: WR={wr:.0f}% ({wins}/{len(sym_trades)}) "
                        f"→ min_score {cfg_score:.2f} → {dyn_score:.2f}"
                    )

        if dyn_score is not None:
            update_dyn_score(symbol, dyn_score)

        effective_min_score = max(cfg_score, dyn_score or 0)
        sig_score = signal.get("score", 0)

        # MeanReversion adjustment: les signaux MR ont un score bas (0.60) par conception
        if signal.get("_strategy") == "MR":
            effective_min_score = min(effective_min_score, 0.55)

        # Tolérance floating point 0.001 pour éviter les faux rejets
        if sig_score < effective_min_score - 0.001:
            return (
                False,
                f"Signal score too low: {sig_score:.4f} < {effective_min_score} "
                f"(cfg={cfg_score}, dyn={dyn_score or 'N/A'})",
            )

        # ── 3. SL/TP obligatoire ──────────────────────────────────────
        sl = signal.get("sl")
        tp = signal.get("tp")
        entry = signal.get("entry_price")
        action = signal.get("action")

        if sl is None or tp is None or sl == 0 or tp == 0:
            try:
                atr = signal.get("atr")
                sl_atr = signal.get("sl_atr", 2.0)
                tp_atr = signal.get("tp_atr", 4.0)
                if entry is None or entry == 0:
                    tick = self.mt5.get_tick(symbol)
                    if tick:
                        entry = tick.ask if action == "BUY" else tick.bid
                if entry and entry > 0 and action:
                    direction = 0 if action == "BUY" else 1
                    logger.debug(
                        f"  [SL_CALC] {symbol}: entry={entry:.2f} atr={atr:.4f} "
                        f"sl_atr={sl_atr:.2f} tp_atr={tp_atr:.2f} dir={direction}"
                    )
                    new_sl, new_tp = self.trailer.calc_sl_tp(symbol, entry, direction, atr, sl_atr, tp_atr)
                    if new_sl is not None and new_tp is not None and new_sl > 0 and new_tp > 0:
                        signal["sl"] = new_sl
                        signal["tp"] = new_tp
                        sl, tp = new_sl, new_tp
            except Exception as exc:
                logger.debug(f"  [SL CALC] {symbol}: echec calcul SL={sl} TP={tp}: {exc}")

            if sl is None or tp is None or sl == 0 or tp == 0:
                return False, f"SL/TP manquant — transaction BLOQUÉE (SL={sl}, TP={tp})"

        # Vérification SL != entry price
        entry_price = signal.get("entry_price", 0)
        if entry_price and sl and abs(float(sl) - float(entry_price)) / max(abs(float(entry_price)), 1) < 0.0001:
            return False, f"SL identique au prix d'entrée ({sl} ≈ {entry_price}) — PAS DE PROTECTION, BLOQUÉ"

        # ── 4. Order block SL adjustment ──────────────────────────────
        obs = signal.get("_structure_obs", [])
        current_atr = signal.get("atr", 0)
        max_sl_atr = 3.0

        # S'assurer que entry et action sont définis
        try:
            _ = entry
        except (NameError, UnboundLocalError):
            entry = signal.get("entry_price", 0) if signal else 0
        try:
            _ = action
        except (NameError, UnboundLocalError):
            action = signal.get("action", "") if signal else ""

        if obs and sl and entry:
            for ob in obs:
                if not ob.get("is_mitigated"):
                    self._adjust_sl_for_ob(symbol, ob, action, sl, entry, current_atr, max_sl_atr, signal)

        # ── 5. RR check per symbol ────────────────────────────────────
        rr_min_sym = sym_params.get("min_rr", 1.5)
        rr_entry = entry_price or signal.get("entry_price", 0)
        if sl and tp and rr_entry and sl != rr_entry:
            rr_actual = abs(float(tp) - float(rr_entry)) / abs(float(sl) - float(rr_entry))
            if rr_actual < rr_min_sym - 0.01:
                return False, (
                    f"RR {rr_actual:.2f} < min_rr {rr_min_sym} pour {symbol} "
                    f"(SL={sl:.5f}, TP={tp:.5f}, entry={rr_entry:.5f})"
                )

        # ── 6. Price staleness ────────────────────────────────────────
        if not self._check_price_staleness(symbol):
            return False, "Stale price: tick > 60s"

        return True, None

    @staticmethod
    def _get_group_for_symbol(symbol: str) -> str | None:
        """Retourne le groupe de corrélation d'un symbole, ou None."""
        for group_name, symbols in POSITION_GROUPS.items():
            if symbol in symbols:
                return group_name
        return None

    def _adjust_sl_for_ob(self, symbol, ob, action, sl, entry, current_atr, max_sl_atr, signal):
        """Ajuste le SL si un order block non mitigé est proche."""
        if not ob.get("is_mitigated"):
            ob_high = ob.get("high", 0)
            ob_low = ob.get("low", 0)
            ob_type = ob.get("type", "")

            if action == "BUY" and ob_type == "bullish" and ob_low > 0:
                if sl < ob_high and sl > ob_low * 0.99:
                    new_sl = ob_low - (ob_high - ob_low) * 0.1
                    if current_atr > 0 and (entry - new_sl) > current_atr * max_sl_atr:
                        min_sl = entry - current_atr * max_sl_atr
                        logger.debug(f"  [SL OB] {symbol}: SL OB {new_sl:.5f} > {max_sl_atr}×ATR → cap à {min_sl:.5f}")
                        new_sl = min_sl
                    if new_sl > 0:
                        min_sl_dist = current_atr * 0.3 if current_atr > 0 else 0.0005
                        if new_sl > entry - min_sl_dist:
                            new_sl = entry - min_sl_dist
                            logger.debug(f"  [SL OB] {symbol}: SL BUY reculé à {new_sl:.5f} (garde entrée)")
                        logger.debug(f"  [SL OB] {symbol}: SL ajusté {sl:.5f} → {new_sl:.5f} (sous OB haussier)")
                        signal["sl"] = new_sl

            elif action == "SELL" and ob_type == "bearish" and ob_high > 0:
                if sl > ob_low and sl < ob_high * 1.01:
                    new_sl = ob_high + (ob_high - ob_low) * 0.1
                    min_sl_dist = current_atr * 0.3 if current_atr > 0 else 0.0005
                    if new_sl < entry + min_sl_dist:
                        new_sl = entry + min_sl_dist
                        logger.debug(f"  [SL OB] {symbol}: SL SELL relevé à {new_sl:.5f} (garde entrée)")
                    if current_atr > 0 and (new_sl - entry) > current_atr * max_sl_atr:
                        max_sl = entry + current_atr * max_sl_atr
                        logger.debug(f"  [SL OB] {symbol}: SL OB {new_sl:.5f} > {max_sl_atr}×ATR → cap à {max_sl:.5f}")
                        new_sl = max_sl
                    if new_sl > 0:
                        logger.debug(f"  [SL OB] {symbol}: SL ajusté {sl:.5f} → {new_sl:.5f} (dessus OB baissier)")
                        signal["sl"] = new_sl
