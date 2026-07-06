"""Trailing & Exit Logic — extracted from ftmo_protector.py.

Handles:
- ATR-based trailing SL (progressive levels by regime)
- Partial TP (50% at 60% of TP → BE)
- Time-stop (12h if profitable, 4h if breakeven)
- Structure exit (BOS/CHoCH invalidation)
- Peak reconstruction from H1 history

Usage:
    trailer = Trailer(mt5, config)
    trailer.check_all_exits(position)
"""

import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import MetaTrader5 as mt5
import numpy as np

import config_simple as cfg
from engine_simple.ftmo_config import (
    ATR_CACHE_TTL,
    BE_BUFFER_BY_REGIME,
    FIRST_LOCK_ATR,
    TRAILING_BY_REGIME,
    get_trailing_for_symbol,
    get_be_buffer_for_symbol,
)
from engine_simple.structure_analyzer import structure_exit_signal
from engine_simple.symbol_profile import SymbolInstitutionalProfile, get_profile as _get_symbol_profile

logger = logging.getLogger("ftmo.trailer")


class Trailer:
    """ATR-based trailing SL + partial TP + time-stop + structure exit."""

    def __init__(self, mt5_connector: Any, config: dict) -> None:
        self.mt5: Any = mt5_connector
        self.config: dict = config

        # State — managed by FTMOProtector, accessed via references
        self.partial_closed: set = set()
        self.trailing_peaks: dict = {}
        self.position_regime: dict = {}
        self.position_meta: dict = {}
        self.position_open_times: dict = {}
        self._time_stop_cooldown: dict = {}
        self._atr_cache: dict = {}
        self._rates_cache: dict = {}
        self._profile_cache: dict = {}
        self.peak_profit: dict = {}

    # ── Note: Utiliser FTMOProtector.check_invariants() pour la séquence production ──

    def _pip_offset(self, symbol: str, pips: int = 10) -> float:
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return 0.001
        point = info.point if info.point else 0.0001
        pip_size = point * (10 if info.digits >= 3 else 1)
        return pips * pip_size

    def _get_atr(self, symbol: str, period: int = 14) -> Optional[float]:
        """Get current ATR in price units (True Range) for a symbol (cached TTL=60s)."""
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached and (now - cached[1]) < ATR_CACHE_TTL:
            return cached[0]
        try:
            rates = self.mt5.get_rates(symbol, "H1", period + 5)
            if rates is None or len(rates) < period:
                return None
            high = np.array([r[2] for r in rates], dtype=float)
            low = np.array([r[3] for r in rates], dtype=float)
            close = np.array([r[4] for r in rates], dtype=float)
            prev_close = np.roll(close, 1)
            prev_close[0] = close[0]
            tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
            # Wilder smoothing (au lieu de SMA) — aligné avec indicators.py:210-212
            val = float(np.mean(tr[:period]))  # initialisation SMA
            for i in range(period, len(tr)):
                val = (val * (period - 1) + tr[i]) / period
            self._atr_cache[symbol] = (val, now)
            return val
        except Exception as e:
            logger.warning(f"ATR calc (True Range) failed for {symbol}: {e}")
            return None

    def _get_profile(self, symbol: str) -> Optional[SymbolInstitutionalProfile]:
        """Get symbol institutional profile from profile cache (cached by symbol)."""
        now = time.time()
        cached = self._profile_cache.get(symbol)
        if cached and (now - cached[1]) < 300:
            return cached[0]
        profile = _get_symbol_profile(symbol)
        self._profile_cache[symbol] = (profile, now)
        return profile

    # ── Partial TP ────────────────────────────────────────────────────

    def _check_partial_tp(self, position: Any) -> None:
        ticket = str(position.ticket)
        if ticket in self.partial_closed:
            return
        entry = position.price_open
        if position.sl is None or position.tp is None or position.sl == position.tp:
            return
        # Only count progress moving TOWARD TP
        if position.type == 0:  # BUY
            if position.price_current <= entry:
                return
            progress = (position.price_current - entry) / max(position.tp - entry, 0.00001)
        else:  # SELL
            if position.price_current >= entry:
                return
            progress = (entry - position.price_current) / max(entry - position.tp, 0.00001)
        if progress < 0.60:
            return
        close_vol = position.volume / 2
        if close_vol < 0.01:
            return
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return
        lot_step = getattr(info, "volume_step", 0.01)
        if isinstance(lot_step, (int, float)) and lot_step > 0:
            close_vol = round(close_vol / lot_step) * lot_step
            close_vol = round(close_vol, 6)
        tick = self.mt5.get_tick(position.symbol)
        if tick is None:
            return
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(
            action=mt5.TRADE_ACTION_DEAL,
            symbol=position.symbol,
            volume=close_vol,
            type=ct,
            position=position.ticket,
            price=price,
            deviation=20,
            magic=cfg.ROBOT_MAGIC,
            comment="PARTIAL_TP",
        )
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(
                f"TP Partiel: {position.symbol} ferme "
                f"{close_vol}/{position.volume} a {price:.5f} "
                f"(profit={position.profit:.2f})"
            )
            self.partial_closed.add(ticket)
            self._persist_partial_closed()
            # Set BE for remaining position
            if info:
                atr_val = self._get_atr(position.symbol)
                if atr_val and atr_val > 0:
                    regime = self.position_regime.get(ticket, "RANGING")
                    # 🔧 Utiliser le buffer BE par SYMBOLE (pas seulement par régime)
                    be_mult = get_be_buffer_for_symbol(position.symbol, regime)
                    be_buffer = be_mult * atr_val
                else:
                    be_buffer = self._pip_offset(position.symbol, 10)
                be_sl = entry + be_buffer if position.type == 0 else entry - be_buffer
                be_sl = round(be_sl, info.digits)
                is_buy = position.type == 0
                sl_improves = (position.sl is None) or (
                    (is_buy and be_sl > position.sl) or (not is_buy and be_sl < position.sl)
                )
                if sl_improves:
                    old_sl = position.sl
                    r = self.mt5.update_sl(position, be_sl)
                    if r and r.retcode == 10009:
                        try:
                            position.sl = be_sl
                        except AttributeError:
                            pass
                    logger.info(f"  [AUDIT] {position.symbol} SL {old_sl}→{be_sl} (BE after partial TP)")
        elif result and result.retcode != 10009:
            logger.warning(f"PARTIAL TP FAILED {position.symbol}: retcode={result.retcode}")

    def _persist_partial_closed(self) -> None:
        try:
            # 🐛 FIX 26 Juin 2026: utiliser le chemin absolu depuis le fichier
            # au lieu du CWD pour éviter les écritures au mauvais endroit
            state_path = Path(__file__).resolve().parent.parent / "runtime" / "robot_state.json"
            if state_path.exists():
                d = json.loads(state_path.read_text())
                d["partial_closed"] = list(self.partial_closed)
                atomic = state_path.with_suffix(".tmp")
                atomic.write_text(json.dumps(d, default=str))
                atomic.replace(state_path)
        except Exception as e:
            logger.debug(f"[PERSIST] partial_closed non persisté: {e}")

    # ── Time-stop ─────────────────────────────────────────────────────

    def _check_time_stop(self, position: Any) -> None:
        ticket = str(position.ticket)
        if ticket not in self.position_open_times:
            return
        ot = self.position_open_times[ticket]["open_time"]
        if isinstance(ot, (int, float)):
            ot = datetime.utcfromtimestamp(ot)
        elapsed = datetime.utcnow() - ot
        hours = elapsed.total_seconds() / 3600

        max_profit = self.position_meta.get(ticket, {}).get("max_profit", position.profit)
        if position.profit > max_profit:
            self.position_meta.setdefault(ticket, {})["max_profit"] = position.profit
            max_profit = position.profit
        max_hours = (
            float(os.environ.get("TIME_STOP_MAX_HOURS_PROFIT", "12"))
            if max_profit > 0
            else float(os.environ.get("TIME_STOP_MAX_HOURS_LOSS", "4"))
        )
        if hours < max_hours:
            return

        last_try = self._time_stop_cooldown.get(ticket, 0)
        if time.time() - last_try < 300:  # 5min (fix M8: était 3600s = 1h)
            return
        self._time_stop_cooldown[ticket] = time.time()

        tick = self.mt5.get_tick(position.symbol)
        if tick is None:
            return
        ct = 1 if position.type == 0 else 0
        price = tick.ask if ct == 0 else tick.bid
        req = dict(
            action=mt5.TRADE_ACTION_DEAL,
            symbol=position.symbol,
            volume=position.volume,
            type=ct,
            position=position.ticket,
            price=price,
            deviation=20,
            magic=cfg.ROBOT_MAGIC,
            comment="TIME_STOP",
        )
        result = self.mt5.order_send(req)
        if result and result.retcode == 10009:
            logger.info(f"Time-stop: {position.symbol} ferme apres {hours:.1f}h (profit={position.profit:.2f})")
        elif result and result.retcode == 10018:
            logger.debug(f"  [TIME-STOP] {position.symbol}: rate limit, reessai dans 1h")
        elif result and result.retcode != 10009:
            logger.warning(f"TIME STOP FAILED {position.symbol}: retcode={result.retcode}")

    # ── ATR Trailing ──────────────────────────────────────────────────

    def _check_step_trailing(self, position: Any) -> None:
        """ATR-based trailing SL — niveaux progressifs adaptés au régime."""
        ticket = str(position.ticket)
        atr_val = self._get_atr(position.symbol)
        if atr_val is None or atr_val <= 0:
            return

        if ticket not in self.trailing_peaks:
            self.trailing_peaks[ticket] = self._reconstruct_peak(position)

        if position.type == 0:
            peak = max(self.trailing_peaks[ticket], position.price_current)
            profit_atr = (peak - position.price_open) / atr_val
        else:
            peak = min(self.trailing_peaks[ticket], position.price_current)
            profit_atr = (position.price_open - peak) / atr_val
        self.trailing_peaks[ticket] = peak

        regime = self.position_regime.get(ticket, "RANGING")
        profile = self._get_profile(position.symbol)
        profile_levels = profile.trailing_profile.get(regime) if profile else None
        levels = profile_levels or get_trailing_for_symbol(position.symbol, regime)
        first_thresh = levels[0][0] if levels else 0.50
        logger.debug(
            f"  [TRAIL] {position.symbol} ticket={ticket} "
            f"ATR={atr_val:.5f} peak={peak:.5f} "
            f"entry={position.price_open:.5f} profit_atr={profit_atr:.2f} "
            f"SL={position.sl:.5f} regime={regime}"
        )
        if profit_atr <= first_thresh:
            return

        trail_dist = levels[-1][1]
        for thresh, dist in reversed(levels):
            if profit_atr > thresh:
                trail_dist = dist
                break

        jitter = 1.0 + random.uniform(-0.10, 0.10)
        trail_distance = trail_dist * atr_val * jitter
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return

        current_price = position.price_current
        entry_price = position.price_open
        try:
            min_stop_points = int(info.trade_stops_level or 0)
        except (TypeError, ValueError):
            min_stop_points = 0
        min_gap = max(trail_distance, min_stop_points * info.point) if min_stop_points > 0 else trail_distance

        if position.type == 0:  # BUY
            trail_sl = peak - trail_distance
            if trail_sl >= current_price:
                retrace_atr = (peak - current_price) / max(atr_val, 1e-10)
                if retrace_atr > 1.5:
                    self.trailing_peaks[ticket] = current_price
                # Retracement > 1 ATR : forcer BE pour limiter la casse
                if retrace_atr > 1.0 and position.sl < position.price_open:
                    self._force_breakeven(position)
                return
            lower = entry_price
            upper = current_price - min_gap
            if lower > upper:
                return
            new_sl = min(trail_sl, upper)
            new_sl = max(new_sl, lower)
        else:  # SELL
            trail_sl = peak + trail_distance
            if trail_sl <= current_price:
                retrace_atr = (current_price - peak) / max(atr_val, 1e-10)
                if retrace_atr > 1.5:
                    self.trailing_peaks[ticket] = current_price
                return
            lower = current_price + min_gap
            upper = entry_price
            if lower > upper:
                return
            new_sl = max(trail_sl, lower)
            new_sl = min(new_sl, upper)

        new_sl = round(new_sl, info.digits)
        if position.type == 0 and new_sl <= position.sl:
            return
        if position.type == 1 and new_sl >= position.sl:
            return

        old_sl = position.sl
        result = self.mt5.update_sl(position, new_sl)
        if result and result.retcode == 10009:
            try:
                position.sl = new_sl
            except AttributeError:
                pass
            self.peak_profit[ticket] = profit_atr
            logger.info(
                f"TrailATR: {position.symbol} SL {old_sl}→{new_sl} "
                f"(peak_profit={profit_atr:.1f}ATR, trail={trail_dist:.2f}ATR)"
            )
            return
        rc = result.retcode if result else -1
        if rc == 10016:
            retry_gap = trail_distance + 2 * atr_val * jitter
            retry_sl = peak - retry_gap if position.type == 0 else peak + retry_gap
            retry_sl = round(retry_sl, info.digits)
            result2 = self.mt5.update_sl(position, retry_sl)
            if result2 and result2.retcode == 10009:
                try:
                    position.sl = retry_sl
                except AttributeError:
                    pass
                self.peak_profit[ticket] = profit_atr
                logger.info(f"TrailATR: {position.symbol} SL {old_sl}→{retry_sl} (retry OK)")
                return
        logger.debug(f"  [TRAIL] FAILED {position.symbol}: retcode={rc}")

    # ── Peak reconstruction ───────────────────────────────────────────

    def _reconstruct_peak(self, position: Any) -> float:
        """Trouve le vrai peak depuis l'ouverture du trade (H1, 48 bars = ~2 jours)."""
        try:
            rates = self.mt5.get_rates(position.symbol, "H1", count=120)  # ~5 jours (fix M9: 48→120)
            if rates is not None and len(rates) > 5:
                pos_open_ts = None
                try:
                    # 🐛 FIX 26 Juin 2026: position.time est un int (Unix timestamp)
                    # dans l'API MT5, pas un datetime. .timestamp() lève AttributeError.
                    pos_open_ts = position.time if isinstance(position.time, (int, float)) else None
                except (AttributeError, TypeError):
                    pass
                if pos_open_ts is not None:
                    filtered = [r for r in rates if r[0] >= pos_open_ts]
                    if len(filtered) >= 2:
                        rates = filtered
                h = np.array([r[2] for r in rates], dtype=float)
                lo = np.array([r[3] for r in rates], dtype=float)
                if position.type == 0:
                    return max(position.price_open, position.price_current, np.max(h))
                else:
                    return min(position.price_open, position.price_current, np.min(lo))
        except Exception as e:
            logger.debug(f"Peak reconstruction failed for {position.symbol}: {e}")
        if position.type == 0:
            return max(position.price_open, position.price_current)
        else:
            return min(position.price_open, position.price_current)

    # ── Force breakeven (pour retracement excessif) ────────────────────

    def _force_breakeven(self, position: Any) -> None:
        """Force le SL au BE quand le retracement dépasse 1 ATR et que le SL est en dessous de l'entrée."""
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return
        atr_val = self._get_atr(position.symbol)
        if atr_val and atr_val > 0:
            regime = self.position_regime.get(str(position.ticket), "RANGING")
            be_buffer = get_be_buffer_for_symbol(position.symbol, regime) * atr_val
        else:
            be_buffer = self._pip_offset(position.symbol, 5)
        be_sl = position.price_open + be_buffer if position.type == 0 else position.price_open - be_buffer
        be_sl = round(be_sl, info.digits)
        is_buy = position.type == 0
        sl_improves = (position.sl is None) or (
            (is_buy and be_sl > position.sl) or (not is_buy and be_sl < position.sl)
        )
        if sl_improves:
            old_sl = position.sl
            r = self.mt5.update_sl(position, be_sl)
            if r and r.retcode == 10009:
                try:
                    position.sl = be_sl
                except AttributeError:
                    pass
                logger.info(f"  [FORCE BE] {position.symbol} SL {old_sl}→{be_sl} (retracement {position.profit:.2f})")

    # ── Structure exit ────────────────────────────────────────────────

    def _check_structure_exit(self, position: Any) -> None:
        """Structure-based exit: resserre le SL si BOS/CHoCH invalide la direction.

        Au lieu de fermer en market order (perdait le trailing ATR),
        on modifie le SL au niveau BOS pour laisser le trailing ou le marché
        décider de la sortie. Préserve les gains déjà verrouillés par le trailing.
        """
        symbol = position.symbol
        now = time.time()
        cached = self._rates_cache.get(symbol)
        if cached and now - cached["time"] < 60:
            rates = cached["rates"]
        else:
            rates = self.mt5.get_rates(symbol, "H1", 30)
            if rates is not None and len(rates) >= 15:
                self._rates_cache[symbol] = {"rates": rates, "time": now}
        if rates is None or len(rates) < 15:
            return
        h1h = np.array([r[2] for r in rates], dtype=float)
        h1l = np.array([r[3] for r in rates], dtype=float)
        h1c = np.array([r[4] for r in rates], dtype=float)
        h1t = np.array([r[0] for r in rates], dtype=float)
        should_exit, reason, candle_idx = structure_exit_signal(position.type, h1h, h1l, h1c, window=5)
        if not should_exit or candle_idx is None:
            return

        # 🐛 FIX 26 Juin 2026: position.time est un int (Unix timestamp)
        # 🐛 CORRIGÉ 26 Juin: ce bloc était APRÈS un `return` → dead code
        try:
            pos_open_ts = position.time if isinstance(position.time, (int, float)) else None
            candle_ts = h1t[candle_idx]
            if pos_open_ts is not None and candle_ts <= pos_open_ts:
                return
        except (AttributeError, IndexError, TypeError):
            return

        # Extraire le niveau BOS du message (ex: "BEARISH_BOS @ 1.13829")
        level = None
        if reason and "@" in reason:
            try:
                level = float(reason.split("@")[1].strip())
            except (ValueError, IndexError):
                pass
        if level is None or level <= 0:
            return

        # RESSERRER LE SL au niveau BOS au lieu de fermer en market order.
        # Cela préserve le trailing ATR qui continue de protéger les gains.
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return
        is_buy = position.type == 0
        proposed_sl = round(level, info.digits)

        # Vérifier que le nouveau SL améliore la protection
        sl_improves = (position.sl is None) or (
            (is_buy and proposed_sl > position.sl) or (not is_buy and proposed_sl < position.sl)
        )
        if not sl_improves:
            # Le SL actuel (trailing ATR) est déjà meilleur → laisser le trailing gérer
            logger.debug(f"  [STRUCT_SL] {symbol}: BOS={level} mais SL actuel={position.sl} meilleur → skip")
            return

        result = self.mt5.update_sl(position, proposed_sl)
        if result and result.retcode == 10009:
            try:
                position.sl = proposed_sl
            except AttributeError:
                pass
            logger.info(f"Structure SL: {symbol} SL→{proposed_sl} ({reason}) profit={position.profit:.2f}")
        elif result and result.retcode != 10009:
            logger.warning(f"STRUCTURE SL FAILED {symbol}: retcode={result.retcode}")

    # ── SL/TP calculation ─────────────────────────────────────────────

    def calc_sl_tp(
        self,
        symbol: str,
        entry: float,
        direction: int,
        atr_val: Optional[float] = None,
        sl_mult: float = 2.0,
        tp_mult: float = 4.0,
    ) -> tuple[Optional[float], Optional[float]]:
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return None, None
        digits = info.digits
        if atr_val and atr_val > 0:
            min_dist = cfg.ATR_MULTIPLIER * atr_val
            # 🐛 FIX 26 Juin 2026: jitter unique pour SL et TP pour préserver RR ratio
            # Avant: jitter_sl et jitter_tp indépendants → RR pouvait tomber à 1.8
            jitter = 1.0 + random.uniform(-0.05, 0.05)  # ±5% sur les deux
            sl_dist = max(sl_mult * atr_val * jitter, min_dist)
            tp_dist = max(tp_mult * atr_val * jitter, min_dist)
        else:
            sl_dist = self.config.get("SL_PIPS", 15) * (0.0001 if "JPY" not in symbol else 0.01)
            tp_dist = sl_dist * self.config.get("TP_MULTIPLIER", 2.0)
        if direction == 0:
            return round(entry - sl_dist, digits), round(entry + tp_dist, digits)
        else:
            return round(entry + sl_dist, digits), round(entry - tp_dist, digits)
