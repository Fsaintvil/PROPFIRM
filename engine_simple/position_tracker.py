"""PositionTracker — suivi institutionnel des positions avec métriques de performance

Extrait de main.py avec améliorations :
  - Performance tracking par symbole
  - Trade history analytics (win rate, expectancy, profit factor)
  - Métriques exportables pour reporting
"""
import logging
import time
from datetime import datetime

import config_simple as cfg
from engine_simple.feature_store import FeatureStore

logger = logging.getLogger("robot.tracker")


class SymbolPerformance:
    def __init__(self):
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0
        self.total_r_multiple = 0.0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.max_consecutive_wins = 0
        self.max_consecutive_losses = 0

    def record(self, profit, r_multiple):
        self.trades += 1
        self.total_profit += profit
        self.total_r_multiple += r_multiple
        if profit > 0:
            self.wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.max_consecutive_wins = max(self.max_consecutive_wins, self.consecutive_wins)
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.max_consecutive_losses = max(self.max_consecutive_losses, self.consecutive_losses)

    @property
    def win_rate(self):
        return self.wins / max(self.trades, 1)

    @property
    def avg_profit(self):
        return self.total_profit / max(self.trades, 1)

    @property
    def avg_r_multiple(self):
        return self.total_r_multiple / max(self.trades, 1)

    @property
    def profit_factor(self):
        gross_profit = sum(1 for _ in range(self.wins))
        gross_loss = sum(1 for _ in range(self.losses))
        return gross_profit / max(gross_loss, 1)

    def summary(self):
        return {
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "total_profit": round(self.total_profit, 2),
            "avg_r": round(self.avg_r_multiple, 2),
            "max_consecutive_losses": self.max_consecutive_losses,
        }


class PositionTracker:
    def __init__(self, ftmo, journal, adaptive, positions_cache, mt5=None, audit=None):
        self.ftmo = ftmo
        self.journal = journal
        self.adaptive = adaptive
        self.positions_cache = positions_cache
        self.mt5 = mt5
        self.audit = audit
        self._previous_tickets = set()
        self._recorded_deals = set()
        self._recorded_position_ids = set()
        self._position_meta = {}
        self.feature_store = FeatureStore()
        self.performance = {}

    def _perf(self, symbol):
        if symbol not in self.performance:
            self.performance[symbol] = SymbolPerformance()
        return self.performance[symbol]

    def init_tickets(self):
        our = [p for p in self.positions_cache.get() if p.magic == cfg.ROBOT_MAGIC]
        self._previous_tickets = {p.ticket for p in our}

    def track_new(self):
        our = [p for p in self.positions_cache.get() if p.magic == cfg.ROBOT_MAGIC]
        for p in our:
            if p.ticket not in self._position_meta:
                order_type = self.mt5.ORDER_TYPE_BUY if p.type == 0 else self.mt5.ORDER_TYPE_SELL
                r1 = self.mt5.calc_profit(order_type, p.symbol, p.volume, p.price_open, p.sl)
                regime = p.comment.replace("ADAPT_", "")[:5] if p.comment.startswith("ADAPT_") else "?"
                meta = dict(
                    symbol=p.symbol, entry=p.price_open,
                    sl=p.sl, lot=p.volume, regime=regime,
                    r1_usd=max(abs(r1 or 0), 1),
                    opened_at=time.time(),
                )
                saved = self.feature_store.load(p.ticket)
                if saved and "dl_features" in saved:
                    meta["dl_features"] = saved["dl_features"]
                    meta["predictions"] = saved.get("predictions", {})
                    logger.debug(f"  [TRACK] {p.symbol} #{p.ticket} restored DL features")
                self._position_meta[p.ticket] = meta
                logger.debug(f"  [TRACK] {p.symbol} #{p.ticket} regime={regime}")

    def check_closed(self):
        current = {p.ticket for p in self.positions_cache.get() if p.magic == cfg.ROBOT_MAGIC}
        closed = self._previous_tickets - current
        for ticket in closed:
            if ticket in self._recorded_deals:
                continue
            since = int(time.time() - 7 * 86400)
            now_ts = int(time.time())
            history = self.mt5.get_history(since, now_ts) or []
            closing = None
            for deal in history:
                if deal.position_id == ticket and deal.magic == cfg.ROBOT_MAGIC and deal.profit != 0:
                    closing = deal
                    break
            if closing is None:
                continue
            pos_key = f"{closing.position_id}_{closing.symbol}"
            if pos_key in self._recorded_position_ids:
                continue
            self._recorded_position_ids.add(pos_key)
            if len(self._recorded_position_ids) > 2000:
                self._recorded_position_ids = set(list(self._recorded_position_ids)[-1500:])
            self._recorded_deals.add(ticket)
            if len(self._recorded_deals) > 2000:
                self._recorded_deals = set(list(self._recorded_deals)[-1500:])
            self.ftmo.record_trade_result(closing.symbol, closing.profit)
            pos_dir = "BUY" if closing.type in (1,) else "SELL"
            self.journal.record(dict(
                symbol=closing.symbol, direction=pos_dir,
                entry=closing.price, sl=0, tp=0, lot=closing.volume,
                profit=closing.profit,
                time_open=str(datetime.fromtimestamp(closing.time)),
                time_close=str(datetime.utcnow()), reason="closed",
            ))
            meta = self._position_meta.pop(ticket, {})
            self.feature_store.delete(ticket)
            regime = meta.get("regime", "?")
            r1 = meta.get("r1_usd", 1)
            r_mul = round(closing.profit / r1, 2) if r1 > 0 else 0
            dl_features = meta.get("dl_features")
            self.adaptive.record_result(closing.symbol, r_mul, regime, dl_features)
            self._perf(closing.symbol).record(closing.profit, r_mul)
            if self.audit:
                self.audit.log_decision("position_closed", {
                    "symbol": closing.symbol,
                    "ticket": ticket,
                    "profit": closing.profit,
                    "r_multiple": r_mul,
                    "regime": regime,
                    "holding_seconds": time.time() - meta.get("opened_at", time.time()),
                })
            pos_correct = closing.profit > 0
            saved_predictions = meta.get("predictions", {})
            if saved_predictions and regime not in ("?", "LIMIT"):
                pred_outcomes = {}
                for mname, maction in saved_predictions.items():
                    pred_outcomes[mname] = (maction == pos_dir) if pos_correct else (maction != pos_dir)
                self.adaptive.record_meta_result(closing.symbol, regime, pred_outcomes)
        self._previous_tickets = current

    def add_meta(self, ticket, data):
        data["opened_at"] = time.time()
        self._position_meta[ticket] = data
        self.feature_store.save(ticket, data)

    def get_active_count(self):
        return len(self._position_meta)

    def performance_summary(self):
        return {sym: perf.summary() for sym, perf in self.performance.items()}

    def global_summary(self):
        total_trades = sum(p.trades for p in self.performance.values())
        total_profit = sum(p.total_profit for p in self.performance.values())
        total_wins = sum(p.wins for p in self.performance.values())
        return {
            "total_trades": total_trades,
            "total_profit": round(total_profit, 2),
            "global_win_rate": round(total_wins / max(total_trades, 1), 3),
            "symbols_tracked": len(self.performance),
        }
