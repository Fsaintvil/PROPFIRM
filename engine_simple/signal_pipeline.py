"""Signal Pipeline — filtrage multi-couches des signaux MOM20x3.

Extrait de main.py:_scan_signals (P1 — Juin 2026).
Chaque phase est une méthode indépendante, testable unitairement.

Flux:
  process(symbol) → SignalResult | None
    ├── phase1_mom20x3()         ← signal brut MOM20x3
    ├── phase2_adx_filter()      ← ADX threshold + bypass
    ├── phase3_session_filter()  ← session active ?
    ├── phase4_news_filter()     ← news économique ?
    ├── phase5_regime_rule()     ← direction = régime ?
    ├── phase6_strategy_selector() ← params par régime
    ├── phase7_volume_profile()  ← POC/VAH/VAL
    ├── phase7b_rvol_cmf()       ← RVOL + Chaikin Money Flow
    ├── phase8_order_flow()      ← tick delta, divergence, OBV divergence
    ├── phase9_mtf_confirm()     ← TF supérieure
    ├── phase10_market_profile() ← Initial Balance
    ├── phase11_vwap()           ← Premium/Discount zones
    └── phase12_adaptive_params() ← risk_mult adaptatif
"""

import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone
import time

import pandas as _pd
import numpy as np

from engine_simple.indicators import chaikin_money_flow, relative_volume, obv, obv_divergence
from engine_simple.feature_pipeline import compute_all_features, compute_score_adjustment

logger = logging.getLogger("signal_pipeline")


@dataclass
class SignalResult:
    """Résultat du pipeline pour un symbole."""

    symbol: str
    signal: dict
    score: float


class SignalPipeline:
    """Pipeline de filtrage multi-couches pour les signaux de trading."""

    def __init__(
        self,
        mt5,
        ftmo,
        adaptive,
        market_memory,
        session_filter,
        news_filter,
        strategy_selector,
        volume_profile,
        order_flow,
        mtf_confirm,
        market_profile,
        vwap_analyzer,
        risk_manager,
        config,
        symbol_limits,
        symbol_timeframes,
    ):
        self.mt5 = mt5
        self.ftmo = ftmo
        self.adaptive = adaptive
        self.market_memory = market_memory
        self.session_filter = session_filter
        self.news_filter = news_filter
        self.strategy_selector = strategy_selector
        self.volume_profile = volume_profile
        self.order_flow = order_flow
        self.mtf_confirm = mtf_confirm
        self.market_profile = market_profile
        self.vwap_analyzer = vwap_analyzer
        self.risk_manager = risk_manager
        self.cfg = config
        self.symbol_limits = symbol_limits
        self.symbol_timeframes = symbol_timeframes
        # Cache des AdaptiveParameters par symbole
        self._adaptive_params: dict = {}
        # Cache get_rates() par (symbole, timeframe) — évite appels MT5 redondants
        self._rates_cache: dict[tuple[str, str], tuple[float, object]] = {}
        self.RATES_CACHE_TTL = 10  # secondes

    def _get_cached_rates(self, symbol: str, tf: str, count: int = 100):
        """get_rates() avec cache TTL pour éviter les appels MT5 redondants.

        Cache par (symbol, tf) avec invalidation après RATES_CACHE_TTL secondes.
        Les appels avec count différent sont traités séparément (clé inclut count).
        """
        key = (symbol, tf, count)
        now = time.time()
        cached = self._rates_cache.get(key)
        if cached and (now - cached[0]) < self.RATES_CACHE_TTL:
            return cached[1]
        rates = self.mt5.get_rates(symbol, tf, count=count)
        self._rates_cache[key] = (now, rates)
        return rates

    def _to_dataframe(self, rates, cols=None):
        """Convertit les rates MT5 (liste de tuples) en DataFrame si nécessaire."""
        if rates is None:
            return None
        if isinstance(rates, _pd.DataFrame):
            return rates
        if cols is None:
            cols = ["time", "open", "high", "low", "close", "volume", "spread", "real_volume"]
        ncols = min(len(cols), len(rates[0]) if len(rates) > 0 else len(cols))
        return _pd.DataFrame([list(r)[:ncols] for r in rates], columns=cols[:ncols])

    def process(
        self,
        symbol: str,
        cycle_count: int,
        degraded_symbols: dict,
        sym_dir_counts: dict,
        sym_total_counts: dict,
        config_limits: dict,
        last_signals: dict,
        log_throttle: dict,
    ) -> SignalResult | None:
        """Exécute les 11 phases de filtrage pour un symbole.

        Args:
            symbol: Nom du symbole
            cycle_count: Cycle actuel (pour throttling logs)
            degraded_symbols: Dict des symboles en mode dégradé
            sym_dir_counts: Compteur positions par (symbole, direction)
            sym_total_counts: Compteur positions totales par symbole
            config_limits: Limites de positions depuis la config

        Returns:
            SignalResult si le signal passe tous les filtres, None sinon
        """
        # Phase 0: Pre-trade check
        pre_ok, pre_checks = self.risk_manager.pre_trade(symbol)
        if not pre_ok:
            failed = [c["rule"] for c in pre_checks if not c["pass"]]
            logger.debug(f"  [PRECHECK] {symbol}: echec {failed}")
            return None

        # Phase 1: MOM20x3 signal
        signal = self._phase1_mom20x3(symbol)
        if signal is None:
            return None

        score = signal.get("score", 0.6)

        # Degraded mode
        if symbol in degraded_symbols:
            signal["_degraded"] = True
            logger.debug(f"  [DEGRADED] {symbol}: mode dégradé actif → lot minimum")

        # Phase 2: ADX threshold
        if not self._phase2_adx_filter(symbol, signal, cycle_count, log_throttle):
            return None

        # Log signal debug
        logger.debug(
            f"  [SIGNAL] {symbol}: score={signal['score']:.2f}, "
            f"conf={signal['confidence']:.2f}, action={signal['action']}, "
            f"strat={signal.get('details', '?')}, "
            f"+DI={signal.get('plus_di', '?'):>5} -DI={signal.get('minus_di', '?'):>5} "
            f"slope={signal.get('adx_slope', '?'):>5}"
        )

        # Phase 3: Session filter
        if not self._phase3_session_filter(symbol, signal):
            return None

        # Phase 4: News filter
        if not self._phase4_news_filter(symbol):
            return None

        # Phase 5: Direction = regime rule
        if not self._phase5_regime_rule(signal):
            return None

        # Phase 5b: BTCUSD RANGING SELL filter (0% WR sur 6 trades documenté)
        if symbol in ("BTCUSD",) and signal.get("action") == "SELL" and signal.get("_regime") == "RANGING":
            logger.debug(f"  [BTCUSD RANGING] {symbol}: SELL en ranging bloqué (0% WR sur 6 trades)")
            return None

        # Phase 6: Strategy selector
        if not self._phase6_strategy_selector(symbol, signal):
            return None

        # Phase 7: Volume Profile
        if not self._phase7_volume_profile(symbol, signal):
            return None

        # Phase 7b: RVOL + CMF
        if not self._phase7b_rvol_cmf(symbol, signal):
            return None

        # Phase 8: Order Flow
        if not self._phase8_order_flow(symbol, signal):
            return None

        # Phase 9: MTF Confirmation
        if not self._phase9_mtf_confirm(symbol, signal):
            return None

        # Phase 10: Market Profile
        if not self._phase10_market_profile(symbol, signal):
            return None

        # Phase 11: VWAP Premium/Discount
        if not self._phase11_vwap(symbol, signal):
            return None

        # Phase 12: Adaptive Params
        self._phase12_adaptive_params(symbol, signal)

        # Phase 13: Feature Scoring (10+ features comme bonus/pénalité)
        self._phase13_feature_scoring(symbol, signal)

        # Phase 14: LightGBM ajuste score et risque selon sa prédiction
        self._phase14_lgb_scoring(symbol, signal)

        # Dynamic position limits based on confidence
        # Seuils AGENTS.md (Juin 2026): conf > 0.85 → 4, > 0.70 → 3, sinon → 1
        sig_conf = signal.get("confidence", 0.0)
        if sig_conf > 0.85:
            max_per_symbol = 4
        elif sig_conf > 0.70:
            max_per_symbol = 3
        else:
            max_per_symbol = 1
        hard_limit = config_limits.get(symbol, 4)
        max_per_symbol = min(max_per_symbol, hard_limit)
        signal["max_per_symbol"] = max_per_symbol

        # Vérifier la limite dans la direction du signal
        sig_action = signal.get("action")
        sig_dir = 0 if sig_action == "BUY" else 1 if sig_action == "SELL" else None
        if sig_dir is not None:
            dir_count = sym_dir_counts.get((symbol, sig_dir), 0)
            if dir_count >= max_per_symbol:
                _last = log_throttle.get("limit", {}).get(symbol, 0)
                if cycle_count - _last >= 30:
                    log_throttle.setdefault("limit", {})[symbol] = cycle_count
                    logger.debug(
                        f"  [LIMIT] {symbol}: déjà {dir_count} position(s) {sig_action} "
                        f"(max={max_per_symbol}, conf={sig_conf:.2f})"
                    )
                return None

        # Vérifier la limite totale par symbole
        max_pos_total = min(max_per_symbol * 2, hard_limit * 2, self.cfg.MAX_POSITIONS)
        total_count = sym_total_counts.get(symbol, 0)
        if total_count >= max_pos_total:
            _last = log_throttle.get("limit", {}).get(symbol, 0)
            if cycle_count - _last >= 30:
                log_throttle.setdefault("limit", {})[symbol] = cycle_count
                logger.debug(
                    f"  [LIMIT] {symbol}: déjà {total_count} position(s) totales "
                    f"(max={max_pos_total}, conf={sig_conf:.2f})"
                )
            return None

        return SignalResult(symbol=symbol, signal=signal, score=score)

    # ── Phase 1: MOM20x3 Signal ───────────────────────────────────────────

    def _phase1_mom20x3(self, symbol: str) -> dict | None:
        """Génère le signal MOM20x3 avec paramètres OnlineLearner."""
        from engine_simple.strategy import MOM20x3, SYMBOL_CONFIG as _SYMBOL_CFG

        tf = self.symbol_timeframes.get(symbol, "H1")

        # OnlineLearner params
        ol_thresh_trending = None
        ol_thresh_ranging = None
        try:
            ol_params = self.adaptive.learner.get_params(symbol, base_thresh=2.5)
            ol_thresh = ol_params.get("thresh", 2.5)
            base_trending = _SYMBOL_CFG.get(symbol, {}).get("threshold_trending", 2.0)
            # ⚠️ P0: OL dépouillé de ses pouvoirs (Supreme Council, 22 Juin 2026)
            # La condition ol_thresh < base_trending n'a JAMAIS été vraie depuis la création
            # car le seed produit thresh=2.5 pour WR<70% et base_trending=2.0-2.5.
            # L'OL continue d'enregistrer les trades et de logger ses recommandations
            # (observateur passif) mais ses seuils sont ignorés.
            # Voir: AGENTS.md → Session Robot Manager — 22 Juin 2026
            if False:
                ol_thresh_trending = ol_thresh
                ol_thresh_ranging = max(1.5, ol_thresh - 0.5)
        except Exception:
            pass

        rates_tf = self.mt5.get_rates(symbol, tf, count=10000)
        if rates_tf is None or len(rates_tf) < 50:
            logger.debug(
                f"  [MOM20x3] {symbol}: rates {tf} insufficient ({0 if rates_tf is None else len(rates_tf)} bars)"
            )
            return None

        mom = MOM20x3(rates_tf, symbol, market_memory=self.market_memory)
        raw = mom.analyze(custom_thresh_trending=ol_thresh_trending, custom_thresh_ranging=ol_thresh_ranging)
        if raw is None:
            return None

        # Higher TF confirmation
        h4_conf = 1.0
        higher_tf = "D1" if tf == "H4" else "H4"
        try:
            higher_cached = self.mt5.get_rates(symbol, higher_tf, count=60)
            if higher_cached is not None and len(higher_cached) > 30:
                hc = np.array([r[4] for r in higher_cached], dtype=float)
                from engine_simple.indicators import ema

                he = ema(hc, 50)
                if len(he) > 0 and not np.isnan(he[-1]) and he[-1] > 0:
                    higher_ema50 = float(he[-1])
                    higher_price = float(hc[-1])
                    if raw["action"] == "BUY" and higher_price < higher_ema50 * 0.998:
                        h4_conf = 0.80
                    elif raw["action"] == "SELL" and higher_price > higher_ema50 * 1.002:
                        h4_conf = 0.80
        except Exception:
            pass

        # MTF Alignment
        tick = self.mt5.get_tick(symbol)
        if self.market_memory is not None and tick:
            try:
                mtf = self.market_memory.get_mtf_alignment(symbol, tick.ask if tick else 0)
                bullish_count = sum(1 for v in mtf.values() if v == "bullish")
                bearish_count = sum(1 for v in mtf.values() if v == "bearish")
                if raw["action"] == "BUY" and bearish_count >= 3:
                    h4_conf *= 0.85
                elif raw["action"] == "SELL" and bullish_count >= 3:
                    h4_conf *= 0.85
                if raw["action"] == "BUY" and bullish_count >= 3:
                    h4_conf = min(1.0, h4_conf * 1.05)
                elif raw["action"] == "SELL" and bearish_count >= 3:
                    h4_conf = min(1.0, h4_conf * 1.05)
            except Exception:
                pass

        # Enrich signal
        entry = tick.ask if tick else 0
        signal = dict(raw)
        signal["symbol"] = symbol
        signal["timeframe"] = tf
        signal["details"] = f"MOM20x3_{tf}"
        signal["quality"] = min(1.0, (signal.get("confidence", 0.5) + 0.1) * h4_conf)
        if h4_conf < 1.0 and signal.get("score", 0.6) > 0.5:
            signal["score"] = max(0.5, signal["score"] * 0.90)

        # Per-symbol risk_mult (base × constante OL)
        # ⚠️ P0: OL risk_mult gelé à 0.75 (valeur seed pour WR<70%).
        # Le OL.get_params() continue d'être loggué mais son risk_mult n'est plus utilisé
        # car il était désynchronisé avec calibration_state.json.
        # Supreme Council décision: gel temporaire en attendant Phase 2.
        symbol_config = self.symbol_limits.get(symbol, {})
        base_risk_mult = symbol_config.get("risk_mult", 1.0)
        signal["risk_mult"] = base_risk_mult * 0.75
        signal["entry_price"] = entry if raw["action"] == "BUY" else (tick.bid if tick else 0)
        signal["higher_tf_conf"] = round(h4_conf, 2)
        atr_price = signal.get("atr", 0)
        price = tick.bid if tick else 0
        signal["atr_pct"] = round(atr_price / price * 100, 4) if price > 0 else 0

        # RSI
        try:
            close_prices = np.array([r[4] for r in rates_tf], dtype=float)
            from engine_simple.indicators import rsi as ind_rsi

            rsi_arr = ind_rsi(close_prices, period=14)
            signal["rsi"] = round(float(rsi_arr[-1]), 1) if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50.0
        except Exception:
            signal["rsi"] = 50.0

        return signal

    # ── Phase 2: ADX Threshold Filter ─────────────────────────────────────

    def _phase2_adx_filter(self, symbol: str, signal: dict, cycle_count: int, log_throttle: dict) -> bool:
        """Vérifie le seuil ADX avec bypass possible pour scores élevés."""
        signal_adx = signal.get("adx", 0)
        sym_cfg = self.symbol_limits.get(symbol, {})
        signal_score = signal.get("score", 0.6)

        ADX_BYPASS_MIN = 15
        if signal_score >= 0.80 and signal_adx >= ADX_BYPASS_MIN:
            logger.debug(f"  [ADX] {symbol}: bypass (score={signal_score:.2f} >= 0.80, ADX={signal_adx:.1f})")
            return True
        elif signal_score >= 0.80 and signal_adx < ADX_BYPASS_MIN:
            logger.info(f"  [ADX] {symbol}: bypass REFUSÉ (ADX={signal_adx:.1f} < {ADX_BYPASS_MIN})")
            return False
        else:
            regime = "RANGING" if signal_adx < 22 else signal.get("_regime", "RANGING")
            adx_thresh = sym_cfg.get("adx_thresh", 20)
            if regime in ("RANGING", "LOW_VOL"):
                adx_thresh = min(adx_thresh, 12)
            if signal_adx < adx_thresh:
                logger.info(f"  [ADX] {symbol}: ADX={signal_adx:.1f} < {adx_thresh} → skip")
                return False
        return True

    # ── Phase 3: Session Filter ────────────────────────────────────────────

    def _phase3_session_filter(self, symbol: str, signal: dict) -> bool:
        if self.session_filter:
            hour_utc = datetime.now(timezone.utc).hour
            session_score = self.session_filter.get_session_score(symbol, hour_utc)
            if session_score < 0.3:
                # Bypass pour signaux forts (score≥0.80 ET ADX≥15)
                # Même règle que DANGER_HOURS — un signal technique fort peut
                # trader en dehors des heures de liquidité optimales
                sig_score = signal.get("score", 0)
                sig_adx = signal.get("adx", 0)
                if sig_score >= 0.80 and sig_adx >= 15:
                    signal["session_score"] = session_score
                    signal["session_bypass"] = True
                    logger.debug(
                        f"  [SESSION] {symbol}: score {session_score:.2f} < 0.3 "
                        f"mais BYPASS pour signal fort (score={sig_score:.2f}, ADX={sig_adx:.0f})"
                    )
                    return True
                logger.debug(f"  [SESSION] {symbol}: score {session_score:.2f} < 0.3 → skip")
                return False
            signal["session_score"] = session_score
        return True

    # ── Phase 4: News Filter ──────────────────────────────────────────────

    def _phase4_news_filter(self, symbol: str) -> bool:
        news_blocked, news_reason = self.news_filter.is_news_blocked(symbol)
        if news_blocked:
            logger.debug(f"  [NEWS] {symbol}: {news_reason} → skip")
            return False
        return True

    # ── Phase 5: Direction = Régime Rule ──────────────────────────────────

    def _phase5_regime_rule(self, signal: dict) -> bool:
        """Évite les trades à contre-tendance (18 Juin 2026)."""
        regime = signal.get("_regime", "RANGING")
        action = signal.get("action", "BUY")
        if (action == "BUY" and regime == "TREND_DOWN") or (action == "SELL" and regime == "TREND_UP"):
            logger.debug(f"  [RÈGLE DIR] {signal.get('symbol')}: {action} en {regime} → contre-tendance, skip")
            return False
        return True

    # ── Phase 6: Strategy Selector ─────────────────────────────────────────

    def _phase6_strategy_selector(self, symbol: str, signal: dict) -> bool:
        regime = signal.get("_regime", "RANGING")
        action = signal.get("action", "BUY")
        signal_adx = signal.get("adx", 0)
        signal_score = signal.get("score", 0.6)

        adjusted_regime = self.strategy_selector.get_regime_for_signal(regime, action)
        strat_params = self.strategy_selector.get_params(
            symbol, adjusted_regime, adx=signal_adx, atr_pct=signal.get("atr_pct", 0.5)
        )
        should_trade, trade_reason = self.strategy_selector.should_trade(
            symbol, adjusted_regime, signal_score, signal_adx
        )
        if not should_trade:
            logger.debug(f"  [STRAT_SEL] {symbol}: {trade_reason} → skip")
            return False
        signal["strat_params"] = strat_params.to_dict() if hasattr(strat_params, "to_dict") else strat_params
        return True

    # ── Phase 7b: RVOL + CMF ────────────────────────────────────────────

    def _phase7b_rvol_cmf(self, symbol: str, signal: dict) -> bool:
        """Relative Volume (RVOL) + Chaikin Money Flow (CMF).

        RVOL < 0.5 → breakout sans volume → pénalité -15%
        RVOL > 2.0 → breakout avec volume fort → bonus +10%
        CMF > seuil → accumulation haussière → bonus BUY / pénalité SELL
        CMF < -seuil → distribution baissière → bonus SELL / pénalité BUY

        Les seuils CMF sont configurables par symbole (default.yaml).
        BTCUSD utilise 0.20 (volume crypto bursty), forex/indices 0.10.
        """
        try:
            tf = self.symbol_timeframes.get(symbol, "H1")
            rates = self._get_cached_rates(symbol, tf, count=100)
            if rates is None or len(rates) < 50:
                return True
            df = self._to_dataframe(rates)
            closes = df["close"].values
            volumes = df["volume"].values
            highs = df["high"].values
            lows = df["low"].values

            # ── RVOL ──
            rvol = relative_volume(volumes, period=50)
            if rvol < 0.5:
                signal["score"] = max(0.3, signal["score"] * 0.75)
                signal["rvol_adj"] = 0.75
                signal["rvol_note"] = "FAIBLE"
            elif rvol > 2.0:
                signal["score"] = min(0.95, signal["score"] * 1.10)
                signal["rvol_adj"] = 1.10
                signal["rvol_note"] = "FORT"
            else:
                signal["rvol_adj"] = 1.0
                signal["rvol_note"] = "normal"
            signal["rvol"] = round(rvol, 2)

            # ── CMF (seuil par symbole) ──
            sym_cfg = self.symbol_limits.get(symbol, {})
            cmf_threshold = sym_cfg.get("cmf_threshold", 0.10)
            cmf = chaikin_money_flow(closes, highs, lows, volumes, period=20)
            sig_action = signal.get("action", "BUY")
            if cmf > cmf_threshold:
                if sig_action == "BUY":
                    signal["score"] = min(0.95, signal["score"] * 1.08)
                else:
                    signal["score"] = max(0.3, signal["score"] * 0.85)
                signal["cmf_adj"] = 1.08 if sig_action == "BUY" else 0.85
                signal["cmf_note"] = "accumulation"
            elif cmf < -cmf_threshold:
                if sig_action == "SELL":
                    signal["score"] = min(0.95, signal["score"] * 1.08)
                else:
                    signal["score"] = max(0.3, signal["score"] * 0.85)
                signal["cmf_adj"] = 1.08 if sig_action == "SELL" else 0.85
                signal["cmf_note"] = "distribution"
            else:
                signal["cmf_adj"] = 1.0
                signal["cmf_note"] = "neutre"
            signal["cmf"] = round(cmf, 3)

        except Exception as e:
            logger.debug(f"  [VOL] {symbol}: erreur RVOL/CMF: {e}")
        return True

    # ── Phase 7: Volume Profile ────────────────────────────────────────────

    def _phase7_volume_profile(self, symbol: str, signal: dict) -> bool:
        try:
            tf_vp = self.symbol_timeframes.get(symbol, "H1")
            recent_vp = self._get_cached_rates(symbol, tf_vp, count=100)
            if recent_vp is not None and len(recent_vp) >= 50:
                df = self._to_dataframe(recent_vp)
                vp_levels = self.volume_profile.analyze(df)
                if vp_levels.poc is not None:
                    current_price = signal.get("entry_price", 0)
                    if current_price == 0:
                        tick = self.mt5.get_tick(symbol)
                        current_price = tick.ask if tick else 0
                    if current_price > 0:
                        dist_poc = abs(current_price - vp_levels.poc) / current_price * 100
                        if dist_poc < 0.1:
                            signal["score"] = min(0.95, signal["score"] * 1.1)
                            signal["vp_boost"] = "POC"
                        elif vp_levels.vah and current_price > vp_levels.vah * 0.999:
                            if signal.get("action") == "BUY":
                                signal["score"] *= 0.9
                                signal["vp_boost"] = "VAH_RESISTANCE"
                        elif vp_levels.val and current_price < vp_levels.val * 1.001:
                            if signal.get("action") == "SELL":
                                signal["score"] *= 0.9
                                signal["vp_boost"] = "VAL_SUPPORT"
                        signal["vp_poc"] = vp_levels.poc
                        signal["vp_vah"] = vp_levels.vah
                        signal["vp_val"] = vp_levels.val
        except Exception as e:
            logger.debug(f"  [VP] {symbol}: erreur VolumeProfile: {e}")
        return True

    # ── Phase 8: Order Flow ────────────────────────────────────────────────

    def _phase8_order_flow(self, symbol: str, signal: dict) -> bool:
        df = None
        try:
            flow_metrics = self.order_flow.analyze_ticks_from_mt5(self.mt5, symbol, count=1000)
            if flow_metrics is None or flow_metrics.total_volume == 0:
                tf_of = self.symbol_timeframes.get(symbol, "H1")
                recent_of = self._get_cached_rates(symbol, tf_of, count=100)
                if recent_of is not None and len(recent_of) >= 20:
                    df = self._to_dataframe(recent_of)
                    flow_metrics = self.order_flow.analyze_bars(df)
            if flow_metrics and flow_metrics.total_volume > 0:
                sig_action = signal.get("action", "BUY")
                flow_adj = self.order_flow.get_flow_adjustment(flow_metrics, sig_action)
                if flow_adj["score_adj"] != 1.0:
                    old_score = signal["score"]
                    signal["score"] = min(0.95, max(0.3, signal["score"] * flow_adj["score_adj"]))
                    signal["flow_adj"] = flow_adj["score_adj"]
                    signal["flow_divergence"] = flow_adj.get("divergence")
                    signal["flow_absorption"] = flow_adj.get("absorption")
                    if signal["score"] < self.cfg.MIN_SIGNAL_SCORE:
                        logger.debug(
                            f"  [FLOW] {symbol}: score {signal['score']:.3f} < {self.cfg.MIN_SIGNAL_SCORE} → skip"
                        )
                        return False
            # ── OBV Divergence (pénalités par symbole) ──
            # Utilise les mêmes barres que l'Order Flow (pas de get_rates() supplémentaire)
            # Les pénalités sont configurables par symbole (default.yaml).
            # BTCUSD: high=0.85, low=0.92 (volume crypto bursty → moins pénalisant)
            # Forex/indices: high=0.70, low=0.85 (standard)
            if "df" in locals() and df is not None and len(df) >= 20:
                sym_cfg_of = self.symbol_limits.get(symbol, {})
                obv_high = sym_cfg_of.get("obv_div_penalty_high", 0.70)
                obv_low = sym_cfg_of.get("obv_div_penalty_low", 0.85)
                closes_df = df["close"].values
                volumes_df = df["volume"].values
                div_type, div_strength = obv_divergence(closes_df, volumes_df, period=20)
                if div_type != "none":
                    penalty = obv_high if div_strength > 0.5 else obv_low
                    signal["score"] = max(0.3, signal["score"] * penalty)
                    signal["obv_divergence"] = div_type
                    signal["obv_div_strength"] = round(div_strength, 2)
        except Exception as e:
            logger.debug(f"  [FLOW] {symbol}: erreur OrderFlow: {e}")
        return True

    # ── Phase 9: MTF Confirmation ──────────────────────────────────────────

    def _phase9_mtf_confirm(self, symbol: str, signal: dict) -> bool:
        try:
            tf_signal = self.symbol_timeframes.get(symbol, "H1")
            higher_tfs = {"H1": "H4", "H4": "D1", "D1": "W1"}
            tf_higher = higher_tfs.get(tf_signal)
            if tf_higher:
                recent_higher = self.mt5.get_rates(symbol, tf_higher, count=100)
                if recent_higher is not None and len(recent_higher) >= 50:
                    df = self._to_dataframe(recent_higher)
                    mtf_confirmed, mtf_factor = self.mtf_confirm.confirm(None, df, signal.get("action", "BUY"))
                    if mtf_factor != 1.0:
                        old_score = signal["score"]
                        signal["score"] = max(0.3, min(0.95, signal["score"] * mtf_factor))
                        signal["mtf_factor"] = mtf_factor
        except Exception as e:
            logger.debug(f"  [MTF] {symbol}: erreur MTFConfirm: {e}")
        return True

    # ── Phase 10: Market Profile ───────────────────────────────────────────

    def _phase10_market_profile(self, symbol: str, signal: dict) -> bool:
        try:
            tf_mp = self.symbol_timeframes.get(symbol, "H1")
            mp_data = self._get_cached_rates(symbol, tf_mp, count=100)
            if mp_data is not None and len(mp_data) >= 10:
                df = self._to_dataframe(mp_data)
                mp_result = self.market_profile.analyze(df, signal_action=signal.get("action"))
                mp_adj = mp_result.get("score_adj", 1.0)
                if mp_adj != 1.0:
                    old_score = signal["score"]
                    signal["score"] = max(0.3, min(0.95, signal["score"] * mp_adj))
                    signal["mp_adj"] = mp_adj
                    signal["mp_session_type"] = mp_result.get("session_type")
                    if signal["score"] < self.cfg.MIN_SIGNAL_SCORE:
                        logger.debug(
                            f"  [MP] {symbol}: score {signal['score']:.3f} < {self.cfg.MIN_SIGNAL_SCORE} → skip"
                        )
                        return False
        except Exception as e:
            logger.debug(f"  [MP] {symbol}: erreur MarketProfile: {e}")
        return True

    # ── Phase 11: VWAP Premium/Discount ────────────────────────────────────

    def _phase11_vwap(self, symbol: str, signal: dict) -> bool:
        try:
            tf_vwap = self.symbol_timeframes.get(symbol, "H1")
            vwap_data = self._get_cached_rates(symbol, tf_vwap, count=100)
            if vwap_data is not None and len(vwap_data) >= 20:
                df = self._to_dataframe(vwap_data)
                vwap_result = self.vwap_analyzer.analyze(df)
                vwap_adj = vwap_result.get("score_adj", 1.0)
                if vwap_adj != 1.0:
                    old_score = signal["score"]
                    signal["score"] = max(0.3, min(0.95, signal["score"] * vwap_adj))
                    signal["vwap_adj"] = vwap_adj
                    signal["vwap_zone"] = vwap_result.get("zone")
                    if signal["score"] < self.cfg.MIN_SIGNAL_SCORE:
                        logger.debug(
                            f"  [VWAP] {symbol}: score {signal['score']:.3f} < {self.cfg.MIN_SIGNAL_SCORE} → skip"
                        )
                        return False
        except Exception as e:
            logger.debug(f"  [VWAP] {symbol}: erreur VWAPAnalyzer: {e}")
        return True

    # ── Phase 12: Adaptive Params ──────────────────────────────────────────

    def _phase12_adaptive_params(self, symbol: str, signal: dict) -> None:
        try:
            from engine_simple.adaptive_params import AdaptiveParameters

            if symbol not in self._adaptive_params:
                self._adaptive_params[symbol] = AdaptiveParameters(symbol)
            ap = self._adaptive_params[symbol]
            adapted = ap.get_adapted_params()
            if adapted.sample_size >= 20:
                current_rm = signal.get("risk_mult", 1.0)
                signal["risk_mult"] = current_rm * adapted.risk_mult
                signal["adaptive_params"] = adapted.to_dict()
        except Exception as e:
            logger.debug(f"  [ADAPTIVE] {symbol}: erreur: {e}")

    # ── Phase 13: Feature Scoring ─────────────────────────────────────────

    def _phase13_feature_scoring(self, symbol: str, signal: dict) -> None:
        """Calcule 25+ features et ajuste le score avec des bonus/pénalités.

        Les features sont persistées dans le signal pour :
        - Diagnostic (logs)
        - Futures phases (Phase 14: LightGBM)
        - Training data collection

        Facteurs (issus de feature_pipeline.compute_score_adjustment):
          EMA alignment      : +8% si tendance confirme, -15% si contre-tendance
          ATR percentile     : -10% si >85%, +5% si <15%
          Vol expansion      : -12% si >30%
          RVOL               : +10% si >2.0, -15% si <0.5
          CMF                : +8% si confirme, -8% si contredit
          VWAP discount      : +6% si prix sous VWAP en BUY
          OBV divergence     : +8% si confirme, -15% si contredit
          Sessions           : +5% London-NY overlap, -5% Asia
          Spread percentile  : -12% si >80%
          Range position     : -8% si contre-intuitif
        """
        try:
            tf = self.symbol_timeframes.get(symbol, "H1")
            rates = self._get_cached_rates(symbol, tf, count=250)
            if rates is None or len(rates) < 50:
                return

            df = self._to_dataframe(rates)
            close = df["close"].values.astype(float)
            high = df["high"].values.astype(float)
            low = df["low"].values.astype(float)
            volume = df["volume"].values.astype(float) if "volume" in df.columns else None

            # Spread history (collecté au fil de l'eau depuis les ticks)
            spread = None
            try:
                tick = self.mt5.get_tick(symbol)
                if tick:
                    spread = getattr(tick, "spread", None) or 0
            except Exception:
                pass

            # Calcul des features
            features = compute_all_features(
                close=close,
                high=high,
                low=low,
                volume=volume,
                spread=spread,
                symbol=symbol,
            )

            # Ajustement du score
            action = signal.get("action", "BUY")
            adj, reasons = compute_score_adjustment(features, action)

            # Appliquer l'ajustement
            old_score = signal.get("score", 0.6)
            new_score = max(0.30, min(0.99, old_score * adj))
            signal["score"] = new_score
            signal["feature_adj"] = round(adj, 3)
            signal["feature_reasons"] = reasons
            signal["feature_count"] = len(features)

            # Persister les features pour le diagnostic
            signal["_features"] = {
                k: round(v, 4) if isinstance(v, float) else v for k, v in features.items() if k not in ("_features",)
            }

            if adj != 1.0:
                logger.info(
                    f"  [FEATURES] {symbol}: adj={adj:.3f} score {old_score:.3f}→{new_score:.3f} "
                    f"| {len(reasons)} raisons"
                )

        except Exception as e:
            logger.debug(f"  [FEATURES] {symbol}: erreur feature scoring: {e}")

    # ── Phase 14: LightGBM ajuste score et risque ──────────────────────────

    def _phase14_lgb_scoring(self, symbol: str, signal: dict) -> None:
        """LightGBM ajuste le score et le risk_mult selon sa prédiction.

        Les features sont déjà calculées par phase13. LGB prédit la probabilité
        de succès du trade. Selon la confiance et l'accord avec MOM20x3 :

          Agree + conf>0.3 → bonus score (+12% max) + risk_mult (+15% max)
          Disagree + conf>0.3 → pénalité score (-20% max) + risk_mult (-25% max)
          Confiance < 0.3 → aucun changement

        Seuil de confiance 0.3 ≈ proba > 0.65 ou < 0.35.
        """
        lgb = getattr(self.adaptive, "lgb", None)
        if lgb is None or not lgb.available:
            return

        features = signal.get("_features", {})
        if len(features) < 10:
            return

        try:
            lgb_result = lgb.predict(features)
            # Valider que le résultat est un vrai dict (pas un mock de test)
            if not isinstance(lgb_result, dict):
                logger.debug(f"  [LGB] {symbol}: predict returned {type(lgb_result).__name__}, skip")
                return
        except Exception as e:
            logger.debug(f"  [LGB] {symbol}: predict error: {e}")
            return

        proba = float(lgb_result.get("probability", 0.5))
        confidence = float(lgb_result.get("confidence", 0.0))
        lgb_action = str(lgb_result.get("action", "HOLD"))
        mom_action = str(signal.get("action", "HOLD"))
        agrees = lgb_action == mom_action

        # Stocker les métadonnées LGB dans le signal (pour logs, tracking, retraining)
        signal["_lgb_score"] = proba
        signal["_lgb_action"] = lgb_action
        signal["_lgb_agrees"] = agrees

        if confidence < 0.3:
            signal["_lgb_impact"] = f"neutre (conf={confidence:.2f} < 0.3)"
            return

        if agrees:
            # ✅ LGB confirme MOM20x3 → bonus score + risque
            score_bonus = confidence * 0.12  # max +12%
            risk_bonus = 1.0 + confidence * 0.15  # max +15%
            old_score = signal.get("score", 0.5)
            signal["score"] = min(0.99, old_score * (1 + score_bonus))
            signal["risk_mult"] = signal.get("risk_mult", 1.0) * risk_bonus
            signal["_lgb_impact"] = (
                f"bonus score+{score_bonus:.1%} risk×{risk_bonus:.2f} (agree, conf={confidence:.2f}, proba={proba:.3f})"
            )
            logger.info(
                f"  [LGB] {symbol}: ✅ agree → score {old_score:.3f}→{signal['score']:.3f}, "
                f"risk×{risk_bonus:.2f} (conf={confidence:.2f}, proba={proba:.3f})"
            )
        else:
            # ❌ LGB contredit MOM20x3 → pénalité score + risque
            score_penalty = confidence * 0.20  # max -20%
            risk_penalty = 1.0 - confidence * 0.25  # max -25%, floor 0.5
            old_score = signal.get("score", 0.5)
            signal["score"] = max(0.30, old_score * (1 - score_penalty))
            signal["risk_mult"] = signal.get("risk_mult", 1.0) * max(0.5, risk_penalty)
            signal["_lgb_impact"] = (
                f"penalty score-{score_penalty:.1%} risk×{risk_penalty:.2f} "
                f"(disagree, conf={confidence:.2f}, proba={proba:.3f})"
            )
            logger.info(
                f"  [LGB] {symbol}: ❌ disagree → score {old_score:.3f}→{signal['score']:.3f}, "
                f"risk×{risk_penalty:.2f} (conf={confidence:.2f}, proba={proba:.3f})"
            )
