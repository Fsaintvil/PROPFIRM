"""Signal Pipeline — filtrage simplifié des signaux MOM20x3.

Simplifié le 25 Juin 2026 — retrait des couches qui tuaient le signal :
  - Phase 8  (Order Flow)      → toujours fallback, jamais de ticks réels
  - Phase 10 (Market Profile)  → bloquait les signaux (seuil 0.7)
  - Phase 11 (VWAP)            → complexité inutile
  - Phase 13 (Feature Scoring) → adj ×0.727 sur XAUUSD, massacre les scores
  - Phase 14 (LightGBM)        → déjà désactivé, code mort

Flux simplifié:
  process(symbol) → SignalResult | None
    ├── phase1_primary_strategy()  ← signal selon Strategy Registry (MOM20x3 par défaut)
    ├── phase2_adx_filter()      ← ADX threshold + bypass
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
        news_filter,
        strategy_selector,
        volume_profile,
        mtf_confirm,
        risk_manager,
        config,
        symbol_limits,
        symbol_timeframes,
        symbol_execution_timeframes=None,
    ):
        self.mt5 = mt5
        self.ftmo = ftmo
        self.adaptive = adaptive
        self.news_filter = news_filter
        self.strategy_selector = strategy_selector
        self.volume_profile = volume_profile
        self.mtf_confirm = mtf_confirm
        self.risk_manager = risk_manager
        self.cfg = config
        self.symbol_limits = symbol_limits
        self.symbol_timeframes = symbol_timeframes
        self.symbol_execution_timeframes = symbol_execution_timeframes or {}
        # Cache des AdaptiveParameters par symbole
        self._adaptive_params: dict = {}
        # Cache get_rates() par (symbole, timeframe, count) — évite appels MT5 redondants
        # 🔧 FIX 16 Juillet 2026: Limite à 200 entrées + purge_expired périodique
        # Sans limite, le cache pouvait accumuler des centaines d'entrées (27 symboles × 3 TF × 3 counts = 243+)
        self._rates_cache: dict[tuple[str, str, int], tuple[float, object]] = {}
        self.RATES_CACHE_TTL = 10  # secondes
        self._rates_cache_max_size = 200
        self._last_rates_purge = 0.0
        self._rates_purge_interval = 300  # secondes (5min)

    def _get_cached_rates(self, symbol: str, tf: str, count: int = 100):
        """get_rates() avec cache TTL pour éviter les appels MT5 redondants.

        Cache par (symbol, tf) avec invalidation après RATES_CACHE_TTL secondes.
        Les appels avec count différent sont traités séparément (clé inclut count).

        🔧 FIX 16 Juillet 2026: Purge périodique + limite de taille (200 entrées max)
        pour éviter l'accumulation mémoire (27 symboles × 3 TF × 3 counts = 243+ clés).
        """
        key = (symbol, tf, count)
        now = time.time()

        # Purge périodique des entrées expirées (toutes les 5 min)
        if now - self._last_rates_purge > self._rates_purge_interval:
            expired_keys = [k for k, v in self._rates_cache.items() if (now - v[0]) >= self.RATES_CACHE_TTL * 2]
            for k in expired_keys:
                del self._rates_cache[k]
            self._last_rates_purge = now

        # LRU-like eviction si le cache dépasse la taille max
        if len(self._rates_cache) >= self._rates_cache_max_size:
            # Supprimer les 25% les plus vieux (basé sur timestamp d'insertion)
            sorted_keys = sorted(self._rates_cache.keys(), key=lambda k: self._rates_cache[k][0])
            evict_count = max(1, len(self._rates_cache) // 4)
            for k in sorted_keys[:evict_count]:
                del self._rates_cache[k]

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

        # Phase 1a: Signal primaire selon Strategy Registry (MOM20x3 par défaut)
        signal = self._phase1_primary_strategy(symbol)

        # Phase 1b: MeanReversion fallback (si stratégie primaire échoue ET marché en RANGING)
        if signal is None:
            signal = self._generate_mr_signal(symbol)
            if signal is not None:
                logger.debug(f"  [MR FALLBACK] {symbol}: stratégie primaire échouée, MeanReversion pris")

        if signal is None:
            return None

        score = signal.get("score", 0.6)

        # Degraded mode
        if symbol in degraded_symbols:
            signal["_degraded"] = True
            logger.debug(f"  [DEGRADED] {symbol}: mode dégradé actif → lot minimum")

        # Phase 1d: H4 Direction Filter (multi-TF professionnel)
        # Vérifie l'alignement du signal H1 avec la tendance H4.
        if not self._phase1d_h4_direction_filter(symbol, signal):
            return None

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

        # 🔧 FIX 22 Juillet 2026: Bypass central unique — remplace les 3 anciens bypass
        # Un signal très fort (score ≥ 0.90 + raw_mom ≥ 0.85) passe à travers
        # les limites de position. Le bypass est UNIQUE et centralisé, contrairement
        # aux 3 anciens bypass dispersés (ADX, VP, MTF) qui créaient une passoire.
        signal_score = signal.get("score", 0.0)
        raw_mom = signal.get("_raw_mom_score", 0.0)
        if signal_score >= 0.90 and raw_mom >= 0.85:
            signal["_central_bypass"] = True
            logger.debug(f"  [BYPASS] {symbol}: central bypass (score={signal_score:.2f}, raw_mom={raw_mom:.2f})")
        else:
            signal["_central_bypass"] = False

        # Dynamic position limits based on confidence (simplifié 1er Juillet 2026)
        # 🔧 FIX 22 Juillet 2026: central bypass override — très fort signal contourne les limites
        if signal.get("_central_bypass", False):
            signal["high_confidence"] = True
            signal["max_per_symbol"] = 4
            logger.debug(
                f"  [BYPASS] {symbol} {signal.get('action', '?')}: central bypass actif "
                f"— limite contournée (max=4/symbole)"
            )
        else:
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
                # sig_conf < 0.85 (else branch du if >= 0.85)
                if sig_conf > 0.70:
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
                hard_limit = config_limits.get(symbol, 4)
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

        # Phase finale: M15 Confirmation (exécution de précision)
        # La dernière bougie M15 fermée doit confirmer la direction du signal.
        # Si pas de confirmation → attendre le prochain cycle (15s).
        if not self._check_m15_confirmation(symbol, signal):
            return None

        # 🔧 RÉTRACTÉ 28 Juillet 2026: Le fingerprint via last_signals était trop agressif.
        # Il bloquait 100% des signaux car les prix ne changent pas assez entre cycles de 15s.
        # Les 3 barrières anti-doublon existantes suffisent :
        #   1. portfolio_controller: MAX_POSITIONS_PER_SYMBOL_PER_DIRECTION = 1
        #   2. trade_executor._recent_trades: fingerprint + timestamp
        #   3. sym_total_counts: mise à jour après chaque trade
        score = signal.get("score", score)
        return SignalResult(symbol=symbol, signal=signal, score=score)

    # ── Phase 1: Primary Strategy (Strategy Registry) ─────────────────────

    def _phase1_primary_strategy(self, symbol: str) -> dict | None:
        """Génère le signal selon la stratégie assignée au symbole (Strategy Registry).

        Délègue à la méthode appropriée selon la stratégie configurée pour ce symbole.
        Par défaut: MOM20x3. Nouveautés: TrendFollow, MeanReversion (via fallback).
        """
        from engine_simple.strategy_registry import get_strategy_for

        strategy_name = get_strategy_for(symbol)
        logger.debug(f"  [STRATEGY] {symbol}: dispatch → {strategy_name}")

        if strategy_name == "MOM20x3":
            return self._phase1_mom20x3(symbol)
        elif strategy_name == "TrendFollow":
            return self._execute_trend_follow(symbol)
        else:
            logger.warning(f"  [STRATEGY] {symbol}: stratégie '{strategy_name}' inconnue, fallback MOM20x3")
            return self._phase1_mom20x3(symbol)

    def _execute_trend_follow(self, symbol: str) -> dict | None:
        """Exécute la stratégie TrendFollow pour un symbole.

        Appelée par _phase1_primary_strategy() quand le Strategy Registry
        assigne "TrendFollow" au symbole.

        TrendFollow est un suivi de tendance basé sur EMA50 + ADX :
        - N'entre QUE si ADX ≥ 22 (tendance)
        - SL large (2.5×ATR), TP large (6.0×ATR)
        - Pas de trades en RANGING
        """
        from engine_simple.strategy_trend_follow import TrendFollow
        from engine_simple.strategy import SYMBOL_CONFIG as _SYMBOL_CFG

        tf = self.symbol_timeframes.get(symbol, "H1")

        # OnlineLearner params (pour risk_mult seulement — pas de thresh tuning)
        ol_risk_mult = 0.75
        try:
            ol_params = self.adaptive.learner.get_params(symbol, base_thresh=2.5)
            ol_risk_mult = ol_params.get("risk_mult", 1.0)
        except Exception:
            pass

        rates_tf = self._get_cached_rates(symbol, tf, count=200)
        if rates_tf is None or len(rates_tf) < 70:  # besoin d'au moins EMA50 + marge
            logger.debug(f"  [TF] {symbol}: rates {tf} insuffisantes ({0 if rates_tf is None else len(rates_tf)} bars)")
            return None

        tf_strat = TrendFollow(rates_tf, symbol)
        raw = tf_strat.analyze()
        if raw is None:
            return None

        # Higher TF confirmation
        h4_conf = 1.0
        higher_tf = "D1" if tf == "H4" else "H4"
        try:
            higher_cached = self._get_cached_rates(symbol, higher_tf, count=60)
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

        # Enrich signal
        tick = self.mt5.get_tick(symbol)
        entry = tick.ask if tick else 0
        signal = dict(raw)
        signal["_raw_mom_score"] = signal.get("score", 0.6)
        signal["symbol"] = symbol
        signal["timeframe"] = tf
        signal["details"] = f"TrendFollow_{tf}"
        signal["quality"] = min(1.0, (signal.get("confidence", 0.5) + 0.1) * h4_conf)
        if h4_conf < 1.0 and signal.get("score", 0.6) > 0.5:
            signal["score"] = max(0.5, signal["score"] * 0.90)

        # 🔓 FIX 8 Juillet: soft block SUPPRIMÉ — l'OL peut maintenant
        # AUGMENTER le risk_mult (ex: WR>82% → risk_mult=1.15).
        # Avant: static_risk_mult = 1.0 écrasait ol_risk_mult=1.15 → effet OL nul.
        # Maintenant: effective = static × ol (OL peut monter ET descendre).
        from engine_simple.symbol_params import get_symbol_param

        static_risk_mult = get_symbol_param(symbol, "risk_mult", 1.0)
        effective_risk_mult = static_risk_mult * ol_risk_mult
        signal["risk_mult"] = effective_risk_mult
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

        logger.debug(
            f"  [TRENDFOLLOW] {signal['action']} {symbol} | "
            f"score={signal['score']:.2f} conf={signal['confidence']:.2f} "
            f"ADX={signal.get('adx', 0):.1f} EMA50_slope={signal.get('ema50_slope', 0):+.2%} "
            f"risk_mult={signal['risk_mult']:.2f}"
        )

        return signal

    def _phase1_mom20x3(self, symbol: str) -> dict | None:
        """Exécute la stratégie MOM20x3 pour un symbole.

        Appelée par _phase1_primary_strategy() quand le Strategy Registry
        assigne "MOM20x3" au symbole.
        NOTE: Garde le nom _phase1_mom20x3 pour compatibilité ascendante.
        """
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
            # 🔓 FIX 8 Juillet: supprimé le cap supérieur à 2.0 qui empêchait l'OL
            # d'être plus sélectif. L'OL peut maintenant monter jusqu'à 2.5×ATR
            # (voire plus si WR très bas). Le plancher à 1.5×ATR est conservé.
            if ol_thresh is not None:
                ol_thresh_clamped = max(1.5, ol_thresh)  # pas de cap supérieur — l'OL décide
                ol_thresh_trending = ol_thresh_clamped
                ol_thresh_ranging = max(1.5, ol_thresh_clamped - 0.5)
                logger.debug(
                    f"  [OL] {symbol}: thresh={ol_thresh_clamped}, risk_mult={ol_risk_mult} "
                    f"(OL→trending={ol_thresh_trending}, ranging={ol_thresh_ranging})"
                )
        except Exception as e:
            logger.warning(f"  [SIGNAL_PIPELINE] phase1_mom20x3 OL: {e}")
            pass

        rates_tf = self._get_cached_rates(
            symbol, tf, count=200
        )  # ⚡ 10000→200: 98% moins de données, suffisant pour MOM20x3
        if rates_tf is None or len(rates_tf) < 50:
            logger.debug(
                f"  [MOM20x3] {symbol}: rates {tf} insufficient ({0 if rates_tf is None else len(rates_tf)} bars)"
            )
            return None

        mom = MOM20x3(rates_tf, symbol)
        raw = mom.analyze(custom_thresh_trending=ol_thresh_trending, custom_thresh_ranging=ol_thresh_ranging)
        if raw is None:
            return None

        # Higher TF confirmation
        h4_conf = 1.0
        higher_tf = "D1" if tf == "H4" else "H4"
        try:
            higher_cached = self._get_cached_rates(symbol, higher_tf, count=60)
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

        # Enrich signal
        tick = self.mt5.get_tick(symbol)
        entry = tick.ask if tick else 0
        signal = dict(raw)
        signal["_raw_mom_score"] = signal.get("score", 0.6)  # 🐛 FIX #7 (3 Juillet): sauve score MOM20x3 brut
        # avant les ajustements pipeline (OBV, VP, etc.)
        # pour que les phases aval puissent décider
        # de ne pas pénaliser un signal fort.
        signal["symbol"] = symbol
        signal["timeframe"] = tf
        signal["details"] = f"MOM20x3_{tf}"
        signal["quality"] = min(1.0, (signal.get("confidence", 0.5) + 0.1) * h4_conf)
        if h4_conf < 1.0 and signal.get("score", 0.6) > 0.5:
            signal["score"] = max(0.5, signal["score"] * 0.90)

        # 🔓 FIX 8 Juillet: soft block SUPPRIMÉ — l'OL peut maintenant
        # AUGMENTER le risk_mult (ex: WR>82% → risk_mult=1.15).
        # Avant: static_risk_mult = 1.0 écrasait ol_risk_mult=1.15 → effet OL nul.
        # Maintenant: effective = static × ol (OL peut monter ET descendre).
        from engine_simple.symbol_params import get_symbol_param, update_dyn_score

        static_risk_mult = get_symbol_param(symbol, "risk_mult", 1.0)
        effective_risk_mult = static_risk_mult * ol_risk_mult
        signal["risk_mult"] = effective_risk_mult
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

    # ── Phase 1d: H4 Direction Filter (Multi-TF professionnel) ────────────
    # Vérifie l'alignement du signal H1 avec la direction H4.
    # Si H4 est en tendance forte (ADX≥22) et le signal H1 va à contre-courant,
    # le score est pénalisé sévèrement ou le signal est rejeté.
    # Ajouté 27 Juillet 2026 — architecture H4→H1→M15.

    def _phase1d_h4_direction_filter(self, symbol: str, signal: dict) -> bool:
        """Filtre les signaux H1 qui vont à contre-courant de la tendance H4.

        Returns:
            False si le signal doit être rejeté (conflit majeur avec H4)
        """
        try:
            h4_rates = self._get_cached_rates(symbol, "H4", count=100)
            if h4_rates is None or len(h4_rates) < 30:
                return True  # pas assez de données H4 → laisser passer

            h4_close = np.array([r[4] for r in h4_rates], dtype=float)
            from engine_simple.indicators import ema, adx as ind_adx

            # ADX H4 pour détecter la force de la tendance
            h4_high = np.array([r[2] for r in h4_rates], dtype=float)
            h4_low = np.array([r[3] for r in h4_rates], dtype=float)
            h4_adx_val, h4_pdi, h4_mdi = ind_adx(h4_high, h4_low, h4_close, period=14)
            h4_adx = float(h4_adx_val)

            # EMA50 H4 pour déterminer la direction
            h4_ema50 = ema(h4_close, 50)
            if len(h4_ema50) < 2 or np.isnan(h4_ema50[-1]):
                return True

            h4_price = float(h4_close[-1])
            h4_ema = float(h4_ema50[-1])
            h4_slope = (float(h4_ema50[-1]) - float(h4_ema50[-5])) / float(h4_ema50[-5]) if len(h4_ema50) >= 5 else 0

            # Déterminer la direction H4
            if h4_price > h4_ema * 1.002 and h4_slope > 0.0005:
                h4_direction = "BUY"
                h4_strength = "strong" if h4_adx >= 22 else "moderate"
            elif h4_price < h4_ema * 0.998 and h4_slope < -0.0005:
                h4_direction = "SELL"
                h4_strength = "strong" if h4_adx >= 22 else "moderate"
            else:
                h4_direction = "NEUTRAL"
                h4_strength = "weak"

            signal_action = signal.get("action", "")
            signal_score = signal.get("score", 0.6)

            logger.debug(
                f"  [H4_DIR] {symbol}: H4={h4_direction}({h4_strength}, ADX={h4_adx:.0f}, "
                f"slope={h4_slope:.4f}) | H1 signal={signal_action} score={signal_score:.2f}"
            )

            # Si H4 est neutre → pas de pénalité (laisser passer le signal H1)
            if h4_direction == "NEUTRAL":
                signal["_h4_dir"] = "NEUTRAL"
                signal["_h4_conf"] = 1.0
                return True

            # Conflit: signal H1 va contre H4
            if signal_action != h4_direction:
                if h4_strength == "strong":
                    # Tendance H4 forte → REJETER le signal contre-tendance
                    logger.debug(
                        f"  [H4_DIR] {symbol}: REJETÉ — {signal_action} contre tendance H4 {h4_direction} (forte)"
                    )
                    return False
                else:
                    # Tendance H4 modérée → pénaliser le score
                    signal["score"] = max(0.30, signal_score * 0.75)
                    signal["_h4_penalty"] = 0.75
                    signal["_h4_dir"] = h4_direction
                    signal["_h4_conf"] = 0.75
                    logger.debug(
                        f"  [H4_DIR] {symbol}: PÉNALITÉ — {signal_action} contre H4 {h4_direction}, "
                        f"score {signal_score:.2f}→{signal['score']:.2f}"
                    )
                    return True
            else:
                # Aligné → bonus (score ×1.05, cap à 0.95)
                signal["score"] = min(0.95, signal_score * 1.05)
                signal["_h4_dir"] = h4_direction
                signal["_h4_conf"] = 1.05
                signal["_h4_aligned"] = True
                logger.debug(f"  [H4_DIR] {symbol}: ALIGNÉ {signal_action} avec H4 {h4_direction} (bonus)")
                return True

        except Exception as e:
            logger.debug(f"  [H4_DIR] {symbol}: erreur: {e}")
            return True  # erreur → laisser passer (failsafe)

    # ── M15 Confirmation (exécution de précision) ─────────────────────────
    # Vérifie que la dernière bougie M15 FERMÉE confirme la direction du signal.
    # Évite d'entrer sur une bougie M15 qui va dans le sens opposé.

    def _check_m15_confirmation(self, symbol: str, signal: dict) -> bool:
        """Vérifie la confirmation M15 avant exécution (assoupli 29 Juil 2026).

        La dernière bougie M15 complètement fermée NE doit PAS être FORTEMENT
        opposée à la direction du signal. Une petite opposition (mèche/bougie
        neutre) est tolérée — le signal H1 a plus de poids qu'une bougie M15
        de faible conviction.

        Returns:
            False si M15 est fortement opposé → reporter au prochain cycle
        """
        try:
            exec_tf = self.symbol_execution_timeframes.get(symbol, "M15")
            m15_rates = self._get_cached_rates(symbol, exec_tf, count=5)
            if m15_rates is None or len(m15_rates) < 3:
                return True  # pas assez de données → laisser passer (failsafe)

            # Dernière bougie COMPLÈTEMENT fermée (avant-dernière, l'avant-dernière est fermée)
            # m15_rates[0] = plus vieille, m15_rates[-1] = plus récente (en cours)
            # Format MT5: (time, open, high, low, close, tick_volume, spread, real_volume)
            closed = m15_rates[-2]  # avant-dernière = dernière fermée
            open_p = float(closed[1])
            high_p = float(closed[2])
            low_p = float(closed[3])
            close_p = float(closed[4])

            action = signal.get("action", "")
            m15_direction = "BUY" if close_p > open_p else "SELL"

            # Ratio corps/range de la bougie M15 — mesure la conviction
            candle_range = high_p - low_p
            body_size = abs(close_p - open_p)
            body_ratio = body_size / candle_range if candle_range > 0 else 0.0

            logger.debug(
                f"  [M15] {symbol}: signal={action}, M15_candle={m15_direction} "
                f"(O={open_p:.4f} H={high_p:.4f} L={low_p:.4f} C={close_p:.4f}, "
                f"body_ratio={body_ratio:.2f})"
            )

            if action == m15_direction:
                # ✅ M15 aligné → confirmé (entrée avec le momentum M15)
                signal["_m15_confirmed"] = True
                signal["_m15_entry_price"] = close_p
                signal["_m15_candle_dir"] = m15_direction
                signal["entry_price"] = close_p  # override pour l'exécution
                logger.debug(f"  [M15] {symbol}: ✅ CONFIRMÉ (aligné) — entry_price={close_p:.4f}")
                return True

            elif body_ratio < 0.40:
                # ✅ M15 opposé MAIS corps faible (<40%) → considéré comme neutre/bruit
                # Le signal H1 a plus de poids qu'une bougie M15 sans conviction
                signal["_m15_confirmed"] = True
                signal["_m15_entry_price"] = close_p
                signal["_m15_candle_dir"] = m15_direction
                signal["entry_price"] = close_p
                logger.debug(
                    f"  [M15] {symbol}: ✅ CONFIRMÉ (opposé mais corps {body_ratio:.0%} < 40%) "
                    f"— considéré neutre, entry_price={close_p:.4f}"
                )
                return True

            else:
                # ❌ M15 fortement opposé (corps >= 40%) → vraie contradiction
                logger.debug(
                    f"  [M15] {symbol}: PAS DE CONFIRMATION — M15 fortement {m15_direction} "
                    f"(body_ratio={body_ratio:.0%}) → attendre prochaine bougie"
                )
                return False

        except Exception as e:
            logger.debug(f"  [M15] {symbol}: erreur: {e}")
            return True  # erreur → laisser passer (failsafe)

    # ── Mean Reversion (RANGING markets) ──────────────────────────────────

    def _generate_mr_signal(self, symbol: str) -> dict | None:
        """Génère un signal MeanReversion en marché RANGING (ADX<18).

        Logique :
        - RSI < 30 → BUY (suracheté, retour vers la moyenne)
        - RSI > 70 → SELL (survendu, retour vers la moyenne)
        - TP = 1.5×ATR (petite cible, les ranges ne vont pas loin)
        - SL = 0.8×ATR (stop serré)
        - risk_mult = 0.75 (MR moins fiable que MOM20x3 en tendance)
        """
        import numpy as np
        from engine_simple.indicators import rsi as ind_rsi

        tf = self.symbol_timeframes.get(symbol, "H1")
        rates = self.mt5.get_rates(symbol, tf, count=100)
        if rates is None or len(rates) < 50:
            return None

        close = np.array([r[4] for r in rates], dtype=float)

        # Calcul RSI
        rsi_val = float(ind_rsi(close, period=14)[-1])

        # Calcul ADX pour confirmer RANGING
        high = np.array([r[2] for r in rates], dtype=float)
        low = np.array([r[3] for r in rates], dtype=float)
        from engine_simple.indicators import adx as ind_adx

        adx_arr = ind_adx(high, low, close, period=14)
        # ❌ BUG CORRIGÉ 2 Juillet 2026: ind_adx retourne (adx, +di, -di) tuple, pas un array
        # adx_arr[-1] donnait minus_di au lieu de l'ADX réel.
        # 🔧 FIX #6 (3 Juillet 2026): fallback=25 était LIBÉRAL (laissait passer MR en mode TRENDING).
        # Maintenant: si ADX indisponible, on skip MR proprement.
        if adx_arr is not None and len(adx_arr) > 0:
            adx_val = float(adx_arr[0])
        else:
            logger.debug(f"  [MR] {symbol}: ADX indisponible → skip MR")
            return None

        # Uniquement en RANGING (ADX < 18)
        if adx_val >= 18:
            return None

        # Vérifier les extrêmes RSI
        if rsi_val < 30:
            action = "BUY"
            score = 0.60
            confidence = 0.50
            logger.debug(f"  [MR] {symbol}: RSI={rsi_val:.1f} < 30 → BUY (oversold, ADX={adx_val:.1f})")
        elif rsi_val > 70:
            action = "SELL"
            score = 0.60
            confidence = 0.50
            logger.debug(f"  [MR] {symbol}: RSI={rsi_val:.1f} > 70 → SELL (overbought, ADX={adx_val:.1f})")
        else:
            return None

        # Prix d'entrée
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return None
        entry_price = tick.ask if action == "BUY" else tick.bid

        # ATR
        from engine_simple.indicators import atr as ind_atr

        atr_arr = ind_atr(high, low, close, period=14)
        atr_val = float(atr_arr[-1]) if atr_arr is not None and len(atr_arr) > 0 else 0

        if atr_val <= 0:
            return None

        signal = {
            "action": action,
            "score": score,
            "confidence": confidence,
            "atr": atr_val,
            "sl_atr": 1.0,  # SL = 1.0×ATR
            "tp_atr": 1.5,  # TP = 1.5×ATR → RR = 1.5
            "risk_mult": 0.75,  # MR moins fiable que MOM
            "entry_price": entry_price,
            "_regime": "RANGING",
            "_strategy": "MR",
            "strategy": "MeanReversion",
            "details": f"MeanReversion_{tf}",
            "timeframe": tf,
            "symbol": symbol,
            "adx": adx_val,
            "rsi": rsi_val,
            "quality": min(1.0, 0.50 + (1.0 - abs(rsi_val - 50) / 50) * 0.30),
            "higher_tf_conf": 1.0,
            "atr_pct": round(atr_val / entry_price * 100, 4) if entry_price > 0 else 0,
        }
        return signal

    # ── Phase 2: ADX Threshold Filter ─────────────────────────────────────

    def _phase2_adx_filter(self, symbol: str, signal: dict, cycle_count: int, log_throttle: dict) -> bool:
        # MeanReversion bypass: les signaux MR sont déjà filtrés par RSI
        if signal.get("_strategy") == "MR":
            logger.debug(f"  [ADX] {symbol}: bypass MR (RSI={signal.get('rsi', 0):.1f})")
            return True
        """Vérifie le seuil ADX. Plus de bypass — le bypass centralisé gère les exceptions."""
        signal_adx = signal.get("adx", 0)
        sym_cfg = self.symbol_limits.get(symbol, {})
        signal_score = signal.get("score", 0.6)

        # 🔧 FIX 22 Juillet 2026: Révision Pipeline — bypass ADX supprimé
        # Ancien: les signaux score>=0.80 bypassaient ADX, créant un trou dans le filtre.
        # Maintenant: TOUS les signaux passent par ADX. Le bypass central (process(), ligne 218)
        # permet aux très bons signaux (score_final>=0.90 ET raw_mom>=0.85) de sauter TOUS les filtres.
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
    # Le paramètre session_filter a été retiré de __init__ (Juillet 2026).
    # Cette méthode est conservée comme placeholder pour compatibilité pipeline.

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
    # 🔧 FIX 22 Juillet 2026: Révision Pipeline — Pénalités SELL rééquilibrées
    # Les pénalités étaient trop agressives (RANGING=0.85, LOW_VOL=0.75) et
    # combinées avec OBV+VP+MTF, un SELL perdait 50% de score avant exécution.
    # Nouveaux seuils: plancher à 0.90 sauf contre-tendance. Bonus ADX si ADX>25
    # et -DI > +DI×1.5 (le momentum baissier est fort).
    # Note: Les SELL en TREND_DOWN ne sont jamais pénalisés.
    SELL_PENALTY_BY_REGIME = {
        "TREND_DOWN": 1.00,  # ✅ SELL avec la tendance → pas de pénalité
        "HIGH_VOL": 0.95,  # 🟡 Haute volatilité → pénalité 5% (était 10%)
        "RANGING": 0.90,  # 🟢 Range → pénalité 10% (était 15%)
        "LOW_VOL": 0.85,  # 🟡 Basse volatilité → pénalité 15% (était 25%)
        "TREND_UP": 0.0,  # 🔴 BLOCKÉ (contre-tendance)
    }

    def _phase5_regime_rule(self, signal: dict) -> bool:
        """Évite les trades à contre-tendance. Applique pénalité SELL par régime.

        Depuis le FIX du 16 Juillet 2026 : les SELL hors TREND_DOWN sont
        systématiquement pénalisés car 37.2% WR global (77% des pertes).
        """
        regime = signal.get("_regime", "RANGING")
        action = signal.get("action")
        symbol = signal.get("symbol", "?")

        # Vérification contre-tendance (inchangé)
        if (action == "BUY" and regime == "TREND_DOWN") or (action == "SELL" and regime == "TREND_UP"):
            logger.debug(f"  [RÈGLE DIR] {symbol}: {action} en {regime} → contre-tendance, skip")
            return False

        # 🔧 FIX 16 Juillet: Pénalité SELL par régime
        if action == "SELL":
            sell_mult = self.SELL_PENALTY_BY_REGIME.get(regime, 0.80)
            if sell_mult < 1.0:
                old_score = signal.get("score", 0.6)
                new_score = max(0.30, old_score * sell_mult)
                signal["score"] = new_score
                signal["sell_penalty"] = sell_mult
                logger.debug(
                    f"  [SELL PENALTY] {symbol}: {regime} → score ×{sell_mult:.2f} ({old_score:.2f} → {new_score:.2f})"
                )
        return True

    # ── Phase 6: Strategy Selector ─────────────────────────────────────────

    def _phase6_strategy_selector(self, symbol: str, signal: dict) -> bool:
        # MeanReversion bypass: pas de sélection de stratégie (signal basé RSI)
        if signal.get("_strategy") == "MR":
            signal["strat_params"] = {"description": "MeanReversion (bypass Phase 6)"}
            return True

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
        # MeanReversion bypass: les filtres volume ne s'appliquent pas au MR (RSI-based)
        if signal.get("_strategy") == "MR":
            signal["rvol_adj"] = 1.0
            signal["cmf_adj"] = 1.0
            signal["rvol_note"] = "bypass_MR"
            signal["cmf_note"] = "bypass_MR"
            return True

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
            # 🔧 30 Juil 2026: Diagnostic RVOL — log des valeurs de volume
            if len(volumes) > 0:
                logger.debug(
                    f"  [VOL] {symbol}: rvol={rvol:.2f}, volumes[:5]={volumes[:5].tolist()}, "
                    f"vol_mean={float(np.mean(volumes[-50:])):.1f}"
                )
            # 🔧 30 Juil 2026: Détection début de bougie — si la bougie courante
            # a moins de 30% du volume moyen des 2 bougies complètes précédentes,
            # le RVOL n'est pas encore significatif (début de cycle H1).
            skip_rvol = False
            if len(volumes) >= 3:
                prev_vol = float(np.mean(volumes[-3:-1]))
                if prev_vol > 0 and volumes[-1] < prev_vol * 0.30:
                    skip_rvol = True
                    logger.debug(
                        f"  [VOL] {symbol}: bougie jeune (vol_courant={volumes[-1]:.0f} < "
                        f"30% × vol_moyen_complet={prev_vol:.0f}) — RVOL ignoré"
                    )
            if skip_rvol:
                signal["rvol_adj"] = 1.0
                signal["rvol_note"] = "early_candle"
            elif rvol < 0.5:
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
        # MeanReversion bypass: les divergences volume ne s'appliquent pas au MR
        if signal.get("_strategy") == "MR":
            signal["obv_div"] = "bypass_MR"
            signal["obv_strength"] = 0.0
            signal["obv_note"] = "bypass_MR"
            return

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
                    # 🐛 FIX #7 (3 Juillet): Ne pas pénaliser les signaux MOM20x3 très forts
                    # Le MOM20x3 a 60% WR historique — l'OBV peut être en conflit temporaire
                    # dans une tendance forte (surtout XAUUSD, BTCUSD).
                    raw_mom = signal.get("_raw_mom_score", 0)
                    if raw_mom >= 0.80 and signal.get("_strategy") != "MR":
                        # Signal MOM20x3 fort → pénalité OBV réduite (max -5% au lieu de -30%)
                        mild_penalty = max(penalty_low, 0.95)
                        signal["score"] = max(0.3, signal["score"] * mild_penalty)
                        signal["obv_div"] = div_type
                        signal["obv_strength"] = round(div_strength, 3)
                        signal["obv_note"] = f"conflict_mild={mild_penalty:.2f}_raw_mom={raw_mom:.2f}"
                    else:
                        # Divergence en conflit → pénalité normale
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
        # MeanReversion bypass: le Volume Profile ne s'applique pas au MR (RSI-based)
        if signal.get("_strategy") == "MR":
            signal["vp_boost"] = "bypass_MR"
            return True

        # 🔧 FIX 22 Juillet 2026: Révision Pipeline — bypass VP supprimé
        # Ancien: les signaux raw_mom>=0.75 bypassaient VP.
        # Maintenant: le bypass central (process() ligne 218) gère UNIFORMÉMENT
        # tous les bypass. VP s'applique à TOUS les signaux sauf MR.

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
        # MeanReversion bypass: pas de confirmation MTF (RSI est le signal)
        if signal.get("_strategy") == "MR":
            return True
        # 🔧 FIX 22 Juillet 2026: Révision Pipeline — bypass MTF supprimé
        # Ancien: les signaux raw_mom>=0.75 bypassaient MTF.
        # Maintenant: le bypass central (process() ligne 218) gère UNIFORMÉMENT
        # tous les bypass. MTF s'applique à TOUS les signaux sauf MR.
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
                # NE PAS multiplier par adapted.risk_mult — l'OL gère déjà le risk
                # via online_history (fenêtre 200 trades). La double pénalité OL×AP
                # réduisait le risk_mult à ~0.39 même pour des symboles corrects.
                # On garde adapted.risk_mult = 1.0 ici et on loggue la valeur pour diagnostic.
                current_rm = signal.get("risk_mult", 1.0)
                if adapted.risk_mult < 0.9:
                    logger.debug(
                        f"  [ADAPTIVE] {symbol}: risk_mult AP={adapted.risk_mult:.2f} ignoré (OL déjà actif), risk_mult final={current_rm:.2f}"
                    )
                # adapted.sl_mult et tp_mult sont ignorés ici car gérés par ftmo_protector/trailer
                signal["adaptive_params"] = adapted.to_dict()
        except Exception as e:
            logger.debug(f"  [ADAPTIVE] {symbol}: erreur: {e}")

    # ── Phase 13+14: Feature Scoring + LightGBM — RETIRÉES 25 Juin 2026
    # Ces phases massacraient les signaux (adj ×0.727 sur XAUUSD, feature pipeline
    # qui n'avait pas assez de données, LGB jamais entraîné). Le code est conservé
    # dans l'historique git (commit 7eab317f6^).
