import logging
import os
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

from engine_simple.indicators import obv, rsi, rsi_divergence

try:
    from engine_simple.market_structure import analyze_market_structure
except ImportError:
    analyze_market_structure = None
from engine_simple.structure_analyzer import multi_tf_alignment
from engine_simple.lightgbm_model import LightGBMModel

logger = logging.getLogger("adaptive")


class MarketRegime:
    """Enhanced regime detection — délègue à regime.py + enrichit avec structure/volume."""

    def __init__(self):
        from engine_simple.regime import RegimeDetector

        self._detector = RegimeDetector()

    def detect(self, rates, symbol: str = "_default"):
        closes = np.array([r[4] for r in rates], dtype=float)
        highs = np.array([r[2] for r in rates], dtype=float)
        lows = np.array([r[3] for r in rates], dtype=float)
        volumes = np.array([r[5] for r in rates], dtype=float) if len(rates[0]) > 5 else np.ones_like(closes)

        if len(closes) < 30:
            return "RANGING", {"adx": 20, "vol_percentile": 0.5, "structure_trend": "unknown"}

        # Hook _adx pour compatibilité tests (peut être patché)
        _adx_result = self._adx(highs, lows, closes)
        if isinstance(_adx_result, (int, float)):
            adx_val = float(_adx_result)
        else:
            adx_val, _, _ = _adx_result

        # Délégation au nouveau détecteur (avec symbole pour hystérésis par symbole)
        regime, meta = self._detector.detect(highs, lows, closes, adx_val=adx_val, symbol=symbol)

        # Enrichissement avec structure de marché, volume, RSI
        _ms = None
        if analyze_market_structure is not None:
            try:
                _ms = analyze_market_structure(highs, lows, closes)
                structure_trend = _ms.get("trend", "unknown")
            except Exception as e:
                logger.warning(f"  [ADAPTIVE] enrich_signal market_structure: {e}")
                structure_trend = "unknown"
        else:
            structure_trend = "unknown"

        obv_arr = obv(closes, volumes)
        obv_trend = 0
        if len(obv_arr) > 20:
            obv_trend = 1 if obv_arr[-1] > obv_arr[-20] else -1

        rsi_arr = rsi(closes)
        rsi_now = rsi_arr[-1] if len(rsi_arr) > 0 and not np.isnan(rsi_arr[-1]) else 50
        div = rsi_divergence(closes, rsi_arr, lookback=20)

        volume_confirms = (obv_trend > 0 and structure_trend == "bullish") or (
            obv_trend < 0 and structure_trend == "bearish"
        )

        # Enrichir avec les données market_structure détaillées
        meta_result = {
            "adx": round(meta["adx"], 1),
            "vol_percentile": round(meta["vol_percentile"], 2),
            "structure_trend": structure_trend,
            "structure_score": round(_ms.get("score", 0) if _ms else 0, 2),
            "obv_trend": obv_trend,
            "rsi": round(rsi_now, 1),
            "volume_confirms": volume_confirms,
            "confidence_bonus": 0.10 if volume_confirms else 0,
            "rsi_divergence": div.get("bullish", False) or div.get("bearish", False),
            "eq_hl_count": _ms.get("equal_highs_lows", {}).get("count", 0) if _ms else 0,
        }
        # Données ICT/SMC détaillées
        if _ms:
            meta_result["unmitigated_obs"] = _ms.get("unmitigated_obs", 0)
            meta_result["unmitigated_fvgs"] = _ms.get("unmitigated_fvgs", 0)
            meta_result["recent_bos"] = _ms.get("recent_bos", False)
            meta_result["recent_choch"] = _ms.get("recent_choch", False)
            meta_result["recent_sweeps"] = _ms.get("recent_sweeps", [])

        return regime, meta_result

    def _adx(self, highs, lows, closes, p=14):
        """Hook pour compatibilité tests. Délègue à regime._calc_adx."""
        return self._detector._calc_adx(highs, lows, closes)


class OnlineLearner:
    def __init__(self, window=200, state_path=None):
        self.window = window
        self.history = {}
        self.adapted_params = {}
        self._state_path = state_path
        self._batch_mode = False  # True → skip save_state() jusqu'à flush()
        if self._state_path:
            self._load_state()

    def batch_mode(self, active=True):
        """Active/désactive le mode batch. En mode batch, save_state() est
        un no-op. Appeler flush() pour sauvegarder une fois à la fin."""
        self._batch_mode = active

    def flush(self):
        """Force la sauvegarde si en mode batch."""
        if self._batch_mode:
            self._batch_mode = False
            self.save_state()
            self._batch_mode = True

    # ── Persistance disque ──────────────────────────────────────────
    STATE_FILENAME = "runtime/ol_state.json"

    def save_state(self, path=None):
        path = path or self._state_path or self.STATE_FILENAME
        try:
            data = {
                "window": self.window,
                "history": {sym: list(h) for sym, h in self.history.items()},
                "adapted_params": self.adapted_params,
            }
            import json

            path = Path(str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            # Écriture atomique : tmp fixe (sans timestamp) + replace
            # Un nom fixe garantit que l'écriture précédente échouée est écrasée
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(path)  # atomique sur NTFS
        except Exception as e:
            logger.warning(f"[OnlineLearner] save_state failed: {e}")  # Warning pour visibilité

    def _load_state(self, path=None):
        path = path or self._state_path or self.STATE_FILENAME
        try:
            import json

            path = str(path)
            if not Path(path).exists():
                # Pas de state disque → nettoyer le lock seed pour permettre re-seed
                seed_csv = Path("runtime/online_learner_seed.csv")
                if seed_csv.exists():
                    lock = seed_csv.with_suffix(".lock")
                    if lock.exists():
                        try:
                            lock.unlink()
                            logger.info("[OnlineLearner] Lock seed nettoyé (state.json absent)")
                        except Exception as e:
                            logger.warning(f"  [ADAPTIVE] _load_state seed_lock: {e}")
                            pass
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded_window = data.get("window", self.window)
            if loaded_window != self.window:
                logger.info(f"[OnlineLearner] window {loaded_window} != config {self.window} — using loaded")
                self.window = loaded_window
            self.history = {}
            for sym, trades in data.get("history", {}).items():
                self.history[sym] = deque(trades[-self.window :], maxlen=self.window)
            self.adapted_params = data.get("adapted_params", {})
            n_trades = sum(len(h) for h in self.history.values())
            logger.info(f"[OnlineLearner] État restauré: {len(self.history)} symboles, {n_trades} trades")
        except Exception as e:
            logger.warning(f"[OnlineLearner] load_state failed: {e}")
            self.history = {}
            self.adapted_params = {}

    def seed_from_csv(self, csv_path: str = "runtime/online_learner_seed.csv"):
        """Pré-remplit l'OnlineLearner depuis un fichier CSV de seed.
        Les trades seed n'écrasent PAS les trades existants (import unique).
        """
        import csv

        path = Path(csv_path)
        if not path.exists():
            logger.info(f"[OnlineLearner] seed CSV {csv_path} non trouvé — skip")
            return
        # Vérifier si déjà seedé (fichier seed lock)
        lock = path.with_suffix(".lock")
        if lock.exists():
            logger.info(f"[OnlineLearner] Seed déjà appliqué ({lock}) — skip")
            return
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("symbol", "").strip()
                if not sym:
                    continue
                try:
                    r_mul = float(row.get("r_multiple", 0))
                except (ValueError, TypeError):
                    r_mul = 0
                regime = row.get("direction", "?")[:5]  # BUY/SELL comme proxy de régime
                if sym not in self.history:
                    self.history[sym] = deque(maxlen=self.window)
                self.history[sym].append({"r": r_mul, "regime": regime})
                count += 1
        # Recalculer les paramètres pour chaque symbole
        for sym in list(self.history.keys()):
            self._update_params(sym)
        # Marquer seed comme appliqué
        try:
            lock.write_text("done")
        except Exception as e:
            logger.warning(f"  [ADAPTIVE] _seed_from_csv lock: {e}")
            pass
        # Persister immédiatement pour que le seed survive aux redémarrages
        try:
            self.save_state()
            logger.info(f"[OnlineLearner] État seedé persisté sur disque")
        except Exception as e:
            logger.warning(f"[OnlineLearner] Échec persistance seed: {e}")
        logger.info(f"[OnlineLearner] Seed: {count} trades chargés depuis {csv_path}")

    # ── Enregistrement ──────────────────────────────────────────────

    def record_trade(self, symbol, r_multiple, regime):
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.window)
        self.history[symbol].append({"r": r_multiple, "regime": regime})
        # ⚠️ CRITIQUE: _update_params peut planter (ex: données corrompues).
        # try/finally garantit que save_state() est TOUJOURS appelée pour
        # ne JAMAIS perdre un trade live. Sans cela, l'OnlineLearner reste
        # figé aux valeurs seed et n'apprend jamais du marché réel.
        try:
            self._update_params(symbol)
        except Exception as e:
            logger.error(f"[OnlineLearner] _update_params échoué pour {symbol}: {e}")
        if not self._batch_mode:
            self.save_state()

    def record_trades_batch(self, symbol, trades):
        """Ajoute plusieurs trades d'un coup sans sauvegarder entre chaque."""
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.window)
        for t in trades:
            self.history[symbol].append(t)
        self._update_params(symbol)
        if not self._batch_mode:
            self.save_state()

    def set_params_direct(self, symbol, params_dict):
        """Définit directement adapted_params sans recalcul (pour restauration)."""
        self.adapted_params[symbol] = params_dict

    def get_params(self, symbol, base_thresh=3.0):
        if symbol not in self.adapted_params:
            # ⚠️ R1: Fallback transparent — pas de paramètres appris pour ce symbole
            # Le risk_mult=1.0 signifie "pas d'ajustement OL" → la config symbole est utilisée telle quelle.
            # Cela arrive si le seed n'a pas encore été chargé ou si _update_params a supprimé
            # les params (trop peu de trades valides). Normal en début de vie du robot.
            logger.debug(f"[OnlineLearner] {symbol}: fallback defaults (no adapted_params)")
            return {"thresh": base_thresh, "risk_mult": 1.0, "sl_mult": 3.0, "tp_mult": 1.0}
        return self.adapted_params[symbol]

    def _update_params(self, symbol):
        h = list(self.history.get(symbol, []))
        # 🔧 R3: Seuil réduit window//4=50 au lieu de window//2=100 (trop long à atteindre en live)
        min_trades = max(15, self.window // 10)
        if len(h) < min_trades:
            return
        # Filtrer les trades avec régime valide (IGNORE les trades UNKNOWN/?)
        # Permissif: seed data utilise direction comme proxy de régime (BUY/SELL) + HIST pour trades historiques
        # 🔧 R2: "IMPORT" ajouté — les trades réels enregistrés par position_tracker.py utilisent ce régime
        valid_regimes = {
            "RANGING",
            "TREND_UP",
            "TREND_DOWN",
            "HIGH_VOL",
            "LOW_VOL",
            "HIST",
            "RAN",
            "BUY",
            "SELL",
            "IMPORT",
        }
        h_valid = [t for t in h if t.get("regime", "") in valid_regimes]

        # Si moins de 5 trades valides ou si la majorité des trades sont invalides,
        # on garde le paramétrage par défaut (ne pas apprendre sur des données pourries)
        if len(h_valid) < 5 or (len(h) > 0 and len(h_valid) / len(h) < 0.3):
            # Trop peu de trades fiables → adapted_params par défaut
            logger.info(
                f"[OnlineLearner] {symbol}: {len(h_valid)}/{len(h)} régimes valides "
                f"— skip apprentissage (données insuffisantes)"
            )
            if symbol in self.adapted_params:
                del self.adapted_params[symbol]
            return

        rr = np.array([t["r"] for t in h_valid])
        wr = np.mean(rr > 0)
        expectancy = np.mean(rr)
        logger.info(
            f"[OnlineLearner] {symbol}: {len(h_valid)} trades valides, WR={wr:.1%}, expectancy={expectancy:.2f}"
        )
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
            "thresh": thresh,
            "risk_mult": risk_mult,
            "sl_mult": 3.0,
            "tp_mult": 1.0,
        }

    def get_summary(self, symbol):
        h = list(self.history.get(symbol, []))
        if not h:
            return {}
        rr = np.array([t["r"] for t in h])
        return {
            "trades": len(h),
            "wr": round(np.mean(rr > 0), 3),
            "avg_r": round(np.mean(rr), 3),
            "expectancy": round(np.mean(rr), 3),
        }


# Symbols ou DL est pire que aleatoire
DL_MIN_SCORE = 0.50  # Abaissé de 0.60→0.50 : le modèle donne 83% de scores entre 0.58-0.60
# À 0.50 : scores 0.50-0.60 acceptés avec risque ×0.5 (même 33% WR × RR 3= profitable)
DL_SAFE_SCORE = 0.60  # Seuil historique : scores >= 0.60 = risque plein


class AdaptiveEngine:
    def __init__(self, mt5, calibration_path=None):
        self.mt5 = mt5
        self.regime = MarketRegime()
        # OnlineLearner persistant : charge l'état depuis le disque,
        # puis seed depuis les fichiers Excel historiques si premier démarrage
        self.learner = OnlineLearner(window=200, state_path=OnlineLearner.STATE_FILENAME)
        self.learner.seed_from_csv("runtime/online_learner_seed.csv")
        # P7: DL désactivé — aucun modèle .pkl trouvé
        self.dl = None
        self.ml = None
        # LightGBM: chargement automatique si le modèle entraîné existe
        self.lgb = LightGBMModel()
        lgb_loaded = self.lgb.load()
        if lgb_loaded:
            logger.info(f"LightGBM chargé: {self.lgb.summary()}")
        else:
            logger.info("LightGBM non disponible — exécutez scripts/train_lightgbm.py")
        self._meta_active = lgb_loaded  # Meta activé uniquement si LGB est dispo
        if self._meta_active:
            from engine_simple.meta_learner import MetaLearner

            self.meta = MetaLearner(recalibration_freq=50)
            self.meta.load_state()
            logger.info(f"Meta-Learner active, tracking {len(self.meta.get_model_names())} models | DL=AVAILABLE")
        else:
            self.meta = None
            logger.info("Meta-Learner BYPASSED — only MOM20x3 active (DL/LGB disabled)")

        self._dl_grey_zone = False  # flag pour risk/2 entre 0.50-0.60
        self.calibration_path = calibration_path
        if calibration_path:
            self._load_calibration(calibration_path)
        # Sync meta_learner.json only when MetaLearner is active
        if self._meta_active:
            self.meta.save_state()
        # Walk-Forward Validator retiré — module archivé dans retired/
        self.validator = None

    def _load_calibration(self, path):
        if not os.path.exists(path):
            logger.warning(f"  [CAL] Calibration file not found: {path}")
            return
        try:
            # SÉCURITÉ: joblib.load = pickle RCE (C-01). Migré vers JSON sécurisé.
            # Vérification que le fichier n'est pas modifié avant chargement.
            if not os.path.exists(path):
                return
            stat = os.stat(path)
            if stat.st_size > 10 * 1024 * 1024:  # >10MB = suspect
                logger.error(f"  [CAL] Fichier calibration trop volumineux ({stat.st_size} bytes) — refusé")
                return
            import json

            with open(path, "r") as f:
                raw = f.read()
            if len(raw) > 50 * 1024 * 1024:  # 50MB max safe JSON
                logger.error("  [CAL] Calibration JSON >50MB — refusé")
                return
            state = json.loads(raw)
            mc = state.get("meta_calibration", {})
            # ← FIX: backward compat — supporte ancien double-nesting ET nouveau format plat
            if "meta_calibration" in mc and isinstance(mc["meta_calibration"], dict):
                mc = mc["meta_calibration"]
            # Restore MetaLearner trackers (only when active)
            if self._meta_active:
                for name, tdata in mc.get("meta_trackers", {}).items():
                    if name in self.meta.trackers:
                        t = self.meta.trackers[name]
                        for stat_key, stat_val in tdata.items():
                            store = getattr(t, stat_key, None)
                            if store is not None:
                                store.clear()
                                store.update(stat_val)
                # Restore regime performance + penalties
                rp = mc.get("meta_regime_performance", {})
                if rp:
                    self.meta.regime_performance.clear()
                    for k, v in rp.items():
                        self.meta.regime_performance[k] = v
                rp_penalty = mc.get("meta_regime_penalty", {})
                if rp_penalty:
                    for model_name, penalties in rp_penalty.items():
                        t = self.meta.trackers.get(model_name)
                        if t:
                            for regime, penalty in penalties.items():
                                t.regime_penalty[regime] = penalty
                self.meta.trades_since_recal = mc.get("meta_trades_since_recal", 0)
            # Restore OnlineLearner history
            ol = state.get("online_history", {})

            # ⚠️ Restaure l'history depuis calibration UNIQUEMENT si l'OL est vide
            # (moins de 5 trades). Cela couvre 2 scénarios:
            #   1. ol_state.json corrompu (écrasé par _save_calibration avec "online_history")
            #   2. Premier démarrage après migration calibration_state.json séparé
            # Si l'OL a déjà des trades réels, on les préserve.
            # Voir: main.py:333 (calibration_path séparé de OnlineLearner.STATE_FILENAME)
            for sym, hist_list in ol.items():
                current_count = len(self.learner.history.get(sym, []))
                if current_count < 5:
                    logger.info(
                        f"  [CAL] Restoring {sym} history: {len(hist_list)} trades "
                        f"from calibration (current={current_count})"
                    )
                    self.learner.history[sym] = deque(maxlen=self.learner.window)
                    for h in hist_list:
                        self.learner.history[sym].append(h)
                    self.learner._update_params(sym)
                else:
                    logger.debug(
                        f"  [CAL] {sym}: preserving {current_count} existing trades (skip calibration restore)"
                    )
            # ⚠️ Restaurer adapted_params depuis la calibration (survit aux redémarrages)
            cal_adapted = state.get("adapted_params", {})
            if cal_adapted:
                for sym, params in cal_adapted.items():
                    self.learner.adapted_params[sym] = params
                logger.info(
                    f"  [CAL] Restored adapted_params for {len(cal_adapted)} symbols: "
                    + ", ".join(f"{s}={p.get('risk_mult', '?')}" for s, p in cal_adapted.items())
                )
            # 🔧 R5: Synchroniser online_learner_state.json depuis la calibration
            # Évite le scénario où le fichier principal est absent mais la calibration existe
            try:
                self.learner.save_state()
            except Exception as e:
                logger.debug(f"  [CAL] Sync online_learner_state.json: {e}")
            counts = sum(len(v) for v in state.get("online_history", {}).values())
            n_trackers = len(mc.get("meta_trackers", {}))
            logger.info(
                f"  [CAL] Loaded calibration: MetaLearner {n_trackers} trackers, OnlineLearner {counts} records"
            )
            # Sync meta_learner.json only when active
            if self._meta_active:
                self.meta.save_state()
        except (KeyError, ValueError, TypeError, AttributeError, OSError) as e:
            logger.warning(f"  [CAL] Failed to load calibration: {e}")

    def _save_calibration(self):
        if not self.calibration_path:
            return
        try:
            state = {
                "online_history": {sym: list(h) for sym, h in self.learner.history.items()},
                # ⚠️ CRITIQUE: adapted_params doit être persisté pour que les
                # paramètres appris (risk_mult, thresh) survivent aux redémarrages.
                # Sans cela, l'OnlineLearner revient aux valeurs par défaut à chaque restart.
                "adapted_params": dict(self.learner.adapted_params),
            }
            if self._meta_active:
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
                        name: dict(tracker.regime_penalty) for name, tracker in self.meta.trackers.items()
                    },
                    "meta_trades_since_recal": self.meta.trades_since_recal,
                }
                state["meta_calibration"] = mc  # ← FIX: pas de double-nesting
                state["meta_trackers"] = {  # ← FIX: exposition directe pour diagnostic
                    name: dict(tracker.global_stats) for name, tracker in self.meta.trackers.items()
                }
            import json

            with open(self.calibration_path, "w") as f:
                json.dump(state, f, indent=2, default=str)
            # Sync meta_learner.json only when active
            if self._meta_active:
                self.meta.save_state()
        except (OSError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"  [CAL] Failed to save calibration: {e}")

    def vigilance(self, symbol, rates_dict):
        """Run full pipeline (regime + DL) for any symbol without needing a signal. Logs everything."""
        h1_rates = rates_dict.get("H1")
        if h1_rates is None or len(h1_rates) < 50:
            return None
        regime, meta = self.regime.detect(h1_rates, symbol=symbol)
        dl_result = None
        dl_label = "N/A"
        if self.dl is not None and self.dl.available:
            try:
                dl_result = self.dl.predict(symbol, rates_dict)
                if dl_result:
                    dl_score = dl_result.get("score", 0)
                    dl_label = f"{dl_result['action']} ({dl_result['buy_prob']:.3f})"
                    if dl_score < DL_MIN_SCORE:
                        dl_label = f"IGNORE (score={dl_score:.2f} < {DL_MIN_SCORE})"
                        dl_result = None
                    elif dl_score < DL_SAFE_SCORE:
                        dl_label = f"GREY (score={dl_score:.2f}, risk/2)"
                    else:
                        dl_label = f"{dl_result['action']} ({dl_result['buy_prob']:.3f})"
                    logger.info(f"  [VIGIL] {symbol}: regime={regime} DL={dl_label} ADX={meta['adx']:.0f}")
            except (ValueError, TypeError, IndexError, AttributeError) as e:
                logger.warning(f"  [VIGIL] {symbol}: DL error: {e}")
        return {
            "symbol": symbol,
            "regime": regime,
            "regime_meta": meta,
            "dl_action": dl_result["action"] if dl_result else None,
            "dl_score": dl_result["score"] if dl_result else None,
            "dl_buy_prob": dl_result["buy_prob"] if dl_result else None,
        }

    def analyze(self, symbol, rates_dict, signal, trade_stats=None):
        h1_rates = rates_dict.get("H1")
        if h1_rates is None or len(h1_rates) < 50:
            return signal

        regime, meta = self.regime.detect(h1_rates, symbol=symbol)
        logger.info(
            f"  [REGIME] {symbol}: {regime} (ADX={meta['adx']}, vol%={meta['vol_percentile']}, "
            f"struct={meta['structure_trend']}, vol_confirm={meta['volume_confirms']})"
        )

        params = dict(self.learner.get_params(symbol))  # copie pour éviter mutation in-place

        # Multi-TF alignment (institutional structure filter)
        d_rates = rates_dict.get("D1")
        h4_rates = rates_dict.get("H4")
        alignment_dir, alignment_score = "NO_TRADE", 0
        if (
            d_rates is not None
            and h4_rates is not None
            and len(d_rates) >= 50
            and len(h4_rates) >= 50
            and len(h1_rates) >= 50
        ):
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

        # FVG + liquidity sweep detection (désactivé — module fvg_detector dans retired/)
        fvg_bonus = 0.0
        sweep_type, sweep_level = None, None
        active_fvgs = []

        # Collect predictions from ALL models
        all_predictions = {"MOM20x3": {"action": signal.get("action", "HOLD"), "score": signal.get("score", 0.5)}}

        dl_result = None
        if self.dl is not None and self.dl.available:
            try:
                dl_result = self.dl.predict(symbol, rates_dict)
                if dl_result:
                    dl_score = dl_result.get("score", 0)
                    if dl_score < DL_MIN_SCORE:
                        logger.info(f"  [DL] {symbol}: IGNORE (score={dl_score:.2f} < {DL_MIN_SCORE})")
                        dl_result = None
                    elif dl_score < DL_SAFE_SCORE:
                        # Zone grise 0.50-0.60 : accepté mais risque réduit
                        all_predictions["DL_LSTM"] = dl_result
                        dl_agrees = dl_result.get("action", "HOLD") == signal.get("action", "HOLD")
                        self._dl_grey_zone = True  # Flag pour risk/2 plus tard
                        logger.info(
                            f"  [DL] {symbol}: {dl_result['action']} (score={dl_score:.3f}, GREY ZONE, agree={dl_agrees})"
                        )
                    else:
                        # Score >= 0.60 : confiance pleine
                        all_predictions["DL_LSTM"] = dl_result
                        self._dl_grey_zone = False
                        dl_agrees = dl_result.get("action", "HOLD") == signal.get("action", "HOLD")
                        logger.info(f"  [DL] {symbol}: {dl_result['action']} (score={dl_score:.3f}, agree={dl_agrees})")
            except (ValueError, TypeError, IndexError, AttributeError, KeyError) as e:
                logger.warning(f"  [DL] {symbol}: predict error: {e}")

        # LightGBM prediction: utilise les features calculées dans phase13
        lgb_result = None
        if self.lgb is not None and self.lgb.available:
            try:
                # Features disponibles depuis signal_pipeline.phase13
                lgb_features = signal.get("_features", {})
                if len(lgb_features) >= 10:
                    lgb_result = self.lgb.predict(lgb_features)
                    if lgb_result and lgb_result["action"] != "HOLD":
                        all_predictions["LGB"] = {
                            "action": lgb_result["action"],
                            "score": lgb_result["probability"],
                        }
                        logger.info(
                            f"  [LGB] {symbol}: {lgb_result['action']} "
                            f"(proba={lgb_result['probability']:.3f}, "
                            f"conf={lgb_result['confidence']:.3f})"
                        )
                        if lgb_result.get("top_features"):
                            top = lgb_result["top_features"]
                            logger.debug(
                                f"  [LGB] {symbol}: top features: " + ", ".join(f"{k}={v:.1%}" for k, v in top.items())
                            )
                    else:
                        logger.debug(f"  [LGB] {symbol}: HOLD (proba={lgb_result.get('probability', 0.5):.3f})")
                else:
                    logger.debug(f"  [LGB] {symbol}: features insuffisantes ({len(lgb_features)} < 10)")
            except Exception as e:
                logger.warning(f"  [LGB] {symbol}: predict error: {e}")

        # Meta-Learner: combine predictions (≥ 2 modèles: MOM20x3 + DL/LGB)
        meta_action, meta_confidence = "HOLD", 0.5
        devil_disagreements = []
        if self._meta_active and len(all_predictions) >= 2:
            meta_action, meta_confidence, _ = self.meta.get_ensemble_action(regime, all_predictions, symbol=symbol)
            devil_disagreements = self.meta.devil_advocate_check(
                regime, all_predictions, signal.get("action", "HOLD"), symbol=symbol
            )

        adapted = dict(signal)

        # SL/TP : préserver les valeurs calibrées par symbole (strategy.py)
        # 🔧 18 Juin 2026: fallback régime seulement si signal n'a PAS sl_atr
        # AVANT: hardcodé à 2.0×ATR — écrasait les profils (ex: US500 1.2×ATR → 2.86×ATR réel)
        if "sl_atr" not in adapted or adapted.get("sl_atr") is None:
            if regime in ("TREND_DOWN", "TREND_UP"):
                adapted["sl_atr"] = 2.0  # R:R = 5.0/2.0 = 2.5
                adapted["tp_atr"] = 5.0
            elif regime == "HIGH_VOL":
                adapted["sl_atr"] = 2.0  # R:R = 5.0/2.0 = 2.5
                adapted["tp_atr"] = 5.0
                params["risk_mult"] *= 0.7
            elif regime == "LOW_VOL":
                adapted["sl_atr"] = 2.0  # R:R = 4.5/2.0 = 2.25
                adapted["tp_atr"] = 4.5
            else:
                adapted["sl_atr"] = 2.0  # R:R = 4.5/2.0 = 2.25
                adapted["tp_atr"] = 4.5

        # OL risk_mult appliqué en multiplicateur du base_risk_mult par symbole
        # (le risk_mult du signal contient déjà base_risk × ol_risk de main.py)
        adapted["risk_mult"] = adapted.get("risk_mult", 1.0)

        # DL grey zone (0.50-0.60) : risk/2
        if getattr(self, "_dl_grey_zone", False):
            adapted["risk_mult"] *= 0.50
            logger.info(f"  [DL GREY ZONE] {symbol}: risk/2 (score DL entre {DL_MIN_SCORE}-{DL_SAFE_SCORE})")
            self._dl_grey_zone = False  # reset

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
        if (
            not _devil_applied
            and dl_result
            and dl_result.get("action", "HOLD") in ("BUY", "SELL")
            and mom_action in ("BUY", "SELL")
        ):
            if dl_result["action"] != mom_action:
                logger.info(f"  [AGREEMENT] {symbol}: MOM={mom_action} DL={dl_result['action']} → DISAGREE, risk/2")
                adapted["risk_mult"] *= 0.5
            else:
                logger.info(f"  [AGREEMENT] {symbol}: MOM={mom_action} DL={dl_result['action']} → AGREE ✓")
                adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.10)

        # Meta-ensemble decision overrides signal if high confidence
        if meta_action != "HOLD" and meta_confidence > 0.65 and meta_action != signal.get("action", "HOLD"):
            logger.info(
                f"  [META] {symbol}: meta={meta_action} (conf={meta_confidence:.2f}) "
                f"vs MOM={signal.get('action')} → meta override"
            )
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
        regime_bonus = {"TREND_UP": 0.08, "TREND_DOWN": 0.08, "HIGH_VOL": -0.05, "LOW_VOL": 0.03, "RANGING": 0.0}.get(
            regime, 0.0
        )
        regime_bonus += meta.get("confidence_bonus", 0)
        adapted["score"] = min(0.99, adapted.get("score", 0.5) + regime_bonus)
        adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + regime_bonus * 0.5)

        # Session boost par symbole (basé sur preferred_hours du symbole)
        # Actif dans ses heures préférées → bonus, en dehors → pénalité
        try:
            from config_simple import SYMBOL_LIMITS as _SYM

            _sym_cfg = _SYM.get(symbol, {})
            _pref = _sym_cfg.get("preferred_hours")
            if _pref is not None and len(_pref) > 0 and len(_pref) < 24:
                h = datetime.utcnow().hour
                if h in _pref:
                    adapted["score"] = min(0.99, adapted.get("score", 0.5) + 0.08)
                    adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.06)
                else:
                    adapted["score"] = max(0.30, adapted.get("score", 0.5) - 0.05)
                    adapted["confidence"] = max(0.30, adapted.get("confidence", 0.5) - 0.04)
        except Exception as e:
            logger.warning(f"  [ADAPTIVE] get_adapted_params session_boost: {e}")
            pass

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
                logger.info(f"  [STATS] {symbol}: WR={wr:.0%} > 65% → bonus +{wr_bonus + 0.02:.0%}")
            if pf < 0.8 and trade_stats.get("trade_count", 0) > 20:
                adapted["risk_mult"] *= 0.5
                logger.info(f"  [STATS] {symbol}: PF={pf:.1f} < 0.8 → risk/2")

        adapted["_regime"] = regime
        adapted["_dl_score"] = dl_result.get("score") if dl_result else None
        adapted["_lgb_score"] = lgb_result.get("probability") if lgb_result else None
        adapted["_lgb_action"] = lgb_result.get("action") if lgb_result else None
        adapted["_meta_action"] = meta_action
        adapted["_meta_confidence"] = round(meta_confidence, 3)
        adapted["_devil"] = len(devil_disagreements)
        adapted["_model_predictions"] = dict(all_predictions)
        # ML agrees : vérifie si au moins un modèle ML (DL ou LGB) agree avec MOM20x3
        _mom_action = signal.get("action", "HOLD")
        _dl_agrees = dl_result and dl_result.get("action", "HOLD") == _mom_action
        _lgb_agrees = lgb_result and lgb_result.get("action") == _mom_action
        adapted["_ml_agrees"] = _dl_agrees or _lgb_agrees
        adapted["_dl_agrees"] = _dl_agrees
        adapted["_lgb_agrees"] = _lgb_agrees
        # Institutional analysis fields
        adapted["_alignment_dir"] = alignment_dir
        adapted["_alignment_score"] = alignment_score
        adapted["_fvgs"] = active_fvgs if active_fvgs else []
        adapted["_sweep_type"] = sweep_type
        adapted["_sweep_level"] = sweep_level

        # Recalibrate meta-learner periodically (only when active)
        if self._meta_active and self.meta.should_recalibrate():
            self.meta.recalibrate()

        return adapted

    def save_calibration(self):
        self._save_calibration()
        if self._meta_active:
            self.meta.save_state()  # persist separate meta_learner.json

    def record_result(self, symbol, r_multiple, regime=None, dl_features=None, batch=False):
        self.learner.record_trade(symbol, r_multiple, regime)
        if not batch:
            self._save_calibration()  # persistence immédiate après chaque trade réel
        if dl_features is not None and self.dl is not None and self.dl.available:
            self.dl.record_trade(symbol, dl_features, r_multiple)

    def record_meta_result(self, symbol, regime, predictions_outcomes):
        if self._meta_active:
            self.meta.record_trade(symbol, regime, predictions_outcomes)
        if self.validator is not None:
            for mname, correct in predictions_outcomes.items():
                self.validator.record(mname, correct, regime, symbol)
        self._save_calibration()  # persistence après chaque meta trade

    def train_dl_if_ready(self):
        if self.dl is not None and self.dl.available:
            total = sum(len(v) for v in self.dl.training_buffer.values())
            if total >= 32:
                self.dl.train_all()
                self._save_calibration()
                n_symbols = sum(1 for v in self.dl.training_buffer.values() if len(v) >= 32)
                logger.info(f"  [DL] Online training: {total} samples across {n_symbols} symbols")

    def build_dl_features(self, rates_dict):
        if self.dl is None or not self.dl.available:
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
