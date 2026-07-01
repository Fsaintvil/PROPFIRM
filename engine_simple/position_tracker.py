"""PositionTracker — suivi institutionnel des positions avec métriques de performance

Extrait de main.py avec améliorations :
  - Performance tracking par symbole
  - Trade history analytics (win rate, expectancy, profit factor)
  - Métriques exportables pour reporting
"""

import json
import logging
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import config_simple as cfg
from engine_simple.feature_store import FeatureStore

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"
RECORDED_POSITIONS_FILE = RUNTIME_DIR / "recorded_positions.json"
REAL_TRADES_FILE = RUNTIME_DIR / "lgb_real_trades.jsonl"  # trades réels avec features pour retraining LGB

logger = logging.getLogger("robot.tracker")

# Symboles dont les trades historiques ne sont PAS importés dans l'OnlineLearner.
# Utilisé quand un symbole change de configuration (ex: allow_shorts true→false)
# et que les anciens trades (sous config différente) contamineraient l'apprentissage.
_SYMBOLS_SKIP_OL_IMPORT: set = {}  # EURUSD retiré (22 Juin, Supreme Council) : obsolète depuis v4.2.0


class SymbolPerformance:
    def __init__(self):
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0
        self.total_r_multiple = 0.0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
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
            self.gross_profit += profit
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.max_consecutive_wins = max(self.max_consecutive_wins, self.consecutive_wins)
        else:
            self.losses += 1
            self.gross_loss += abs(profit)
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
        return self.gross_profit / max(self.gross_loss, 1)

    def summary(self):
        return {
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_profit, 2),
            "avg_trade": round(self.avg_profit, 2),
            "max_dd": 0.0,
            "sharpe": 0.0,
        }


def _log_real_trade(closing, meta: dict) -> None:
    """Sauvegarde un trade fermé avec ses features dans runtime/lgb_real_trades.jsonl.

    Format JSONL : chaque ligne est un trade complet avec features + outcome.
    Utilisé par scripts/train_lightgbm.py pour le retraining hebdomadaire.

    Filtre : seuls les trades avec un vrai ticket MT5 (> 10000) ET des features
    complètes sont loggés. Les trades historiques/seed (ticket=1, features vides)
    sont silencieusement ignorés pour éviter la contamination des données.
    """
    # Vérifier que c'est un vrai trade MT5 (pas seed/historique)
    ticket = closing.position_id if hasattr(closing, "position_id") else getattr(closing, "ticket", 0)
    if not ticket or ticket <= 1:
        return

    features = meta.get("_features", {})
    # Vérifier que les features sont complètes (pas un trade pré-Phase14)
    if not features or len(features) < 5:
        return

    pos_dir = "SELL" if closing.type == 1 else "BUY"
    r1_usd = meta.get("r1_usd", 1)
    r_multiple = round(closing.profit / r1_usd, 2) if r1_usd > 0 else 0
    # Features vectorisées — LightGBM désactivé, fallback vide
    features_vec = []

    record = {
        "symbol": closing.symbol,
        "ticket": ticket,
        "profit": round(closing.profit, 2),
        "r_multiple": r_multiple,
        "is_winner": closing.profit > 0,
        "regime": meta.get("regime", "UNKNOWN"),
        "direction": pos_dir,
        "entry": float(meta.get("entry", getattr(closing, "price", 0))),
        "exit": float(closing.price),
        "sl": float(meta.get("sl", 0)),
        "tp": float(meta.get("tp", 0)),
        "lot": float(closing.volume),
        "feature_adj": meta.get("feature_adj", 1.0),
        "feature_reasons": meta.get("feature_reasons", {}),
        "opened_at": meta.get("opened_at", 0),
        "closed_at": time.time(),
        # Features complètes (dict + vecteur pour compatibilité)
        "features": {k: round(v, 6) if isinstance(v, float) else v for k, v in features.items()},
        "features_vec": features_vec,
        "predictions": meta.get("predictions", {}),
    }

    try:
        REAL_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REAL_TRADES_FILE, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        logger.debug(f"[LGB] Real trade logged: {closing.symbol} profit={closing.profit:+.2f}")
    except Exception as e:
        logger.debug(f"[LGB] Failed to write real trade: {e}")


class PositionTracker:
    def __init__(self, ftmo, journal, adaptive, positions_cache, mt5=None, audit=None):
        self.ftmo = ftmo
        self.journal = journal
        self.adaptive = adaptive
        self.positions_cache = positions_cache
        self.mt5 = mt5
        self.audit = audit
        self._previous_tickets = set()
        self._recorded_deals = OrderedDict()  # Ordered set: insertion order preserved
        self._recorded_position_ids = OrderedDict()  # pour pruning FIFO déterministe
        self._max_recorded = 2000
        self._trim_target = 1500
        self._position_meta = {}
        self.feature_store = FeatureStore()
        self.performance = {}
        self._start_time = int(time.time())  # timestamp démarrage du robot

    def _perf(self, symbol):
        if symbol not in self.performance:
            self.performance[symbol] = SymbolPerformance()
        return self.performance[symbol]

    def init_tickets(self):
        our = [p for p in self.positions_cache.get() if p.magic == cfg.ROBOT_MAGIC]
        self._previous_tickets = {p.ticket for p in our}

    def _load_recorded_positions(self):
        """Charge les position_ids persistés depuis le fichier disque.
        Évite de réimporter les mêmes trades historiques après un redémarrage."""
        try:
            if RECORDED_POSITIONS_FILE.exists() and RECORDED_POSITIONS_FILE.stat().st_size > 10:
                with open(RECORDED_POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                ids = data.get("recorded_position_ids", [])
                self._recorded_position_ids = OrderedDict.fromkeys(ids)
                deals = data.get("recorded_deals", [])
                self._recorded_deals = OrderedDict.fromkeys(deals)
                logger.info(
                    f"[TRACKER] Persist: {len(self._recorded_position_ids)} position_ids "
                    f"et {len(self._recorded_deals)} deals chargés"
                )
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"[TRACKER] Impossible de charger recorded_positions: {e}")
            self._recorded_position_ids = OrderedDict()
            self._recorded_deals = OrderedDict()

    def _save_recorded_positions(self):
        """Persiste les position_ids sur disque pour éviter les réimports au prochain démarrage."""
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "recorded_position_ids": list(self._recorded_position_ids.keys()),
                "recorded_deals": list(self._recorded_deals.keys()),
                "max_recorded": self._max_recorded,
                "updated_at": datetime.utcnow().isoformat(),
            }
            with open(RECORDED_POSITIONS_FILE, "w") as f:
                json.dump(data, f)
        except OSError as e:
            logger.warning(f"[TRACKER] Sauvegarde recorded_positions échouée: {e}")

    def import_history(self):
        """Importe l'historique MT5 des trades fermés (au démarrage).
        Charge d'abord les position_ids persistés pour éviter les doublons."""
        self._load_recorded_positions()
        # Mode batch OnlineLearner : éviter 40+ saves/calibration pendant import
        if hasattr(self.adaptive, "learner") and hasattr(self.adaptive.learner, "batch_mode"):
            self.adaptive.learner.batch_mode(True)
        try:
            import MetaTrader5 as mt5

            since = int(time.time() - cfg.HISTORY_LOOKBACK_DAYS * 86400)
            now_ts = int(time.time())
            deals = mt5.history_deals_get(since, now_ts) or []
            recorded = 0
            for d in deals:
                if d.magic != cfg.ROBOT_MAGIC or d.profit == 0:
                    continue
                # P3: Filtrer par whitelist — ignorer les symboles inactifs (EURUSD, etc.)
                if d.symbol not in cfg.SYMBOLS:
                    continue
                # P2: Filtrer les trades de plus de 48h à l'import (contamination seed/historique)
                trade_dt = getattr(d, "time", None)
                if trade_dt is not None:
                    from datetime import datetime as _dt

                    try:
                        if isinstance(trade_dt, (int, float)):
                            trade_age = time.time() - trade_dt
                        else:
                            # ⚠️ MT5 peut retourner datetime ou int selon version
                            trade_ts = trade_dt.timestamp() if hasattr(trade_dt, "timestamp") else float(trade_dt)
                            trade_age = time.time() - trade_ts
                        if trade_age > 48 * 3600:
                            continue
                    except Exception as e:
                        logger.warning(f"  [TRACKER] import_history trade_age: {e}")
                        pass
                pos_key = f"{d.position_id}_{d.symbol}"
                if pos_key in self._recorded_position_ids:
                    continue
                self._recorded_position_ids[pos_key] = None
                self._recorded_deals[d.ticket] = None
                # Passer le vrai timestamp MT5 pour que challenge.py puisse filtrer
                # les trades de plus de 48h (évite de polluer WR avec des trades anciens)
                trade_dt = getattr(d, "time", None)
                self.ftmo.record_trade_result(d.symbol, d.profit, historical=True, trade_time=trade_dt)
                # ⛔ NE PAS appeler performance_monitor.record_trade() ici !
                # Les trades historiques sont pour le cooldown/consecutive losses, PAS pour les stats.
                # Appeler record_trade() ici corrompt les stats quotidiennes (double-count à chaque restart).
                # Le performance_monitor est mis à jour PAR les trades LIVE (position_tracker.track_new).
                # ✅ OnlineLearner est alimenté ici avec les trades historiques
                # (r_multiple simplifié = ±1, car on n'a pas le SL dans l'historique MT5)
                # ⛔ EURUSD exclu de l'OL : les trades historiques datent de l'ancienne config
                # allow_shorts=false (WR=3.3%), qui contaminerait l'apprentissage en ligne.
                # L'OL apprendra EURUSD uniquement via les trades live (config actuelle).
                hist_r = 1.0 if d.profit > 0 else -1.0
                if d.symbol not in _SYMBOLS_SKIP_OL_IMPORT:
                    try:
                        self.adaptive.record_result(d.symbol, hist_r, regime="HIST", batch=True)
                    except Exception as e:
                        logger.debug(f"[TRACKER] OnlineLearner import skip: {e}")
                recorded += 1
            if recorded > 0:
                logger.info(
                    f"[TRACKER] Import historique: {recorded} trades fermés importés (FTMO seulement, pas perf monitor)"
                )
            # Persister l'état mis à jour (même si recorded=0, pour sauvegarder les IDs déjà connus)
            self._save_recorded_positions()
        except Exception as e:
            logger.warning(f"[TRACKER] Import historique echoue: {e}")
        finally:
            # Sauvegarde unique après tout l'import batch
            if hasattr(self.adaptive, "learner") and hasattr(self.adaptive.learner, "flush"):
                self.adaptive.learner.flush()
            if hasattr(self.adaptive, "_save_calibration"):
                try:
                    self.adaptive._save_calibration()
                except Exception as e:
                    logger.warning(f"  [TRACKER] import_history calibration: {e}")
                    pass

    def track_new(self):
        our = [p for p in self.positions_cache.get() if p.magic == cfg.ROBOT_MAGIC]
        for p in our:
            # P3: Filtrer par whitelist — ignorer les symboles inactifs
            if p.symbol not in cfg.SYMBOLS:
                continue
            if p.ticket not in self._position_meta:
                order_type = self.mt5.ORDER_TYPE_BUY if p.type == 0 else self.mt5.ORDER_TYPE_SELL
                r1 = self.mt5.calc_profit(order_type, p.symbol, p.volume, p.price_open, p.sl)
                raw_regime = p.comment.replace("ADAPT_", "") if p.comment.startswith("ADAPT_") else "LEGACY"
                # Re-traduire le code court (3 lettres) en nom complet de régime
                # Le commentaire MT5 stocke "RAN" pour "RANGING", "DOW" pour "TREND_DOWN", etc.
                REGIME_SHORT_TO_FULL = {
                    "TRE": "TREND_UP",
                    "DOW": "TREND_DOWN",
                    "RAN": "RANGING",
                    "HIG": "HIGH_VOL",
                    "LOW": "LOW_VOL",
                }
                regime = REGIME_SHORT_TO_FULL.get(raw_regime, raw_regime)
                meta = dict(
                    symbol=p.symbol,
                    entry=p.price_open,
                    sl=p.sl,
                    tp=p.tp,
                    lot=p.volume,
                    regime=regime,
                    r1_usd=max(abs(r1 or 0), 1),
                    opened_at=time.time(),
                )
                saved = self.feature_store.load(p.ticket)
                if saved:
                    # Restaurer TOUS les champs sauvegardés par add_meta (features, predictions, etc.)
                    for k in ("_features", "predictions", "feature_adj", "feature_reasons"):
                        if k in saved:
                            meta[k] = saved[k]
                    # Compatibilité ascendante : dl_features → _features
                    if "dl_features" in saved and "_features" not in meta:
                        meta["_features"] = saved["dl_features"]
                    logger.debug(
                        f"  [TRACK] {p.symbol} #{p.ticket} restored saved meta: {set(saved.keys()) & {'_features', 'predictions', 'feature_adj', 'feature_reasons'}}"
                    )
                self._position_meta[p.ticket] = meta
                logger.debug(f"  [TRACK] {p.symbol} #{p.ticket} regime={regime}")

    def check_closed(self):
        current = {p.ticket for p in self.positions_cache.get() if p.magic == cfg.ROBOT_MAGIC}
        closed = self._previous_tickets - current
        if closed:
            logger.info(
                f"  [TRACKER] Closed tickets detected: {closed}, previous={self._previous_tickets}, current={current}"
            )
        for ticket in closed:
            if ticket in self._recorded_deals:
                logger.debug(f"  [TRACKER] ticket {ticket} already recorded")
                continue
            # Prune FIFO si le seuil est dépassé (déterministe : supprime les plus anciens)
            if len(self._recorded_deals) >= self._max_recorded:
                self._recorded_deals = OrderedDict(list(self._recorded_deals.items())[-self._trim_target :])
            if len(self._recorded_position_ids) >= self._max_recorded:
                self._recorded_position_ids = OrderedDict(
                    list(self._recorded_position_ids.items())[-self._trim_target :]
                )
            since = int(time.time() - cfg.HISTORY_LOOKBACK_DAYS * 86400)
            now_ts = int(time.time())
            history = self.mt5.get_history(since, now_ts) or []
            logger.debug(f"  [TRACKER] query history for ticket {ticket}: {len(history)} deals")
            closing = None
            for deal in history:
                if deal.position_id == ticket and deal.magic == cfg.ROBOT_MAGIC and deal.profit != 0:
                    closing = deal
                    break
            if closing is None:
                # Fallback: chercher par position ID directement (plus fiable que time-range)
                try:
                    import MetaTrader5 as mt5

                    direct = mt5.history_deals_get(position=ticket)
                    if direct and len(direct) > 0:
                        for d in direct:
                            if d.profit != 0:
                                closing = d
                                logger.info(f"  [TRACKER] Found closing deal via direct lookup: {closing.profit:.2f}")
                                break
                except Exception as e:
                    logger.debug(f"Direct lookup failed for ticket {ticket}: {e}")

            if closing is None:
                logger.info(
                    f"  [TRACKER] Ticket {ticket} ferme sans historique MT5 (gap de session — normal apres reconnexion)"
                )
                # Marquer comme traité pour éviter les logs répétés
                self._recorded_deals[ticket] = None
                continue
            logger.info(
                f"  [TRACKER] Found closing deal for {closing.symbol} ticket {ticket}: profit={closing.profit:.2f}"
            )
            # P3: Whitelist — ignorer les symboles inactifs (contamination EURUSD)
            if closing.symbol not in cfg.SYMBOLS:
                logger.debug(f"  [TRACKER] Skipping {closing.symbol} (not in SYMBOLS whitelist)")
                self._recorded_deals[ticket] = None
                continue
            pos_key = f"{closing.position_id}_{closing.symbol}"
            if pos_key in self._recorded_position_ids:
                continue
            self._recorded_position_ids[pos_key] = None
            self._recorded_deals[ticket] = None
            # 🔒 Si le trade a été fermé AVANT le démarrage du robot, c'est un replay historique
            # → ne pas incrémenter consecutive_losses (sinon circuit breaker trip au restart)
            # H-07: double vérification robuste (time float + timestamp int)
            deal_time = getattr(closing, "time", None)
            if deal_time is None:
                deal_time = getattr(closing, "timestamp", 0)
            is_historical = bool(deal_time) and float(deal_time) < float(self._start_time)
            # Prendre le vrai timestamp MT5 pour que challenge.py filtre les trades >48h
            trade_dt = getattr(closing, "time", None)
            if isinstance(trade_dt, (int, float)):
                trade_dt = datetime.utcfromtimestamp(trade_dt)
            self.ftmo.record_trade_result(closing.symbol, closing.profit, historical=is_historical, trade_time=trade_dt)
            # Persister immédiatement pour éviter la réimportation au prochain redémarrage
            self._save_recorded_positions()
            meta = self._position_meta.pop(ticket, {})
            # 🆕 LGB: Logger le trade réel avec ses features pour retraining futur
            try:
                _log_real_trade(closing, meta)
            except Exception as e:
                logger.debug(f"[LGB] Log real trade failed: {e}")
            # Performance Monitor — suivi autonome des métriques
            try:
                from engine_simple.performance_monitor import record_trade

                regime = meta.get("regime", "UNKNOWN")
                # MT5: POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1
                pos_dir = "BUY" if closing.type == 0 else "SELL"
                record_trade(closing.symbol, closing.profit, regime, pos_dir)
            except Exception as e:
                logger.warning(f"[TRACK] record_trade failed: {e}")  # ne jamais bloquer le cycle

            # ── Phase 12-13: AdaptiveParams + WFO — update après trade fermé ──
            try:
                from engine_simple.adaptive_params import get_adaptive

                # Update AdaptiveParams
                ap = get_adaptive(closing.symbol)
                win = closing.profit > 0
                ap.record_trade(pnl=closing.profit, win=win, regime=meta.get("regime", "UNKNOWN"))

                # WFO retiré (archivé dans retired/) — pas de mise à jour

                logger.debug(
                    f"  [LEARN] {closing.symbol}: profit={closing.profit:+.2f}, "
                    f"win={win}, adaptive_wr={ap.get_adapted_params().win_rate:.1%}"
                )
            except Exception as e:
                logger.debug(f"  [LEARN] {closing.symbol}: erreur update: {e}")

            # MT5: POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1
            pos_dir = "SELL" if closing.type == 1 else "BUY"
            try:
                self.journal.record(
                    dict(
                        symbol=closing.symbol,
                        direction=pos_dir,
                        entry=meta.get("entry", closing.price),
                        exit_price=closing.price,
                        sl=meta.get("sl", 0),
                        tp=meta.get("tp", 0),
                        lot=closing.volume,
                        profit=closing.profit,
                        time_open=str(datetime.fromtimestamp(meta.get("opened_at", closing.time))),
                        time_close=str(datetime.utcnow()),
                        reason="closed",
                    )
                )
            except Exception as e:
                logger.warning(f"[TRACK] journal.record failed for {closing.symbol}: {e}")
            self.feature_store.delete(ticket)
            regime = meta.get("regime", "UNKNOWN")
            r1 = meta.get("r1_usd", 1)
            r_mul = round(closing.profit / r1, 2) if r1 > 0 else 0
            dl_features = meta.get("dl_features")
            self.adaptive.record_result(closing.symbol, r_mul, regime, dl_features)
            self._perf(closing.symbol).record(closing.profit, r_mul)
            if self.audit:
                self.audit.log_decision(
                    "position_closed",
                    {
                        "symbol": closing.symbol,
                        "ticket": ticket,
                        "profit": closing.profit,
                        "r_multiple": r_mul,
                        "regime": regime,
                        "holding_seconds": time.time() - meta.get("opened_at", time.time()),
                    },
                )
            pos_correct = closing.profit > 0
            saved_predictions = meta.get("predictions", {})
            # Fallback: si pas de prédictions stockées, MOM20x3 est le seul modèle
            if not saved_predictions:
                saved_predictions = {"MOM20x3": {"action": pos_dir, "score": 0.5}}
            if saved_predictions and regime not in ("?", "LIMIT"):
                pred_outcomes = {}
                for mname, maction in saved_predictions.items():
                    # maction peut être un dict {"action":"BUY",...} ou une string "BUY"
                    action = maction.get("action", "HOLD") if isinstance(maction, dict) else maction
                    pred_outcomes[mname] = (action == pos_dir) if pos_correct else (action != pos_dir)
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
