import logging
import os
from collections import deque
from datetime import datetime

import joblib
import numpy as np

from engine_simple.dl_ensemble import DLEnsemble
from engine_simple.fvg_detector import detect_fvg, detect_liquidity_sweep, filter_active_fvgs, fvg_score
from engine_simple.indicators import obv, rsi, rsi_divergence
from engine_simple.market_structure import analyze_market_structure
from engine_simple.meta_learner import MetaLearner
from engine_simple.structure_analyzer import multi_tf_alignment
try:
    from engine_simple.walk_forward_validator import WalkForwardValidator
except ImportError:
    try:
        from scripts.recalibration.walk_forward_validator import WalkForwardValidator
    except ImportError:
        WalkForwardValidator = None  # fallback : pas de validateur

logger = logging.getLogger("adaptive")


class MarketRegime:
    """Enhanced regime detection — délègue à regime.py + enrichit avec structure/volume."""

    def __init__(self):
        from engine_simple.regime import RegimeDetector
        self._detector = RegimeDetector()

    def detect(self, rates):
        closes = np.array([r[4] for r in rates], dtype=float)
        highs = np.array([r[2] for r in rates], dtype=float)
        lows = np.array([r[3] for r in rates], dtype=float)
        volumes = np.array([r[5] for r in rates], dtype=float) if len(rates[0]) > 5 else np.ones_like(closes)

        if len(closes) < 30:
            return "RANGING", {"adx": 20, "vol_percentile": 0.5, "structure_trend": "unknown"}

        # Hook _adx pour compatibilité tests (peut être patché)
        adx_val = self._adx(highs, lows, closes)

        # Délégation au nouveau détecteur
        regime, meta = self._detector.detect(highs, lows, closes, adx_val=adx_val)

        # Enrichissement avec structure de marché, volume, RSI
        ms = analyze_market_structure(highs, lows, closes)
        structure_trend = ms.get("trend", "unknown")

        obv_arr = obv(closes, volumes)
        obv_trend = 0
        if len(obv_arr) > 20:
            obv_trend = 1 if obv_arr[-1] > obv_arr[-20] else -1

        rsi_arr = rsi(closes)
        rsi_now = rsi_arr[-1] if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50
        div = rsi_divergence(closes, rsi_arr, lookback=20)

        volume_confirms = (obv_trend > 0 and structure_trend == "bullish") or \
                          (obv_trend < 0 and structure_trend == "bearish")

        return regime, {
            "adx": round(meta["adx"], 1),
            "vol_percentile": round(meta["vol_percentile"], 2),
            "structure_trend": structure_trend,
            "structure_score": round(ms.get("score", 0), 2),
            "obv_trend": obv_trend,
            "rsi": round(rsi_now, 1),
            "volume_confirms": volume_confirms,
            "confidence_bonus": 0.10 if volume_confirms else 0,
            "rsi_divergence": div.get("bullish", False) or div.get("bearish", False),
            "eq_hl_count": ms.get("equal_highs_lows", {}).get("count", 0),
        }

    def _adx(self, highs, lows, closes, p=14):
        """Hook pour compatibilité tests. Délègue à regime._calc_adx."""
        return self._detector._calc_adx(highs, lows, closes)


class OnlineLearner:
    def __init__(self, window=200):
        self.window = window
        self.history = {}
        self.adapted_params = {}

    def record_trade(self, symbol, r_multiple, regime):
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.window)
        self.history[symbol].append({"r": r_multiple, "regime": regime})
        self._update_params(symbol)

    def get_params(self, symbol, base_thresh=3.0):
        if symbol not in self.adapted_params:
            return {"thresh": base_thresh, "risk_mult": 1.0, "sl_mult": 3.0, "tp_mult": 1.0}
        return self.adapted_params[symbol]

    def _update_params(self, symbol):
        h = list(self.history.get(symbol, []))
        if len(h) < self.window // 2:
            return
        rr = np.array([t["r"] for t in h])
        wr = np.mean(rr > 0)
        expectancy = np.mean(rr)
        thresh = 2.5
        risk_mult = 1.0
        if wr < 0.70:
            thresh = 2.5
            risk_mult = 0.75
        elif wr > 0.82:
            thresh = 2.0
            risk_mult = 1.15
        elif wr > 0.78:
            thresh = 2.3
            risk_mult = 1.05
        if expectancy < 0 and len(h) > 10:
            risk_mult = 0.5
        self.adapted_params[symbol] = {
            "thresh": thresh, "risk_mult": risk_mult,
            "sl_mult": 3.0, "tp_mult": 1.0,
        }

    def get_summary(self, symbol):
        h = list(self.history.get(symbol, []))
        if not h:
            return {}
        rr = np.array([t["r"] for t in h])
        return {
            "trades": len(h), "wr": round(np.mean(rr > 0), 3),
            "avg_r": round(np.mean(rr), 3), "expectancy": round(np.mean(rr), 3),
        }


# Symbols ou DL est pire que aleatoire
DL_MIN_SCORE = 0.60  # score < 0.60 = 33.5% accuracy (pire que hasard)

class AdaptiveEngine:
    def __init__(self, mt5, calibration_path=None):
        self.mt5 = mt5
        self.regime = MarketRegime()
        self.learner = OnlineLearner()
        self.meta = MetaLearner(recalibration_freq=50)
        self.dl = DLEnsemble()
        # ML Ensemble (RF/XGB/LGBM) désactivé — info-only à 45%, 581 MB RAM pour rien
        self.ml = None
        self.lgb = None  # LightGBM désactivé (poids=0)
        logger.info(f"Meta-Learner ready, tracking {len(self.meta.get_model_names())} models")
        self.calibration_path = calibration_path
        if calibration_path:
            self._load_calibration(calibration_path)
        if WalkForwardValidator is not None:
            self.validator = WalkForwardValidator(snapshot_interval=50)
            logger.info("Walk-Forward Validator ready")
        else:
            self.validator = None
            logger.warning("Walk-Forward Validator unavailable (module moved)")

    def _load_calibration(self, path):
        if not os.path.exists(path):
            logger.warning(f"  [CAL] Calibration file not found: {path}")
            return
        try:
            state = joblib.load(path)
            mc = state.get("meta_calibration", {})
            mc_calibrated = mc.get("meta_calibration", mc)
            # Restore MetaLearner trackers
            for name, tdata in mc_calibrated.get("meta_trackers", {}).items():
                if name in self.meta.trackers:
                    t = self.meta.trackers[name]
                    for stat_key, stat_val in tdata.items():
                        store = getattr(t, stat_key, None)
                        if store is not None:
                            store.clear()
                            store.update(stat_val)
            # Restore regime performance + penalties
            rp = mc_calibrated.get("meta_regime_performance", {})
            if rp:
                self.meta.regime_performance.clear()
                for k, v in rp.items():
                    self.meta.regime_performance[k] = v
            rp_penalty = mc_calibrated.get("meta_regime_penalty", {})
            if rp_penalty:
                for model_name, penalties in rp_penalty.items():
                    t = self.meta.trackers.get(model_name)
                    if t:
                        for regime, penalty in penalties.items():
                            t.regime_penalty[regime] = penalty
            self.meta.trades_since_recal = mc_calibrated.get("meta_trades_since_recal", 0)
            # Restore OnlineLearner history
            ol = state.get("online_history", {})
            for sym, hist_list in ol.items():
                self.learner.history[sym] = deque(maxlen=self.learner.window)
                for h in hist_list:
                    self.learner.history[sym].append(h)
                self.learner._update_params(sym)
            counts = sum(len(v) for v in state.get("online_history", {}).values())
            n_trackers = len(mc_calibrated.get("meta_trackers", {}))
            logger.info(f"  [CAL] Loaded calibration: MetaLearner {n_trackers} "
                        f"trackers, OnlineLearner {counts} records")
        except (KeyError, ValueError, TypeError, AttributeError, OSError) as e:
            logger.warning(f"  [CAL] Failed to load calibration: {e}")

    def _save_calibration(self):
        if not self.calibration_path:
            return
        try:
            mc = {
                "meta_trackers": {
                    name: {
                        "regime_stats": dict(tracker.regime_stats),
                        "global_stats": dict(tracker.global_stats),
                        "symbol_stats": dict(tracker.symbol_stats),
                    }
                    for name, tracker in self.meta.trackers.items()
                },
                "meta_regime_performance": dict(self.meta.regime_performance),
                "meta_regime_penalty": {
                    name: dict(tracker.regime_penalty)
                    for name, tracker in self.meta.trackers.items()
                },
                "meta_trades_since_recal": self.meta.trades_since_recal,
            }
            state = {
                "meta_calibration": {"meta_calibration": mc},
                "online_history": {
                    sym: list(h) for sym, h in self.learner.history.items()
                },
            }
            joblib.dump(state, self.calibration_path)
        except (OSError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"  [CAL] Failed to save calibration: {e}")

    def vigilance(self, symbol, rates_dict):
        """Run full pipeline (regime + DL) for any symbol without needing a signal. Logs everything."""
        h1_rates = rates_dict.get("H1")
        if h1_rates is None or len(h1_rates) < 50:
            return None
        regime, meta = self.regime.detect(h1_rates)
        dl_result = None
        dl_label = "N/A"
        if self.dl.available:
            try:
                dl_result = self.dl.predict(symbol, rates_dict)
                if dl_result:
                    dl_score = dl_result.get("score", 0)
                    dl_label = f"{dl_result['action']} ({dl_result['buy_prob']:.3f})"
                    if dl_score < DL_MIN_SCORE:
                        dl_label = f"IGNORE (score={dl_score:.2f} < {DL_MIN_SCORE})"
                        dl_result = None
                    logger.info(f"  [VIGIL] {symbol}: regime={regime} DL={dl_label} ADX={meta['adx']:.0f}")
            except (ValueError, TypeError, IndexError, AttributeError) as e:
                logger.debug(f"  [VIGIL] {symbol}: DL error: {e}")
        return {"symbol": symbol, "regime": regime, "regime_meta": meta,
                "dl_action": dl_result["action"] if dl_result else None,
                "dl_score": dl_result["score"] if dl_result else None,
                "dl_buy_prob": dl_result["buy_prob"] if dl_result else None}

    def analyze(self, symbol, rates_dict, signal, trade_stats=None):
        h1_rates = rates_dict.get("H1")
        if h1_rates is None or len(h1_rates) < 50:
            return signal

        regime, meta = self.regime.detect(h1_rates)
        logger.info(f"  [REGIME] {symbol}: {regime} (ADX={meta['adx']}, vol%={meta['vol_percentile']}, "
            f"struct={meta['structure_trend']}, vol_confirm={meta['volume_confirms']})")

        params = dict(self.learner.get_params(symbol))  # copie pour éviter mutation in-place

        # Multi-TF alignment (institutional structure filter)
        d_rates = rates_dict.get("D1")
        h4_rates = rates_dict.get("H4")
        alignment_dir, alignment_score = "NO_TRADE", 0
        if d_rates is not None and h4_rates is not None and len(d_rates) >= 50 and len(h4_rates) >= 50 and len(h1_rates) >= 50:
            d_close = np.array([r[4] for r in d_rates], dtype=float)
            h4_close = np.array([r[4] for r in h4_rates], dtype=float)
            h1_close = np.array([r[4] for r in h1_rates], dtype=float)
            alignment_dir, alignment_score = multi_tf_alignment(d_close, h4_close, h1_close)
            if alignment_dir != "NO_TRADE":
                signal_action = signal.get("action", "HOLD")
                if alignment_dir == signal_action or alignment_score >= 2:
                    logger.info(f"  [STRUCTURE] {symbol}: multi-TF={alignment_dir} (score={alignment_score}) → OK")
                else:
                    logger.info(f"  [STRUCTURE] {symbol}: multi-TF={alignment_dir} vs signal={signal_action} → CONFLIT")
            else:
                logger.info(f"  [STRUCTURE] {symbol}: multi-TF={alignment_score} → neutre/conflit")

        # FVG + liquidity sweep detection (institutional)
        h1h_arr = np.array([r[2] for r in h1_rates], dtype=float)
        h1l_arr = np.array([r[3] for r in h1_rates], dtype=float)
        h1c_arr = np.array([r[4] for r in h1_rates], dtype=float)
        raw_fvgs = detect_fvg(h1h_arr, h1l_arr, lookback=10)
        active_fvgs = filter_active_fvgs(raw_fvgs, h1h_arr[-1], h1l_arr[-1])
        fvg_bonus = 0.0
        if active_fvgs:
            fvg_bonus = fvg_score(active_fvgs, signal.get("action", "HOLD"))
            if fvg_bonus != 0:
                logger.info(f"  [FVG] {symbol}: {len(active_fvgs)} actifs, bonus={fvg_bonus:+.2f}")
        sweep_type, sweep_level = None, None
        if h4_rates is not None and len(h4_rates) >= 10:
            h4h_arr = np.array([r[2] for r in h4_rates], dtype=float)
            h4l_arr = np.array([r[3] for r in h4_rates], dtype=float)
            sweep_type, sweep_level = detect_liquidity_sweep(h4h_arr, h4l_arr, h1h_arr, h1l_arr, h1c_arr)
            if sweep_type:
                logger.info(f"  [LIQUIDITY] {symbol}: {sweep_type} @ {sweep_level}")

        # Collect predictions from ALL models
        all_predictions = {"MOM20x3": {"action": signal.get("action", "HOLD"), "score": signal.get("score", 0.5)}}

        dl_result = None
        if self.dl.available:
            try:
                dl_result = self.dl.predict(symbol, rates_dict)
                if dl_result:
                    dl_score = dl_result.get("score", 0)
                    if dl_score < DL_MIN_SCORE:
                        logger.info(f"  [DL] {symbol}: IGNORE (score={dl_score:.2f} < {DL_MIN_SCORE})")
                        dl_result = None
                    else:
                        all_predictions["DL_LSTM"] = dl_result
                        dl_agrees = dl_result.get("action", "HOLD") == signal.get("action", "HOLD")
                        logger.info(f"  [DL] {symbol}: {dl_result['action']} (score={dl_score:.3f}, agree={dl_agrees})")
            except (ValueError, TypeError, IndexError, AttributeError, KeyError) as e:
                logger.debug(f"  [DL] {symbol}: predict error: {e}")


        # Meta-Learner: combine predictions (≥ 2 modèles suffit: MOM20x3 + DL LSTM)
        meta_action, meta_confidence = "HOLD", 0.5
        devil_disagreements = []
        if len(all_predictions) >= 2:
            meta_action, meta_confidence, _ = (
                self.meta.get_ensemble_action(regime, all_predictions, symbol=symbol))
            devil_disagreements = self.meta.devil_advocate_check(
                regime, all_predictions, signal.get("action", "HOLD"), symbol=symbol)

        adapted = dict(signal)

        # Regime-based SL/TP (R:R ≥ 2.0 pour FTMO expectancy positive)
        if regime == "TREND_DOWN" or regime == "TREND_UP":
            adapted["sl_atr"] = 2.0  # R:R = 5.0/2.0 = 2.5
            adapted["tp_atr"] = 5.0
        elif regime == "HIGH_VOL":
            adapted["sl_atr"] = 2.0  # R:R = 5.0/2.0 = 2.5
            adapted["tp_atr"] = 5.0
            params["risk_mult"] *= 0.7
        elif regime == "LOW_VOL":
            adapted["sl_atr"] = 2.0  # R:R = 4.5/2.0 = 2.25 (FTMO: SL plus large évite stop-outs)
            adapted["tp_atr"] = 4.5
        else:
            adapted["sl_atr"] = 2.0  # R:R = 4.5/2.0 = 2.25 (RANGING: SL plus large = moins de bruit)
            adapted["tp_atr"] = 4.5

        adapted["risk_mult"] = params["risk_mult"]

        # DL ignored en regime RANGING → risk/2 (MOM20x3 seul en ranging est bruyant)
        if dl_result is None and regime == "RANGING":
            adapted["risk_mult"] *= 0.5
            logger.info(f"  [DL-IGNORE RANGING] {symbol}: risk/2 (MOM20x3 seul en ranging, DL score<{DL_MIN_SCORE})")

        # Devil's Advocate: modèle fort (poids>0.15) en désaccord → risk/2
        # Prioritaire sur le simple MOM/DL check (seuil plus haut : score>0.65)
        _devil_applied = False
        if len(devil_disagreements) > 0:
            adapted["risk_mult"] *= 0.5
            _devil_applied = True
            models_str = [d["model"] for d in devil_disagreements]
            logger.info(f"  [DEVIL] {symbol}: {models_str} disagree → risk/2")

        # MOM/DL AGREEMENT check (seulement si Devil n'a pas déjà réduit le risque)
        mom_action = signal.get("action", "HOLD")
        if not _devil_applied and dl_result and dl_result.get("action", "HOLD") in ("BUY", "SELL") and mom_action in ("BUY", "SELL"):
            if dl_result["action"] != mom_action:
                logger.info(f"  [AGREEMENT] {symbol}: MOM={mom_action} "
                            f"DL={dl_result['action']} → DISAGREE, risk/2")
                adapted["risk_mult"] *= 0.5
            else:
                logger.info(f"  [AGREEMENT] {symbol}: MOM={mom_action} DL={dl_result['action']} → AGREE ✓")
                adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.10)

        # Meta-ensemble decision overrides signal if high confidence
        if meta_action != "HOLD" and meta_confidence > 0.65 and meta_action != signal.get("action", "HOLD"):
            logger.info(f"  [META] {symbol}: meta={meta_action} (conf={meta_confidence:.2f}) "
                        f"vs MOM={signal.get('action')} → meta override")
            adapted["action"] = meta_action
            adapted["confidence"] = min(0.95, meta_confidence)
            adapted["score"] = min(0.99, meta_confidence)

        # Structure alignment bonus/penalty
        if alignment_score >= 2:
            adapted["score"] = min(0.99, adapted.get("score", 0.5) + 0.10)
            adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.08)
            logger.info(f"  [STRUCTURE] {symbol}: alignment={alignment_score} → +0.10 score")
        elif alignment_score <= -2:
            if signal.get("action") == "SELL":
                adapted["score"] = min(0.99, adapted.get("score", 0.5) + 0.10)
                adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.08)
                logger.info(f"  [STRUCTURE] {symbol}: alignment={alignment_score} → bearish OK +0.10")
            else:
                adapted["risk_mult"] *= 0.5
                logger.info(f"  [STRUCTURE] {symbol}: alignment={alignment_score} → bearish vs BUY, risk/2")
        elif alignment_score == 0:
            adapted["risk_mult"] *= 0.5
            logger.info(f"  [STRUCTURE] {symbol}: conflit alignment=0 → risk/2")

        # FVG bonus
        if fvg_bonus:
            adapted["score"] = min(0.99, adapted.get("score", 0.5) + fvg_bonus)
            adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + abs(fvg_bonus) * 0.5)

        # Regime bonus
        regime_bonus = {"TREND_UP": 0.08, "TREND_DOWN": 0.08, "HIGH_VOL": -0.05,
                        "LOW_VOL": 0.03, "RANGING": 0.0}.get(regime, 0.0)
        regime_bonus += meta.get("confidence_bonus", 0)
        adapted["score"] = min(0.99, adapted.get("score", 0.5) + regime_bonus)
        adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + regime_bonus * 0.5)

        # Session boost institutionnel (basé sur liquidité réelle)
        h = datetime.utcnow().hour
        if 7 <= h < 9:
            adapted["score"] = min(0.99, adapted.get("score", 0.5) + 0.10)
            adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.08)
        elif 13 <= h < 15:
            adapted["score"] = min(0.99, adapted.get("score", 0.5) + 0.08)
            adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.06)
        elif 0 <= h < 6:
            adapted["score"] = max(0.30, adapted.get("score", 0.5) - 0.10)
            adapted["confidence"] = max(0.30, adapted.get("confidence", 0.5) - 0.08)

        # Meta confidence boost
        if meta_action == adapted.get("action") and meta_confidence > 0.6:
            adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + meta_confidence * 0.1)

        # Trade stats: historique réel par symbole → ajustement confiance/risque
        if trade_stats and trade_stats.get("trade_count", 0) >= 10:
            wr = trade_stats.get("trade_winrate", 0.5)
            pf = trade_stats.get("trade_profit_factor", 1)
            # WR > 65% → bonus confiance, WR < 45% → pénalité
            wr_bonus = (wr - 0.5) * 0.2
            # Profit factor > 1.5 → confirmation de qualité
            pf_bonus = min(0.05, (pf - 1.0) * 0.03)
            # Ajustement score/confiance
            adapted["score"] = min(0.99, adapted.get("score", 0.5) + wr_bonus + pf_bonus)
            adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + wr_bonus * 0.5 + pf_bonus * 0.5)
            # Ajustement risque : WR faible → risque réduit
            if wr < 0.45:
                adapted["risk_mult"] *= 0.7
                logger.info(f"  [STATS] {symbol}: WR={wr:.0%} < 45% → risk/1.43")
            elif wr > 0.65:
                logger.info(f"  [STATS] {symbol}: WR={wr:.0%} > 65% → bonus +{wr_bonus+.02:.0%}")
            if pf < 0.8 and trade_stats.get("trade_count", 0) > 20:
                adapted["risk_mult"] *= 0.5
                logger.info(f"  [STATS] {symbol}: PF={pf:.1f} < 0.8 → risk/2")

        adapted["_regime"] = regime
        adapted["_dl_score"] = dl_result.get("score") if dl_result else None
        adapted["_meta_action"] = meta_action
        adapted["_meta_confidence"] = round(meta_confidence, 3)
        adapted["_devil"] = len(devil_disagreements)
        adapted["_model_predictions"] = dict(all_predictions)
        if dl_result:
            adapted["_ml_agrees"] = dl_result.get("action", "HOLD") == signal.get("action", "HOLD")
        else:
            adapted["_ml_agrees"] = None
        # Institutional analysis fields
        adapted["_alignment_dir"] = alignment_dir
        adapted["_alignment_score"] = alignment_score
        adapted["_fvgs"] = active_fvgs if active_fvgs else []
        adapted["_sweep_type"] = sweep_type
        adapted["_sweep_level"] = sweep_level

        # Recalibrate meta-learner periodically
        if self.meta.should_recalibrate():
            self.meta.recalibrate()

        return adapted

    def save_calibration(self):
        self._save_calibration()

    def record_result(self, symbol, r_multiple, regime=None, dl_features=None):
        self.learner.record_trade(symbol, r_multiple, regime)
        if dl_features is not None and self.dl.available:
            self.dl.record_trade(symbol, dl_features, r_multiple)

    def record_meta_result(self, symbol, regime, predictions_outcomes):
        self.meta.record_trade(symbol, regime, predictions_outcomes)
        for mname, correct in predictions_outcomes.items():
            self.validator.record(mname, correct, regime, symbol)

    def train_dl_if_ready(self):
        if self.dl.available:
            total = sum(len(v) for v in self.dl.training_buffer.values())
            if total >= 32:
                self.dl.train_all()
                self._save_calibration()
                n_symbols = sum(1 for v in self.dl.training_buffer.values() if len(v) >= 32)
                logger.info(f"  [DL] Online training: {total} samples across {n_symbols} symbols")

    def build_dl_features(self, rates_dict):
        if not self.dl.available:
            return None
        h1 = rates_dict.get("H1")
        if h1 is None:
            return None
        try:
            return self.dl._build_sequence(h1)
        except (ValueError, TypeError, IndexError):
            return None

    def get_validation_report(self):
        return self.validator.get_report()

    def get_report(self, symbol):
        return self.learner.get_summary(symbol)
