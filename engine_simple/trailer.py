"""Trailing & Exit Logic — extracted from ftmo_protector.py.

Handles:
- ATR-based trailing SL (progressive levels by regime)
- Partial TP (75% at 65% of TP → BE) — config R3 31 Juillet 2026
- Time-stop (12h if profitable default, 8h XAUUSD, 4h BTCUSD/SOLUSD/USDJPY loss)
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import MetaTrader5 as mt5
import numpy as np

import config_simple as cfg
from engine_simple.state_manager import update_state_field
from engine_simple.ftmo_config import (
    ATR_CACHE_TTL,
    BE_BUFFER_BY_REGIME,
    FIRST_LOCK_ATR,
    TRAILING_BY_REGIME,
    get_trailing_for_symbol,
    get_be_buffer_for_symbol,
    is_trailing_disabled,
)
from engine_simple.structure_analyzer import structure_exit_signal
from engine_simple.symbol_profile import SymbolInstitutionalProfile, get_profile as _get_symbol_profile

logger = logging.getLogger("ftmo.trailer")

# 🔧 FIX 17 Août 2026: paliers du BE progressif (montée PLUS rapide du SL).
# Problème: le BE s'arrêtait à entry+0.15×ATR dès 1.30×ATR de profit → entre
# 1.30×ATR et le lock N1 (BTCUSD TREND=2.50×ATR, fallback=1.80×ATR) le SL
# restait FIXE (zone morte) → les gagnants pouvaient rendre jusqu'à ~2×ATR
# de profit avant que le trailing ne prenne le relais. Ex: BTCUSD à 2.27×ATR
# de profit avait encore SL=entry+0.15×ATR (logs 17/08 20:24).
# Nouveau: paliers fixes tous les 0.30×ATR qui font monter le SL de +0.15×ATR
# à chaque palier. Le SL suit le profit de façon quasi-linéaire tout en
# restant TOUJOURS sous le trailing N1 (peak−trail_dist), donc compatible.
# 1.00×ATR → entry          (BE pur, inchangé)
# 1.30×ATR → entry+0.15×ATR (inchangé)
# 1.60×ATR → entry+0.30×ATR
# 1.90×ATR → entry+0.45×ATR
# 2.20×ATR → entry+0.60×ATR
# 2.50×ATR → entry+0.75×ATR  (raccord au lock N1 BTCUSD TREND)
# Valeurs en (seuil_profit_atr, buffer_sl_atr) — croissantes strictement.
BE_PROGRESSIVE_LEVELS = [
    (1.00, 0.00),  # breakeven pur
    (1.30, 0.15),
    (1.60, 0.30),
    (1.90, 0.45),
    (2.20, 0.60),
    (2.50, 0.75),
]


class Trailer:
    """ATR-based trailing SL + partial TP + time-stop + structure exit."""

    def __init__(self, mt5_connector: Any, config: dict, shared_lock: Any = None) -> None:
        self.mt5: Any = mt5_connector
        self.config: dict = config

        # State — managed by FTMOProtector, accessed via references
        # 🔧 FIX 16 Juillet 2026: shared_lock protège les 6 dicts partagés
        # contre les race conditions (ThreadPoolExecutor interleaving).
        # Note: threading.RLock est une factory function, pas une classe —
        # on utilise Any pour la type hint.
        self._shared_lock: Any = shared_lock
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
        # 🐛 FIX 16 Août 2026 (Log Analyst): le jitter ±10% était re-tiré à CHAQUE
        # cycle (15s). Le ratchet sl_improves poussait le SL vers la borne serrée
        # (0.9×) au fil des cycles → trailing silencieusement ~10% plus serré que
        # la config backtestée (déviation WR/PF sans trace dans les logs).
        # Correction: un jitter FIXE par (ticket, palier atteint) — tiré une seule
        # fois quand le palier change, comme le fait déjà calc_sl_tp pour l'entrée.
        self._trail_jitter_cache: dict = {}

    # ── Note: Utiliser FTMOProtector.check_invariants() pour la séquence production ──

    def _pip_offset(self, symbol: str, pips: int = 10) -> float:
        info = self.mt5.get_symbol_info(symbol)
        if info is None:
            return 0.001
        point = info.point if info.point else 0.0001
        pip_size = point * (10 if info.digits >= 3 else 1)
        return pips * pip_size

    def _get_atr(self, symbol: str, period: int = 14) -> Optional[float]:
        """Get current ATR in price units (True Range) for a symbol (cached TTL=60s).

        🐛 FIX 16 Août 2026 (Audit m5): utilise le TIMEFRAME du symbole au lieu
        de durcir H1. Avant, le trailing/partial TP des positions XAUUSD (signal
        H4) utilisait un ATR H1 incohérent avec l'ATR de la stratégie (4× plus
        granulaire → multiplicateurs ATR mal appliqués).
        """
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached and (now - cached[1]) < ATR_CACHE_TTL:
            return cached[0]
        try:
            # Timeframe du symbole (H1 par défaut, H4 pour XAUUSD)
            try:
                import config_simple as _cfg

                tf = getattr(_cfg, "SYMBOL_TIMEFRAMES", {}).get(symbol, "H1") or "H1"
            except Exception:
                tf = "H1"
            rates = self.mt5.get_rates(symbol, tf, period + 5)
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
        # SOLUTION A: Pas de partial TP pour les symboles sans trailing
        if is_trailing_disabled(position.symbol):
            return
        ticket = str(position.ticket)
        if ticket in self.partial_closed:
            return
        entry = position.price_open
        if position.sl is None or position.tp is None or position.sl == position.tp:
            return
        # 🔧 FIX 30 Juillet 2026: Utiliser le PEAK (pas price_current) pour le calcul
        # du progress. Avant: un pic à 2.29×ATR puis retracement à 1.50×ATR faisait
        # rater le partial TP car le progress calculé avec price_current était < 40%.
        # Maintenant: utilise trailing_peaks (ou price_current en fallback si pas
        # encore initialisé par _check_step_trailing).
        ticket_str = str(position.ticket)
        trailing_peak = self.trailing_peaks.get(ticket_str)
        if position.type == 0:  # BUY
            progress_price = max(
                trailing_peak if trailing_peak is not None else position.price_current, position.price_current
            )
            if progress_price <= entry:
                return
            progress = (progress_price - entry) / max(position.tp - entry, 0.00001)
        else:  # SELL
            progress_price = min(
                trailing_peak if trailing_peak is not None else position.price_current, position.price_current
            )
            if progress_price >= entry:
                return
            progress = (entry - progress_price) / max(entry - position.tp, 0.00001)
        # 🔧 29 Juil 2026: 0.70→0.40 — déclencher partial TP plus tôt pour sécuriser
        # les gains avant que le marché ne retrace. Avec TP=5.0×ATR, partial à 40% = 2.0×ATR.
        # Équité baissait de +$5 à +$1 en 12min car le partial était trop loin (3.5×ATR).
        # 🔧 31 Juil 2026 (Quant Auditor — R3): 0.40→0.65 — la config 40% fermait la moitié
        # du trade dès 1.6R (début du move), coupant la course vers le TP. À 65% (=3.25R
        # sur TP 5×ATR), la moitié close est déjà en zone rentable ET la moitié restante a
        # une vraie chance d'atteindre le TP 4-6×ATR. Le backtest 158K trades (PF>1.1)
        # n'avait PAS de partial TP à 40% — cette valeur n'a jamais été validée.
        # 🔧 19 Août 2026 (Council): seuil du partial TP configurable par symbole.
        # XAUUSD: partial_tp_progress=0.55 (verrouille 75% du volume PLUS tôt —
        # WR 28.6% mais gros winners, il faut sécuriser les gains avant retracement).
        # Défaut global: 0.65 (R3 31 Juillet — laisser la course vers le TP).
        sym_limits = getattr(cfg, "SYMBOL_LIMITS", {})
        partial_progress = 0.65
        sym_pp = sym_limits.get(position.symbol, {}).get("partial_tp_progress")
        if isinstance(sym_pp, (int, float)) and 0.30 <= sym_pp <= 0.95:
            partial_progress = float(sym_pp)
        if progress < partial_progress:
            return
        # 🔧 19 Août 2026 (Backtest Optimizations): fraction fermée 50%→75%
        # (PTP_75pct: fermer 75% au partial, garder 25% pour la course vers le TP).
        # Backtest 7 paires forex H1 2012-2026: pertes −1.6% vs baseline, DD −0.2pt,
        # cohérent avec la recherche (partial 75% = fraction optimale, arXiv 2604.27150).
        close_vol = position.volume * 0.75
        if close_vol < 0.01:
            return
        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return
        lot_step = getattr(info, "volume_step", 0.01)
        if isinstance(lot_step, (int, float)) and lot_step > 0:
            # 🐛 FIX 16 Août 2026 (Audit m1): round() Python = arrondi bancaire
            # (round(2.5)=2, round(0.025/0.01)=0.02) → partial TP fermait 40/60
            # au lieu de 50/50. Quantisation déterministe au lot_step le plus proche.
            steps = close_vol / lot_step
            # Arrondi demi-vers-le-haut explicite (évite le biais bancaire)
            import math

            close_vol = (math.floor(steps + 0.5) if steps >= 0 else math.ceil(steps - 0.5)) * lot_step
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
            # 🔧 FIX C4: Vérifier volume réellement fermé
            filled_vol = getattr(result, "volume", close_vol)
            if isinstance(filled_vol, (int, float)) and abs(filled_vol - close_vol) > 0.001:
                logger.warning(
                    f"[PARTIAL FILL] {position.symbol}: demandé close {close_vol} lot, "
                    f"fillé {filled_vol} lot. Position restante plus grosse que prévu."
                )
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
        """Persiste partial_closed avec lock thread-safe.

        Utilise state_manager.update_state_field() qui acquiert le même
        lock que save_full_state() dans main.py — évite la race condition
        où trailer.py et main.py écrivent concurrentiellement robot_state.json.
        """
        try:
            state_path = str(Path(__file__).resolve().parent.parent / "runtime" / "robot_state.json")
            update_state_field(state_path, "partial_closed", list(self.partial_closed))
        except Exception as e:
            logger.debug(f"[PERSIST] partial_closed non persisté: {e}")

    # ── Time-stop ─────────────────────────────────────────────────────

    def _check_time_stop(self, position: Any) -> None:
        ticket = str(position.ticket)
        if ticket not in self.position_open_times:
            return
        ot = self.position_open_times[ticket]["open_time"]
        if isinstance(ot, (int, float)):
            ot = datetime.fromtimestamp(ot, tz=timezone.utc)
        # 🔧 FIX AUDIT: Gérer les datetimes naïves (compat legacy) en ajoutant UTC timezone.
        if ot.tzinfo is None:
            ot = ot.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - ot
        hours = elapsed.total_seconds() / 3600

        max_profit = self.position_meta.get(ticket, {}).get("max_profit", position.profit)
        if position.profit > max_profit:
            self.position_meta.setdefault(ticket, {})["max_profit"] = position.profit
            max_profit = position.profit
        # 🔧 21 Août 2026 (Robot Manager P0): time-stop loss 3h→2h — les trades perdants
        # sont déjà perdants à 2h, pas besoin d'attendre 3h (−$224 sur 18 time_stops, WR 4%)
        # Le momentum MOM20x3 est un signal court terme — si pas de profit après 2h, très peu
        # de chances de redevenir gagnant.
        # 🔧 FIX 28 Août 2026: time-stop conditionnel — ne fire QUE si profit < 0
        # Données: time_stop WR = 4.9% (58/61 = losers). Les trades profitables
        # sont gérés par le trailing stop (N1-N5). Le time-stop sur trades profitables
        # les ferme trop tôt (ex: SOLUSD +$356 fermé à 5h au lieu de laisser courir).
        # SEULE exception: fermeture pré-weekend (déjà gérée ci-dessus).
        if position.profit > 0 and not self._is_weekend_close_window(position.symbol):
            return  # le trailing stop gère les trades profitables (sauf weekend)

        max_hours = (
            float(os.environ.get("TIME_STOP_MAX_HOURS_PROFIT", "12"))
            if max_profit > 0
            else float(os.environ.get("TIME_STOP_MAX_HOURS_LOSS", "2"))
        )
        # 🔧 FIX 30 Aout 2026: time-stop loss 2h→3h pour crypto (BTCUSD, SOLUSD).
        # La zone 5-12h génère 86% du profit (+$1,489) avec 40 trades. Le time-stop
        # loss à 2h ferme trop tôt les trades crypto qui auraient besoin de 3-5h
        # pour se développer. Augmenter à 3h pour crypto, garder 2h pour le forex.
        # 🔧 1 Sept 2026: extension au forex — le momentum a besoin de 3h pour se
        # développer. 25.9% des exits = time-stop, les trades forex sont coupés trop tôt.
        _LOSS_TIME_3H_SYMBOLS = ("BTCUSD", "SOLUSD", "EURUSD", "GBPUSD", "USDJPY",
                                  "USDCAD", "AUDUSD", "NZDUSD", "USDCHF")
        if max_profit <= 0 and position.symbol in _LOSS_TIME_3H_SYMBOLS:
            max_hours = 3.0
        # 🔧 FIX 19 Août 2026 (Council): time-stop profit configurable par symbole.
        # XAUUSD: time_stop_max_hours_profit=8.0 (défaut 12h) → sécurise les gains
        # plus vite sur un symbole à WR faible (28.6% GR) mais gros winners.
        sym_limits = getattr(cfg, "SYMBOL_LIMITS", {})
        if max_profit > 0:
            sym_ts = sym_limits.get(position.symbol, {}).get("time_stop_max_hours_profit")
            if isinstance(sym_ts, (int, float)) and 1.0 <= sym_ts <= 48.0:
                max_hours = min(max_hours, float(sym_ts))
        # 🔧 24 Août 2026 (Robot Manager P5): time-stop loss configurable par symbole.
        # XAUUSD: time_stop_max_hours_loss=4.0 (défaut 2h) → laisse plus de temps
        # aux trades perdants sur un symbole à volatilité élevée (ATR H4=$36.95).
        # 🔧 FIX C5: min() pour les pertes (SERRER le timeout, pas l'étendre).
        # max() étendait le timeout — un XAUUSD à 4.0h tenait les perdants 2× trop longtemps.
        if max_profit <= 0:
            sym_ts_loss = sym_limits.get(position.symbol, {}).get("time_stop_max_hours_loss")
            if isinstance(sym_ts_loss, (int, float)) and 1.0 <= sym_ts_loss <= 48.0:
                max_hours = min(max_hours, float(sym_ts_loss))
        # 🔧 FIX 17 Août 2026 (Log Analyst): fermeture pré-weekend.
        # Problème: un trade dont le time-stop est dû le vendredi soir restait
        # exposé tout le week-end (le time-stop échoue avec 10018 marché fermé
        # → T3 AUDUSD 519685971 ouvert 52h). Pour les symboles qui FERMENT le
        # week-end (weekend_trading=false), on réduit max_hours pendant la
        # fenêtre pré-clôture afin de fermer AVANT que le marché ne ferme.
        if self._is_weekend_close_window(position.symbol):
            max_hours = min(
                max_hours,
                float(os.environ.get("WEEKEND_CLOSE_MAX_HOURS", "2")),
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
            # 10018 = TRADE_RETCODE_MARKET_CLOSED (marché fermé). NB: TOO_MANY_REQUESTS
            # est le retcode 10020 (pas 10018) — doc corrigée 16 Août 2026.
            # 🐛 FIX 16 Août 2026: le message "reessai dans 1h" était trompeur — le
            # cooldown réel _time_stop_cooldown est 300s (5 min, fix M8). Pendant le
            # week-end (Forex fermé), le time-stop échoue ainsi en boucle toutes les
            # 5 min jusqu'à l'ouverture — comportement normal, message clarifié.
            logger.debug(
                f"  [TIME-STOP] {position.symbol}: retcode 10018 (marché fermé / rate limit), "
                f"réessai dans {self._time_stop_cooldown.get(ticket, 0) and '300s'}"
            )
        elif result and result.retcode != 10009:
            logger.warning(f"TIME STOP FAILED {position.symbol}: retcode={result.retcode}")

    def _is_weekend_close_window(self, symbol: str) -> bool:
        """🔧 FIX 17 Août 2026 (Log Analyst): détecte la fenêtre pré-weekend.

        Un trade dont le time-stop est dû le vendredi soir ne peut PAS être
        fermé pendant le week-end (marché fermé → retcode 10018) et reste
        exposé 48h+ (T3 AUDUSD 519685971, 52h). Pour les symboles qui FERMENT
        le week-end (weekend_trading=false dans symbol_limits), on réduit
        max_hours pendant la fenêtre de clôture afin de fermer AVANT.

        Fenêtre: vendredi (weekday=4) après WEEKEND_CLOSE_HOUR_UTC (défaut 16h UTC).
        """
        # Symboles 24/7 (crypto BTCUSD/SOLUSD...) → pas de fermeture week-end
        sym_limits = getattr(cfg, "SYMBOL_LIMITS", {})
        weekend_ok = sym_limits.get(symbol, {}).get("weekend_trading", True)
        if weekend_ok:
            return False
        try:
            close_hour = int(os.environ.get("WEEKEND_CLOSE_HOUR_UTC", "16"))
        except ValueError:
            close_hour = 16
        now = datetime.now(timezone.utc)
        return now.weekday() == 4 and now.hour >= close_hour

    # ── ATR Trailing ──────────────────────────────────────────────────

    def _check_step_trailing(self, position: Any) -> None:
        """ATR-based trailing SL — désactivé pour les symboles Solution A."""
        # SOLUTION A: Pas de trailing pour les symboles cibles
        if is_trailing_disabled(position.symbol):
            return
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
            f"SL={position.sl if position.sl is not None else 0.0:.5f} regime={regime}"
        )
        if profit_atr <= first_thresh:
            return

        trail_dist = levels[-1][1]
        for thresh, dist in reversed(levels):
            if profit_atr > thresh:
                trail_dist = dist
                break

        # 🐛 FIX 16 Août 2026: jitter FIXE par ticket au lieu de re-tirer à chaque cycle.
        # Sans cache, le SL cliquetait vers 0.9× (borne serrée du jitter) au fil des
        # cycles → trailing silencieusement ~10% plus serré que la config backtestée
        # (déviation WR/PF sans trace dans les logs). Le jitter est tiré UNE seule
        # fois par trade (au premier trailing) — il varie entre trades (anti-hunting,
        # conforme à la doc) mais reste stable pendant la vie du trade. Le retry
        # 10016 (ci-dessous) réutilise le même jitter que le chemin principal.
        jitter = self._trail_jitter_cache.get(ticket)
        if jitter is None:
            jitter = 1.0 + random.uniform(-0.10, 0.10)
            self._trail_jitter_cache[ticket] = jitter
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
                # 🐛 FIX 16 Août 2026: garde None-safe (position.sl peut être None
                # pour une position manuelle avec magic — TypeError attrapé par
                # check_invariants désactivait SILENCIEUSEMENT le trailing du ticket)
                if retrace_atr > 1.0 and (position.sl is None or position.sl < position.price_open):
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
                # 🔧 FIX 20 Août 2026 (Robot Manager): symétrie BUY/SELL — force BE.
                # Avant, le SELL ne forçait JAMAIS le breakeven sur retracement > 1 ATR
                # (le BUY le faisait L.479-480) → un SELL qui retraçait fort restait
                # exposé avec SL sous l'entrée. Impact actuel nul (BUY-only config),
                # mais dette défensive si les shorts sont réactivés.
                if retrace_atr > 1.0 and (position.sl is None or position.sl > position.price_open):
                    self._force_breakeven(position)
                return
            lower = current_price + min_gap
            upper = entry_price
            if lower > upper:
                return
            new_sl = max(trail_sl, lower)
            new_sl = min(new_sl, upper)

        new_sl = round(new_sl, info.digits)
        # 🐛 FIX 16 Août 2026: garde None-safe — si position.sl est None (position
        # manuelle sans SL), on applique le trailing directement au lieu de lever
        # TypeError (qui désactivait silencieusement le trailing de ce ticket).
        cur_sl = position.sl
        if position.type == 0 and (cur_sl is not None and new_sl <= cur_sl):
            return
        if position.type == 1 and (cur_sl is not None and new_sl >= cur_sl):
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
            # 🔧 FIX AUDIT H2: retry_gap = trail_distance AU LIEU DE +2×ATR.
            # L'ancien code ajoutait 2×ATR au gap → SL encore plus loin du peak →
            # $74+ de profit rendu sur XAUUSD. Le retry 10016 = "SL trop proche du prix"
            # → on recule SL de trail_distance (pas de 2×ATR supplémentaire).
            retry_gap = trail_distance
            retry_sl = peak - retry_gap if position.type == 0 else peak + retry_gap
            # 🐛 FIX 10 Août 2026: le chemin retry 10016 n'avait AUCUNE garde sl_improves.
            # retry_sl = peak - retry_gap pouvait passer SOUS l'entrée (profit non sécurisé)
            # ou SOUS le SL actuel → le SL RECULAIT (protection réduite). On applique les
            # mêmes bornes que le chemin principal (lignes 353-377) : jamais sous l'entrée,
            # jamais pire que le SL actuel.
            if position.type == 0:  # BUY
                retry_sl = max(retry_sl, entry_price)
                if position.sl is not None and retry_sl <= position.sl:
                    logger.debug(
                        f"  [TRAIL] retry 10016 {position.symbol}: retry_sl {retry_sl:.5f} "
                        f"≤ SL actuel {position.sl:.5f} — skip (anti-recul)"
                    )
                    return
            else:  # SELL
                retry_sl = min(retry_sl, entry_price)
                if position.sl is not None and retry_sl >= position.sl:
                    logger.debug(
                        f"  [TRAIL] retry 10016 {position.symbol}: retry_sl {retry_sl:.5f} "
                        f"≥ SL actuel {position.sl:.5f} — skip (anti-recul)"
                    )
                    return
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
        """Trouve le vrai peak depuis l'ouverture du trade (H1, 48 bars = ~2 jours).

        ⚠️ Divergence documentée (20 Août 2026, Robot Manager) : la reconstruction
        utilise H1 en dur alors que `_get_atr` utilise le timeframe du symbole
        (H4 pour XAUUSD). C'est INTENTIONNEL : le peak H1 est plus fin (capture
        les wicks H1 dans une bougie H4), ce qui donne un peak plus précis pour
        le trailing. ATR H4 reste la référence pour les multiplicateurs. Impact
        mesuré : XAUUSD H4 ATR ≈ $37, les wicks H1 sont typiquement < $10 → le
        peak H1 est légèrement plus haut que le peak H4, ce qui favorise la
        protection (SL un peu plus loin du prix)."""
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

    # ── Progressive breakeven (avant N1) ──────────────────────────────

    def _check_progressive_be(self, position: Any) -> None:
        """Progressive breakeven: sécurise un profit minimal AVANT le trailing N1.

        - profit > 1.00×ATR → SL = entry (breakeven pur, zéro perte)
        - profit > 1.30×ATR → SL = entry + 0.15×ATR (petit gain garanti)
        - puis paliers tous les 0.30×ATR → SL + 0.15×ATR à chaque palier
          (1.60→0.30, 1.90→0.45, 2.20→0.60, 2.50→0.75×ATR)

        🔧 FIX 31 Juillet 2026 (Quant Auditor): les seuils précédents (0.80/0.50×ATR)
        coupaient 62% des gagnants à <0.5R avant même le lock N1 (1.20×ATR).
        En repoussant à 1.00/1.30×ATR, les trades faibles ont une chance d'atteindre
        la zone N1 au lieu d'être stoppés net sur le bruit.

        🔧 FIX 17 Août 2026: montée PLUS rapide. Avant, le SL restait fixe à
        entry+0.15×ATR entre 1.30×ATR et le lock N1 (zone morte pouvant rendre
        ~2×ATR de profit). Désormais des paliers BE_PROGRESSIVE_LEVELS font monter
        le SL de +0.15×ATR tous les 0.30×ATR de profit, tout en restant toujours
        sous le trailing N1 (peak−trail_dist) grâce à la garde sl_improves.
        Ne s'applique QUE si le SL actuel est moins bon (sl_improves).
        Permet de garantir qu'un trade qui atteint +1.00×ATR puis retrace
        ne repars pas à zéro.
        """
        # 🔧 FIX 30 Aout 2026: min hold time 15min — éviter que le BE progressif
        # resserre le SL trop tôt après l'entrée (84 trades <30min = bruit pur).
        # Le SL initial au broker reste actif (protection), mais on ne resserre pas
        # avant 15 minutes pour laisser le trade respirer.
        pos_age_min = (time.time() - position.time) / 60 if hasattr(position, 'time') and position.time else 0
        if pos_age_min < 15:
            return

        # SOLUTION A: Pas de BE progressif pour les symboles sans trailing
        if is_trailing_disabled(position.symbol):
            return
        atr_val = self._get_atr(position.symbol)
        if atr_val is None or atr_val <= 0:
            return
        entry = position.price_open
        is_buy = position.type == 0

        # 🔧 FIX 17 Août 2026 (Log Analyst): utiliser le PEAK (pas price_current)
        # pour le calcul du profit_atr — même pattern que _check_partial_tp
        # (FIX 30 Juillet 2026). Avant: un pic à 1.06×ATR puis retracement sous
        # 1.00×ATR faisait rater le BE (le SL restait au niveau d'entrée d'origine,
        # exposé au week-end — ticket AUDUSD 519685971, SL figé 52h). Le BE doit
        # sécuriser le profit DÈS QUE le peak a été atteint, pas quand le prix
        # est redescendu.
        ticket_str = str(position.ticket)
        trailing_peak = self.trailing_peaks.get(ticket_str)
        if is_buy:
            profit_price = max(
                trailing_peak if trailing_peak is not None else position.price_current,
                position.price_current,
            )
            profit_atr = (profit_price - entry) / atr_val
        else:
            profit_price = min(
                trailing_peak if trailing_peak is not None else position.price_current,
                position.price_current,
            )
            profit_atr = (entry - profit_price) / atr_val

        if profit_atr <= 1.00:
            return

        # Choisir le palier le plus haut atteint (croissants strictement)
        target_buffer = 0.0
        for thresh, buffer in BE_PROGRESSIVE_LEVELS:
            if profit_atr > thresh:
                target_buffer = buffer
            else:
                break
        target_sl = entry + target_buffer * atr_val if is_buy else entry - target_buffer * atr_val

        info = self.mt5.get_symbol_info(position.symbol)
        if info is None:
            return
        target_sl = round(target_sl, info.digits)

        sl_improves = (position.sl is None) or (
            (is_buy and target_sl > position.sl) or (not is_buy and target_sl < position.sl)
        )
        if not sl_improves:
            return

        old_sl = position.sl
        r = self.mt5.update_sl(position, target_sl)
        if r and r.retcode == 10009:
            try:
                position.sl = target_sl
            except AttributeError:
                pass
            logger.info(f"  [PROG BE] {position.symbol} SL {old_sl}→{target_sl} (profit_atr={profit_atr:.2f})")

    # ── Structure exit ────────────────────────────────────────────────

    def _check_structure_exit(self, position: Any) -> None:
        """Structure-based exit: resserre le SL si BOS/CHoCH invalide la direction.

        Au lieu de fermer en market order (perdait le trailing ATR),
        on modifie le SL au niveau BOS pour laisser le trailing ou le marché
        décider de la sortie. Préserve les gains déjà verrouillés par le trailing.

        🔧 FIX 30 Aout 2026: min hold time 15min — éviter que le structure exit
        resserre le SL trop tôt après l'entrée (84 trades <30min = bruit pur).
        """
        # Min hold time: ne pas resserre le SL avant 15 minutes
        pos_age_min = (time.time() - position.time) / 60 if hasattr(position, 'time') and position.time else 0
        if pos_age_min < 15:
            return

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
        sl_mult: float = 1.8,  # 🔧 24 Juil: 2.0→1.8 (W/L ratio)
        tp_mult: float = 5.0,  # 🔧 24 Juil: 4.0→5.0 (W/L ratio)
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
            # 🐛 FIX 13 Juil 2026: préserver le ratio RR quand sl_mult < ATR_MULTIPLIER
            # Avant: tp_dist avait aussi min_dist comme plancher, ce qui détruisait le RR
            # (ex: sl_mult=1.0, tp_mult=1.5 → les deux clampaient à 1.5×ATR → RR=1.00)
            tp_dist = sl_dist * tp_mult / sl_mult
        else:
            sl_dist = self.config.get("SL_PIPS", 15) * (0.0001 if "JPY" not in symbol else 0.01)
            tp_dist = sl_dist * self.config.get("TP_MULTIPLIER", 2.0)
        if direction == 0:
            return round(entry - sl_dist, digits), round(entry + tp_dist, digits)
        else:
            return round(entry + sl_dist, digits), round(entry - tp_dist, digits)
