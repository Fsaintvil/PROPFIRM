"""
MOM20x3 — Stratégie Momentum 20 périodes avec filtres avancés.

Principe :
  c[i] - c[i-20] > seuil × ATR  →  Breakout haussier (BUY)
  c[i-20] - c[i] > seuil × ATR  →  Breakout baissier (SELL)

Seuils adaptatifs selon ADX (validés backtest 12+ ans, 67% WR) :
  ADX ≥ 22 (trending) : seuil = 2.5 × ATR (unifié avec regime.py 22/18)
  ADX < 22 (ranging)  : seuil = 2.0 × ATR
  Plafonné à 2.5 × ATR max, plancher 1.5 × ATR

Filtres additionnels (Juin 2026 — Audit Profond) :
  - ADX slope : ADX doit être en hausse (Wilder's smoothing, half=len/3)
  - +DI/-DI    : BUY nécessite +DI > -DI×0.8, SELL nécessite -DI > +DI×0.8
  - Pullback   : entrée différée après retracement vers EMA20 (ATR-based band)
  - DI Override: short-term momentum (5 périodes) peut inverser si ADX≥22
  - NaN guard  : momentum NaN/Inf → skip silencieux

Aucun overlay ICT/SMC (FVG, Order Blocks, Killzones, etc.)
"""

import logging

import numpy as np

from engine_simple.indicators import adx, atr, ema

try:
    from engine_simple.market_structure import analyze_market_structure
except ImportError:
    analyze_market_structure = None

logger = logging.getLogger("strategy")

# THRESHOLD_TRENDING / THRESHOLD_RANGING sont DÉPRÉCIÉS (Juin 2026)
# Les seuils réels viennent de SYMBOL_CONFIG (per-symbol) ou DEFAULT_SYMBOL_CONFIG.
# Ces constantes ne sont plus utilisées dans le calcul du signal.
THRESHOLD_MAX = 2.5  # Plafond absolu (clamping sécurité)
THRESHOLD_MIN = 1.5  # Plancher absolu (clamping sécurité)

# ============================================================================
# PARAMÈTRES SPÉCIFIQUES PAR ACTIF — Calibration Production
# ============================================================================
# Chaque actif a des caractéristiques de volatilité différentes.
# Les paramètres ci-dessous sont calibrés individuellement.
#
# Sources:
#   - Backtest 12+ ans (158,964 trades)
#   - Données live FTMO (47 trades analysés)
#   - Caractéristiques de marché (ATR, spread, sessions)
#   - Règles FTMO (DD 10%, daily loss 2%)
# ============================================================================

# ── SL/TP par actif et par régime ────────────────────────────────────────
# XAUUSD H4: Or — volatilité élevée (ATR≈90-100pts H4), tendances longues
#   → Migration de H1 (perdant 12 ans) vers H4 (WR 68.6%, DD 6.9%)
# BTCUSD H1: Bitcoin — volatilité EXTRÊME (ATR 8.7% H1), momentum rapide
#   → risk_mult 0.50 pour DD FTMO-safe (~9.0% attendu)
# US500.cash H1: S&P 500 — volatilité modérée, sessions US limitées
#   → Le plus stable des 3 (DD 6.5%, risk_mult 1.0)

SYMBOL_CONFIG = {
    # ═══════════════════════════════════════════════════════════════════════
    # XAUUSD H4 — Or (Juin 2026)
    # Caractéristiques: ATR~90-100pts H4, tendances longues, London+NY overlap
    # Backtest 12+ ans H4: WR 68.6%, PF 1.16, DD 6.9%
    # Timeframe: H4 (H1 = -$187K/12ans → HORS-JEU)
    # Justification complète dans config/default.yaml:XAUUSD
    # ═══════════════════════════════════════════════════════════════════════
    "XAUUSD": {
        # Momentum 18 périodes H4 = 72h (Scénario A: +20% trades)
        "momentum_period": 18,
        # SL/TP trending: 1.8/5.0 (RR 2.78 — SL plus serré H4)
        "sl_atr_trending": 1.8,
        "tp_atr_trending": 5.0,
        # SL/TP ranging: 1.5/3.5 (RR 2.33 — TP plus proche en range)
        "sl_atr_ranging": 1.5,
        "tp_atr_ranging": 3.5,
        # Seuils ATR (validés backtest 12+ ans, assouplis mode modéré)
        "threshold_trending": 2.0,  # Mode modéré: -0.5 vs 2.5
        "threshold_ranging": 1.5,  # Mode modéré: -0.5 vs 2.0
        # Filtres ADX (plus stricts en H4 — tendances plus pures)
        "adx_slope_threshold": -14.0,  # Mode modéré: +75% vs -8.0
        "adx_slope_threshold_strong": -20.0,  # Scénario A: assoupli -18.0→-20.0 pour +20% trades (plus de signaux forts passent l'ADX slope)
        # Pullback bandes (H4 → pullbacks plus larges)
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        # Volume filter thresholds (standard forex/indices)
        "cmf_threshold": 0.10,
        "obv_div_penalty_high": 0.70,
        "obv_div_penalty_low": 0.85,
        # Sessions préférées (London+NY overlap élargi)
        "preferred_hours": list(range(24)),  # 24/7 — pas de blocage horaire
        # News filter
        "news_minutes_before": 10,
        "news_minutes_after": 10,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD H1 — Bitcoin (Juin 2026)
    # Caractéristiques: Volatilité EXTRÊME (ATR 8.7% H1), 24/7
    # Backtest 12+ ans H1: WR 69.8%, PF 1.19, DD 5.6%
    # Timeframe: H1 (seul TF viable pour crypto)
    # Justification complète dans config/default.yaml:BTCUSD
    # ═══════════════════════════════════════════════════════════════════════
    "BTCUSD": {
        # Momentum 22 périodes H1 = 22h (Scénario A: +20% trades)
        "momentum_period": 22,
        # SL/TP trending: 3.0/7.0 (RR 2.33 — large pour gaps crypto)
        "sl_atr_trending": 3.0,
        "tp_atr_trending": 7.0,
        # SL/TP ranging: 2.5/5.0 (RR 2.0)
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 5.0,
        # Seuils ATR (abaissés — ADX crypto peu fiable, capter plus de signaux)
        "threshold_trending": 2.0,
        "threshold_ranging": 1.5,
        # Filtres ADX (très permissifs — ADX crypto bruité)
        "adx_slope_threshold": -6.0,  # Mode modéré: 2x vs -3.0
        "adx_slope_threshold_strong": -10.0,  # Mode modéré: +67% vs -6.0
        # Pullback bandes larges (BTC fait des pullbacks violents)
        "pullback_band_trending": 0.8,
        "pullback_band_ranging": 0.5,
        # Volume filter thresholds (crypto — volume bursty → relaxed)
        "cmf_threshold": 0.20,
        "obv_div_penalty_high": 0.85,
        "obv_div_penalty_low": 0.92,
        # Sessions 24/7 — crypto ne dort jamais
        "preferred_hours": list(range(24)),
        # News filter
        "news_minutes_before": 15,
        "news_minutes_after": 15,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # US500.cash H4 — S&P 500 Index (Juin 2026 — MIGRÉ DE H1)
    # Caractéristiques: Volatilité modérée (VIX-dependent), sessions US
    # Backtest H4 12+ ans: WR 68.4%, PF 1.07, DD 6.5%
    #   → H1 2026 YTD PF 0.95 (-$2,861) → HORS-JEU
    #   → H4 2026 YTD PF 1.51 (+$3,625) → MIGRATION
    # Timeframe: H4 (H1 perdant en 2026)
    # Justification complète dans config/default.yaml:US500.cash
    # ═══════════════════════════════════════════════════════════════════════
    "US500.cash": {
        # Momentum 18 périodes H4 = 72h (Scénario A: +20% trades)
        "momentum_period": 18,
        # SL/TP trending: 1.5/4.0 (RR 2.67 — SL serré, US500 H4 peu volatile)
        "sl_atr_trending": 1.5,
        "tp_atr_trending": 4.0,
        # SL/TP ranging: 1.2/3.0 (RR 2.5)
        "sl_atr_ranging": 1.2,
        "tp_atr_ranging": 3.0,
        # Seuils ATR (abaissés — US500 moins volatile en H4)
        "threshold_trending": 2.0,
        "threshold_ranging": 1.5,
        # Filtres ADX (plus permissifs — H4 moins de bougies)
        "adx_slope_threshold": -10.0,  # Mode modéré: 2x vs -5.0
        "adx_slope_threshold_strong": -14.0,  # Mode modéré: +40% vs -10.0
        # Pullback bandes serrées (indices font peu de pullbacks profonds)
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        # Volume filter thresholds (standard indices)
        "cmf_threshold": 0.10,
        "obv_div_penalty_high": 0.70,
        "obv_div_penalty_low": 0.85,
        # Sessions préférées (US market hours élargies)
        "preferred_hours": list(range(24)),  # 24/7 — pas de blocage horaire
        # News filter (renforcé — indice sensible aux news US)
        "news_minutes_before": 20,
        "news_minutes_after": 20,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EURUSD H1 — Euro/Dollar US (Juin 2026 — réactivé 17 Juin)
    # Caractéristiques: Forex majeur, spreads serrés, liquidité extrême
    # Backtest H1 12+ ans: WR 68.6%, PF 1.12, DD 3.7% (excellent)
    # Live FTMO (Juin 2026): WR 77.3% sur 22 trades (nettoyé des simulés)
    # Timeframe: H1 (seul TF viable pour EURUSD intraday)
    # Justification complète dans config/default.yaml:EURUSD
    # ═══════════════════════════════════════════════════════════════════════
    "EURUSD": {
        # Momentum 18 périodes H1 = 18h (plus réactif que 20 — reco analyse 9 Juin)
        "momentum_period": 18,
        # SL/TP trending: 1.5/4.5 (RR 3.0 — SL serré, EURUSD moins volatile)
        "sl_atr_trending": 1.5,
        "tp_atr_trending": 4.5,
        # SL/TP ranging: 1.2/3.0 (RR 2.5)
        "sl_atr_ranging": 1.2,
        "tp_atr_ranging": 3.0,
        # Seuils ATR (standard MOM20x3, validés backtest 12+ ans)
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        # Filtres ADX (standard forex)
        "adx_slope_threshold": -5.0,  # standard forex
        "adx_slope_threshold_strong": -8.0,  # pour raw_score > 0.70
        # Pullback bandes modérées (EURUSD pullbacks modérés)
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        # Volume filter thresholds (standard forex)
        "cmf_threshold": 0.10,
        "obv_div_penalty_high": 0.70,
        "obv_div_penalty_low": 0.85,
        # Sessions préférées (London+NY overlap)
        "preferred_hours": [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
        # News filter
        "news_minutes_before": 15,
        "news_minutes_after": 15,
    },
}

# Fallback par défaut
DEFAULT_SYMBOL_CONFIG = {
    "momentum_period": 20,
    "sl_atr_trending": 2.0,
    "tp_atr_trending": 5.0,
    "sl_atr_ranging": 1.5,
    "tp_atr_ranging": 4.0,
    "threshold_trending": 2.5,
    "threshold_ranging": 2.0,
    "adx_slope_threshold": -10.0,  # Mode modéré: +67% vs -6.0
    "adx_slope_threshold_strong": -14.0,  # Mode modéré: +40% vs -10.0
    "pullback_band_trending": 0.5,
    "pullback_band_ranging": 0.3,
    # Volume filter thresholds (default — standard forex/indices)
    "cmf_threshold": 0.10,
    "obv_div_penalty_high": 0.70,
    "obv_div_penalty_low": 0.85,
    "preferred_hours": list(range(24)),
    "news_minutes_before": 5,
    "news_minutes_after": 5,
}

# Compatibilité avec l'ancien code (momentum periods)
SYMBOL_MOMENTUM_PERIODS = {sym: cfg["momentum_period"] for sym, cfg in SYMBOL_CONFIG.items()}

# Périodes par défaut
DEFAULT_SYMBOL_MOMENTUM_PERIOD = 20


def _get_symbol_config(symbol: str | None) -> dict:
    """Retourne la configuration complète d'un symbole."""
    if symbol is None:
        return DEFAULT_SYMBOL_CONFIG
    return SYMBOL_CONFIG.get(symbol, DEFAULT_SYMBOL_CONFIG)


def _get_momentum_period(symbol: str | None) -> int:
    """Retourne la période momentum adaptée au symbole."""
    if symbol is None:
        return DEFAULT_SYMBOL_MOMENTUM_PERIOD
    return SYMBOL_MOMENTUM_PERIODS.get(symbol, DEFAULT_SYMBOL_MOMENTUM_PERIOD)


def mom20x3_signal(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    period: int = 20,
    atr_period: int = 14,
    adx_period: int = 14,
    symbol: str | None = None,
    custom_thresh_trending: float | None = None,
    custom_thresh_ranging: float | None = None,
    market_memory=None,
) -> dict | None:
    """Génère un signal MOM20x3 avec filtres ADX slope, +DI/-DI, pullback, S/R et patterns.

    Args:
        close: np.array de prix de clôture (au moins period + 1 éléments)
        high: np.array de prix hauts
        low: np.array de prix bas
        period: période du momentum (défaut 20)
        atr_period: période ATR (défaut 14)
        adx_period: période ADX (défaut 14)
        symbol: nom du symbole pour période adaptative (None = défaut)
        custom_thresh_trending: seuil trending personnalisé (OnlineLearner)
        custom_thresh_ranging: seuil ranging personnalisé (OnlineLearner)

    Returns:
        dict avec 'action' ('BUY'/'SELL'), 'score', 'atr', 'adx',
        'plus_di', 'minus_di', 'adx_slope', 'pullback_active',
        'sl_atr', 'tp_atr', 'thresh_used', 'ol_thresh_applied'
        ou None si pas de signal
    """
    # Configuration spécifique par symbole
    sym_cfg = _get_symbol_config(symbol)

    # Période adaptative par symbole
    if symbol is not None:
        period = _get_momentum_period(symbol)

    if len(close) < period + max(adx_period, 5):
        return None
    if len(high) < max(atr_period + 5, 30):
        return None
    if len(low) < max(atr_period + 5, 30):
        return None

    # === ATR ===
    atr_val = atr(high, low, close, atr_period)
    if atr_val is None or len(atr_val) == 0:
        return None
    current_atr = float(atr_val[-1])
    if current_atr <= 0:
        current_atr = 0.001

    # === ADX complet avec +DI/-DI ===
    adx_val, plus_di, minus_di = adx(high, low, close, adx_period)

    # === ADX slope : vérifier si ADX est en hausse (vrai Wilder's smoothing) ===
    # On calcule ADX sur la première moitié de la fenêtre pour comparer
    # comme dans backtest_production.py (validé 67% WR)
    adx_slope = 0.0
    half = min(14, len(close) // 3)
    if len(close) > adx_period * 2 + half:
        adx_prev, _, _ = adx(high[:-half], low[:-half], close[:-half], adx_period)
        adx_slope = adx_val - adx_prev

    # === Seuil adaptatif selon ADX (calculé avant les filtres) ===
    # Support OnlineLearner: custom_thresh_* surcharge les hardcodés
    # Utilise les seuils spécifiques au symbole
    is_trending = adx_val >= 22  # unifié avec regime.py (22 entrée / 18 sortie)
    if is_trending:
        thresh = custom_thresh_trending if custom_thresh_trending is not None else sym_cfg["threshold_trending"]
    else:
        thresh = custom_thresh_ranging if custom_thresh_ranging is not None else sym_cfg["threshold_ranging"]
    thresh = max(THRESHOLD_MIN, min(THRESHOLD_MAX, thresh))

    # === Momentum brut (période adaptative) avec garde NaN ===
    mom = float(close[-1] - close[-period - 1])
    if np.isnan(mom) or np.isinf(mom):
        logger.debug(f"  [MOM20x3] {symbol}: momentum NaN/Inf → skip")
        return None
    mom_abs = abs(mom)
    threshold_value = thresh * current_atr

    # Score de confiance (0.0 - 1.0) — utilisé par les filtres
    if mom_abs > 0:
        raw_score = min(1.0, mom_abs / (threshold_value * 2))
    else:
        raw_score = 0.0

    # === Market Structure Filter (ICT/SMC) ===
    # Analyse BOS/CHOCH/OB pour confirmer ou infirmer la direction du signal
    _structure = None
    _struct_trend = "unknown"
    _struct_score = 0
    _unmitigated_obs = 0
    _unmitigated_fvgs = 0
    if analyze_market_structure is not None and len(high) >= 30 and len(low) >= 30:
        try:
            _structure = analyze_market_structure(high, low, close, lookback=100)
            _struct_trend = _structure.get("trend", "unknown")
            _struct_score = _structure.get("score", 0)
            _unmitigated_obs = _structure.get("unmitigated_obs", 0)
            _unmitigated_fvgs = _structure.get("unmitigated_fvgs", 0)

            # Pénalité si structure contredit le signal
            if mom > 0 and _struct_trend == "bearish":
                raw_score *= 0.80  # -20%
                logger.debug(f"  [STRUCT] {symbol}: BUY mais structure bearish → score -20%")
            elif mom < 0 and _struct_trend == "bullish":
                raw_score *= 0.80
                logger.debug(f"  [STRUCT] {symbol}: SELL mais structure bullish → score -20%")

            # Bonus si structure confirme
            if mom > 0 and _struct_trend == "bullish":
                raw_score = min(1.0, raw_score * 1.10)  # +10%
                logger.debug(f"  [STRUCT] {symbol}: BUY + structure bullish → score +10%")
            elif mom < 0 and _struct_trend == "bearish":
                raw_score = min(1.0, raw_score * 1.10)
                logger.debug(f"  [STRUCT] {symbol}: SELL + structure bearish → score +10%")

            # Pénalité si BOS/CHOCH récent contredit
            if _structure.get("recent_bos"):
                bos = _structure.get("bos", {})
                if (mom > 0 and bos.get("bearish_bos")) or (mom < 0 and bos.get("bullish_bos")):
                    raw_score *= 0.85
                    logger.debug(f"  [STRUCT] {symbol}: BOS contredit signal → score -15%")
        except Exception as e:
            logger.debug(f"  [STRUCT] {symbol}: erreur analyse market_structure: {e}")

    # Initialiser les variables utilisées par les filtres
    pullback_active = False  # False par défaut = bloquer sauf si pullback confirmé
    pullback_dist = 0.0
    pullback_band = 0.0

    # === Filtre ADX slope : refuser si ADX baisse significativement ===
    # 🔧 Audit 14 Juin 2026: seuils calibrés par symbole
    #   - XAUUSD: -6.0 standard, -10.0 fort signal (tendances longues)
    #   - BTCUSD: -5.0 standard, -8.0 fort signal (crypto bruité)
    #   - US500.cash: -6.0 standard, -10.0 fort signal (indices stables)
    adx_slope_ok = True
    adx_slope_threshold = sym_cfg["adx_slope_threshold"]

    if raw_score > 0.50:
        adx_slope_threshold = sym_cfg["adx_slope_threshold_strong"]

    if adx_slope < adx_slope_threshold:
        adx_slope_ok = False
        logger.debug(
            f"  [MOM20x3] ADX slope={adx_slope:.1f} < {adx_slope_threshold:.1f} → skip (raw_score={raw_score:.2f})"
        )

    # === Filtre +DI/-DI directionnel ===
    # Règle : +DI > -DI pour BUY, -DI > +DI pour SELL.
    # Mais en marché transitionnel, le momentum 20p peut encore être haussier
    # alors que les DIs sont déjà baissiers (-DI > +DI). Dans ce cas, on vérifie
    # le momentum COURT (5 périodes) : s'il confirme les DIs, on override le signal.
    dir_filter_ok = True
    di_suggests = None  # None=pas de suggestion, "BUY" ou "SELL" si override possible
    if close[-1] > close[-period - 1]:  # BUY bias from momentum
        # H-09: Adoucissement ×0.8 — tolère un écart DI allant jusqu'à 20%
        # avant de bloquer. Évite les rejets en marché transitionnel où le
        # momentum 20p précède le croisement DI de quelques bougies.
        if plus_di <= minus_di * 0.8:
            dir_filter_ok = False
            di_suggests = "SELL"
            logger.debug(
                f"  [MOM20x3] FILTRE DIR: {symbol} BUY mais +DI={plus_di:.1f} <= -DI×0.8={minus_di * 0.8:.1f}"
                f" → vérification short-term"
            )
    else:  # SELL bias
        # H-09: Même adoucissement ×0.8 pour les signaux SELL
        if minus_di <= plus_di * 0.8:
            dir_filter_ok = False
            di_suggests = "BUY"
            logger.debug(
                f"  [MOM20x3] FILTRE DIR: {symbol} SELL mais -DI={minus_di:.1f} <= +DI×0.8={plus_di * 0.8:.1f}"
                f" → vérification short-term"
            )

    # === Signal directionnel (seuil dépassé) ===
    action = None
    score = 0.0

    if mom > 0 and mom_abs >= threshold_value:
        action = "BUY"
        score = 0.35 + raw_score * 0.60  # range [0.35-0.95] pour distinguer signaux faibles/forts
    elif mom < 0 and mom_abs >= threshold_value:
        action = "SELL"
        score = 0.35 + raw_score * 0.60

    if action is None:
        logger.debug(
            f"  [MOM20x3] {symbol}: mom={mom:.5f} < thresh={threshold_value:.5f} "
            f"(thresh={thresh:.2f}×ATR={current_atr:.5f}) → no signal"
        )
        return None

    # === DI Override : si le filtre directionnel bloque mais que le short-term
    # momentum confirme la direction suggérée par les DIs, on override ===
    # Seuil abaissé à 0.5×threshold (= 1.0×ATR au lieu de 2.0×ATR) car le DI cross
    # (+DI > -DI ou -DI > +DI) fournit déjà une confirmation directionnelle forte.
    # Cela permet de rattraper les transitions de marché plus tôt.
    if not dir_filter_ok and di_suggests is not None:
        short_period = 5
        if len(close) >= short_period + 2:
            short_mom = float(close[-1] - close[-short_period - 1])
            short_mom_abs = abs(short_mom)
            # 🔒 DI Override durci : pas d'override en RANGING (ADX<22)
            # En ranging, le short-term momentum n'est que du bruit.
            # Seuil ×4 (2.0×ATR au lieu de 0.5×) → quasiment jamais d'override.
            if adx_val < 22:
                override_thresh = threshold_value * 2.0
            else:
                override_thresh = threshold_value * 0.75  # ~1.5×ATR (moins permissif qu'avant)
            if di_suggests == "SELL" and short_mom < -override_thresh:
                # Short-term momentum confirme la baisse → override en SELL
                action = "SELL"
                short_raw_score = min(1.0, short_mom_abs / (threshold_value * 2))
                score = 0.35 + short_raw_score * 0.60
                dir_filter_ok = True
                logger.info(
                    f"  [MOM20x3] DI OVERRIDE: {symbol} 20p BUY→SELL "
                    f"(short_mom={short_mom:.5f} override_thresh={override_thresh:.5f})"
                )
            elif di_suggests == "BUY" and short_mom > override_thresh:
                # Short-term momentum confirme la hausse → override en BUY
                action = "BUY"
                short_raw_score = min(1.0, short_mom_abs / (threshold_value * 2))
                score = 0.35 + short_raw_score * 0.60
                dir_filter_ok = True
                logger.info(
                    f"  [MOM20x3] DI OVERRIDE: {symbol} 20p SELL→BUY "
                    f"(short_mom={short_mom:.5f} override_thresh={override_thresh:.5f})"
                )

    # === Liquidity Sweep Filter ===
    # Un sweep de liquidité récent indique un faux signal probable
    if _structure is not None:
        recent_sweeps = _structure.get("recent_sweeps", [])
        if recent_sweeps:
            last_sweep = recent_sweeps[-1]
            sweep_type = last_sweep.get("type", "")
            if (action == "BUY" and sweep_type == "bullish_sweep") or (
                action == "SELL" and sweep_type == "bearish_sweep"
            ):
                raw_score *= 0.85  # -15%
                logger.debug(f"  [STRUCT] {symbol}: liquidity sweep {sweep_type} récent → score -15%")

    # === Appliquer les filtres (ADX slope + directionnel) ===
    if not adx_slope_ok:
        logger.debug(f"  [MOM20x3] {action} {symbol}: ADX slope={adx_slope:.1f} → skip")
        return None

    if not dir_filter_ok:
        logger.debug(f"  [MOM20x3] {action} {symbol}: direction filter → skip")
        return None

    # === Pullback check : prix proche de EMA20 ===
    # Bande de pullback calibrée par symbole (ATR-based)
    # XAUUSD: 0.5×ATR trending, 0.3×ATR ranging (or = tendances longues)
    # BTCUSD: 0.6×ATR trending, 0.4×ATR ranging (crypto = plus de bruit)
    # US500.cash: 0.4×ATR trending, 0.25×ATR ranging (indices = plus serré)
    ema_period = 20
    ema20_arr = ema(close, ema_period)
    pullback_active = False
    pullback_dist = 0.0
    pullback_band = 0.0
    if len(ema20_arr) > 0 and not np.isnan(ema20_arr[-1]):
        ema20_val = float(ema20_arr[-1])
        if ema20_val > 0:
            pullback_dist = (float(close[-1]) - ema20_val) / ema20_val * 100
            # Bande de pullback ATR-based (calibrée par symbole)
            atr_mult_pullback = sym_cfg["pullback_band_trending"] if is_trending else sym_cfg["pullback_band_ranging"]
            pullback_band = (atr_mult_pullback * current_atr) / ema20_val * 100
            pullback_band = max(0.05, min(1.0, pullback_band))  # clamp 0.05%-1.0%
            if abs(pullback_dist) < pullback_band:
                pullback_active = True

    # === Pullback info + filter pour signaux faibles ===
    # MOM20x3 est une stratégie momentum qui entre sur breakouts, pas sur retracements EMA20.
    # Le pullback est informatif pour les signaux forts (score >= seuil config).
    # Pour les signaux faibles (score < seuil), un pullback actif est requis comme confirmation.
    from engine_simple.ftmo_config import PULLBACK_FILTER_SCORE_THRESHOLD

    if not pullback_active and pullback_band > 0:
        if score < PULLBACK_FILTER_SCORE_THRESHOLD:
            logger.info(
                f"  [PULLBACK] {action} {symbol}: score={score:.2f} < 0.65 + pas de pullback "
                f"(dist={pullback_dist:.2f}% > band={pullback_band:.2f}%) → skip"
            )
            return None
        logger.debug(
            f"  [MOM20x3] {action} {symbol}: pas de pullback vers EMA20 "
            f"(dist={pullback_dist:.2f}% > band={pullback_band:.2f}%) → OK (momentum)"
        )

    # === S/R Level Filter (MarketMemory) ===
    # Pénalité si prix proche d'une résistance/support majeur
    _nearest_support = None
    _nearest_resistance = None
    if market_memory is not None and symbol is not None:
        try:
            current_price = float(close[-1])
            sr_levels = market_memory.get_nearby_levels(symbol, current_price, distance=0.5)
            for level in sr_levels:
                if level["type"] == "support" and (
                    _nearest_support is None or level["price"] > _nearest_support["price"]
                ):
                    _nearest_support = level
                elif level["type"] == "resistance" and (
                    _nearest_resistance is None or level["price"] < _nearest_resistance["price"]
                ):
                    _nearest_resistance = level

            # Pénalité si prix proche d'une résistance majeure et signal BUY
            if _nearest_resistance and _nearest_resistance.get("strength") == "major":
                dist_pct = abs(current_price - _nearest_resistance["price"]) / current_price * 100
                if dist_pct < 0.3:  # < 0.3% de la résistance
                    score = max(0.35, score * 0.85)  # -15%
                    logger.debug(
                        f"  [S/R] {symbol}: BUY proche résistance {_nearest_resistance['price']:.5f} → score -15%"
                    )

            # Pénalité si prix proche d'un support majeur et signal SELL
            if _nearest_support and _nearest_support.get("strength") == "major":
                dist_pct = abs(current_price - _nearest_support["price"]) / current_price * 100
                if dist_pct < 0.3:
                    score = max(0.35, score * 0.85)
                    logger.debug(f"  [S/R] {symbol}: SELL proche support {_nearest_support['price']:.5f} → score -15%")
        except Exception as e:
            logger.debug(f"  [S/R] {symbol}: erreur S/R levels: {e}")

    # === Pattern Confluence (MarketMemory) ===
    _pattern_signal = "NEUTRE"
    _pattern_confidence = 0.0
    if market_memory is not None and symbol is not None and len(close) >= 30:
        try:
            import pandas as pd

            recent_df = pd.DataFrame(
                {
                    "open": close[-30:] * 0.999,  # approximation open ≈ close × 0.999
                    "close": close[-30:],
                    "high": high[-30:],
                    "low": low[-30:],
                }
            )
            pattern_ctx = market_memory.get_pattern_context(symbol, recent_df, use_dtw=False)
            _pattern_signal = pattern_ctx.get("signal", "NEUTRE")
            _pattern_confidence = pattern_ctx.get("confidence", 0.0)

            # Bonus/pénalité selon concordance
            if _pattern_signal == "HAUSSE" and action == "BUY":
                score = min(0.99, score + 0.10)  # +10% bonus
                logger.debug(f"  [PATTERN] {symbol}: HAUSSE + BUY → score +10%")
            elif _pattern_signal == "BAISSE" and action == "SELL":
                score = min(0.99, score + 0.10)
                logger.debug(f"  [PATTERN] {symbol}: BAISSE + SELL → score +10%")
            elif _pattern_signal == "BAISSE" and action == "BUY":
                score = max(0.35, score - 0.10)  # -10% penalty
                logger.debug(f"  [PATTERN] {symbol}: BAISSE + BUY → score -10%")
            elif _pattern_signal == "HAUSSE" and action == "SELL":
                score = max(0.35, score - 0.10)
                logger.debug(f"  [PATTERN] {symbol}: HAUSSE + SELL → score -10%")
        except Exception as e:
            logger.debug(f"  [PATTERN] {symbol}: erreur pattern detection: {e}")

    # SL/TP selon le régime ADX — paramètres spécifiques par symbole
    if is_trending:
        sl_atr = sym_cfg["sl_atr_trending"]
        tp_atr = sym_cfg["tp_atr_trending"]
    else:
        sl_atr = sym_cfg["sl_atr_ranging"]
        tp_atr = sym_cfg["tp_atr_ranging"]

    # Confidence basée sur le score final (qui reflète le momentum utilisé)
    confidence = min(0.95, 0.40 + (score - 0.50) / 0.45 * 0.50)
    confidence = max(0.40, confidence)

    logger.debug(
        f"  [MOM20x3] {action} {symbol or ''} | mom={mom:.5f} thresh={threshold_value:.5f} "
        f"ADX={adx_val:.1f} +DI={plus_di:.1f} -DI={minus_di:.1f} "
        f"slope={adx_slope:.1f} pullback={pullback_active} "
        f"pb_band={pullback_band:.3f}% score={score:.2f}"
    )

    return {
        "action": action,
        "score": min(0.99, score),
        "confidence": confidence,
        "atr": current_atr,
        "adx": round(adx_val, 1),
        "plus_di": round(plus_di, 1),
        "minus_di": round(minus_di, 1),
        "adx_slope": round(adx_slope, 1),
        "pullback_active": pullback_active,
        "pullback_dist": round(pullback_dist, 3),
        "pullback_band": round(pullback_band, 4),
        "sl_atr": sl_atr,
        "tp_atr": tp_atr,
        "thresh_used": round(thresh, 2),
        "ol_thresh_applied": (custom_thresh_trending is not None) or (custom_thresh_ranging is not None),
        "mom_abs": round(mom_abs, 5),
        "threshold_value": round(threshold_value, 5),
        "momentum_period": period,
        "is_trending": is_trending,
        "_regime": "TREND_UP"
        if (is_trending and action == "BUY")
        else "TREND_DOWN"
        if (is_trending and action == "SELL")
        else "RANGING",
        "_ml_agrees": None,
        "_model_predictions": {"MOM20x3": action},
        "_dl_score": None,
        # Market structure data (ICT/SMC)
        "structure_trend": _struct_trend,
        "structure_score": _struct_score,
        "unmitigated_obs": _unmitigated_obs,
        "unmitigated_fvgs": _unmitigated_fvgs,
        "_structure_obs": _structure.get("order_blocks", []) if _structure else [],
        # S/R levels data (MarketMemory)
        "nearest_support": _nearest_support["price"] if _nearest_support else None,
        "nearest_resistance": _nearest_resistance["price"] if _nearest_resistance else None,
        # Pattern data (MarketMemory)
        "pattern_signal": _pattern_signal,
        "pattern_confidence": round(_pattern_confidence, 3),
    }


class MOM20x3:
    """Wrapper de la stratégie MOM20x3 — utilise la période adaptative par symbole."""

    def __init__(self, rates: list, symbol: str, period: int | None = None, market_memory=None):
        self.rates = rates
        self.symbol = symbol
        self.period = period or _get_momentum_period(symbol)
        self.market_memory = market_memory
        self._parse_rates()

    def _parse_rates(self):
        if self.rates is None or len(self.rates) < self.period + 5:
            self._close = None
            self._high = None
            self._low = None
            return
        self._close = np.array([r[4] for r in self.rates], dtype=float)
        self._high = np.array([r[2] for r in self.rates], dtype=float)
        self._low = np.array([r[3] for r in self.rates], dtype=float)

    def analyze(
        self, custom_thresh_trending: float | None = None, custom_thresh_ranging: float | None = None
    ) -> dict | None:
        if self._close is None:
            return None
        return mom20x3_signal(
            self._close,
            self._high,
            self._low,
            period=self.period,
            symbol=self.symbol,
            custom_thresh_trending=custom_thresh_trending,
            custom_thresh_ranging=custom_thresh_ranging,
            market_memory=self.market_memory,
        )

    def __call__(self) -> dict | None:
        return self.analyze()
