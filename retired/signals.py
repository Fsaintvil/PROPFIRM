import logging
from types import MappingProxyType

import numpy as np

from engine_simple.fvg_detector import (
    detect_fvg,
    detect_liquidity_sweep,
    filter_active_fvgs,
    find_imbalances,
    find_order_blocks,
)
from engine_simple.indicators import (
    adx,
    rsi,
)
from engine_simple.market_structure import analyze_market_structure
from engine_simple.rate_cache import RateCache
from engine_simple.session_analyzer import analyze_sessions, get_active_killzone
from engine_simple.structure_analyzer import multi_tf_alignment, multi_tf_bias

logger = logging.getLogger("signals")

STRATS = MappingProxyType({
    # ⚠️ Code mort — SignalGenerator désactivé Juin 2026.
    # Le robot utilise MOM20x3 de strategy.py (6 symboles champions).
    "XAUUSD": [{"tf": "H1", "period": 30, "thresh": 1.5, "sl": 2.0, "tp": 5.0}],
    "BTCUSD": [{"tf": "H1", "period": 20, "thresh": 1.5, "sl": 2.0, "tp": 5.0}],
    "US500.cash": [{"tf": "H1", "period": 24, "thresh": 1.5, "sl": 2.0, "tp": 5.0}],
})

ATR_PERIODS = {"H1": 14, "H4": 20, "D1": 20}

SMC_SCORE_WEIGHTS = {
    "macro_bias": 0.20,
    "structure": 0.20,
    "fvg_ob": 0.15,
    "liquidity_sweep": 0.10,
    "killzone": 0.10,
    "session_position": 0.05,
    "multi_tf_alignment": 0.10,
}


class SignalGenerator:
    def __init__(self, mt5):
        self.mt5 = mt5
        self._cache_ttl = 15
        self._rate_cache_db = RateCache()

    def _get_rates_cached(self, symbol, tf, count):
        cached = self._rate_cache_db.get_rates(symbol, tf, count)
        if cached is not None:
            return cached
        data = self.mt5.get_rates(symbol, tf, count)
        if data is not None:
            self._rate_cache_db.set_rates(symbol, tf, count, data, ttl=self._cache_ttl)
        return data

    def analyze(self, symbol, overrides=None):
        strats = STRATS.get(symbol)
        if strats is None:
            return None

        rates_dict = {}
        for cfg in strats:
            tf = cfg["tf"]
            data = self._get_rates_cached(symbol, tf, max(300, cfg["period"] + 200))
            if data is not None and len(data) >= max(cfg["period"], 30):
                rates_dict[tf] = data

        h1_data = self._get_rates_cached(symbol, "H1", 200)
        if h1_data is not None and len(h1_data) >= 50:
            rates_dict["H1"] = h1_data

        h4_data = self._get_rates_cached(symbol, "H4", 120)
        if h4_data is not None and len(h4_data) >= 50:
            rates_dict["H4"] = h4_data

        d1_data = self._get_rates_cached(symbol, "D1", 60)
        if d1_data is not None and len(d1_data) >= 50:
            rates_dict["D1"] = d1_data

        if not rates_dict:
            return None

        h1 = rates_dict.get("H1")
        if h1 is None:
            h1 = h1_data
        if h1 is None or len(h1) < 30:
            return None

        hh = np.array([r[2] for r in h1], dtype=float)
        ll = np.array([r[3] for r in h1], dtype=float)
        cc = np.array([r[4] for r in h1], dtype=float)
        h1_adx, _, _ = self._calc_adx(hh, ll, cc)
        rsi_arr = rsi(cc)
        rsi_arr[-1] if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50

        is_ranging = h1_adx < 25
        base_thresh = 2.5 if h1_adx >= 25 else 2.0
        sym_thresh_mult = {"XAUUSD": 1.25}.get(symbol, 1.0)
        base_thresh *= sym_thresh_mult
        base_thresh = max(1.2, min(3.0, base_thresh))

        adx_val = h1_adx

        # ── ICT/SMC Analysis ──
        macro = self._analyze_macro_bias(rates_dict)
        structure = self._analyze_h1_structure(rates_dict)
        fvg_analysis = self._analyze_fvg_obs(rates_dict, symbol)
        session_info = self._analyze_sessions(rates_dict)
        liq = self._analyze_liquidity(rates_dict, symbol)
        multi_tf = self._analyze_multi_tf(rates_dict)

        # Combined score from all ICT/SMC factors
        entry_signal, action, score, confidence, details = self._compute_smc_signal(
            macro, structure, fvg_analysis, session_info, liq, multi_tf,
            symbol, rates_dict,
        )

        if not entry_signal:
            logger.debug(f"  [ICT] {symbol}: no SMC signal (macro={macro['direction']}, "
                         f"structure={structure.get('trend','?')}, session={session_info.get('current_session','?')})")
            return None

        if action is None:
            return None

        # Compute ATR for SL/TP
        h1_atr = self._calc_atr(hh, ll, cc)
        mean_atr = h1_atr

        # Build confidence indicators for meta-learner
        agg = self._build_confluence_dict(macro, structure, fvg_analysis, session_info, liq, multi_tf)
        agg["adx"] = round(adx_val, 1)

        logger.info(f"  [ICT] {symbol}: {action} | score={score:.2f} conf={confidence:.2f} | "
                     f"macro={macro['direction']} struct={structure.get('trend','?')} "
                     f"fvg={len(fvg_analysis['active_fvgs'])} ob={len(fvg_analysis['active_obs'])} "
                     f"liq={liq['sweep_type'] or 'none'} session={session_info.get('current_session','?')}"
                     f" bias={session_info.get('session_bias','?')}")

        regime = structure.get("trend", "RANGING").upper()
        # market_structure.py retourne "BULLISH"/"BEARISH" (minuscules après .upper())
        # On normalise vers le format attendu par FTMO Protector
        regime_map = {"BULLISH": "TREND_UP", "BEARISH": "TREND_DOWN"}
        regime = regime_map.get(regime, regime)
        if regime not in ("TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL"):
            regime = "RANGING"

        # ── H1 Trend Gatekeeper (diversification timeframes) ──
        # Vérifie que le signal M5 ne va pas à l'encontre de la tendance H1.
        # Si conflit → confiance réduite de 30% (pénalité douce, pas blocage total).
        # Seuil ajusté de 50%→70% le 7 Juin 2026 (trop de faux négatifs).
        h1_ema20 = np.mean(cc[-20:]) if len(cc) >= 20 else None
        h1_ema50 = np.mean(cc[-50:]) if len(cc) >= 50 else None
        if h1_ema20 is not None and h1_ema50 is not None:
            h1_trend = "BUY" if h1_ema20 > h1_ema50 else "SELL" if h1_ema20 < h1_ema50 else "NEUTRAL"
            if h1_trend not in ("NEUTRAL", action):
                confidence *= 0.70  # conflit de timeframe → prudence douce
                logger.info(f"  [TREND FILTER] {symbol}: M5={action} vs H1={h1_trend} "
                            f"(EMA20={h1_ema20:.5f} EMA50={h1_ema50:.5f}) → confiance 70%")
            elif h1_trend == action:
                confidence *= 1.10  # alignement → bonus
                confidence = min(confidence, 1.0)
                logger.debug(f"  [TREND FILTER] {symbol}: M5={action} aligne H1={h1_trend} → bonus +10%")
            else:
                logger.debug(f"  [TREND FILTER] {symbol}: H1 NEUTRAL, aucun ajustement")

        return {
            "symbol": symbol,
            "action": action,
            "score": round(score, 2),
            "confidence": round(confidence, 2),
            "details": details,
            "quality": min(1.0, confidence + 0.1),
            "atr": mean_atr,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "risk_mult": 1.0,
            "rates": rates_dict,
            "adx": round(adx_val, 1),
            "is_ranging": is_ranging,
            "_regime": regime,
            "_ml_agrees": None,
            "_model_predictions": {"ICT_SMC": action},
            "_dl_score": None,
            "_confluence": {k: round(v, 3) for k, v in agg.items() if isinstance(v, (int, float))},
            "_fvgs": fvg_analysis["active_fvgs"][:5],
        }

    def _analyze_macro_bias(self, rates_dict):
        d1 = rates_dict.get("D1")
        h4 = rates_dict.get("H4")
        h1 = rates_dict.get("H1")
        d_close = np.array([r[4] for r in d1], dtype=float) if d1 is not None and len(d1) >= 50 else None
        h4_close = np.array([r[4] for r in h4], dtype=float) if h4 is not None and len(h4) >= 50 else None
        h1_close = np.array([r[4] for r in h1], dtype=float) if h1 is not None and len(h1) >= 50 else None

        if d_close is not None and h4_close is not None and h1_close is not None:
            bias = multi_tf_bias(d_close, h4_close, h1_close)
        elif d_close is not None:
            d_ma20 = np.mean(d_close[-20:])
            d_ma50 = np.mean(d_close[-50:])
            diff = (d_ma20 - d_ma50) / max(d_ma50, 0.0001)
            direction = "BUY" if diff > 0.0005 else "SELL" if diff < -0.0005 else "NEUTRAL"
            bias = {"direction": direction, "alignment": 1 if direction == "BUY" else -1 if direction == "SELL" else 0, "conviction": abs(diff) * 100}
        else:
            bias = {"direction": "NEUTRAL", "alignment": 0, "conviction": 0}

        return bias

    def _analyze_h1_structure(self, rates_dict):
        h1 = rates_dict.get("H1")
        if h1 is None or len(h1) < 30:
            return {"trend": "neutral", "score": 0, "bos_type": None, "choch_type": None, "has_sweep": False}

        hh = np.array([r[2] for r in h1], dtype=float)
        ll = np.array([r[3] for r in h1], dtype=float)
        cc = np.array([r[4] for r in h1], dtype=float)

        ms = analyze_market_structure(hh, ll, cc)
        return ms

    def _analyze_fvg_obs(self, rates_dict, symbol):
        h1 = rates_dict.get("H1")
        result = {"active_fvgs": [], "active_obs": [], "active_imbalances": [], "fvg_bonus": 0.0}

        if h1 is None or len(h1) < 20:
            return result

        hh = np.array([r[2] for r in h1], dtype=float)
        ll = np.array([r[3] for r in h1], dtype=float)
        cc = np.array([r[4] for r in h1], dtype=float)

        raw_fvgs = detect_fvg(hh, ll, lookback=10)
        curr_high = hh[-1] if len(hh) > 0 else 0
        curr_low = ll[-1] if len(ll) > 0 else 0
        result["active_fvgs"] = filter_active_fvgs(raw_fvgs, curr_high, curr_low)

        obs = find_order_blocks(hh, ll, cc, lookback=20)
        result["active_obs"] = [ob for ob in obs if not ob.get("is_mitigated", True)]

        imbalances = find_imbalances(hh, ll, cc, lookback=20)
        result["active_imbalances"] = [im for im in imbalances if not im.get("is_mitigated", True)]

        return result

    def _analyze_sessions(self, rates_dict):
        h1 = rates_dict.get("H1")
        result = {"current_session": "off_hours", "active_killzone": None,
                   "session_weight": 0, "session_bias": "neutral", "session_position": 0.5}

        if h1 is not None and len(h1) >= 24:
            hh = np.array([r[2] for r in h1], dtype=float)
            ll = np.array([r[3] for r in h1], dtype=float)
            cc = np.array([r[4] for r in h1], dtype=float)
            tt = np.array([r[0] for r in h1], dtype=float)
            sess = analyze_sessions(hh, ll, cc, tt)
            result.update(sess)

        active_kz = get_active_killzone()
        result["active_killzone"] = active_kz

        return result

    def _analyze_liquidity(self, rates_dict, symbol):
        result = {"sweep_type": None, "sweep_level": None, "has_sweep": False}

        h4 = rates_dict.get("H4")
        h1 = rates_dict.get("H1")
        if h4 is None or h1 is None or len(h4) < 10 or len(h1) < 5:
            return result

        h4h = np.array([r[2] for r in h4], dtype=float)
        h4l = np.array([r[3] for r in h4], dtype=float)
        h1h = np.array([r[2] for r in h1], dtype=float)
        h1l = np.array([r[3] for r in h1], dtype=float)
        h1c = np.array([r[4] for r in h1], dtype=float)

        sweep_type, sweep_level = detect_liquidity_sweep(h4h, h4l, h1h, h1l, h1c)
        result["sweep_type"] = sweep_type
        result["sweep_level"] = sweep_level
        result["has_sweep"] = sweep_type is not None

        return result

    def _analyze_multi_tf(self, rates_dict):
        d1 = rates_dict.get("D1")
        h4 = rates_dict.get("H4")
        h1 = rates_dict.get("H1")

        if d1 is not None and h4 is not None and h1 is not None:
            d_close = np.array([r[4] for r in d1], dtype=float) if len(d1) >= 50 else None
            h4_close = np.array([r[4] for r in h4], dtype=float) if len(h4) >= 50 else None
            h1_close = np.array([r[4] for r in h1], dtype=float) if len(h1) >= 50 else None
            if d_close is not None and h4_close is not None and h1_close is not None:
                direction, alignment = multi_tf_alignment(d_close, h4_close, h1_close)
                return {"direction": direction, "alignment": alignment}

        return {"direction": "NO_TRADE", "alignment": 0}

    def _compute_smc_signal(self, macro, structure, fvg_analysis, session_info, liq, multi_tf, symbol, rates_dict):
        action = None
        entry_signal = False
        factors = {"macro_bias": 0, "structure": 0, "fvg_ob": 0, "liquidity_sweep": 0,
                    "killzone": 0, "session_position": 0, "multi_tf_alignment": 0}

        # Macro bias
        macro_dir = macro.get("direction", "NEUTRAL")
        if macro_dir == "BUY":
            factors["macro_bias"] = 1
        elif macro_dir == "SELL":
            factors["macro_bias"] = -1
        else:
            factors["macro_bias"] = 0

        # Market structure
        struct_score = structure.get("score", 0)
        factors["structure"] = struct_score

        # BOS/CHOCH confirmation
        structure.get("recent_bos", False)
        structure.get("recent_choch", False)
        recent_sweeps = structure.get("recent_sweeps", [])
        structure.get("sweeps", [])

        # Structure-aligned bias

        # FVG/OB analysis
        active_fvgs = fvg_analysis["active_fvgs"]
        active_obs = fvg_analysis["active_obs"]
        active_imbalances = fvg_analysis["active_imbalances"]

        # Count support/resistance FVGs for each direction
        bull_fvgs = sum(1 for f in active_fvgs if f["type"] == "BULL")
        bear_fvgs = sum(1 for f in active_fvgs if f["type"] == "BEAR")
        bull_obs = sum(1 for ob in active_obs if "bullish" in ob.get("type", ""))
        bear_obs = sum(1 for ob in active_obs if "bearish" in ob.get("type", ""))
        bull_imbalances = sum(1 for im in active_imbalances if "bullish" in im.get("type", ""))
        bear_imbalances = sum(1 for im in active_imbalances if "bearish" in im.get("type", ""))

        fvg_bias = (bull_fvgs + bull_obs + bull_imbalances) - (bear_fvgs + bear_obs + bear_imbalances)
        factors["fvg_ob"] = np.sign(fvg_bias) * min(1.0, abs(fvg_bias) * 0.3)

        # Liquidity sweep
        sweep_type = liq.get("sweep_type")
        if sweep_type == "SWEEP_LOW":
            factors["liquidity_sweep"] = 1
        elif sweep_type == "SWEEP_HIGH":
            factors["liquidity_sweep"] = -1

        # Sweeps from market structure
        if recent_sweeps and not sweep_type:
            for sw in recent_sweeps:
                if sw["type"] == "bullish_sweep":
                    if factors["liquidity_sweep"] == 0:
                        factors["liquidity_sweep"] = 1
                elif sw["type"] == "bearish_sweep" and factors["liquidity_sweep"] == 0:
                    factors["liquidity_sweep"] = -1

        # Killzone (non-directionnel: booste le score du signal, pas un côté)
        active_kz = session_info.get("active_killzone")
        factors["killzone"] = 0  # neutre — ne favorise ni BUY ni SELL

        # Session position (directional: près du haut = résistance, près du bas = support)
        sess_pos = session_info.get("session_position", 0.5)
        # Normaliser session_position en direction: 0→bas = support(BUY), 1→haut = resistance(SELL)
        factors["session_position"] = (1.0 - sess_pos * 2)  # -1 (haut=SELL) à +1 (bas=BUY)

        # Multi-TF alignment
        multi_tf.get("direction", "NO_TRADE")
        mtf_alignment = multi_tf.get("alignment", 0)
        factors["multi_tf_alignment"] = mtf_alignment / 3.0 if mtf_alignment != 0 else 0

        # Calculate weighted score for each direction
        buy_score = 0
        sell_score = 0
        for factor, value in factors.items():
            weight = SMC_SCORE_WEIGHTS.get(factor, 0.1)
            if value > 0:
                buy_score += weight * value
            elif value < 0:
                sell_score += weight * abs(value)

        # Additional bonus for extreme confluence
        if factors["macro_bias"] > 0 and factors["structure"] > 0 and factors["liquidity_sweep"] > 0:
            buy_score += 0.10
        if factors["macro_bias"] < 0 and factors["structure"] < 0 and factors["liquidity_sweep"] < 0:
            sell_score += 0.10

        if factors["macro_bias"] > 0 and factors["killzone"] > 0 and fvg_bias > 0:
            buy_score += 0.05
        if factors["macro_bias"] < 0 and factors["killzone"] > 0 and fvg_bias < 0:
            sell_score += 0.05

        # H1 trendline alignment
        tlines = structure.get("trendlines", {})
        if tlines.get("ascending") and factors["macro_bias"] > 0:
            buy_score += 0.05
        if tlines.get("descending") and factors["macro_bias"] < 0:
            sell_score += 0.05

        # Killzone bonus (non-directionnel: booste la confiance des DEUX côtés)
        if active_kz:
            kz_bonus = 0.05
            buy_score += kz_bonus
            sell_score += kz_bonus

        # Determine action
        threshold = 0.30
        if buy_score > sell_score and buy_score >= threshold:
            action = "BUY"
            entry_signal = True
            score = min(0.95, 0.15 + buy_score)
            confidence = min(0.90, 0.15 + buy_score * 0.8)
            details = f"ICT_BUY_{active_kz or session_info.get('current_session','?')}_S{buy_score:.1f}"
        elif sell_score > buy_score and sell_score >= threshold:
            action = "SELL"
            entry_signal = True
            score = min(0.95, 0.15 + sell_score)
            confidence = min(0.90, 0.15 + sell_score * 0.8)
            details = f"ICT_SELL_{active_kz or session_info.get('current_session','?')}_S{sell_score:.1f}"
        else:
            entry_signal = False
            action = None
            score = 0.0
            confidence = 0.0
            details = f"ICT_NO_TRADE_B{buy_score:.1f}_S{sell_score:.1f}"

        return entry_signal, action, score, confidence, details

    def _build_confluence_dict(self, macro, structure, fvg_analysis, session_info, liq, multi_tf):
        return {
            "macro_bias": 1 if macro.get("direction") == "BUY" else -1 if macro.get("direction") == "SELL" else 0,
            "macro_conviction": macro.get("conviction", 0),
            "structure_score": structure.get("score", 0),
            "structure_trend": 1 if structure.get("trend") == "bullish" else -1 if structure.get("trend") == "bearish" else 0,
            "bullish_bos": 1 if structure.get("bos", {}).get("bullish_bos") else 0,
            "bearish_bos": 1 if structure.get("bos", {}).get("bearish_bos") else 0,
            "bullish_choch": 1 if structure.get("choch", {}).get("bullish_choch") else 0,
            "bearish_choch": 1 if structure.get("choch", {}).get("bearish_choch") else 0,
            "active_fvgs": len(fvg_analysis.get("active_fvgs", [])),
            "active_obs": len(fvg_analysis.get("active_obs", [])),
            "active_imbalances": len(fvg_analysis.get("active_imbalances", [])),
            "fvg_bias": 0,
            "liq_sweep": 1 if liq.get("sweep_type") == "SWEEP_LOW" else -1 if liq.get("sweep_type") == "SWEEP_HIGH" else 0,
            "killzone_active": 1 if session_info.get("active_killzone") else 0,
            "session_weight": session_info.get("session_weight", 0),
            "session_bias": 1 if session_info.get("session_bias") == "premium" else -1 if session_info.get("session_bias") == "discount" else 0,
            "mtf_alignment": multi_tf.get("alignment", 0),
            "unmitigated_obs": structure.get("unmitigated_obs", 0),
            "unmitigated_fvgs": structure.get("unmitigated_fvgs", 0),
        }

    def _get_atr(self, symbol, period=14):
        rates = self._get_rates_cached(symbol, "H1", period + 10)
        if rates is None or len(rates) < period:
            return None
        hh = np.array([r[2] for r in rates], dtype=float)
        ll = np.array([r[3] for r in rates], dtype=float)
        cc = np.array([r[4] for r in rates], dtype=float)
        tr = np.maximum(hh[1:] - ll[1:], np.maximum(np.abs(hh[1:] - cc[:-1]), np.abs(ll[1:] - cc[:-1])))
        return float(np.mean(tr[-period:]))

    def _calc_atr(self, high, low, close, period=14):
        tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
        return float(np.mean(tr[-period:])) if len(tr) >= period else 0.001

    def _calc_adx(self, high, low, close, p=14):
        return adx(high, low, close, p)


