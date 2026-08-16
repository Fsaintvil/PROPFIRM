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

        # ── 1c. Garde RÉGIME STRICTE: AUCUN momentum en RANGING (ADX < 20) ──
        # ✅ RÉACTIVÉ + RENFORCÉ 05 Aout 2026 (Robot Manager) — décision utilisateur
        # "Protection + gate de régime". La désactivation du 04 Aout (dégel total)
        # a laissé le robot trader en RANGING — marché du 28/07 au 04/08 — et le
        # résultat est un désastre : WR live 27.1%, -$306. Preuves live :
        # EURUSD 0/8 (ADX 14.7), NZDUSD 1/8 (ADX 16.6), EURGBP 1/9 (ADX 18.5)
        # = 2/25 (8% WR) en RANGING, pendant que USOIL (ADX 35, TREND) et
        # USDJPY (ADX 58, TREND) ne perdent pas. MOM20x3 est un breakout de
        # momentum : en RANGING chaque breakout est un retournement.
        # 🚫 GATE STRICT (v2 05 Aout) : le 1er essai (exception score ≥ 0.85 avec
        # risk_mult ×0.35) laissait encore passer EURUSD (ADX 13.6, score 0.95) et
        # GBPUSD (ADX 13.5, score 0.90) — or même les hauts scores perdent en RANGING
        # (EURUSD 0/8 live). La protection prime : on ATTEND un vrai TREND (ADX > 20).
        _adx = signal.get("adx")
        _regime = signal.get("_regime", "")
        if _adx is not None and float(_adx) < 20.0 and _regime == "RANGING":
            return False, (
                f"Régime RANGING (ADX={float(_adx):.1f} < 20) — momentum MOM20x3 non fiable, "
                f"signal rejeté (garde régime STRICTE 05 Aout 2026)"
            )

        # ── 2. Signal quality gate (dynamic min_score) ─────────────────
        sym_params = get_symbol_params(symbol)
        # 🔧 31 Juillet 2026: Utilise MIN_SIGNAL_SCORE global (0.70) comme baseline.
        # Le per-symbol cfg_score ne peut qu'AUGMENTER ce seuil, jamais le baisser.
        # Empêche les signaux de score < 0.70 de passer.
        from config_simple import MIN_SIGNAL_SCORE

        # 🔧 FIX 31 Juillet 2026: Exception ciblée par symbole au plancher global.
        # SOLUSD: backtest sain (WR 65%) mais scores plafonnent à 0.69 (< 0.70).
        # Le seuil 0.60 est déjà configuré dans strategy.py pour SOLUSD — on
        # autorise ici explicitement ce symbole sous le plancher global 0.70.
        # TOUS les autres symboles gardent le plancher strict de 0.70.
        MIN_SCORE_EXCEPTIONS = {
            "SOLUSD": 0.60,  # débloqué 31 Juillet 2026 — edge validé (WR 65%)
        }
        global_floor = MIN_SCORE_EXCEPTIONS.get(symbol, MIN_SIGNAL_SCORE)
        cfg_score = max(sym_params.get("cfg_score", global_floor), global_floor)

        # Dynamic min_score basé sur WR réel (50 derniers trades)
        sym_trades = self._symbol_trade_history.get(symbol, [])
        dyn_score: Optional[float] = None
        if len(sym_trades) >= 15:
            wins = sum(1 for t in sym_trades if t.get("profit", 0) > 0)
            wr = wins / len(sym_trades)
            if wr < 0.50:
                # 🔧 FIX 14 Juillet 2026: min_score dynamique qui monte quand WR baisse
                # Ancien code: dyn_score = max(cfg_score, 0.60) → toujours = cfg_score → inopérant
                # Nouveau: +0.5 pt par tranche de 10% sous 50% (ex: WR=30% → +0.10 de pénalité)
                dyn_score = min(cfg_score + (0.50 - wr) * 0.5, 0.90)
                if abs(dyn_score - cfg_score) > 0.01:
                    logger.info(
                        f"  [DYNAMIC SCORE] {symbol}: WR={wr:.0f}% ({wins}/{len(sym_trades)}) "
                        f"→ min_score {cfg_score:.2f} → {dyn_score:.2f}"
                    )

        if dyn_score is not None:
            update_dyn_score(symbol, dyn_score)

        effective_min_score = max(cfg_score, dyn_score or 0)
        sig_score = signal.get("score", 0)

        # MeanReversion adjustment: les signaux MR ont un score bas (0.60) par conception
        # ⚠️ MR DÉSACTIVÉ 16 Août 2026 — conservé par compatibilité (aucun signal MR ne sera généré)
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
                # 🔧 FIX 24 Juillet 2026 — W/L ratio 0.87 → viser 1.2-1.5
                # SL réduit 2.0→1.8×ATR pour limiter les pertes
                # TP augmenté 4.0→5.0×ATR pour laisser les gains courir
                sl_atr = signal.get("sl_atr", 1.8)
                tp_atr = signal.get("tp_atr", 5.0)
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
        """Ajuste le SL si un order block non mitigé est proche.

        🔧 FIX 23 Juillet 2026: L'OB ne doit JAMAIS rendre le SL plus serré que la stratégie.
        L'OB peut seulement ÉLARGIR le SL (le placer plus loin de l'entrée) pour éviter
        d'être sweepé. Si l'OB est trop proche de l'entrée, on garde le SL stratégique.
        """
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
                    # 🔧 FIX 23 Juillet 2026: OB ne doit pas serrer le SL
                    # Si new_sl est plus PROCHE de l'entrée que le SL stratégique,
                    # on garde le SL stratégique (OB trop proche → pas d'ajustement)
                    if new_sl > sl:  # BUY: SL plus haut = plus proche de l'entrée
                        logger.debug(f"  [SL OB] {symbol}: OB trop proche de l'entrée → garde SL stratégique {sl:.5f}")
                        new_sl = sl
                    if new_sl > 0:
                        min_sl_dist = current_atr * 0.3 if current_atr > 0 else 0.0005
                        if new_sl > entry - min_sl_dist:
                            new_sl = entry - min_sl_dist
                            logger.debug(f"  [SL OB] {symbol}: SL BUY reculé à {new_sl:.5f} (garde entrée)")
                        if new_sl != sl:
                            logger.debug(f"  [SL OB] {symbol}: SL ajusté {sl:.5f} → {new_sl:.5f} (sous OB haussier)")
                        signal["sl"] = new_sl

            elif action == "SELL" and ob_type == "bearish" and ob_high > 0:
                if sl > ob_low and sl < ob_high * 1.01:
                    new_sl = ob_high + (ob_high - ob_low) * 0.1
                    # 🔧 FIX 23 Juillet 2026: OB ne doit pas serrer le SL
                    # Si new_sl est plus PROCHE de l'entrée que le SL stratégique,
                    # on garde le SL stratégique (OB trop proche → pas d'ajustement)
                    if new_sl < sl:  # SELL: SL plus bas = plus proche de l'entrée
                        logger.debug(f"  [SL OB] {symbol}: OB trop proche de l'entrée → garde SL stratégique {sl:.5f}")
                        new_sl = sl
                    min_sl_dist = current_atr * 0.3 if current_atr > 0 else 0.0005
                    if new_sl < entry + min_sl_dist:
                        new_sl = entry + min_sl_dist
                        logger.debug(f"  [SL OB] {symbol}: SL SELL relevé à {new_sl:.5f} (garde entrée)")
                    if current_atr > 0 and (new_sl - entry) > current_atr * max_sl_atr:
                        max_sl = entry + current_atr * max_sl_atr
                        logger.debug(f"  [SL OB] {symbol}: SL OB {new_sl:.5f} > {max_sl_atr}×ATR → cap à {max_sl:.5f}")
                        new_sl = max_sl
                    if new_sl > 0:
                        if new_sl != sl:
                            logger.debug(f"  [SL OB] {symbol}: SL ajusté {sl:.5f} → {new_sl:.5f} (dessus OB baissier)")
                        signal["sl"] = new_sl
