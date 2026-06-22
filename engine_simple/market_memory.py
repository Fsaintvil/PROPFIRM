"""
Market Memory v2 — Mémoire profonde multi-timeframe du marché
=============================================================
Connaît par cœur 16 ans de M15/H1/H4/D1 pour chaque symbole.
Fournit au robot :
  - Profil statistique complet (100+ métriques par symbole)
  - Niveaux clés SR multi-timeframe (swing, fractales, HVN/VA)
  - Pattern matching DTW (Dynamic Time Warping) + patterns chartistes
  - Structure de marché multi-timeframe avec zones de value
"""

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("market_memory")

DATA_DIR = Path("data")
FEATURES_DIR = DATA_DIR / "features"
CACHE_DIR = DATA_DIR / "cache"

SYMBOLS = ["XAUUSD", "BTCUSD", "US500.cash"]
TIMEFRAMES_TIERS = {
    "fast": "M15",
    "medium": "H1",
    "slow": "H4",
    "macro": "D1",
}

# ============================================================
# 1. MARKET PROFILE — Empreinte statistique multi-timeframe
# ============================================================


class MarketProfile:
    """Profil statistique complet d'un symbole sur 4 timeframes."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.data: dict[str, pd.DataFrame] = {}
        self.stats: dict = {}
        self._loaded = False

    def load(self):
        """Charge les données depuis les fichiers parquet pour tous les TF."""
        for tf in ["M15", "H1", "H4", "D1"]:
            path = FEATURES_DIR / f"{self.symbol}_{tf}_features.parquet"
            if path.exists():
                df = pd.read_parquet(path)
                self.data[tf] = df
                logger.info(f"  {self.symbol} {tf}: {len(df)} candles chargées")
            else:
                # Fallback sur H1 si M15/H4 manquent
                if tf in ("M15", "H4"):
                    fallback = FEATURES_DIR / f"{self.symbol}_H1_features.parquet"
                    if fallback.exists():
                        self.data[tf] = pd.read_parquet(fallback)
                        logger.info(f"  {self.symbol} {tf}: fallback H1 ({len(self.data[tf])} candles)")

        self._compute_stats()
        self._loaded = True
        return self

    def _compute_stats(self):
        """Calcule toutes les statistiques du profil (100+ métriques)."""
        h1 = self.data.get("H1")
        d1 = self.data.get("D1")
        m15 = self.data.get("M15")
        h4 = self.data.get("H4")

        if h1 is None or len(h1) == 0:
            return

        s = {}
        h = h1

        # ── Période couverte ──
        s["periode"] = {
            "debut": str(h["timestamp"].min()),
            "fin": str(h["timestamp"].max()),
            "annees": round((h["timestamp"].max() - h["timestamp"].min()).days / 365.25, 1),
            "candles_h1": len(h),
            "candles_m15": len(m15) if m15 is not None else 0,
            "candles_h4": len(h4) if h4 is not None else 0,
            "candles_d1": len(d1) if d1 is not None else 0,
        }

        # ── Volatilité multi-timeframe ──
        s["volatilite"] = {
            "atr_mean_h1": float(h["atr_14"].mean()),
            "atr_median_h1": float(h["atr_14"].median()),
            "range_pct_mean_h1": float(h["range_pct"].mean()),
            "range_pct_max_h1": float(h["range_pct"].max()),
            "range_pct_95pct_h1": float(h["range_pct"].quantile(0.95)),
            "volatility_20_mean_h1": float(h["volatility_20"].mean()),
        }
        if m15 is not None:
            s["volatilite"]["range_pct_mean_m15"] = float(m15["range_pct"].mean())
        if h4 is not None:
            s["volatilite"]["range_pct_mean_h4"] = float(h4["range_pct"].mean())
        if d1 is not None:
            s["volatilite"]["range_pct_mean_d1"] = float(d1["range_pct"].mean())
            s["volatilite"]["atr_mean_d1"] = float(d1["atr_14"].mean())

        # ── RSI distribution ──
        s["rsi"] = {
            "mean": float(h["rsi_14"].mean()),
            "median": float(h["rsi_14"].median()),
            "overbought_pct": float((h["rsi_14"] > 70).mean() * 100),
            "oversold_pct": float((h["rsi_14"] < 30).mean() * 100),
            "neutral_pct": float(((h["rsi_14"] >= 30) & (h["rsi_14"] <= 70)).mean() * 100),
            "stoch_overbought_pct": float((h["stoch_k"] > 80).mean() * 100) if "stoch_k" in h.columns else 0,
            "stoch_oversold_pct": float((h["stoch_k"] < 20).mean() * 100) if "stoch_k" in h.columns else 0,
        }

        # ── Tendance ──
        for tf_name, tf_df in [("h1", h), ("h4", h4), ("d1", d1)]:
            if tf_df is not None:
                key = f"tendance_{tf_name}"
                s[key] = {
                    "hausse_pct": float((tf_df["return_20"] > 0).mean() * 100),
                    "baisse_pct": float((tf_df["return_20"] < 0).mean() * 100),
                    "return_20_mean": float(tf_df["return_20"].mean()),
                    "return_20_std": float(tf_df["return_20"].std()),
                }

        # ── MACD ──
        s["macd"] = {
            "signal_achat_h1": float((h["macd_hist"] > 0).mean() * 100),
            "signal_vente_h1": float((h["macd_hist"] < 0).mean() * 100),
            "macd_mean_h1": float(h["macd"].mean()),
        }
        if d1 is not None and "macd_hist" in d1.columns:
            s["macd"]["signal_achat_d1"] = float((d1["macd_hist"] > 0).mean() * 100)
            s["macd"]["signal_vente_d1"] = float((d1["macd_hist"] < 0).mean() * 100)

        # ── ADX / Trend strength ──
        s["adx"] = {
            "mean_h1": float(h["adx"].mean()),
            "trending_pct_h1": float((h["adx"] >= 25).mean() * 100),
            "ranging_pct_h1": float((h["adx"] < 25).mean() * 100),
        }

        # ── Distance aux moyennes mobiles ──
        for p in [10, 20, 50, 100, 200]:
            col = f"dist_sma_{p}"
            if col in h.columns:
                s[f"dist_sma_{p}"] = {
                    "mean": float(h[col].mean()),
                    "std": float(h[col].std()),
                    "below_pct": float((h[col] < 0).mean() * 100),
                    "above_pct": float((h[col] > 0).mean() * 100),
                }

        # ── Volume / Order flow ──
        s["volume"] = {
            "mean_h1": float(h["volume"].mean()),
            "ratio_mean_h1": float(h["volume_ratio_20"].mean()),
            "ratio_std_h1": float(h["volume_ratio_20"].std()),
            "high_volume_pct_h1": float((h["volume_ratio_20"] > 1.5).mean() * 100),
            "low_volume_pct_h1": float((h["volume_ratio_20"] < 0.5).mean() * 100),
        }
        if "volume_pressure" in h.columns:
            s["volume"]["pressure_mean"] = float(h["volume_pressure"].mean())
            s["volume"]["pressure_std"] = float(h["volume_pressure"].std())

        # ── Candles ──
        s["candles"] = {
            "body_pct_mean": float(h["body_pct"].mean()),
            "doji_pct": float((h["is_doji"] == 1).mean() * 100),
            "marubozu_pct": float((h["is_marubozu"] == 1).mean() * 100),
            "hammer_pct": float((h["is_hammer"] == 1).mean() * 100),
            "shooting_star_pct": float((h["is_shooting_star"] == 1).mean() * 100),
            "upper_wick_mean": float(h["upper_wick"].mean()),
            "lower_wick_mean": float(h["lower_wick"].mean()),
        }

        # ── Sessions ──
        s["sessions"] = {
            "asia_pct": float((h["session_asia"] == 1).mean() * 100),
            "london_pct": float((h["session_london"] == 1).mean() * 100),
            "ny_pct": float((h["session_ny"] == 1).mean() * 100),
            "overlap_pct": float((h["session_overlap_london_ny"] == 1).mean() * 100),
        }

        # ── D1 vue macro ──
        if d1 is not None:
            s["d1"] = {
                "range_pct_mean": float(d1["range_pct"].mean()),
                "range_pct_max": float(d1["range_pct"].max()),
                "atr_mean": float(d1["atr_14"].mean()),
                "return_1_mean": float(d1["return_1"].mean()),
                "return_1_std": float(d1["return_1"].std()),
                "hausse_pct": float((d1["return_1"] > 0).mean() * 100),
                "baisse_pct": float((d1["return_1"] < 0).mean() * 100),
                "plus_haut_historique": float(d1["high"].max()),
                "plus_bas_historique": float(d1["low"].min()),
            }

        self.stats = s

    def get_summary(self) -> dict:
        """Retourne un résumé lisible du profil."""
        s = self.stats
        if not s:
            return {"symbol": self.symbol, "status": "non chargé"}
        return {
            "symbol": self.symbol,
            "periode": f"{s['periode']['debut'][:10]} → {s['periode']['fin'][:10]} ({s['periode']['annees']} ans)",
            "candles": f"H1:{s['periode']['candles_h1']:,} M15:{s['periode']['candles_m15']:,} H4:{s['periode']['candles_h4']:,} D1:{s['periode']['candles_d1']:,}",
            "atr_moyen_h1": f"{s['volatilite']['atr_mean_h1']:.5f}",
            "range_moyen_pct": f"{s['volatilite']['range_pct_mean_h1']:.2f}%",
            "rsi_moyen": f"{s['rsi']['mean']:.1f}",
            "rsi_surachat": f"{s['rsi']['overbought_pct']:.1f}%",
            "rsi_survente": f"{s['rsi']['oversold_pct']:.1f}%",
            "tendance_hausse_h1": f"{s.get('tendance_h1', {}).get('hausse_pct', 0):.1f}%",
            "macd_achat_h1": f"{s['macd']['signal_achat_h1']:.1f}%",
            "adx_trending": f"{s['adx']['trending_pct_h1']:.1f}%",
            "doji_pct": f"{s['candles']['doji_pct']:.1f}%",
            "volume_eleve": f"{s['volume']['high_volume_pct_h1']:.1f}%",
        }


# ============================================================
# 2. MARKET STRUCTURE — Niveaux clés multi-timeframe
# ============================================================


class MarketStructure:
    """Niveaux de support/résistance et structure du marché multi-TF."""

    def __init__(self, symbol: str, data: dict[str, pd.DataFrame]):
        self.symbol = symbol
        self.data = data
        self.key_levels: list[dict] = []
        self.swing_highs: dict[str, list] = {"M15": [], "H1": [], "H4": [], "D1": []}
        self.swing_lows: dict[str, list] = {"M15": [], "H1": [], "H4": [], "D1": []}
        self.value_zones: list[dict] = []
        self._computed = False

    def compute(self) -> "MarketStructure":
        """Calcule tous les niveaux clés sur tous les timeframes."""
        for tf in ["D1", "H4", "H1", "M15"]:
            df = self.data.get(tf)
            if df is not None and len(df) > 200:
                self._find_swing_points(df, tf)
                self._find_sr_levels(df, tf)

        # Fusion et dédoublonnage des niveaux
        self._merge_levels()
        self._find_value_zones()
        self._computed = True
        return self

    def _find_swing_points(self, df: pd.DataFrame, tf: str):
        """Trouve les swing highs/lows avec fenêtre adaptative."""
        window = {"M15": 20, "H1": 5, "H4": 3, "D1": 3}[tf]
        high = df["high"].values
        low = df["low"].values
        times = df["timestamp"].values

        for i in range(window, len(df) - window):
            if high[i] == max(high[i - window : i + window + 1]):
                strength = "major" if high[i] == max(high[max(0, i - window * 4) : i + window * 4 + 1]) else "minor"
                self.swing_highs[tf].append(
                    {
                        "price": float(high[i]),
                        "time": str(pd.Timestamp(times[i]))[:16],
                        "strength": strength,
                        "timeframe": tf,
                    }
                )
            if low[i] == min(low[i - window : i + window + 1]):
                strength = "major" if low[i] == min(low[max(0, i - window * 4) : i + window * 4 + 1]) else "minor"
                self.swing_lows[tf].append(
                    {
                        "price": float(low[i]),
                        "time": str(pd.Timestamp(times[i]))[:16],
                        "strength": strength,
                        "timeframe": tf,
                    }
                )

        major_h = len([s for s in self.swing_highs[tf] if s["strength"] == "major"])
        major_l = len([s for s in self.swing_lows[tf] if s["strength"] == "major"])
        logger.debug(f"  {self.symbol} {tf}: {major_h} swing highs, {major_l} swing lows majeurs")

    def _find_sr_levels(self, df: pd.DataFrame, tf: str):
        """Niveaux SR par timeframes (annualisés pour D1, récents pour M15)."""
        levels = []

        if tf == "D1":
            # Niveaux annuels
            df["year"] = df["timestamp"].dt.year
            for year, group in df.groupby("year"):
                levels.append(
                    {
                        "price": float(group["high"].max()),
                        "type": "resistance",
                        "source": f"annual_high_{int(year)}",
                        "strength": "major",
                        "timeframe": tf,
                    }
                )
                levels.append(
                    {
                        "price": float(group["low"].min()),
                        "type": "support",
                        "source": f"annual_low_{int(year)}",
                        "strength": "major",
                        "timeframe": tf,
                    }
                )

            # Niveaux psychologiques
            px_range = (df["low"].min(), df["high"].max())
            magnitude = 10 ** max(0, int(np.log10(px_range[1]) - 2))
            if magnitude > 0:
                psych_levels = np.arange(
                    np.floor(px_range[0] / magnitude) * magnitude,
                    np.ceil(px_range[1] / magnitude) * magnitude + magnitude,
                    max(magnitude, 0.0001),
                )
                for pl in psych_levels:
                    levels.append(
                        {
                            "price": float(pl),
                            "type": "psychological",
                            "source": "round_number",
                            "strength": "medium",
                            "timeframe": tf,
                        }
                    )

        elif tf == "H4":
            # Niveaux mensuels/trimestriels
            df["month"] = df["timestamp"].dt.month
            df["year"] = df["timestamp"].dt.year
            for (year, month), group in df.groupby(["year", "month"]):
                levels.append(
                    {
                        "price": float(group["high"].max()),
                        "type": "resistance",
                        "source": f"monthly_high_{int(year)}_{int(month)}",
                        "strength": "medium",
                        "timeframe": tf,
                    }
                )
                levels.append(
                    {
                        "price": float(group["low"].min()),
                        "type": "support",
                        "source": f"monthly_low_{int(year)}_{int(month)}",
                        "strength": "medium",
                        "timeframe": tf,
                    }
                )

        elif tf == "H1":
            # Niveaux hebdomadaires
            df["week"] = df["timestamp"].dt.isocalendar().week.astype(int)
            df["year"] = df["timestamp"].dt.year
            for (year, week), group in df.groupby(["year", "week"]):
                levels.append(
                    {
                        "price": float(group["high"].max()),
                        "type": "resistance",
                        "source": f"weekly_high_{int(year)}_w{int(week)}",
                        "strength": "medium",
                        "timeframe": tf,
                    }
                )
                levels.append(
                    {
                        "price": float(group["low"].min()),
                        "type": "support",
                        "source": f"weekly_low_{int(year)}_w{int(week)}",
                        "strength": "medium",
                        "timeframe": tf,
                    }
                )

            # Niveaux récents (dernières 2000H ~ 3 mois)
            recent = df.tail(2000)
            for offset in [0, 100, 500, 1000]:
                chunk = recent.tail(max(100, len(recent) - offset)).head(100)
                if len(chunk) > 0:
                    levels.append(
                        {
                            "price": float(chunk["high"].max()),
                            "type": "resistance",
                            "source": f"recent_high_{offset}",
                            "strength": "minor",
                            "timeframe": tf,
                        }
                    )
                    levels.append(
                        {
                            "price": float(chunk["low"].min()),
                            "type": "support",
                            "source": f"recent_low_{offset}",
                            "strength": "minor",
                            "timeframe": tf,
                        }
                    )

        elif tf == "M15":
            # Niveaux quotidiens récents
            df["day"] = df["timestamp"].dt.date
            for day, group in list(df.groupby("day"))[-90:]:  # 90 derniers jours
                levels.append(
                    {
                        "price": float(group["high"].max()),
                        "type": "resistance",
                        "source": f"daily_high_{day}",
                        "strength": "minor",
                        "timeframe": tf,
                    }
                )
                levels.append(
                    {
                        "price": float(group["low"].min()),
                        "type": "support",
                        "source": f"daily_low_{day}",
                        "strength": "minor",
                        "timeframe": tf,
                    }
                )

        self.key_levels.extend(levels)

    def _merge_levels(self):
        """Fusionne les niveaux proches (regroupement par clusters)."""
        if not self.key_levels:
            return

        prices = np.array([l["price"] for l in self.key_levels])
        prices_sorted = np.sort(prices)
        eps = 0.0005  # 0.5 pips de tolérance

        clusters = []
        current = [prices_sorted[0]]
        for p in prices_sorted[1:]:
            if p - current[-1] < eps:
                current.append(p)
            else:
                clusters.append(np.mean(current))
                current = [p]
        clusters.append(np.mean(current))

        # Reconstruire les niveaux fusionnés
        merged = []
        for c in clusters:
            related = [l for l in self.key_levels if abs(l["price"] - c) < eps]
            types = [l["type"] for l in related]
            strengths = [l["strength"] for l in related]
            sources = [l["source"] for l in related]
            merged.append(
                {
                    "price": round(c, 5),
                    "type": "resistance" if "resistance" in types else "support",
                    "strength": "major" if "major" in strengths else "medium" if "medium" in strengths else "minor",
                    "source": sources[0],
                    "count": len(related),
                    "timeframes": list(set(l["timeframe"] for l in related if "timeframe" in l)),
                }
            )
        self.key_levels = merged

    def _find_value_zones(self):
        """Zones de value (HVN) basées sur le volume profile."""
        d1 = self.data.get("D1")
        if d1 is None or "volume" not in d1.columns:
            return

        # Zones de volume élevé
        d1 = d1.copy()
        d1["price_bucket"] = (d1["close"] * 10000).round(0) / 10000
        vol_profile = d1.groupby("price_bucket")["volume"].sum()
        vol_profile = vol_profile / vol_profile.max()

        for price, vol_ratio in vol_profile.items():
            if vol_ratio > 0.7:
                self.value_zones.append(
                    {
                        "price": float(price),
                        "type": "high_value",
                        "strength": "major" if vol_ratio > 0.85 else "medium",
                        "volume_ratio": float(vol_ratio),
                    }
                )

        logger.debug(f"  {self.symbol}: {len(self.value_zones)} zones de value détectées")

    def get_nearby_levels(self, price: float, distance_pct: float = 0.5) -> list[dict]:
        """Retourne les niveaux clés à proximité d'un prix (tous timeframes)."""
        nearby = []
        for level in self.key_levels:
            dist = abs(level["price"] - price) / price * 100
            if dist < distance_pct:
                nearby.append({**level, "distance_pct": round(dist, 3)})
        nearby.sort(key=lambda x: x["distance_pct"])

        # Ajouter les zones de value à proximité
        for zv in self.value_zones:
            dist = abs(zv["price"] - price) / price * 100
            if dist < distance_pct:
                nearby.append({**zv, "distance_pct": round(dist, 3)})

        return sorted(nearby, key=lambda x: x["distance_pct"])

    def get_key_level_longterm(self) -> dict:
        """Retourne les niveaux clés long terme (D1) pour le contexte macro."""
        d1_levels = [l for l in self.key_levels if "D1" in (l.get("timeframes", ["D1"])) and l["strength"] == "major"]
        return {
            "supports_majeurs": sorted([l["price"] for l in d1_levels if l["type"] == "support"], reverse=True)[:5],
            "resistances_majeures": sorted([l["price"] for l in d1_levels if l["type"] == "resistance"])[:5],
        }

    def get_mtf_alignment(self, price: float) -> dict:
        """Alignement multi-timeframe autour d'un prix (tendance par TF)."""
        alignment = {}
        for tf in ["M15", "H1", "H4", "D1"]:
            df = self.data.get(tf)
            if df is None or len(df) < 50:
                alignment[tf] = "unknown"
                continue
            recent = df.tail(20)
            ema20 = recent["close"].mean()
            ema50 = df.tail(50)["close"].mean()
            price_vs_ema = (price - ema20) / ema20 * 100

            if price_vs_ema > 0.5 and ema20 > ema50:
                alignment[tf] = "bullish"
            elif price_vs_ema < -0.5 and ema20 < ema50:
                alignment[tf] = "bearish"
            else:
                alignment[tf] = "neutral"

        return alignment


# ============================================================
# 3. PATTERN MATCHER — DTW + Chart Patterns
# ============================================================


class PatternMatcher:
    """Reconnaissance de patterns : DTW + Chartistes + Similarité."""

    def __init__(self, data: dict[str, pd.DataFrame]):
        self.data = data
        self._cache: dict = {}

    # ── A. DTW (Dynamic Time Warping) ──

    def _dtw_distance(self, a: np.ndarray, b: np.ndarray) -> float:
        """Distance DTW entre deux séries normalisées."""
        n, m = len(a), len(b)
        dtw = np.full((n + 1, m + 1), np.inf)
        dtw[0, 0] = 0
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                cost = (a[i - 1] - b[j - 1]) ** 2
                dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])
        return np.sqrt(dtw[n, m])

    def find_similar_dtw(
        self, recent: pd.DataFrame, n_results: int = 5, tf: str = "H1", use_dtw: bool = False
    ) -> list[dict]:
        """
        Trouve les N séquences historiques les plus similaires.
        recent: les dernières 20-120 bougies
        use_dtw: True = DTW (plus lent, meilleur), False = Euclidien (rapide)
        """
        df = self.data.get(tf)
        if df is None or len(df) < 200:
            return self._try_fallback(recent, n_results)

        if len(recent) < 10:
            return []

        recent_close = recent["close"].values
        recent_norm = (recent_close - recent_close[0]) / recent_close[0]
        all_close = df["close"].values
        window = len(recent)
        best_matches = []

        step = max(1, window // 4)  # Chevauchement 75%
        n_searches = (len(all_close) - window) // step
        if n_searches <= 0:
            return []

        for i in range(0, len(all_close) - window, step):
            chunk = all_close[i : i + window]
            chunk_norm = (chunk - chunk[0]) / chunk[0]

            if use_dtw:
                dist = self._dtw_distance(recent_norm, chunk_norm) / window
            else:
                dist = np.sqrt(np.mean((recent_norm - chunk_norm) ** 2))
            best_matches.append((dist, i))

        best_matches.sort(key=lambda x: x[0])

        results = []
        for dist, idx in best_matches[:n_results]:
            future_start = idx + window
            future_end = min(future_start + window // 2, len(all_close))
            future = all_close[future_start:future_end]

            if len(future) > 5:
                future_return = (future[-1] - chunk[-1]) / chunk[-1] * 100
                future_max = max(future) if len(future) > 0 else chunk[-1]
                future_min = min(future) if len(future) > 0 else chunk[-1]
                max_dd = (future_min - chunk[-1]) / chunk[-1] * 100
                max_run = (future_max - chunk[-1]) / chunk[-1] * 100
            else:
                future_return = 0
                max_dd = 0
                max_run = 0

            std_norm = max(np.std(recent_norm), 0.0001)
            results.append(
                {
                    "similarity": round(1 - dist / (2 * std_norm), 3),
                    "distance": round(dist, 5),
                    "index": int(idx),
                    "timestamp": str(df.iloc[idx]["timestamp"]),
                    "future_return_pct": round(future_return, 2),
                    "future_max_run_pct": round(max_run, 2),
                    "future_max_dd_pct": round(max_dd, 2),
                    "direction": "HAUSSE" if future_return > 0 else "BAISSE",
                }
            )

        return results

    def _try_fallback(self, recent: pd.DataFrame, n_results: int) -> list[dict]:
        """Fallback: essaie un autre timeframe."""
        for tf in ["H4", "D1"]:
            df = self.data.get(tf)
            if df is not None and len(df) > 200:
                return self.find_similar_dtw(recent, n_results, tf=tf)
        return []

    # ── B. PATTERNS CHARTISTES ──

    def detect_chart_patterns(self, recent: pd.DataFrame) -> list[dict]:
        """Détecte les patterns chartistes classiques."""
        patterns: list[dict] = []
        open_p = recent["open"].values
        close = recent["close"].values
        high = recent["high"].values
        low = recent["low"].values
        n = len(close)

        if n < 20:
            return patterns

        # 1. Double Top / Double Bottom
        for lookback in [20, 30, 40]:
            if n < lookback * 2:
                continue
            h_segment = high[-lookback:]
            l_segment = low[-lookback:]
            h_max1 = np.max(h_segment[: lookback // 2])
            h_max2 = np.max(h_segment[lookback // 2 :])
            h_max1_idx = np.argmax(h_segment[: lookback // 2])
            h_max2_idx = np.argmax(h_segment[lookback // 2 :]) + lookback // 2

            l_min1 = np.min(l_segment[: lookback // 2])
            l_min2 = np.min(l_segment[lookback // 2 :])

            # Double Top : deux sommets proches
            if abs(h_max1 - h_max2) / h_max1 < 0.002 and h_max2_idx - h_max1_idx > lookback // 4:
                neckline = min(l_segment[h_max1_idx : h_max2_idx + 1])
                patterns.append(
                    {
                        "pattern": "double_top",
                        "strength": "major" if abs(h_max1 - neckline) / neckline > 0.01 else "minor",
                        "price1": float(h_max1),
                        "price2": float(h_max2),
                        "neckline": float(neckline),
                        "target": float(neckline - (h_max1 - neckline)),
                        "timeframe": "H1",
                    }
                )

            # Double Bottom : deux creux proches
            if abs(l_min1 - l_min2) / l_min1 < 0.002:
                l_min1_idx = np.argmin(l_segment[: lookback // 2])
                l_min2_idx = np.argmin(l_segment[lookback // 2 :]) + lookback // 2
                neckline = max(h_segment[l_min1_idx : l_min2_idx + 1])
                if l_min2_idx - l_min1_idx > lookback // 4:
                    patterns.append(
                        {
                            "pattern": "double_bottom",
                            "strength": "major" if abs(neckline - l_min1) / neckline > 0.01 else "minor",
                            "price1": float(l_min1),
                            "price2": float(l_min2),
                            "neckline": float(neckline),
                            "target": float(neckline + (neckline - l_min1)),
                            "timeframe": "H1",
                        }
                    )

        # 2. Head and Shoulders / Inverse H&S
        if n >= 40:
            mid = n // 2
            left_shoulder = max(high[5 : mid - 5])
            head = max(high[mid - 5 : mid + 5])
            right_shoulder = max(high[mid + 5 : -5])

            if head > left_shoulder and head > right_shoulder and abs(left_shoulder - right_shoulder) / head < 0.01:
                neckline = (min(low[5:mid]) + min(low[mid:-5])) / 2
                patterns.append(
                    {
                        "pattern": "head_and_shoulders",
                        "strength": "major",
                        "left_shoulder": float(left_shoulder),
                        "head": float(head),
                        "right_shoulder": float(right_shoulder),
                        "neckline": float(neckline),
                        "target": float(neckline - (head - neckline)),
                        "timeframe": "H1",
                    }
                )

            # Inverse H&S
            left_shoulder_l = min(low[5 : mid - 5])
            head_l = min(low[mid - 5 : mid + 5])
            right_shoulder_l = min(low[mid + 5 : -5])

            if (
                head_l < left_shoulder_l
                and head_l < right_shoulder_l
                and abs(left_shoulder_l - right_shoulder_l) / max(abs(head_l), 0.0001) < 0.01
            ):
                neckline = (max(high[5:mid]) + max(high[mid:-5])) / 2
                patterns.append(
                    {
                        "pattern": "inverse_head_and_shoulders",
                        "strength": "major",
                        "left_shoulder": float(left_shoulder_l),
                        "head": float(head_l),
                        "right_shoulder": float(right_shoulder_l),
                        "neckline": float(neckline),
                        "target": float(neckline + (neckline - head_l)),
                        "timeframe": "H1",
                    }
                )

        # 3. Bull Flag / Bear Flag (continuation patterns)
        if n >= 20:
            trend_part = close[:10]
            flag_part = close[-10:]
            trend_move = trend_part[-1] - trend_part[0]

            if trend_move > 0 and max(flag_part) - min(flag_part) < abs(trend_move) * 0.3:
                # Consolidation après hausse = Bull Flag
                patterns.append(
                    {
                        "pattern": "bull_flag",
                        "strength": "medium",
                        "trend_pct": float(trend_move / trend_part[0] * 100),
                        "flag_range_pct": float((max(flag_part) - min(flag_part)) / flag_part[0] * 100),
                        "timeframe": "H1",
                    }
                )
            elif trend_move < 0 and max(flag_part) - min(flag_part) < abs(trend_move) * 0.3:
                # Consolidation après baisse = Bear Flag
                patterns.append(
                    {
                        "pattern": "bear_flag",
                        "strength": "medium",
                        "trend_pct": float(trend_move / trend_part[0] * 100),
                        "flag_range_pct": float((max(flag_part) - min(flag_part)) / flag_part[0] * 100),
                        "timeframe": "H1",
                    }
                )

        # 4. Engulfing patterns (sur les 5 dernières bougies)
        for i in range(max(1, n - 5), n):
            if i < 2:
                continue
            body_prev = close[i - 1] - open_p[i - 1]
            body_curr = close[i] - open_p[i]
            # Bullish Engulfing
            if body_prev < 0 and body_curr > 0 and close[i] > open_p[i - 1] and open_p[i] < close[i - 1]:
                patterns.append(
                    {
                        "pattern": "bullish_engulfing",
                        "strength": "medium",
                        "index": i,
                        "timeframe": "H1",
                    }
                )
            # Bearish Engulfing
            elif body_prev > 0 and body_curr < 0 and close[i] < open_p[i - 1] and open_p[i] > close[i - 1]:
                patterns.append(
                    {
                        "pattern": "bearish_engulfing",
                        "strength": "medium",
                        "index": i,
                        "timeframe": "H1",
                    }
                )

        return patterns

    # ── C. SIGNAL AGRÉGÉ ──

    def get_pattern_signal(self, recent: pd.DataFrame, use_dtw: bool = False, tf: str = "H1") -> dict:
        """Signal complet : pattern matching + chartistes."""
        # Pattern matching historique
        matches = self.find_similar_dtw(recent, n_results=10, tf=tf, use_dtw=use_dtw)

        # Patterns chartistes
        chart_patterns = self.detect_chart_patterns(recent)

        if not matches:
            base_signal = {"signal": "NEUTRE", "confidence": 0, "details": "pas de pattern"}
        else:
            hausse = sum(1 for m in matches if m["future_return_pct"] > 0)
            baisse = sum(1 for m in matches if m["future_return_pct"] < 0)
            total = len(matches)

            if hausse > baisse * 2 and hausse >= 4:
                avg_return = np.mean([m["future_return_pct"] for m in matches if m["future_return_pct"] > 0])
                base_signal = {
                    "signal": "HAUSSE",
                    "confidence": round(hausse / total, 2),
                    "avg_return_pct": round(avg_return, 2),
                    "matches": hausse,
                    "total": total,
                }
            elif baisse > hausse * 2 and baisse >= 4:
                avg_return = np.mean([m["future_return_pct"] for m in matches if m["future_return_pct"] < 0])
                base_signal = {
                    "signal": "BAISSE",
                    "confidence": round(baisse / total, 2),
                    "avg_return_pct": round(avg_return, 2),
                    "matches": baisse,
                    "total": total,
                }
            else:
                base_signal = {
                    "signal": "NEUTRE",
                    "confidence": 0,
                    "details": f"partagé {hausse}/{baisse}",
                }

        # Ajustement par patterns chartistes
        chart_signal = self._chart_pattern_signal(chart_patterns)

        # Fusion
        if base_signal["signal"] != "NEUTRE" and chart_signal["signal"] != "NEUTRE":
            if base_signal["signal"] == chart_signal["signal"]:
                # Accord → confiance renforcée
                confidence = min(1.0, base_signal.get("confidence", 0) + chart_signal.get("confidence", 0) * 0.3)
                return {
                    **base_signal,
                    "confidence": confidence,
                    "boosted": True,
                    "chart_patterns": chart_patterns,
                }
            else:
                # Désaccord → confiance réduite
                return {
                    **base_signal,
                    "confidence": base_signal.get("confidence", 0) * 0.5,  # type: ignore[operator]
                    "conflict": True,
                    "chart_signal": chart_signal,
                    "chart_patterns": chart_patterns,
                }

        return {**base_signal, "chart_patterns": chart_patterns}

    def _chart_pattern_signal(self, patterns: list[dict]) -> dict:
        """Extrait un signal global des patterns chartistes."""
        if not patterns:
            return {"signal": "NEUTRE", "confidence": 0}

        bullish_patterns = {"double_bottom", "inverse_head_and_shoulders", "bull_flag", "bullish_engulfing"}
        bearish_patterns = {"double_top", "head_and_shoulders", "bear_flag", "bearish_engulfing"}

        bullish = sum(1 for p in patterns if p["pattern"] in bullish_patterns)
        bearish = sum(1 for p in patterns if p["pattern"] in bearish_patterns)
        total = len(patterns)

        if bullish > bearish and bullish >= 2:
            return {"signal": "HAUSSE", "confidence": round(bullish / total, 2), "count": bullish}
        elif bearish > bullish and bearish >= 2:
            return {"signal": "BAISSE", "confidence": round(bearish / total, 2), "count": bearish}
        return {"signal": "NEUTRE", "confidence": 0}


# ============================================================
# 4. MARKET MEMORY — Intégration complète multi-timeframe
# ============================================================


class MarketMemory:
    """
    Connaît le marché par cœur (4 timeframes, 16+ ans).
    Pour chaque symbole : profil statistique, niveaux clés, patterns DTW + chartistes.
    """

    def __init__(self):
        self.profiles: dict[str, MarketProfile] = {}
        self.structures: dict[str, MarketStructure] = {}
        self.matchers: dict[str, PatternMatcher] = {}
        self._loaded = False
        self._load_time = 0

    def load_all(self, force: bool = False) -> "MarketMemory":
        """Charge la mémoire pour tous les symboles (4 timeframes)."""
        if self._loaded and not force:
            return self

        logger.info("Chargement de la mémoire du marché (4 timeframes)...")
        for symbol in SYMBOLS:
            profile = MarketProfile(symbol).load()
            self.profiles[symbol] = profile

            if profile.data:
                struct = MarketStructure(symbol, profile.data).compute()
                self.structures[symbol] = struct

                matcher = PatternMatcher(profile.data)
                self.matchers[symbol] = matcher

        self._loaded = True
        self._load_time = time.time()
        logger.info(f"Mémoire chargée: {len(self.profiles)} symboles × 4 timeframes")
        return self

    def get_summary(self, symbol: str | None = None) -> dict | list:
        """Résumé de la mémoire pour un ou tous les symboles."""
        if symbol:
            p = self.profiles.get(symbol)
            return p.get_summary() if p else {"error": f"{symbol} non trouvé"}
        return [p.get_summary() for p in self.profiles.values() if p._loaded]

    def get_nearby_levels(self, symbol: str, price: float, distance: float = 0.5) -> list[dict]:
        """Niveaux clés proches d'un prix (tous timeframes)."""
        struct = self.structures.get(symbol)
        if struct and struct._computed:
            return struct.get_nearby_levels(price, distance)
        return []

    def get_pattern_context(self, symbol: str, recent: pd.DataFrame, use_dtw: bool = False, tf: str = "H1") -> dict:
        """Contexte de pattern pour un symbole."""
        matcher = self.matchers.get(symbol)
        if matcher:
            return matcher.get_pattern_signal(recent, use_dtw=use_dtw, tf=tf)
        return {"signal": "NEUTRE", "confidence": 0, "details": "pas de matcher"}

    def get_mtf_alignment(self, symbol: str, price: float) -> dict:
        """Alignement multi-timeframe."""
        struct = self.structures.get(symbol)
        if struct and struct._computed:
            return struct.get_mtf_alignment(price)
        return {}

    def get_market_context(self, symbol: str, current_price: float, recent: pd.DataFrame | None = None) -> dict:
        """Contexte complet du marché pour un symbole."""
        profile = self.profiles.get(symbol)
        struct = self.structures.get(symbol)
        matcher = self.matchers.get(symbol)

        context = {
            "symbol": symbol,
            "profil": profile.get_summary() if profile and profile._loaded else {},
            "niveaux_proches": struct.get_nearby_levels(current_price, 0.5) if struct and struct._computed else [],
            "mtf_alignment": struct.get_mtf_alignment(current_price) if struct and struct._computed else {},
            "pattern": matcher.get_pattern_signal(recent) if matcher and recent is not None else {},
        }

        # Ajouter les niveaux long terme
        if struct and struct._computed:
            context["niveaux_long_terme"] = struct.get_key_level_longterm()

        return context

    def print_report(self):
        """Affiche un rapport complet de la mémoire du marché."""
        print("\n" + "=" * 80)
        print("  MÉMOIRE DU MARCHÉ — RAPPORT COMPLET (4 TIMEFRAMES)")
        print("=" * 80)

        for symbol in SYMBOLS:
            p = self.profiles.get(symbol)
            if not p or not p._loaded:
                continue

            s = p.stats
            print(f"\n{'─' * 80}")
            print(f"  {symbol}")
            print(f"{'─' * 80}")
            print(
                f"  Période : {s['periode']['debut'][:10]} → {s['periode']['fin'][:10]} ({s['periode']['annees']} ans)"
            )
            print(
                f"  Bougies : M15={s['periode']['candles_m15']:,} | H1={s['periode']['candles_h1']:,} | H4={s['periode']['candles_h4']:,} | D1={s['periode']['candles_d1']:,}"
            )
            print(
                f"  ATR H1 : {s['volatilite']['atr_mean_h1']:.5f} | Range H1: {s['volatilite']['range_pct_mean_h1']:.2f}%"
            )
            print(
                f"  RSI : {s['rsi']['mean']:.1f} (OB {s['rsi']['overbought_pct']:.1f}%, OS {s['rsi']['oversold_pct']:.1f}%)"
            )
            print(f"  ADX trending : {s['adx']['trending_pct_h1']:.1f}% | ranging: {s['adx']['ranging_pct_h1']:.1f}%")
            print(f"  MACD achat : {s['macd']['signal_achat_h1']:.1f}% | vente : {s['macd']['signal_vente_h1']:.1f}%")
            print(f"  Doji : {s['candles']['doji_pct']:.1f}% | Marubozu: {s['candles']['marubozu_pct']:.1f}%")
            print(
                f"  Patterns bougies : Hammer {s['candles']['hammer_pct']:.1f}% | Shooting Star {s['candles']['shooting_star_pct']:.1f}%"
            )
            print(f"  Volume élevé (>1.5x) : {s['volume']['high_volume_pct_h1']:.1f}% du temps")

            tend_h1 = s.get("tendance_h1", {})
            print(
                f"  Tendance H1 : hausse {tend_h1.get('hausse_pct', 0):.1f}% | baisse {tend_h1.get('baisse_pct', 0):.1f}%"
            )
            tend_d1 = s.get("tendance_d1", {})
            print(
                f"  Tendance D1 : hausse {tend_d1.get('hausse_pct', 0):.1f}% | baisse {tend_d1.get('baisse_pct', 0):.1f}%"
            )

            if "d1" in s:
                print(f"  Plus haut historique : {s['d1']['plus_haut_historique']:.5f}")
                print(f"  Plus bas historique : {s['d1']['plus_bas_historique']:.5f}")

            # Niveaux clés
            struct = self.structures.get(symbol)
            if struct and struct._computed:
                print(f"\n  Niveaux clés : {len(struct.key_levels)} (dont {len(struct.value_zones)} zones de value)")
                d1_highs = [
                    l
                    for l in struct.key_levels
                    if "D1" in (l.get("timeframes", ["D1"]))
                    and l.get("type") == "resistance"
                    and l.get("strength") == "major"
                ]
                d1_lows = [
                    l
                    for l in struct.key_levels
                    if "D1" in (l.get("timeframes", ["D1"]))
                    and l.get("type") == "support"
                    and l.get("strength") == "major"
                ]
                if d1_highs:
                    closest_high = min(d1_highs, key=lambda x: abs(x["price"] - float(d1_highs[0]["price"])))
                    print(f"  Résistance D1 majeure : {closest_high['price']:.5f}")
                if d1_lows:
                    closest_low = min(d1_lows, key=lambda x: abs(x["price"] - float(d1_lows[0]["price"])))
                    print(f"  Support D1 majeur : {closest_low['price']:.5f}")

        print("\n" + "=" * 80)
        print("  MÉMOIRE CHARGÉE — Prêt pour l'anticipation")
        print("=" * 80)
