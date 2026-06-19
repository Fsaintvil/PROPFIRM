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
    ├── phase8_order_flow()      ← tick delta, divergence
    ├── phase9_mtf_confirm()     ← TF supérieure
    ├── phase10_market_profile() ← Initial Balance
    ├── phase11_vwap()           ← Premium/Discount zones
    └── phase12_adaptive_params() ← risk_mult adaptatif
"""

import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone

import pandas as _pd
import numpy as np

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

        # Phase 6: Strategy selector
        if not self._phase6_strategy_selector(symbol, signal):
            return None

        # Phase 7: Volume Profile
        if not self._phase7_volume_profile(symbol, signal):
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

        # Dynamic position limits based on confidence
        # Nouveaux seuils (Juin 2026):  conf > 0.90 → 4, > 0.80 → 3, > 0.70 → 2, sinon → 1
        sig_conf = signal.get("confidence", 0.0)
        if sig_conf > 0.90:
            max_per_symbol = 4
        elif sig_conf > 0.80:
            max_per_symbol = 3
        elif sig_conf > 0.70:
            max_per_symbol = 2
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
            if ol_thresh < base_trending:
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

        # Per-symbol risk_mult (base × OL)
        symbol_config = self.symbol_limits.get(symbol, {})
        base_risk_mult = symbol_config.get("risk_mult", 1.0)
        ol_risk_mult = 1.0
        try:
            ol_params = self.adaptive.learner.get_params(symbol, base_thresh=2.5)
            ol_risk_mult = ol_params.get("risk_mult", 1.0)
        except Exception:
            pass
        signal["risk_mult"] = base_risk_mult * ol_risk_mult
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

    # ── Phase 7: Volume Profile ────────────────────────────────────────────

    def _phase7_volume_profile(self, symbol: str, signal: dict) -> bool:
        try:
            tf_vp = self.symbol_timeframes.get(symbol, "H1")
            recent_vp = self.mt5.get_rates(symbol, tf_vp, count=100)
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
        try:
            flow_metrics = self.order_flow.analyze_ticks_from_mt5(self.mt5, symbol, count=1000)
            if flow_metrics is None or flow_metrics.total_volume == 0:
                tf_of = self.symbol_timeframes.get(symbol, "H1")
                recent_of = self.mt5.get_rates(symbol, tf_of, count=100)
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
            mp_data = self.mt5.get_rates(symbol, tf_mp, count=100)
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
            vwap_data = self.mt5.get_rates(symbol, tf_vwap, count=100)
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
