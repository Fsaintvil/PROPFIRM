"""Order Flow Analyzer — Analyse du flux de commandes (ticks) + divergence delta.

Calcule les métriques de order flow à partir des données de ticks MT5 :
- Net delta: volume acheteur - vendeur (basé sur le mouvement du tick)
- Cumulative delta: delta cumulé sur la période
- Absorption: gros volume avec peu de mouvement = absorption institutionnelle
- Divergence delta: prix monte mais delta baisse = piège haussier
- Imbalance: déséquilibre bid/ask

Usage:
    flow = OrderFlowAnalyzer()
    metrics = flow.analyze_ticks_from_mt5(mt5_connector, "BTCUSD")
    result = flow.get_flow_adjustment(metrics, signal_action)
    if result["score_adj"] < 0.9:
        logger.info(f"Delta divergence détectée: {result['reason']}")
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass

logger = logging.getLogger("order_flow")

# Seuils
DIVERGENCE_LOOKBACK = 50  # Bougies pour détecter divergence
DIVERGENCE_MIN_CONF = 0.3  # Force minimale pour signaler divergence
ABSORPTION_VOL_MULT = 2.5  # Multiplicateur volume pour absorption
ABSORPTION_PRICE_MULT = 0.3  # Multiplicateur ATR pour mouvement max


@dataclass
class FlowMetrics:
    """Métriques de order flow."""

    net_delta: float = 0.0
    cum_delta: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    total_volume: float = 0.0
    imbalance: float = 0.0
    absorption: float = 0.0  # 0-1 : force de l'absorption
    large_orders: int = 0
    avg_trade_size: float = 0.0
    delta_divergence: str = "none"  # "bullish", "bearish", "none"
    divergence_strength: float = 0.0
    is_tick_data: bool = False

    def to_dict(self) -> dict:
        return {
            "net_delta": self.net_delta,
            "cum_delta": self.cum_delta,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "total_volume": self.total_volume,
            "imbalance": self.imbalance,
            "absorption": self.absorption,
            "large_orders": self.large_orders,
            "avg_trade_size": self.avg_trade_size,
            "delta_divergence": self.delta_divergence,
            "divergence_strength": self.divergence_strength,
            "is_tick_data": self.is_tick_data,
        }


class OrderFlowAnalyzer:
    """Analyse le flux de commandes avec support des ticks MT5."""

    _delta_history: dict[str, np.ndarray] = {}  # Cache du delta par symbole

    def __init__(self, large_order_threshold: float = 1.5, absorption_threshold: float = 2.5):
        self.large_order_threshold = large_order_threshold
        self.absorption_threshold = absorption_threshold

    def analyze_ticks_from_mt5(self, mt5_connector, symbol: str, count: int = 1000) -> FlowMetrics:
        """Analyse les ticks réels depuis MT5.

        Utilise mt5.copy_ticks_from() pour obtenir les données tick.
        Estime le delta réel via le mouvement du prix tick par tick.

        Args:
            mt5_connector: Instance du connecteur MT5
            symbol: Symbole (ex: "BTCUSD")
            count: Nombre de ticks à analyser

        Returns:
            FlowMetrics
        """
        try:
            import MetaTrader5 as mt5

            ticks = mt5.copy_ticks_from(symbol, pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=30), count)
        except Exception as e:
            logger.warning(f"  [FLOW] analyze_ticks copy_ticks: {e}")
            ticks = None

        if ticks is None or len(ticks) < 20:
            logger.debug(
                f"  [FLOW] {symbol}: pas assez de ticks ({len(ticks) if ticks is not None else 0})"
                f" — fallback analyze_bars"
            )
            return FlowMetrics(is_tick_data=False)

        df = pd.DataFrame(ticks)
        return self._compute_from_ticks(df, symbol)

    def _compute_from_ticks(self, df: pd.DataFrame, symbol: str) -> FlowMetrics:
        """Calcule les métriques depuis un DataFrame de ticks réels."""
        if "last" not in df.columns or "volume" not in df.columns:
            return FlowMetrics(is_tick_data=False)

        data = df.copy()
        vol = data["volume"].values.astype(float)

        # Direction réelle du tick : last ≥ last[-1] = buy (initiated by buyer)
        data["direction"] = np.where(data["last"] >= data["last"].shift(1), 1, -1)
        direction = data["direction"].values

        buy_mask = direction == 1
        sell_mask = direction == -1

        buy_volume = vol[buy_mask].sum()
        sell_volume = vol[sell_mask].sum()
        total_volume = vol.sum()

        if total_volume == 0:
            return FlowMetrics(is_tick_data=True)

        net_delta = buy_volume - sell_volume
        cum_delta = np.sum(vol * direction)
        imbalance = abs(net_delta) / max(total_volume, 1e-10)

        # Large orders (tick volume)
        avg_size = vol.mean()
        large_orders = int(np.sum(vol > avg_size * self.large_order_threshold))

        # Absorption : gros volume avec petit mouvement de prix
        absorption = 0.0
        if len(data) > 50:
            last_ticks = data.tail(min(200, len(data) // 2))
            recent_vol = last_ticks["volume"].sum()
            half_vol = data.head(len(data) // 2)["volume"].sum() if len(data) > 100 else recent_vol
            price_range = last_ticks["last"].max() - last_ticks["last"].min()
            avg_tick_move = np.mean(np.abs(np.diff(last_ticks["last"].values)))

            # Absorption = volume anormalement élevé + mouvement anormalement faible
            vol_ratio = recent_vol / max(half_vol / max(len(data) // 2, 1) * len(last_ticks), 1)
            move_ratio = avg_tick_move / max(price_range / max(len(last_ticks), 1), 1e-10)

            if vol_ratio > self.absorption_threshold and move_ratio < 0.5:
                absorption = min(1.0, (vol_ratio - 1.0) / 3.0)

        # Divergence delta : comparer prix et delta sur les N dernières barres
        divergence, div_strength = self._detect_delta_divergence(data, symbol)

        # Mettre à jour le cache du delta
        self._delta_history.setdefault(symbol, np.array([]))
        self._delta_history[symbol] = np.append(self._delta_history[symbol], net_delta)[-500:]

        return FlowMetrics(
            net_delta=round(net_delta, 2),
            cum_delta=round(cum_delta, 2),
            buy_volume=round(buy_volume, 2),
            sell_volume=round(sell_volume, 2),
            total_volume=round(total_volume, 2),
            imbalance=round(imbalance, 4),
            absorption=round(absorption, 3),
            large_orders=large_orders,
            avg_trade_size=round(avg_size, 2),
            delta_divergence=divergence,
            divergence_strength=round(div_strength, 3),
            is_tick_data=True,
        )

    def _detect_delta_divergence(self, tick_df: pd.DataFrame, symbol: str) -> tuple[str, float]:
        """Détecte la divergence entre prix et delta cumulé.

        Bullish divergence: le prix fait un plus bas mais le delta cumulé fait un plus haut
        Bearish divergence: le prix fait un plus haut mais le delta cumulé fait un plus bas

        Returns:
            (type, strength): "bullish"/"bearish"/"none", force 0-1
        """
        if len(tick_df) < DIVERGENCE_LOOKBACK:
            # Pas assez de données pour une divergence fiable
            # Utiliser le cache historique si disponible
            hist = self._delta_history.get(symbol, np.array([]))
            if len(hist) < 20:
                return "none", 0.0
            # Analyser les N dernières valeurs de delta
            delta_chunk = hist[-DIVERGENCE_LOOKBACK:]
            price_chunk = None
        else:
            # Échantillonner le delta sur des fenêtres de ticks
            n_windows = min(DIVERGENCE_LOOKBACK, len(tick_df) // 10)
            if n_windows < 5:
                return "none", 0.0

            chunk_size = len(tick_df) // n_windows
            prices = []
            deltas = []
            for i in range(n_windows):
                chunk = tick_df.iloc[i * chunk_size : (i + 1) * chunk_size]
                if len(chunk) < 2:
                    continue
                prices.append(chunk["last"].mean())
                chunk_dir = np.where(chunk["last"].values >= np.roll(chunk["last"].values, 1), 1, -1)
                chunk_dir[0] = 0
                deltas.append(np.sum(chunk["volume"].values * chunk_dir))

            if len(prices) < 5:
                return "none", 0.0

            price_chunk = np.array(prices)
            delta_chunk = np.array(deltas)

        # Normaliser
        if price_chunk is not None:
            p_min, p_max = price_chunk.min(), price_chunk.max()
            d_min, d_max = delta_chunk.min(), delta_chunk.max()
        else:
            p_min = p_max = 0
            d_min, d_max = delta_chunk.min(), delta_chunk.max()

        if p_max - p_min < 1e-10 or d_max - d_min < 1e-10:
            return "none", 0.0

        # Dernière fenêtre
        if price_chunk is not None:
            last_price = price_chunk[-1]
            last_delta = delta_chunk[-1]
            price_low = price_chunk.min()
            price_high = price_chunk.max()
            delta_low = delta_chunk.min()
            delta_high = delta_chunk.max()
        else:
            return "none", 0.0

        # Bearish divergence: prix ≥ 80e percentile, delta ≤ 20e percentile
        p80 = np.percentile(price_chunk, 80)
        d20 = np.percentile(delta_chunk, 20)
        if last_price >= p80 and delta_high <= d20:
            strength = min(1.0, abs(last_delta - d20) / max(abs(d20), 1))
            if strength >= DIVERGENCE_MIN_CONF:
                return "bearish", strength

        # Bullish divergence: prix ≤ 20e percentile, delta ≥ 80e percentile
        p20 = np.percentile(price_chunk, 20)
        d80 = np.percentile(delta_chunk, 80)
        if last_price <= p20 and delta_low >= d80:
            strength = min(1.0, abs(last_delta - d80) / max(abs(d80), 1))
            if strength >= DIVERGENCE_MIN_CONF:
                return "bullish", strength

        return "none", 0.0

    def analyze_bars(self, df: pd.DataFrame) -> FlowMetrics:
        """Analyse le flow à partir de barres OHLCV (fallback si pas de ticks).

        Estime le delta basé sur la direction de la bougie (close vs open).
        Moins précis que les ticks réels mais toujours utile.
        """
        if df is None or len(df) < 10:
            return FlowMetrics(is_tick_data=False)

        data = df.tail(100).copy()
        data["bullish"] = data["close"] >= data["open"]
        vol = data["volume"].values.astype(float)
        bullish = data["bullish"].values

        buy_volume = vol[bullish].sum()
        sell_volume = vol[~bullish].sum()
        total_volume = vol.sum()

        if total_volume == 0:
            return FlowMetrics(is_tick_data=False)

        net_delta = buy_volume - sell_volume
        cum_delta = np.sum(np.where(bullish, vol, -vol))
        imbalance = abs(net_delta) / total_volume

        # Large bars
        avg_vol = vol.mean()
        large_orders = int(np.sum(vol > avg_vol * self.large_order_threshold))

        # Absorption approximative sur barres
        absorption = 0.0
        if len(data) > 20:
            recent = data.tail(20)
            recent_vol_sum = recent["volume"].sum()
            prior_vol_sum = data.head(20)["volume"].sum() if len(data) > 40 else recent_vol_sum
            recent_range = recent["high"].max() - recent["low"].min()
            recent_atr = recent_range / max(len(recent), 1)

            if prior_vol_sum > 0 and recent_atr > 1e-10:
                vol_ratio = recent_vol_sum / prior_vol_sum
                if vol_ratio > 2.0 and recent_range < avg_vol * 0.5:
                    absorption = min(1.0, (vol_ratio - 1.0) / 3.0)

        return FlowMetrics(
            net_delta=round(net_delta, 2),
            cum_delta=round(cum_delta, 2),
            buy_volume=round(buy_volume, 2),
            sell_volume=round(sell_volume, 2),
            total_volume=round(total_volume, 2),
            imbalance=round(imbalance, 4),
            absorption=round(absorption, 3),
            large_orders=large_orders,
            avg_trade_size=round(avg_vol, 2),
            delta_divergence="none",
            divergence_strength=0.0,
            is_tick_data=False,
        )

    def get_flow_adjustment(self, metrics: FlowMetrics, signal_action: str) -> dict:
        """Calcule l'ajustement de score basé sur les métriques de flow.

        Args:
            metrics: FlowMetrics de l'analyse
            signal_action: "BUY" ou "SELL"

        Returns:
            dict avec score_adj, reason, details
        """
        if metrics.total_volume == 0:
            return {"score_adj": 1.0, "reason": None}

        score_adj = 1.0
        reasons = []

        # 1. Delta direction
        flow_is_bullish = metrics.net_delta > 0
        flow_is_bearish = metrics.net_delta < 0

        if flow_is_bullish and signal_action == "BUY":
            boost = min(0.08, metrics.imbalance * 0.12)
            score_adj = min(1.12, score_adj + boost)
            reasons.append(f"delta_BUY({metrics.imbalance:.2f})")
        elif flow_is_bearish and signal_action == "SELL":
            boost = min(0.08, metrics.imbalance * 0.12)
            score_adj = min(1.12, score_adj + boost)
            reasons.append(f"delta_SELL({metrics.imbalance:.2f})")
        elif flow_is_bullish and signal_action == "SELL":
            penalty = min(0.12, metrics.imbalance * 0.15)
            score_adj = max(0.80, score_adj - penalty)
            reasons.append(f"delta_oppose_BUY({metrics.imbalance:.2f})")
        elif flow_is_bearish and signal_action == "BUY":
            penalty = min(0.12, metrics.imbalance * 0.15)
            score_adj = max(0.80, score_adj - penalty)
            reasons.append(f"delta_oppose_SELL({metrics.imbalance:.2f})")

        # 2. Divergence delta (très fort signal)
        if metrics.delta_divergence == "bullish" and signal_action == "BUY":
            boost = min(0.15, metrics.divergence_strength * 0.20)
            score_adj = min(1.20, score_adj + boost)
            reasons.append(f"divergence_bullish({metrics.divergence_strength:.2f})")
        elif metrics.delta_divergence == "bearish" and signal_action == "SELL":
            boost = min(0.15, metrics.divergence_strength * 0.20)
            score_adj = min(1.20, score_adj + boost)
            reasons.append(f"divergence_bearish({metrics.divergence_strength:.2f})")
        elif metrics.delta_divergence == "bullish" and signal_action == "SELL":
            penalty = min(0.20, metrics.divergence_strength * 0.25)
            score_adj = max(0.65, score_adj - penalty)
            reasons.append(f"divergence_oppose_bullish({metrics.divergence_strength:.2f})")
        elif metrics.delta_divergence == "bearish" and signal_action == "BUY":
            penalty = min(0.20, metrics.divergence_strength * 0.25)
            score_adj = max(0.65, score_adj - penalty)
            reasons.append(f"divergence_oppose_bearish({metrics.divergence_strength:.2f})")

        # 3. Absorption (contre-tendance potentielle)
        if metrics.absorption > 0.3:
            # Absorption forte = possible retournement
            score_adj = max(0.75, score_adj - metrics.absorption * 0.10)
            reasons.append(f"absorption({metrics.absorption:.2f})")

        # 4. Large orders (confirmation)
        if metrics.large_orders > 3:
            if (flow_is_bullish and signal_action == "BUY") or (flow_is_bearish and signal_action == "SELL"):
                score_adj = min(1.10, score_adj * 1.03)
                reasons.append("large_orders")

        # Indiquer si on utilise des ticks réels ou barres
        if not metrics.is_tick_data:
            score_adj = 0.5 + score_adj * 0.5  # Atténuer de 50% si barres uniquement

        return {
            "score_adj": round(min(1.50, max(0.50, score_adj)), 4),
            "reason": " + ".join(reasons) if reasons else None,
            "net_delta": metrics.net_delta,
            "imbalance": metrics.imbalance,
            "divergence": metrics.delta_divergence,
            "absorption": metrics.absorption,
            "is_tick_data": metrics.is_tick_data,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_flow = OrderFlowAnalyzer()


def analyze_ticks_from_mt5(mt5_connector, symbol: str, count: int = 1000) -> FlowMetrics:
    """Analyse les ticks réels depuis MT5 (fonction convenience)."""
    return _default_flow.analyze_ticks_from_mt5(mt5_connector, symbol, count)


def analyze_bars(df: pd.DataFrame) -> FlowMetrics:
    """Analyse les barres OHLCV (fonction convenience)."""
    return _default_flow.analyze_bars(df)


def get_flow_signal(metrics: FlowMetrics) -> tuple[str, float]:
    """Retourne le signal de flow (fonction convenience, ancienne API)."""
    if metrics.total_volume == 0:
        return "NEUTRAL", 0.0
    if metrics.net_delta > 0:
        return "BUY", min(metrics.imbalance * 2, 1.0)
    elif metrics.net_delta < 0:
        return "SELL", min(metrics.imbalance * 2, 1.0)
    return "NEUTRAL", 0.0


def get_flow_adjustment(metrics: FlowMetrics, signal_action: str) -> dict:
    """Calcule l'ajustement de score (fonction convenience)."""
    return _default_flow.get_flow_adjustment(metrics, signal_action)
