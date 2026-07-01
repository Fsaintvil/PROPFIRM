"""Signal Pipeline — filtrage simplifié des signaux MOM20x3.

Simplifié le 25 Juin 2026 — retrait des couches qui tuaient le signal :
  - Phase 8  (Order Flow)      → toujours fallback, jamais de ticks réels
  - Phase 10 (Market Profile)  → bloquait les signaux (seuil 0.7)
  - Phase 11 (VWAP)            → complexité inutile
  - Phase 13 (Feature Scoring) → adj ×0.727 sur XAUUSD, massacre les scores
  - Phase 14 (LightGBM)        → déjà désactivé, code mort

Flux simplifié:
  process(symbol) → SignalResult | None
    ├── phase1_mom20x3()         ← signal brut MOM20x3
    ├── phase2_adx_filter()      ← ADX threshold + bypass
    ├── phase3_session_filter()  ← session active ?
    ├── phase4_news_filter()     ← news économique ?
    ├── phase5_regime_rule()     ← direction = régime ?
    ├── phase6_strategy_selector() ← params par régime
    ├── phase7_volume_profile()  ← POC/VAH/VAL
    ├── phase7b_rvol_cmf()       ← RVOL + Chaikin Money Flow
    ├── phase9_mtf_confirm()     ← TF supérieure
    └── phase12_adaptive_params() ← risk_mult adaptatif
"""

import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone
import time

import pandas as _pd
import numpy as np

from engine_simple.indicators import chaikin_money_flow, obv_divergence, relative_volume

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
        mtf_confirm,
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
        self.mtf_confirm = mtf_confirm
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
            reasons = {c["rule"]: c["reason"] for c in pre_checks if not c["pass"]}
            logger.debug(f"  [PRECHECK] {symbol}: echec {failed}, reasons={reasons}")
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

        # Phase 7b: RVOL + CMF
        if not self._phase7b_rvol_cmf(symbol, signal):
            return None

        # Phase 7c: OBV Divergence
        self._phase7c_obv_divergence(symbol, signal)

        # Phase 9: MTF Confirmation
        if not self._phase9_mtf_confirm(symbol, signal):
            return None

        # Phase 12: Adaptive Params
        self._phase12_adaptive_params(symbol, signal)

        # Dynamic position limits based on confidence (simplifié 1er Juillet 2026)
        sig_conf = signal.get("confidence", 0.0)
        sig_action = signal.get("action")
        HIGH_CONF_CONFIDENCE = 0.85  # seuil unique haute confiance

        if sig_conf >= HIGH_CONF_CONFIDENCE:
            # 🔥 HIGH CONFIDENCE : positions supplémentaires autorisées
            # mais corrélation et limites totales protégées par portfolio_controller
            signal["high_confidence"] = True
            max_per_symbol = 3  # max 3 positions/symbole en haute confiance
            signal["max_per_symbol"] = max_per_symbol
            logger.debug(f"  [HIGH CONF] {symbol} {sig_action} conf={sig_conf:.2f} — cap={max_per_symbol}/symbole")
        else:
            if sig_conf > 0.85:
                max_per_symbol = 3
            elif sig_conf > 0.70:
                max_per_symbol = 2
            else:
                max_per_symbol = 1
            hard_limit = config_limits.get(symbol, 4)
            max_per_symbol = min(max_per_symbol, hard_limit)
            signal["max_per_symbol"] = max_per_symbol

            # Vérifier la limite dans la direction du signal
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

        # 🐛 FIX 26 Juin 2026: utiliser signal["score"] (modifié par les phases)
        # au lieu du score capturé à l'entrée (ligne 147) qui n'était jamais mis à jour.
        # Cause du bug : toutes les phases modifient signal["score"] in-place mais
        # le score retourné dans SignalResult restait celui d'origine, créant un
        # décalage entre result.score (tri) et signal["score"] (check FTMO).
        # Log signal final APRÈS toutes les phases (y compris pénalités RVOL/CMF/OBV/VP/MTF)
        logger.debug(
            f"  [SIGNAL] {symbol}: score={signal.get('score', 0):.2f}, "
            f"conf={signal.get('confidence', 0):.2f}, action={signal.get('action', '?')}, "
            f"strat={signal.get('details', '?')}, "
            f"rvol_adj={signal.get('rvol_adj', 1.0):.2f} "
            f"cmf_adj={signal.get('cmf_adj', 1.0):.2f} "
            f"risk_mult={signal.get('risk_mult', 1.0):.2f}"
        )

        score = signal.get("score", score)
        return SignalResult(symbol=symbol, signal=signal, score=score)

    # ── Phase 1: MOM20x3 Signal ───────────────────────────────────────────

    def _phase1_mom20x3(self, symbol: str) -> dict | None:
        """Génère le signal MOM20x3 avec paramètres OnlineLearner."""
        from engine_simple.strategy import MOM20x3, SYMBOL_CONFIG as _SYMBOL_CFG

        tf = self.symbol_timeframes.get(symbol, "H1")

        # OnlineLearner params — réactivé 25 Juin 2026 (calibration fixée)
        ol_thresh_trending = None
        ol_thresh_ranging = None
        ol_risk_mult = 0.75  # fallback si OL indisponible
        try:
            ol_params = self.adaptive.learner.get_params(symbol, base_thresh=2.5)
            ol_thresh = ol_params.get("thresh", 2.5)
            ol_risk_mult = ol_params.get("risk_mult", 1.0)
            # Appliquer le seuil OL si disponible, avec bornes sécurité [1.5, 2.5]
            if ol_thresh is not None:
                ol_thresh_clamped = max(1.5, min(2.0, ol_thresh))  # cap à 2.0 (↓ 2.5 pour + de trades)
                ol_thresh_trending = ol_thresh_clamped
                ol_thresh_ranging = max(1.5, ol_thresh_clamped - 0.5)
                logger.debug(
                    f"  [OL] {symbol}: thresh={ol_thresh_clamped}, risk_mult={ol_risk_mult} "
                    f"(OL→trending={ol_thresh_trending}, ranging={ol_thresh_ranging})"
                )
        except Exception as e:
            logger.warning(f"  [SIGNAL_PIPELINE] phase1_mom20x3 OL: {e}")
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
        except Exception as e:
            logger.warning(f"  [SIGNAL_PIPELINE] phase1_mom20x3 higher_tf: {e}")
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
            except Exception as e:
                logger.warning(f"  [SIGNAL_PIPELINE] phase1_mom20x3 mtf_alignment: {e}")
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

        # Per-symbol risk_mult (base × OL risk_mult)
        # 🟢 Réactivé 25 Juin 2026: calibration_state.json séparé et fonctionnel.
        # OL risk_mult s'adapte au WR :
        #   WR<70% → ×0.75, WR 78-82% → ×1.05, WR>82% → ×1.15
        #   expectancy<0 → ×0.5 (protection)
        symbol_config = self.symbol_limits.get(symbol, {})
        base_risk_mult = symbol_config.get("risk_mult", 1.0)
        # ol_risk_mult est capturé dans le bloc OL ci-dessus (fallback=0.75 si exception)
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
        except Exception as e:
            logger.warning(f"  [SIGNAL_PIPELINE] phase1_mom20x3 rsi: {e}")
            signal["rsi"] = 50.0

        return signal

    # ── Phase 2: ADX Threshold Filter ─────────────────────────────────────

    def _phase2_adx_filter(self, symbol: str, signal: dict, cycle_count: int, log_throttle: dict) -> bool:
        """Vérifie le seuil ADX avec bypass possible pour scores élevés."""
        signal_adx = signal.get("adx", 0)
        sym_cfg = self.symbol_limits.get(symbol, {})
        signal_score = signal.get("score", 0.6)

        ADX_BYPASS_MIN = 10  # Juil 2026: réduit 12→10 pour US100/US500/NZDUSD (ADX 10-11, scores ≥0.80)
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

    # ── Phase 3: Session Filter — RETIRÉ 26 Juin 2026 ────────────────────
    # Le module session_filter.py a été déplacé dans retired/ car il utilisait
    # des horaires fixes qui ne correspondaient pas aux symboles 24/7.
    # Les heures dangereuses (12:00 UTC) sont gérées par DANGER_HOURS dans
    # main.py et ftmo_protector.py.
    # Le champ self.session_filter est toujours None (main.py:346).

    def _phase3_session_filter(self, symbol: str, signal: dict) -> bool:
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
        """Évite les trades à contre-tendance (18 Juin 2026).
        Override pour signaux très forts (score≥0.90) avec risque réduit de 50%."""
        regime = signal.get("_regime", "RANGING")
        action = signal.get("action")
        symbol = signal.get("symbol", "?")
        score = signal.get("score", 0)
        if (action == "BUY" and regime == "TREND_DOWN") or (action == "SELL" and regime == "TREND_UP"):
            # 🔥 OVERRIDE pour signaux TRÈS FORTS (score ≥ 0.90)
            if score >= 0.90:
                logger.info(
                    f"  [RÈGLE DIR] OVERRIDE: {action} {symbol} en {regime} (score={score:.2f}≥0.90) — risque -50%"
                )
                signal["risk_mult"] = signal.get("risk_mult", 1.0) * 0.50
                return True
            logger.debug(f"  [RÈGLE DIR] {symbol}: {action} en {regime} → contre-tendance, skip")
            return False
        return True

    # ── Phase 6: Strategy Selector ─────────────────────────────────────────

    def _phase6_strategy_selector(self, symbol: str, signal: dict) -> bool:
        regime = signal.get("_regime", "RANGING")
        action = signal.get("action")
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
                signal["score"] = max(0.3, signal["score"] * 0.92)
                signal["rvol_adj"] = 0.92
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
            sig_action = signal.get("action")
            if cmf > cmf_threshold:
                if sig_action == "BUY":
                    signal["score"] = min(0.95, signal["score"] * 1.08)
                else:
                    signal["score"] = max(0.3, signal["score"] * 0.92)
                signal["cmf_adj"] = 1.08 if sig_action == "BUY" else 0.92
                signal["cmf_note"] = "accumulation"
            elif cmf < -cmf_threshold:
                if sig_action == "SELL":
                    signal["score"] = min(0.95, signal["score"] * 1.08)
                else:
                    signal["score"] = max(0.3, signal["score"] * 0.92)
                signal["cmf_adj"] = 1.08 if sig_action == "SELL" else 0.92
                signal["cmf_note"] = "distribution"
            else:
                signal["cmf_adj"] = 1.0
                signal["cmf_note"] = "neutre"
            signal["cmf"] = round(cmf, 3)

        except Exception as e:
            logger.debug(f"  [VOL] {symbol}: erreur RVOL/CMF: {e}")
        return True

    # ── Phase 7c: OBV Divergence ──────────────────────────────────────────

    def _phase7c_obv_divergence(self, symbol: str, signal: dict) -> None:
        """OBV Divergence — conflit prix/volume.

        Détecte les divergences entre la tendance prix et l'OBV (On-Balance Volume).
        - OBV bullish divergence (prix baisse, OBV monte) → accumulation cachée
        - OBV bearish divergence (prix monte, OBV baisse) → distribution cachée

        Les pénalités sont configurables par symbole dans default.yaml.
        BTCUSD utilise 0.85/0.92 (volume crypto moins fiable), forex 0.70/0.85.
        """
        try:
            tf = self.symbol_timeframes.get(symbol, "H1")
            rates = self._get_cached_rates(symbol, tf, count=100)
            if rates is None or len(rates) < 50:
                return
            df = self._to_dataframe(rates)
            closes = df["close"].values
            volumes = df["volume"].values

            div_type, div_strength = obv_divergence(closes, volumes, period=20)

            sym_cfg = self.symbol_limits.get(symbol, {})
            penalty_high = sym_cfg.get("obv_div_penalty_high", 0.70)
            penalty_low = sym_cfg.get("obv_div_penalty_low", 0.85)
            sig_action = signal.get("action")

            if div_type != "none" and div_strength > 0.1:
                direction_ok = (div_type == "bullish" and sig_action == "BUY") or (
                    div_type == "bearish" and sig_action == "SELL"
                )
                if direction_ok:
                    # Divergence dans la même direction → bonus léger
                    signal["score"] = min(0.95, signal["score"] * 1.05)
                    signal["obv_div"] = div_type
                    signal["obv_strength"] = round(div_strength, 3)
                    signal["obv_note"] = "confirms"
                else:
                    # Divergence en conflit → pénalité
                    penalty = penalty_low if div_strength < 0.5 else penalty_high
                    signal["score"] = max(0.3, signal["score"] * penalty)
                    signal["obv_div"] = div_type
                    signal["obv_strength"] = round(div_strength, 3)
                    signal["obv_note"] = f"conflict_penalty={penalty:.2f}"
            else:
                signal["obv_div"] = "none"
                signal["obv_strength"] = 0.0
                signal["obv_note"] = "none"
        except Exception as e:
            logger.debug(f"  [OBV] {symbol}: erreur OBV Divergence: {e}")
            signal["obv_div"] = "none"
            signal["obv_strength"] = 0.0
            signal["obv_note"] = "error"

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
                    mtf_confirmed, mtf_factor = self.mtf_confirm.confirm(None, df, signal.get("action"))
                    if mtf_factor != 1.0:
                        old_score = signal["score"]
                        signal["score"] = max(0.3, min(0.95, signal["score"] * mtf_factor))
                        signal["mtf_factor"] = mtf_factor
        except Exception as e:
            logger.debug(f"  [MTF] {symbol}: erreur MTFConfirm: {e}")
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

    # ── Phase 13+14: Feature Scoring + LightGBM — RETIRÉES 25 Juin 2026
    # Ces phases massacraient les signaux (adj ×0.727 sur XAUUSD, feature pipeline
    # qui n'avait pas assez de données, LGB jamais entraîné). Le code est conservé
    # dans l'historique git (commit 7eab317f6^).
