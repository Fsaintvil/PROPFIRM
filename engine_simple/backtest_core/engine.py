"""
BacktestEngine — Orchestrateur principal du backtest.

Flux :
  1. Charger les données (via DataLoader)
  2. Précalculer les indicateurs
  3. Itérer bar par bar
  4. Pour chaque barre :
     a. Mettre à jour les positions ouvertes (SL/TP/trailing/partial)
     b. Vérifier les timeouts
     c. Générer les signaux (via Strategy)
     d. Exécuter les ordres (via ExecutionEngine)
     e. Appliquer les coûts (via CostModel)
  5. Calculer les métriques (via MetricsCalculator)
  6. Retourner BacktestResult

Usage :
    engine = BacktestEngine(config)
    result = engine.run(symbol="EURUSD", strategy=my_strategy,
                        data=df, timeframe="H1")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from engine_simple.backtest_core.costs import CostModel, get_pip_info
from engine_simple.backtest_core.execution import (
    ExecutionEngine,
    MarketCond,
    FillResult,
)
from engine_simple.backtest_core.metrics import MetricsCalculator
from engine_simple.backtest_core.trade import SimTrade

logger = logging.getLogger("backtest_core.engine")


# ─── Configuration types ──────────────────────────────────────────────────


@dataclass
class BacktestConfig:
    """Configuration complète d'un backtest."""

    # Capital
    initial_balance: float = 200_000.0
    risk_per_trade: float = 0.0044  # 0.44% du capital par trade

    # Timeouts
    timeout_bars: dict[str, int] = None  # Par TF : {"H1": 120, "H4": 60, "D1": 30}

    # Limites
    max_positions: int = 5
    max_positions_per_symbol: int = 2
    min_bars_between_trades: int = 5
    min_bars_warmup: int = 80

    # Exécution
    latency_ms: float = 100
    requote_prob: float = 0.02
    enable_partial_fill: bool = True

    # Coûts
    costs_config: Optional[dict] = None

    # Trailing
    trailing_levels: Optional[dict] = None
    be_buffer_atr: float = 0.80

    # Multi timeframe (HTF = confirmation, LTF = entrée)
    htf_timeframe: Optional[str] = None
    htf_weight: float = 0.90

    def __post_init__(self):
        if self.timeout_bars is None:
            self.timeout_bars = {"H1": 120, "H4": 60, "D1": 30}


@dataclass
class BacktestResult:
    """Résultat complet d'un backtest."""

    symbol: str
    timeframe: str
    strategy_name: str
    config: BacktestConfig

    # Trades
    trades: list[SimTrade] = field(default_factory=list)

    # Courbes
    equity_curve: list[float] = field(default_factory=list)
    balance_curve: list[float] = field(default_factory=list)
    dd_curve: list[float] = field(default_factory=list)
    dates: list[datetime] = field(default_factory=list)

    # Métriques brutes
    total_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0

    # Métriques calculées
    metrics: dict = field(default_factory=dict)

    # Stats execution
    n_signals: int = 0
    n_rejected: int = 0  # Signaux rejetés (max positions, etc.)
    n_requotes: int = 0
    n_partial_fills: int = 0
    total_costs: float = 0.0

    @property
    def closed_trades(self) -> list[SimTrade]:
        return [t for t in self.trades if t.closed]

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.profit_usd_cost > 0)
        return wins / len(self.closed_trades) * 100

    @property
    def net_profit(self) -> float:
        return sum(t.profit_usd_cost for t in self.closed_trades)


# ═══════════════════════════════════════════════════════════════════════════
# BacktestEngine
# ═══════════════════════════════════════════════════════════════════════════


class BacktestEngine:
    """
    Moteur de backtest universel.

    Supporte :
      - Tout timeframe (H1, H4, D1, M15, M5, M1, tick)
      - Toute stratégie implémentant l'interface Signal
      - Multi-timeframe (HTF pour confirmation, LTF pour entrée)
      - Coûts réalistes (CostModel)
      - Exécution réaliste (ExecutionEngine)
      - Gestion de positions multiples
    """

    def __init__(self, config: Optional[BacktestConfig | dict] = None):
        if config is None:
            self.config = BacktestConfig()
        elif isinstance(config, dict):
            self.config = BacktestConfig(**config)
        else:
            self.config = config

        # Initialiser les sous-modules
        self.cost_model = CostModel(self.config.costs_config)
        self.execution_engine = ExecutionEngine(
            latency_ms=self.config.latency_ms,
            requote_prob=self.config.requote_prob,
            enable_partial_fill=self.config.enable_partial_fill,
        )

        self.metrics = MetricsCalculator()

    # ─── Run ──────────────────────────────────────────────────────────────

    def run(self, symbol: str, strategy, data, timeframe: str = "H1", htf_data=None) -> BacktestResult:
        """
        Exécute un backtest complet pour un symbole.

        Args:
            symbol: Symbole à tester
            strategy: Instance d'une classe implémentant .generate()
            data: pd.DataFrame avec colonnes open/high/low/close/volume[/spread]
            timeframe: Timeframe des données
            htf_data: Données du timeframe supérieur (pour confirmation)

        Returns:
            BacktestResult avec tous les trades et métriques
        """
        result = BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            strategy_name=strategy.name() if hasattr(strategy, "name") else strategy.__class__.__name__,
            config=self.config,
        )

        # Extraire les arrays numpy
        close = data["close"].values.astype(float)
        high = data["high"].values.astype(float)
        low = data["low"].values.astype(float)
        open_p = data["open"].values.astype(float)
        volume = data.get("volume", data.get("tick_volume", None))
        if volume is not None:
            volume = volume.values.astype(float)
        spread_col = data["spread"].values.astype(float) if "spread" in data.columns else None
        times = data["timestamp"].values if "timestamp" in data.columns else np.arange(len(data))

        # Convertir les timestamps en datetime
        dates = self._parse_dates(times)

        n = len(close)
        warmup = self.config.min_bars_warmup

        # Période momentum (depuis la config de la stratégie ou défaut 20)
        mom_period = getattr(strategy, "momentum_period", 20)

        # État interne
        open_trades: list[SimTrade] = []
        all_trades: list[SimTrade] = []
        equity_curve = []
        balance_curve = []
        dd_curve = []
        date_series = []

        balance = self.config.initial_balance
        peak_balance = balance

        bars_since_last_trade = 99
        n_signals = 0
        n_rejected = 0
        n_requotes = 0
        n_partial_fills = 0
        total_costs = 0.0

        # ─── Boucle principale ───────────────────────────────────────────
        for i in range(warmup, n):
            bar_time = dates[i] if i < len(dates) else datetime.utcnow()

            # --- 1. Mettre à jour les positions ouvertes ---
            still_open = []
            atr_v = self._get_atr(high, low, close, i)

            for trade in open_trades:
                # Mettre à jour le peak
                trade.update_peak(high[i], low[i])

                # Vérifier partial TP
                if atr_v > 0:
                    trade.check_partial_tp(atr_v)

                # Vérifier SL/TP
                gap_open = open_p[i] if abs(open_p[i] - close[i - 1]) > atr_v * 3 else None
                trade.check_sl_tp(high[i], low[i], close[i], i, bar_time, gap_open)

                # Mettre à jour le trailing
                if atr_v > 0:
                    trade.update_trailing(atr_v)

                # Vérifier timeout
                max_bars = self.config.timeout_bars.get(timeframe, 120)
                trade.check_timeout(i, bar_time, max_bars)

                if trade.closed:
                    # Calculer les coûts de sortie
                    exit_cost = self.cost_model.get_total_cost(
                        symbol=symbol,
                        direction=trade.direction,
                        lot=trade.lot,
                        entry_price=trade.entry,
                        exit_price=trade.close_price,
                        entry_time=trade.open_time,
                        exit_time=bar_time,
                        volatility=self._get_volatility(atr_v, close[i]),
                        historical_spread=spread_col[i] if spread_col is not None else None,
                    )
                    trade.apply_exit_costs(exit_cost)
                    total_costs += trade.total_cost
                    all_trades.append(trade)
                else:
                    still_open.append(trade)

            open_trades = still_open
            bars_since_last_trade += 1

            # --- 2. Enregistrer l'equity (tous les bars) ---
            # PnL flottant des positions ouvertes
            float_pnl = sum(t.get_floating_pnl(close[i]) for t in open_trades)
            # PnL réalisé des trades fermés
            realized_pnl = sum(t.profit_usd_cost for t in all_trades if t.closed)
            equity = self.config.initial_balance + realized_pnl + float_pnl
            equity_curve.append(equity)
            balance_curve.append(balance)
            dd = (peak_balance - equity) / peak_balance * 100 if peak_balance > 0 else 0
            dd_curve.append(dd)
            date_series.append(bar_time)
            if equity > peak_balance:
                peak_balance = equity

            # --- 3. Générer le signal ---
            if len(open_trades) >= self.config.max_positions:
                continue

            # Vérifier max positions par symbole
            symbol_positions = sum(1 for t in open_trades if t.symbol == symbol)
            if symbol_positions >= self.config.max_positions_per_symbol:
                continue

            # Vérifier intervalle minimum entre trades
            if bars_since_last_trade < self.config.min_bars_between_trades:
                continue

            # Calculer le régime
            regime = self._detect_regime(high, low, close, i)

            # Appeler la stratégie
            signal = strategy.generate(
                bar_idx=i,
                data={
                    "open": open_p,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "spread": spread_col,
                },
                regime=regime,
                open_positions=open_trades,
                timestamp=bar_time,
            )

            if signal is None:
                continue

            n_signals += 1

            # --- 3. Vérifier les conditions d'entrée ---
            # RR minimum
            entry_price = getattr(signal, "entry_price", close[i])
            rr = abs(signal.tp - entry_price) / max(abs(signal.sl - entry_price), 1e-10)
            if rr < 2.0 and getattr(signal, "enforce_rr", True):
                n_rejected += 1
                continue
            # Pas de trade dans la même direction déjà ouvert
            if any(t.action == signal.action and t.symbol == symbol for t in open_trades):
                n_rejected += 1
                continue

            # --- 4. Exécution ---
            pip_size, _ = get_pip_info(symbol)
            spread_pips = self.cost_model.get_spread(
                symbol,
                bar_time,
                historical_spread=spread_col[i] if spread_col is not None else None,
                volatility=self._get_volatility(atr_v, close[i]),
            )
            cond = MarketCond(
                spread_pips=spread_pips,
                volatility=self._get_volatility(atr_v, close[i]),
                bid=close[i],
                ask=close[i],
            )

            fill = self.execution_engine.execute_market_order(
                order_type=0 if signal.action == "BUY" else 1,
                lot=self._calculate_lot(balance, entry_price, signal.sl, symbol),
                close_price=close[i],
                cond=cond,
                timestamp=bar_time,
            )

            if fill.requoted:
                n_requotes += 1
            if fill.partial_fill:
                n_partial_fills += 1

            if fill.filled_lot <= 0:
                n_rejected += 1
                continue

            # --- 5. Créer le trade ---
            entry_cost = self.cost_model.get_total_cost(
                symbol=symbol,
                direction=0 if signal.action == "BUY" else 1,
                lot=fill.filled_lot,
                entry_price=fill.fill_price,
                exit_price=fill.fill_price,  # Temporaire
                entry_time=bar_time,
                exit_time=bar_time,
                volatility=cond.volatility,
                historical_spread=spread_col[i] if spread_col is not None else None,
            )

            trade = SimTrade(
                symbol=symbol,
                action=signal.action,
                entry=fill.fill_price,
                sl=signal.sl,
                tp=signal.tp,
                atr_val=atr_v if atr_v > 0 else 0.001,
                regime=regime,
                bar_idx=i,
                bar_time=bar_time,
                lot=fill.filled_lot,
                timeframe=timeframe,
                entry_cost=entry_cost,
                trailing_levels=self.config.trailing_levels,
                be_buffer_atr=self.config.be_buffer_atr,
            )

            open_trades.append(trade)
            bars_since_last_trade = 0

        # ─── Fin de la boucle : fermer les positions restantes ──────────
        for trade in open_trades:
            trade.force_close(close[-1], n - 1, dates[-1] if dates else datetime.utcnow(), reason="END_OF_TEST")
            exit_cost = self.cost_model.get_total_cost(
                symbol=symbol,
                direction=trade.direction,
                lot=trade.lot,
                entry_price=trade.entry,
                exit_price=trade.close_price,
                entry_time=trade.open_time,
                exit_time=dates[-1] if dates else datetime.utcnow(),
            )
            trade.apply_exit_costs(exit_cost)
            total_costs += trade.total_cost
            all_trades.append(trade)

        # ─── Calculer les métriques ──────────────────────────────────────
        result.trades = all_trades
        result.equity_curve = equity_curve
        result.balance_curve = balance_curve
        result.dd_curve = dd_curve
        result.dates = date_series
        result.total_trades = len([t for t in all_trades if t.closed])
        result.n_wins = sum(1 for t in all_trades if t.closed and t.profit_usd_cost > 0)
        result.n_losses = sum(1 for t in all_trades if t.closed and t.profit_usd_cost <= 0)
        result.n_signals = n_signals
        result.n_rejected = n_rejected
        result.n_requotes = n_requotes
        result.n_partial_fills = n_partial_fills
        result.total_costs = total_costs

        # Calculer les métriques avancées
        result.metrics = self.metrics.compute(
            all_trades,
            self.config.initial_balance,
            equity_curve if equity_curve else None,
            date_series if date_series else None,
        )

        logger.info(
            f"[BACKTEST] {symbol} {timeframe} — "
            f"{result.total_trades} trades, "
            f"WR {result.win_rate:.1f}%, "
            f"PnL ${result.net_profit:.2f}, "
            f"DD {result.metrics.get('max_dd_pct', 0):.1f}%"
        )

        return result

    # ─── Run multi-symboles ──────────────────────────────────────────────

    def run_multi(
        self, symbols: list[str], strategy, data_dict: dict[str, ...], timeframe: str = "H1"
    ) -> dict[str, BacktestResult]:
        """
        Exécute un backtest sur plusieurs symboles.

        Args:
            symbols: Liste de symboles
            strategy: Instance de stratégie (partagée ou par symbole)
            data_dict: Dict {symbole: DataFrame}
            timeframe: Timeframe commun

        Returns:
            Dict {symbole: BacktestResult}
        """
        results = {}
        for symbol in symbols:
            if symbol not in data_dict:
                logger.warning(f"Pas de données pour {symbol}, skip")
                continue
            results[symbol] = self.run(symbol, strategy, data_dict[symbol], timeframe)
        return results

    # ─── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_dates(times) -> list[datetime]:
        """Convertit les timestamps en datetime."""
        dates = []
        for t in times:
            if isinstance(t, (int, float, np.integer, np.floating)):
                dates.append(datetime.utcfromtimestamp(float(t)))
            elif isinstance(t, np.datetime64):
                dates.append(t.astype("M8[ms]").astype(datetime))
            elif isinstance(t, datetime):
                dates.append(t)
            elif isinstance(t, str):
                try:
                    dates.append(datetime.fromisoformat(t))
                except (ValueError, TypeError):
                    dates.append(datetime.utcnow())
            else:
                dates.append(datetime.utcnow())
        return dates

    @staticmethod
    def _get_atr(high, low, close, i, period=14):
        """Calcule l'ATR courant."""
        if i < period + 1:
            return 0.0
        tr = np.maximum(
            high[i] - low[i],
            np.maximum(
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            ),
        )
        # ATR lissé simple
        if i == period:
            tr_vals = [
                np.maximum(
                    high[j] - low[j],
                    np.maximum(
                        abs(high[j] - close[j - 1]),
                        abs(low[j] - close[j - 1]),
                    ),
                )
                for j in range(i - period + 1, i + 1)
            ]
            return float(np.mean(tr_vals))
        else:
            prev_atr = np.mean(
                [
                    np.maximum(
                        high[j] - low[j],
                        np.maximum(
                            abs(high[j] - close[j - 1]),
                            abs(low[j] - close[j - 1]),
                        ),
                    )
                    for j in range(i - period, i)
                ]
            )
            return float((prev_atr * (period - 1) + tr) / period)

    @staticmethod
    def _get_volatility(atr_v: float, price: float) -> str:
        """Détermine le niveau de volatilité."""
        if price <= 0 or atr_v <= 0:
            return "normal"
        atr_pct = atr_v / price * 100
        if atr_pct > 3.0:
            return "extreme"
        if atr_pct > 1.5:
            return "high"
        if atr_pct < 0.3:
            return "low"
        return "normal"

    @staticmethod
    def _detect_regime(high, low, close, i, period=14, adx_thresh=22):
        """Détection simplifiée du régime de marché."""
        if i < period * 2:
            return "RANGING"

        # ADX simplifié
        tr = np.mean(
            [
                max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
                for j in range(i - period + 1, i + 1)
            ]
        )
        up = high[i] - high[i - 1] if i > 0 else 0
        down = low[i - 1] - low[i] if i > 0 else 0

        pos_dm = up if up > down and up > 0 else 0
        neg_dm = down if down > up and down > 0 else 0

        pos_di = pos_dm / tr * 100 if tr > 0 else 0
        neg_di = neg_dm / tr * 100 if tr > 0 else 0

        dx = abs(pos_di - neg_di) / (pos_di + neg_di) * 100 if (pos_di + neg_di) > 0 else 0

        # Lissage ADX
        adx_vals = []
        for j in range(i - period, i + 1):
            if j < period * 2:
                continue
            tr_j = np.mean(
                [
                    max(high[k] - low[k], abs(high[k] - close[k - 1]), abs(low[k] - close[k - 1]))
                    for k in range(j - period + 1, j + 1)
                ]
            )
            up_j = high[j] - high[j - 1] if j > 0 else 0
            down_j = low[j - 1] - low[j] if j > 0 else 0
            pos_dm_j = up_j if up_j > down_j and up_j > 0 else 0
            neg_dm_j = down_j if down_j > up_j and down_j > 0 else 0
            pos_di_j = pos_dm_j / tr_j * 100 if tr_j > 0 else 0
            neg_di_j = neg_dm_j / tr_j * 100 if tr_j > 0 else 0
            dx_j = abs(pos_di_j - neg_di_j) / (pos_di_j + neg_di_j) * 100 if (pos_di_j + neg_di_j) > 0 else 0
            adx_vals.append(dx_j)

        adx = np.mean(adx_vals) if adx_vals else dx

        # MA slope
        ma_short = np.mean(close[max(0, i - 20) : i + 1])
        ma_long = np.mean(close[max(0, i - 50) : i + 1])
        ma_slope = (ma_short - ma_long) / ma_long * 100 if ma_long > 0 else 0

        # ATR %
        atr_v = BacktestEngine._get_atr(high, low, close, i)
        atr_pct = atr_v / close[i] * 100 if close[i] > 0 else 0

        if adx >= adx_thresh:
            if ma_slope > 0.2:
                return "TREND_UP"
            elif ma_slope < -0.2:
                return "TREND_DOWN"
            else:
                return "RANGING"
        else:
            if atr_pct > 1.5:
                return "HIGH_VOL"
            elif atr_pct < 0.2:
                return "LOW_VOL"
            else:
                return "RANGING"

    # ─── Per-symbol max lot ───────────────────────────────────────────────
    # Limite la taille de lot maximale pour prévenir le sur-risque
    SYMBOL_MAX_LOTS: dict[str, float] = {
        "EURUSD": 1.0,
        "GBPUSD": 1.0,
        "USDJPY": 1.0,
        "USDCHF": 1.0,
        "USDCAD": 1.0,
        "AUDUSD": 1.0,
        "NZDUSD": 1.0,
        "EURJPY": 1.0,
        "GBPJPY": 1.0,
        "XAUUSD": 0.10,
        "XAGUSD": 0.10,
        "BTCUSD": 0.01,
        "ETHUSD": 0.05,
        # Indices — pip_size=1.0, risque ~$1/point/lot (USD) ou ~$0.009/point/lot (JP225)
        # Avec SL ~200 points et risk_per_trade=0.44% (880$ sur 200K) :
        #   US500/US100 : 880/(200*1.0)=4.4 lots → max 5.0
        #   JP225       : 880/(200*0.0091)=483 lots → max 200 (limite broker)
        "US500.cash": 5.0,
        "US100.cash": 5.0,
        "JP225.cash": 200.0,
        "US30.cash": 5.0,
        "USOIL.cash": 0.05,
        "UKOIL.cash": 0.05,
        "NATGAS.cash": 0.05,
    }

    def _calculate_lot(self, balance: float, entry: float, sl: float, symbol: str = "EURUSD") -> float:
        """Calcule la taille du lot basée sur le risque, avec limites par symbole."""
        if entry <= 0 or sl <= 0:
            return 0.01

        risk_amount = balance * self.config.risk_per_trade
        sl_distance = abs(entry - sl)

        if sl_distance <= 0:
            return 0.01

        pip_size, pip_value = get_pip_info(symbol)
        # Estimation simple : risque / distance
        lot = risk_amount / (sl_distance / pip_size * pip_value) if sl_distance > 0 else 0.01

        # Limites par symbole
        max_lot = self.SYMBOL_MAX_LOTS.get(symbol, 1.0)
        min_lot = 0.001 if symbol in ("BTCUSD", "ETHUSD") else 0.01

        return round(max(min_lot, min(max_lot, lot)), 3 if min_lot < 0.01 else 2)
