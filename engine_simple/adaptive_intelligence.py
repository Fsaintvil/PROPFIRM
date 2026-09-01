from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from engine_simple.indicators import obv, rsi, rsi_divergence

try:
    from engine_simple.market_structure import analyze_market_structure
except ImportError:
    analyze_market_structure = None
from engine_simple.structure_analyzer import multi_tf_alignment


logger = logging.getLogger("adaptive")


def _is_burst_history(hist: list, min_trades: int = 15, burst_ratio: float = 0.5) -> bool:
    """🐛 FIX 31 Juillet 2026: Détecte une history contaminée par replay/burst.

    Signature de contamination: une rafale de trades du même symbole avec
    des intervalles < 1s (ex: 200 trades EURUSD en 10 min, 96% des gaps < 1s).
    Ce sont des trades "rejoués" par un cache MT5 vide (timeout) — PAS des
    trades réels. Les vrais trades espacent d'au moins plusieurs secondes.

    Args:
        hist: liste des trades {r, regime, time?, profit?, win?}
        min_trades: seuil minimum de trades pour déclencher l'analyse
        burst_ratio: ratio de gaps < 1s au-delà duquel on considère contaminé

    Returns:
        True si l'history est contaminée (à rejeter)
    """
    if len(hist) < min_trades:
        return False
    timestamps = []
    for h in hist:
        t = h.get("time")
        if isinstance(t, str):
            try:
                timestamps.append(datetime.fromisoformat(t))
            except (ValueError, TypeError):
                continue
    # Pas assez de timestamps exploitables → on ne peut pas juger → laisser passer
    if len(timestamps) < min_trades:
        return False
    timestamps.sort()
    gaps = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
    if not gaps:
        return False
    sub_1s = sum(1 for g in gaps if g < 1.0)
    return (sub_1s / len(gaps)) >= burst_ratio


def _purge_burst_entries(hist: list, min_gap_s: float = 1.0) -> list:
    """🔧 FIX 30 Août 2026: Purge les entrées burst d'un historique mixte.

    Contrairement à _is_burst_history qui rejette TOUT l'historique,
    cette fonction SUPPRIME UNIQUEMENT les entrées en burst (gaps < min_gap_s)
    et conserve les trades réels. Utile quand un symbole a 3 vrais trades
    mélangés à 75 trades synthétiques.

    Algorithme:
    1. Trie les trades par timestamp
    2. Détecte les runs consécutives de gaps < min_gap_s ( clusters burst)
    3. Supprime les trades des clusters burst (garde le 1er de chaque cluster)
    4. Réordonne dans l'ordre original

    Returns:
        Liste nettoyée (sous-ensemble de hist)
    """
    if len(hist) < 3:
        return list(hist)

    # Indexer par position originale pour préserver l'ordre
    indexed = [(i, t) for i, t in enumerate(hist) if t.get("time")]
    if len(indexed) < 3:
        return list(hist)

    # Trier par timestamp pour détecter les bursts
    try:
        sorted_idx = sorted(range(len(indexed)), key=lambda j: indexed[j][1]["time"])
    except (KeyError, TypeError):
        return list(hist)

    # Trouver les indices à supprimer (entrées burst)
    to_remove = set()
    run_start = 0
    for k in range(1, len(sorted_idx)):
        try:
            t1 = datetime.fromisoformat(indexed[sorted_idx[k-1]][1]["time"].replace("+00:00","").replace("+02:00",""))
            t2 = datetime.fromisoformat(indexed[sorted_idx[k]][1]["time"].replace("+00:00","").replace("+02:00",""))
            gap = (t2 - t1).total_seconds()
            if gap >= min_gap_s:
                # Fin du run — marquer les entrées burst (sauf la première du run)
                run_len = k - run_start
                if run_len >= 5:  # cluster de ≥5 trades rapprochés = burst
                    for j in range(run_start + 1, k):  # garder le 1er, supprimer les suivants
                        orig_idx = indexed[sorted_idx[j]][0]
                        to_remove.add(orig_idx)
                run_start = k
        except (ValueError, TypeError):
            run_start = k + 1

    # Dernier run
    run_len = len(sorted_idx) - run_start
    if run_len >= 5:
        for j in range(run_start + 1, len(sorted_idx)):
            orig_idx = indexed[sorted_idx[j]][0]
            to_remove.add(orig_idx)

    if not to_remove:
        return list(hist)

    cleaned = [t for i, t in enumerate(hist) if i not in to_remove]
    return cleaned


class MarketRegime:
    """Enhanced regime detection — délègue à regime.py + enrichit avec structure/volume."""

    def __init__(self) -> None:
        from engine_simple.regime import RegimeDetector

        self._detector = RegimeDetector()

    def detect(self, rates: list, symbol: str = "_default") -> tuple[str, dict]:
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
            "adx": round(meta.get("adx", 0), 1),
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

    def _adx(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, p: int = 14) -> Any:
        """Hook pour compatibilité tests. Délègue à regime._calc_adx."""
        return self._detector._calc_adx(highs, lows, closes)


class OnlineLearner:
    def __init__(self, window: int = 200, state_path: Optional[str] = None, burst_max: int = 25) -> None:
        import threading
        self._lock = threading.RLock()  # 🔧 FIX C6: RLock (reentrant) car save_state() est appelé depuis record_trade()
        self.window = window
        self.history = {}
        self.adapted_params = {}
        self._state_path = state_path
        self._batch_mode = False  # True → skip save_state() jusqu'à flush()
        # 🐛 FIX 28 Juillet 2026: Rate limiter anti-contamination
        # Rejette les trades si > MAX_BURST trades du même symbole arrivent en < BURST_WINDOW secondes.
        # Empêche les rafales de trades historiques (ex: 166 EURUSD trades en 16s) de contaminer l'OL.
        # burst_max=25 par défaut (40 trades en 2.7s = 74/5s > 25 → bloqué).
        # Les tests unitaires peuvent passer burst_max=999 pour désactiver la limite.
        self._last_trade_times: dict[str, list[float]] = {}  # symbol → [timestamps]
        self._BURST_MAX_TRADES = burst_max  # max trades/5s/symbole avant rejection
        self._BURST_WINDOW_SEC = 5.0  # fenêtre en secondes
        if self._state_path:
            self._load_state()

    def batch_mode(self, active: bool = True) -> None:
        """Active/désactive le mode batch. En mode batch, save_state() est
        un no-op. Appeler flush() pour sauvegarder une fois à la fin."""
        self._batch_mode = active

    def flush(self) -> None:
        """Force la sauvegarde si en mode batch."""
        if self._batch_mode:
            self._batch_mode = False
            self.save_state()
            self._batch_mode = True

    # ── Persistance disque ──────────────────────────────────────────
    STATE_FILENAME = "runtime/ol_state.json"

    def save_state(self, path: Optional[str] = None) -> None:
        path_str = path or self._state_path or self.STATE_FILENAME
        with self._lock:  # 🔧 FIX C6: serialiser avec record_trade/_update_params
            try:
                # 🔧 FIX 30 Août 2026: Purger les bursts AVANT sauvegarde
                # Empêche les données contaminées d'être persistées sur disque.
                cleaned_history = {}
                for sym, h in self.history.items():
                    trade_list = list(h)
                    if len(trade_list) >= 15 and _is_burst_history(trade_list):
                        cleaned = _purge_burst_entries(trade_list)
                        if len(cleaned) >= 5:
                            cleaned_history[sym] = deque(cleaned[-self.window :], maxlen=self.window)
                            logger.debug(f"[OL SAVE] {sym}: burst purgé ({len(trade_list)}→{len(cleaned)})")
                        else:
                            logger.warning(f"[OL SAVE] {sym}: trop peu de trades propres après purge, skip")
                    else:
                        cleaned_history[sym] = h

                data = {
                    "window": self.window,
                    "history": {sym: list(h) for sym, h in cleaned_history.items()},
                    "adapted_params": self.adapted_params,
                }
                import json

                p = Path(str(path_str))
                p.parent.mkdir(parents=True, exist_ok=True)
                # Écriture atomique : tmp fixe (sans timestamp) + fsync + replace
                import os
                tmp = p.with_suffix(".json.tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    import json as _json
                    _json.dump(data, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())  # 🔧 FIX C8: garantir écriture disque
                tmp.replace(p)  # atomique sur NTFS
            except Exception as e:
                logger.warning(f"[OnlineLearner] save_state failed: {e}")

    def _load_state(self, path: Optional[str] = None) -> None:
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
                logger.info(
                    f"[OnlineLearner] window fichier={loaded_window} != config={self.window} — using config ({self.window})"
                )
            self.history = {}
            valid_regimes = {"RANGING", "TREND_UP", "TREND_DOWN", "HIGH_VOL", "LOW_VOL"}
            for sym, trades in data.get("history", {}).items():
                # 🔧 FIX 30 Août 2026: Purge intelligent des bursts
                # Au lieu de rejeter TOUT l'historique (ancien comportement),
                # on purge UNIQUEMENT les entrées burst et on garde les vrais trades.
                trade_list = list(trades)
                if _is_burst_history(trade_list):
                    # Historique mixte probable — purger les entrées burst
                    cleaned = _purge_burst_entries(trade_list)
                    if len(cleaned) >= 5:
                        n_removed = len(trade_list) - len(cleaned)
                        logger.warning(
                            f"[OnlineLearner] {sym}: burst nettoyé — {n_removed} entries supprimées, "
                            f"{len(cleaned)} trades propres conservés"
                        )
                        trade_list = cleaned
                    else:
                        # Trop peu de trades propres restants → rejeter entièrement
                        logger.warning(
                            f"[OnlineLearner] {sym}: history contaminée "
                            f"({len(trade_list)} trades, {len(cleaned)} propres < 5) — PURGE complète"
                        )
                        continue

                # 🔧 FIX 1 Sept 2026: Purger les entrées avec régime invalide
                # (BUY/SELL/HIST/DOW/RAN/SEED/SYNTHETIC = entrées legacy/synthétiques)
                n_before = len(trade_list)
                regime_cleaned = [t for t in trade_list if t.get("regime", "") in valid_regimes]
                n_purged = n_before - len(regime_cleaned)
                if n_purged > 0 and len(regime_cleaned) > 0:
                    logger.info(
                        f"[OnlineLearner] {sym}: {n_purged} entrées régime invalide purgées, "
                        f"{len(regime_cleaned)} propres conservées"
                    )
                    trade_list = regime_cleaned

                self.history[sym] = deque(trade_list[-self.window :], maxlen=self.window)
            # 🐛 FIX 31 Juillet 2026: Purger les adapted_params des symboles contaminés
            # (leurs params sont dérivés des 200 trades synthétiques → non fiables)
            cal_adapted = data.get("adapted_params", {})
            valid_regimes = {"RANGING", "TREND_UP", "TREND_DOWN", "HIGH_VOL", "LOW_VOL"}
            # 🔧 1 Sept 2026: min_trades fixé à 10 (ancien max(10, window//10) = 20 bugué)
            min_trades = 10
            for sym in list(cal_adapted.keys()):
                if sym not in self.history:
                    logger.warning(f"[OnlineLearner] {sym}: adapted_params purgés (history absente/contaminée)")
                    del cal_adapted[sym]
                    continue
                # 🔧 FIX 20 Août 2026 (Robot Manager): même garde que _update_params
                # (L.418 min_trades valides). Sans elle, les params pré-GR (NZDUSD/
                # USDJPY/US30.cash/GBPJPY/USOIL, 10-17 trades < 20) étaient restaurés
                # depuis ol_state.json à chaque redémarrage — purge du 19/08 annulée.
                n_valid = sum(
                    1 for t in self.history[sym]
                    if t.get("regime", "") in valid_regimes and abs(t.get("r", 0)) >= 0.05
                )
                if n_valid < min_trades:
                    logger.warning(
                        f"[OnlineLearner] {sym}: adapted_params purgés "
                        f"(history insuffisante {n_valid} valid < {min_trades} — params pré-GR)"
                    )
                    del cal_adapted[sym]
            self.adapted_params = cal_adapted
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
                self.history[sym].append(
                    {
                        "r": r_mul,
                        "regime": regime,
                        "time": row.get("timestamp", datetime.now(timezone.utc).isoformat())
                        if "timestamp" in row
                        else datetime.now(timezone.utc).isoformat(),
                    }
                )
                # enrichir si disponible
                try:
                    pnl = float(row.get("pnl", 0))
                    if pnl != 0:
                        self.history[sym][-1]["profit"] = pnl
                        self.history[sym][-1]["win"] = pnl > 0
                except (ValueError, TypeError):
                    pass
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

    # 🔧 FIX 28 Juillet 2026: régimes exclus du OnlineLearner.
    # Seules les données RÉELLES (trades live exécutés sur MT5) sont acceptées.
    # Les régimes HIST (import historique), SYNTHETIC (rejeu/test), SEED (initialisation)
    # corrompent l'apprentissage avec des patterns WLWLWL artificiels.
    _REAL_REGIMES = {"TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL", "UNKNOWN"}

    def record_trade(
        self, symbol: str, r_multiple: float, regime: str, profit: Optional[float] = None, win: Optional[bool] = None
    ) -> None:
        # 🔧 FIX 28 Juillet 2026: Rejeter toute donnée non-réelle
        if regime not in self._REAL_REGIMES:
            logger.debug(
                f"  [OL] {symbol}: skipping regime={regime} (real data only — accepted: {sorted(self._REAL_REGIMES)})"
            )
            return
        # 🐛 FIX 28 Juillet 2026: Rate limiter anti-rafale
        now = time.time()
        with self._lock:  # 🔧 FIX C6: protéger history + adapted_params
            if symbol not in self._last_trade_times:
                self._last_trade_times[symbol] = []
            self._last_trade_times[symbol] = [t for t in self._last_trade_times[symbol] if now - t < self._BURST_WINDOW_SEC]
            if len(self._last_trade_times[symbol]) >= self._BURST_MAX_TRADES:
                logger.warning(
                    f"  [OL BURST] {symbol}: {len(self._last_trade_times[symbol])} trades en "
                    f"{self._BURST_WINDOW_SEC:.0f}s ≥ {self._BURST_MAX_TRADES} — rejeté (rafale suspecte)"
                )
                return
            self._last_trade_times[symbol].append(now)
            if symbol not in self.history:
                self.history[symbol] = deque(maxlen=self.window)
            entry = {
                "r": r_multiple,
                "regime": regime,
                "time": datetime.now(timezone.utc).isoformat(),
            }
            if profit is not None:
                entry["profit"] = profit
            if win is not None:
                entry["win"] = win
            self.history[symbol].append(entry)
            try:
                self._update_params(symbol)
            except Exception as e:
                logger.error(f"[OnlineLearner] _update_params échoué pour {symbol}: {e}")
            if not self._batch_mode:
                self.save_state()

    def get_params(self, symbol: str, base_thresh: float = 3.0) -> dict:
        # 🔧 FIX 10 Juillet 2026: Appliquer SYMBOL_MAX_RISK sur TOUS les retours
        from engine_simple.ftmo_config import SYMBOL_MAX_RISK

        with self._lock:  # 🔧 FIX C6: lecture atomique de adapted_params
            if symbol not in self.adapted_params:
                logger.debug(f"[OnlineLearner] {symbol}: fallback defaults (no adapted_params)")
                params = {"thresh": base_thresh, "risk_mult": 1.0, "sl_mult": 2.0, "tp_mult": 5.0}
            else:
                params = dict(self.adapted_params[symbol])

        # 🛡️ SYMBOL_MAX_RISK override — s'applique même au fallback
        max_risk = SYMBOL_MAX_RISK.get(symbol)
        if max_risk is not None:
            params["risk_mult"] = min(params.get("risk_mult", 1.0), max_risk)
        return params

    def _update_params(self, symbol: str) -> None:
        h = list(self.history.get(symbol, []))
        # 🔧 1 Sept 2026: min_trades fixé à 10 (ancien max(10, window//10) = 20 bugué)
        min_trades = 10
        if len(h) < min_trades:
            return

        # Filtrer les trades avec régime valide (5 régimes de marché uniquement)
        # 🔧 FIX 14 Juillet 2026: Retiré RAN/BUY/SELL/DOW — ces régimes invalides
        # (anciens bugs d'enregistrement) corrompaient l'apprentissage. Les 5 vrais
        # régimes sont définis dans regime.py et validés par MarketRegime.
        valid_regimes = {
            "RANGING",
            "TREND_UP",
            "TREND_DOWN",
            "HIGH_VOL",
            "LOW_VOL",
        }
        h_valid_all = [t for t in h if t.get("regime", "") in valid_regimes]
        # 🔧 FIX 1 Sept 2026: seuil bruit abaissé 0.1→0.05
        # L'ancien seuil 0.1 rejetait les petits winners (BE exits, partial TPs) = r=0.0-0.09.
        # Un winner à r=0.02 est un trade RÉEL (small win > noise). Seuil 0.05 garde
        # les wins significatifs tout en filtrant le vrai bruit (r=±0.01-0.04).
        h_valid = [t for t in h_valid_all if abs(t.get("r", 0)) >= 0.05]
        filtered_noise = len(h_valid_all) - len(h_valid)

        # 🐛 FIX 03 Aout 2026: exiger min_trades (20) trades VALIDES, pas 5.
        # L'ancien seuil de 5 permettait d'adapter risk_mult sur des échantillons de
        # 5-17 trades = bruit (ex: risk_mult=0.50 sur USDJPY/NZDUSD/USOIL calibré sur
        # rien). L'adaptation sur bruit DIVISE les gains par 2 et auto-renforce la perte.
        if len(h_valid) < min_trades or (len(h) > 0 and len(h_valid) / len(h) < 0.3):
            logger.info(
                f"[OnlineLearner] {symbol}: {len(h_valid)}/{len(h)} régimes valides "
                f"(dont {filtered_noise} bruit r<0.1 filtré)"
                f"— skip apprentissage (données insuffisantes)"
            )
            if symbol in self.adapted_params:
                del self.adapted_params[symbol]
            return

        rr = np.array([t["r"] for t in h_valid])

        # 📊 RÉCENCE PONDÉRÉE : les 50 derniers trades comptent 2× plus
        # Empêche l'OL de garder un vieux "champion" qui perd depuis 50 trades
        n_recent = min(50, len(h_valid))
        recent_rr = rr[-n_recent:]
        wr_full = float(np.mean(rr > 0))
        wr_recent = float(np.mean(recent_rr > 0)) if n_recent >= 5 else wr_full
        expectancy_full = float(np.mean(rr))
        expectancy_recent = float(np.mean(recent_rr)) if n_recent >= 5 else expectancy_full
        total_r_sum = float(np.sum(rr))
        total_r_recent = float(np.sum(recent_rr)) if n_recent >= 5 else total_r_sum

        logger.info(
            f"[OnlineLearner] {symbol}: {len(h_valid)} trades, "
            f"WR plein={wr_full:.0%} récent={wr_recent:.0%}, "
            f"expect={expectancy_full:.2f}/{expectancy_recent:.2f}, "
            f"total_r={total_r_sum:.0f}/{total_r_recent:.0f}"
        )

        # ⚖️ DÉCISION : combiner récent + plein avec poids 40/60
        wr_eff = 0.4 * wr_recent + 0.6 * wr_full
        exp_eff = 0.4 * expectancy_recent + 0.6 * expectancy_full

        thresh = 2.0
        risk_mult = 1.0

        # 🏆 CHAMPION : WR > 55%, expectancy > 0.5, PnL total > 0, + récent pas en chute libre
        is_proven_winner = (
            wr_full > 0.53
            and wr_recent > 0.45  # 🆕 récent ne doit pas être catastrophique
            and expectancy_full > 0.4
            and total_r_sum > 0
            and len(h_valid) >= 15
        )

        if is_proven_winner:
            thresh = 2.0
            risk_mult = 1.0  # risque plein pour les vrais champions
            logger.info(
                f"  → Champion: WR plein={wr_full:.0%} récent={wr_recent:.0%}, "
                f"expect={expectancy_full:.2f}, total_r={total_r_sum:.0f}, risk_mult=1.0"
            )

        elif wr_eff < 0.60:
            # 🔴 WR BAS : plus sélectif + risque réduit
            # 🔧 FIX 22 Juillet 2026: Threshold dynamique — plus le WR est bas,
            # plus le threshold monte pour filtrer les signaux faibles.
            # Formule: thresh = max(2.5, 2.0 + (0.60 - wr_eff) * 2.5)
            # Ex: wr_eff=0.40 → max(2.5, 2.0+0.50)=2.5 | wr_eff=0.20 → max(2.5, 2.0+1.0)=3.0
            thresh = max(2.5, 2.0 + (0.60 - wr_eff) * 2.5)
            # Le risk diminue progressivement avec le WR effectif
            risk_mult = max(0.70, min(0.90, 0.50 + wr_eff))
            logger.info(f"  → WR_eff={wr_eff:.0%} < 60% : thresh={thresh:.2f}, risk_mult={risk_mult:.2f}")

        # 🟡 ZONE GRISE : WR entre 60% et 70% — réduction modérée
        # 🔧 30 Juil 2026 (PROFESSIONAL SOLUTION): Cette branche manquait.
        # GBPUSD (200T, WR 51%) tombait entre les branches WR<60% et WR>78%
        # sans adaptation. Maintenant, WR 60-70% reçoit une réduction modérée.
        elif wr_eff < 0.70:
            # Formule intermédiaire: moins de pénalité que WR<60%, mais pas neutre
            thresh = max(2.25, 2.0 + (0.70 - wr_eff) * 1.5)
            risk_mult = max(0.80, min(0.95, 0.65 + wr_eff * 0.3))
            logger.info(f"  → WR_eff={wr_eff:.0%} < 70% (zone grise): thresh={thresh:.2f}, risk_mult={risk_mult:.2f}")

        elif wr_eff > 0.78:
            # 🟢 WR HAUT : plus agressif MAIS plafonné à 1.0 (recovery mode)
            # 🔧 FIX 22 Juillet 2026: Threshold dynamique — plus le WR est haut,
            # plus le threshold descend pour prendre plus de signaux.
            # Formule: thresh = min(1.5, 2.0 - (wr_eff - 0.78) * 2.5)
            # Ex: wr_eff=0.85 → min(1.5, 2.0-0.175)=1.825→1.5 | wr_eff=0.95 → min(1.5, 2.0-0.425)=1.5
            thresh = min(1.5, 2.0 - (wr_eff - 0.78) * 2.5)
            risk_mult = 1.0  # cap à 1.0
            logger.info(f"  → WR_eff={wr_eff:.0%} > 78% : thresh={thresh:.2f}, risk_mult=1.0 (cap recovery)")

        # ⛔ EXPECTANCY NÉGATIVE : pénalité supplémentaire
        if exp_eff < 0 and not is_proven_winner:
            risk_mult = min(risk_mult, 0.75)
            logger.info(f"  → expectancy_eff={exp_eff:.2f} < 0: risk_mult baissé à {risk_mult:.2f}")

        # 🔧 PF-based penalty
        if len(h_valid) >= 10:
            wins = rr[rr > 0]
            losses = rr[rr < 0]
            if len(wins) > 0 and len(losses) > 0:
                pf = float(sum(wins)) / max(float(abs(sum(losses))), 0.001)
                if pf < 0.8:
                    risk_mult *= max(0.70, pf)
                    logger.info(f"  → PF={pf:.2f} < 0.8, risk_mult ajusté à {risk_mult:.2f}")

        # 🔴 FIX 10 Juillet 2026: Per-symbol max risk override (depuis ftmo_config.py)
        # Centralisé dans ftmo_config.SYMBOL_MAX_RISK pour que get_params() l'applique aussi.
        from engine_simple.ftmo_config import SYMBOL_MAX_RISK

        max_risk_override = SYMBOL_MAX_RISK.get(symbol)
        if max_risk_override is not None:
            risk_mult = min(risk_mult, max_risk_override)
            if max_risk_override < 0.60:
                logger.info(f"  → Override {symbol}: risk_mult plafonné à {max_risk_override} (max_risk symbole)")

        # 🚫 PLANCHER ABSOLU : risk_mult ne peut pas descendre en dessous de 0.60
        # Évite la spirale mortelle WR bas → risque 0 → pas de trades → pas de récupération
        # 🔧 OPTIMIZER 9 Juillet 2026: ↑ 0.50→0.60 — stop spirale descendante des symboles sous-performants
        # Note: le plancher 0.60 NE S'APPLIQUE PAS aux symboles avec un override explicite
        # (ex: XAUUSD qui doit rester à 0.30 max, ou les hard blocks à 0.0)
        if SYMBOL_MAX_RISK.get(symbol) is None:
            risk_mult = max(0.60, risk_mult)
        self.adapted_params[symbol] = {
            "thresh": thresh,
            "risk_mult": risk_mult,
            "sl_mult": 2.0,
            "tp_mult": 5.0,
        }

    def get_summary(self, symbol: str) -> dict:
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


# 🔧 FIX 28 Août 2026: DL constants supprimés (code mort — aucun modèle actif)


class AdaptiveEngine:
    def __init__(self, mt5: Any, calibration_path: Optional[str] = None) -> None:
        self.mt5 = mt5
        self.regime = MarketRegime()
        # OnlineLearner persistant : charge l'état depuis le disque,
        # puis seed depuis les fichiers Excel historiques si premier démarrage
        self.learner = OnlineLearner(window=200, state_path=OnlineLearner.STATE_FILENAME)
        self.learner.seed_from_csv("runtime/online_learner_seed.csv")
        self.calibration_path = calibration_path
        if calibration_path:
            self._load_calibration(calibration_path)

    def _load_calibration(self, path: str) -> None:
        if not os.path.exists(path):
            logger.warning(f"  [CAL] Calibration file not found: {path}")
            return
        try:
            # SÉCURITÉ: joblib.load = pickle RCE (C-01). Migré vers JSON sécurisé.
            # Vérification que le fichier n'est pas modifié avant chargement.
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
            # Restore OnlineLearner history
            ol = state.get("online_history", {})

            # ⚠️ Restaure l'history depuis calibration UNIQUEMENT si l'OL est vide
            # (moins de 5 trades). Cela couvre 2 scénarios:
            #   1. ol_state.json corrompu (écrasé par _save_calibration avec "online_history")
            #   2. Premier démarrage après migration calibration_state.json séparé
            # Si l'OL a déjà des trades réels, on les préserve.
            # Voir: main.py:333 (calibration_path séparé de OnlineLearner.STATE_FILENAME)
            for sym, hist_list in ol.items():
                # 🐛 FIX 31 Juillet 2026: Rejeter les histories contaminées par burst
                # (même garde que _load_state). Sans ça, la calibration restaurée
                # réinjectait les 200 trades synthétiques en contournant le rate limiter.
                if _is_burst_history(list(hist_list)):
                    logger.warning(
                        f"  [CAL] {sym}: history contaminée détectée "
                        f"({len(hist_list)} trades, burst gaps < 1s) — PURGE au chargement"
                    )
                    continue
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
            # 🔧 FIX 28 Juillet 2026: Appliquer SYMBOL_MAX_RISK pour éviter
            # que des risk_mult=0.0 corrompus (issus d'anciennes sessions) bloquent
            # des symboles qui ont un max_risk > 0 dans la config actuelle.
            from engine_simple.ftmo_config import SYMBOL_MAX_RISK

            cal_adapted = state.get("adapted_params", {})
            if cal_adapted:
                n_restored = 0
                n_skipped = 0
                for sym, params in cal_adapted.items():
                    rm = params.get("risk_mult", 1.0)
                    max_risk = SYMBOL_MAX_RISK.get(sym)
                    # 🔧 FIX 28 Juillet 2026: Guard renforcé — rejette TOUT risk_mult anormal
                    # Ancien guard: rm <= 0.0 (trop permissif — laissait passer 0.0258, 0.0859, 0.24)
                    # Nouveau guard: rm < 0.3 ou rm > max_risk*1.5 ou max_risk=0 → skip
                    skip = False
                    reason = "OK"  # 🔧 FIX 31 Juillet: évite "reason possibly unbound"
                    if max_risk is not None:
                        if rm < 0.3:
                            # risk_mult anormalement bas (vestige de bug de multiplication en chaîne)
                            skip = True
                            reason = f"risk_mult={rm:.4f} < 0.3 (anomalously low)"
                        elif max_risk == 0.0:
                            # symbole désactivé
                            skip = True
                            reason = f"max_risk={max_risk} (symbol disabled)"
                        elif rm > max_risk * 1.5:
                            # risk_mult bien au-dessus du max autorisé
                            skip = True
                            reason = f"risk_mult={rm:.4f} > {max_risk}×1.5 (exceeds max_risk)"
                        else:
                            # Appliquer le cap SYMBOL_MAX_RISK
                            params["risk_mult"] = min(rm, max_risk)
                    # 🐛 FIX 31 Juillet 2026: Ne jamais restaurer les adapted_params
                    # d'un symbole dont l'history est contaminée/absente — les params
                    # sont dérivés des trades synthétiques → non fiables en live.
                    contaminated = sym not in self.learner.history
                    if not skip and contaminated:
                        skip = True
                        reason = "history contaminée/absente (params dérivés du bruit)"
                    # 🔧 FIX 20 Août 2026 (Robot Manager): même garde que l'apprentissage
                    # (_update_params L.418 min_trades) appliquée à la RESTAURATION.
                    # Sans elle, les adapted_params pré-GR (ex: NZDUSD thresh=2.62,
                    # USDJPY 2.75, US30.cash 2.67 — tous < 20 trades) étaient restaurés
                    # à chaque redémarrage, contournant la purge du 19/08 23:55 et
                    # filtrant les signaux avec des thresh périmés. On laisse l'OL
                    # recalculer proprement une fois ≥20 trades valides accumulés.
                    if not skip:
                        hist = list(self.learner.history.get(sym, []))
                        valid_regimes = {"RANGING", "TREND_UP", "TREND_DOWN", "HIGH_VOL", "LOW_VOL"}
                        n_valid = sum(
                            1 for t in hist
                            if t.get("regime", "") in valid_regimes and abs(t.get("r", 0)) >= 0.05
                        )
                        min_trades = 10  # 🔧 1 Sept 2026: fixé à 10 (ancien max(10, window//10) = 20 bugué)
                        if n_valid < min_trades:
                            skip = True
                            reason = f"history insuffisante ({n_valid} valid < {min_trades}) — purge des params pré-GR"
                    if skip:
                        n_skipped += 1
                        logger.debug(f"  [CAL] Skip {sym}: {reason} — let OL recalc from real data")
                        continue
                    self.learner.adapted_params[sym] = params
                    n_restored += 1
                logger.info(
                    f"  [CAL] Restored adapted_params: {n_restored} symbols "
                    f"({n_skipped} skipped — zero risk with active max_risk)"
                )
            # 🔧 R5: Synchroniser online_learner_state.json depuis la calibration
            # Évite le scénario où le fichier principal est absent mais la calibration existe
            try:
                self.learner.save_state()
            except Exception as e:
                logger.debug(f"  [CAL] Sync online_learner_state.json: {e}")
            counts = sum(len(v) for v in state.get("online_history", {}).values())
            logger.info(f"  [CAL] Loaded calibration: OnlineLearner {counts} records")
        except (KeyError, ValueError, TypeError, AttributeError, OSError) as e:
            logger.warning(f"  [CAL] Failed to load calibration: {e}")

    def _save_calibration(self) -> None:
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
            import json

            # 🔧 FIX M-ML1 (16 Août 2026): Écriture atomique tmp+replace.
            # `with open(path, "w")` écrivait DIRECTEMENT dans le fichier final :
            # un force-kill pendant l'écriture laissait calibration_state.json tronqué
            # → perte de TOUT l'apprentissage OL au redémarrage.
            # Même pattern que OnlineLearner.save_state() (l.187-191) : tmp fixe
            # (sans timestamp, l'écriture précédente échouée est écrasée) + replace.
            p = Path(self.calibration_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            tmp.replace(p)  # atomique sur NTFS
        except (OSError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"  [CAL] Failed to save calibration: {e}")

    def vigilance(self, symbol: str, rates_dict: dict) -> Optional[dict]:
        """Run regime detection for any symbol without needing a signal. Logs everything."""
        h1_rates = rates_dict.get("H1")
        if h1_rates is None or len(h1_rates) < 50:
            return None
        regime, meta = self.regime.detect(h1_rates, symbol=symbol)
        logger.info(f"  [VIGIL] {symbol}: regime={regime} ADX={meta['adx']:.0f}")
        return {
            "symbol": symbol,
            "regime": regime,
            "regime_meta": meta,
        }

    def analyze(self, symbol: str, rates_dict: dict, signal: dict, trade_stats: Optional[dict] = None) -> dict:
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
        adapted["risk_mult"] = adapted.get("risk_mult", 1.0)

        # Structure alignment bonus/penalty
        if alignment_score >= 2 and signal.get("action") == "BUY":
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
                h = datetime.now(timezone.utc).hour
                if h in _pref:
                    adapted["score"] = min(0.99, adapted.get("score", 0.5) + 0.08)
                    adapted["confidence"] = min(0.95, adapted.get("confidence", 0.5) + 0.06)
                else:
                    adapted["score"] = max(0.30, adapted.get("score", 0.5) - 0.05)
                    adapted["confidence"] = max(0.30, adapted.get("confidence", 0.5) - 0.04)
        except Exception as e:
            logger.warning(f"  [ADAPTIVE] get_adapted_params session_boost: {e}")
            pass

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
        # Institutional analysis fields
        adapted["_alignment_dir"] = alignment_dir
        adapted["_alignment_score"] = alignment_score
        adapted["_fvgs"] = active_fvgs if active_fvgs else []
        adapted["_sweep_type"] = sweep_type
        adapted["_sweep_level"] = sweep_level

        # 🔓 FIX 8 Juillet: cap élargi à 2.0 (était 1.5) pour donner plus
        # de marge à l'OL quand WR>82%. Le plancher à 0.6 est conservé.
        # Les multiplications successives (OL × structure × régime × stats) peuvent
        # produire des risk_mult < 0.4 ou > 2.5, générant des lots absurdes.
        # 🔧 OPTIMIZER 9 Juillet 2026: ↑ 0.5→0.6 — stop spirale descendante
        adapted["risk_mult"] = max(0.6, min(adapted.get("risk_mult", 1.0), 2.0))

        return adapted

    def save_calibration(self) -> None:
        self._save_calibration()

    # 🔧 FIX 28 Juillet 2026: Seuils régime pour données réelles uniquement.
    # Copie de OnlineLearner._REAL_REGIMES pour le guard au niveau AdaptiveEngine.
    _REAL_REGIMES = {"TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL", "UNKNOWN"}

    def record_result(
        self,
        symbol: str,
        r_multiple: float,
        regime: Optional[str] = None,
        batch: bool = False,
        profit: Optional[float] = None,
        win: Optional[bool] = None,
    ) -> None:
        actual_regime = regime or "UNKNOWN"
        # 🔧 FIX 28 Juillet 2026: Guard données réelles au niveau AdaptiveEngine
        # Double protection : record_result + record_trade rejettent les données non-réelles.
        # HIST, SYNTHETIC, SEED sont exclus — seuls les vrais trades MT5 alimentent l'OL.
        if actual_regime not in self._REAL_REGIMES:
            logger.debug(
                f"  [ADAPTIVE] {symbol}: skipping regime={actual_regime} (real data only at AdaptiveEngine level)"
            )
            return
        self.learner.record_trade(symbol, r_multiple, actual_regime, profit=profit, win=win)
        if not batch:
            self._save_calibration()  # persistence immédiate après chaque trade réel

    def get_report(self, symbol: str) -> dict:
        return self.learner.get_summary(symbol)
