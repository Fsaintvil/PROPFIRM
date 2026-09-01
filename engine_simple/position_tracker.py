"""PositionTracker — suivi institutionnel des positions avec métriques de performance

Extrait de main.py avec améliorations :
  - Performance tracking par symbole
  - Trade history analytics (win rate, expectancy, profit factor)
  - Métriques exportables pour reporting
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config_simple as cfg
from engine_simple.feature_store import FeatureStore

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"
RECORDED_POSITIONS_FILE = RUNTIME_DIR / "recorded_positions.json"
REAL_TRADES_FILE = RUNTIME_DIR / "lgb_real_trades.jsonl"  # trades réels avec features pour retraining LGB

logger = logging.getLogger("robot.tracker")

# Symboles dont les trades historiques ne sont PAS importés dans l'OnlineLearner.
# Utilisé quand un symbole change de configuration (ex: allow_shorts true→false)
# et que les anciens trades (sous config différente) contamineraient l'apprentissage.
_SYMBOLS_SKIP_OL_IMPORT: set = set()  # EURUSD retiré (22 Juin, Supreme Council) : obsolète depuis v4.2.0


class SymbolPerformance:
    def __init__(self) -> None:
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

    def record(self, profit: float, r_multiple: float) -> None:
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
    def win_rate(self) -> float:
        return self.wins / max(self.trades, 1)

    @property
    def avg_profit(self) -> float:
        return self.total_profit / max(self.trades, 1)

    @property
    def avg_r_multiple(self) -> float:
        return self.total_r_multiple / max(self.trades, 1)

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / max(self.gross_loss, 1)

    def summary(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_profit, 2),
            "avg_trade": round(self.avg_profit, 2),
            "max_dd": 0.0,
            "sharpe": 0.0,
        }


def _log_real_trade(closing: Any, meta: dict[str, Any]) -> None:
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

    # 🔧 FIX 29 Juillet 2026: closing.type est le type du DEAL de fermeture
    # DEAL_TYPE_BUY (0) = rachat pour fermer une position SELL → direction réelle = SELL
    # DEAL_TYPE_SELL (1) = vente pour fermer une position BUY → direction réelle = BUY
    pos_dir = "BUY" if closing.type == 1 else "SELL"
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
    def __init__(
        self, ftmo: Any, journal: Any, adaptive: Any, positions_cache: Any, mt5: Any = None, audit: Any = None
    ) -> None:
        self.ftmo = ftmo
        self.journal = journal
        self.adaptive = adaptive
        self.positions_cache = positions_cache
        self.mt5 = mt5
        self.audit = audit
        self._previous_tickets = set()
        self._recorded_deals = OrderedDict()  # Ordered set: insertion order preserved
        self._recorded_position_ids = OrderedDict()  # pour pruning FIFO déterministe
        # 🔧 FIX 30 Aout 2026: augmenter les seuils de pruning pour éviter les doublons
        # Avant: 2000/1500 → pruning fréquent, position_ids prunés puis ré-importés au restart
        # Après: 5000/4000 → pruning rare, suffisant pour des mois de trading
        self._max_recorded = 5000
        self._trim_target = 4000
        self._position_meta = {}
        # ⭐⭐ FIX 12 Août 2026 — Retry des fermetures sans deal MT5 (gap de session)
        # Problème observé (analyse de la journée du 11/08) : 2 XAUUSD fermées pendant
        # la reprise post-gel machine (S3) ont été détectées par check_closed() AVANT que
        # MT5 n'ait synchronisé leur deal de fermeture. L'ancien chemin `closing is None`
        # marquait le ticket "recorded" et ABANDONNAIT le PnL → trades absents de
        # trades_log.csv, de daily_pnl_by_date et du performance monitor (−240.65$
        # invisibles sur la journée du 11/08, chute d'équité non expliquée par les stats).
        # Le fix : mettre le ticket en file d'attente de retry pendant CLOSE_RETRY_ATTEMPTS
        # cycles (~15s chacun ≈ 90s). Si le deal apparaît entre-temps (sync MT5 post-
        # reconnexion), le trade est enregistré normalement (CSV + perf monitor + daily_pnl).
        # Sinon, l'import_history() au prochain restart le récupérera (comportement FTMO
        # conservé). Un ticket qui "réapparaît" en position = glitch MT5 → retry annulé.
        self.CLOSE_RETRY_ATTEMPTS = 6
        self._pending_closures: dict[int, int] = {}  # ticket -> tentatives restantes
        self._meta_extra = {}  # Stockage temporaire pour ATR/sl_atr/tp_atr avant que track_new() crée le meta
        self.feature_store = FeatureStore()
        self.performance = {}
        self._start_time = int(time.time())  # timestamp démarrage du robot

    def _perf(self, symbol: str) -> SymbolPerformance:
        if symbol not in self.performance:
            self.performance[symbol] = SymbolPerformance()
        return self.performance[symbol]

    def init_tickets(self) -> None:
        positions = self.positions_cache.get() or []  # 🔧 FIX: None → []
        our = [p for p in positions if p.magic == cfg.ROBOT_MAGIC]
        self._previous_tickets = {p.ticket for p in our}

    def _load_recorded_positions(self) -> None:
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

    def _save_recorded_positions(self) -> None:
        """Persiste les position_ids sur disque pour éviter les réimports au prochain démarrage."""
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "recorded_position_ids": list(self._recorded_position_ids.keys()),
                "recorded_deals": list(self._recorded_deals.keys()),
                "max_recorded": self._max_recorded,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            # 🐛 FIX 16 Août 2026 (Audit M-EX6): écriture ATOMIQUE (tmp + replace).
            # Avant: open("w") direct → si crash pendant l'écriture, fichier corrompu →
            # _load_recorded_positions échoue → réimport des deals ≤48h → DOUBLONS
            # comptés dans le challenge (daily PnL, consistency, WR).
            import os as _os

            _tmp = Path(str(RECORDED_POSITIONS_FILE) + ".tmp")
            with open(_tmp, "w") as f:
                json.dump(data, f)
                f.flush()
                _os.fsync(f.fileno())
            _tmp.replace(RECORDED_POSITIONS_FILE)
        except OSError as e:
            logger.warning(f"[TRACKER] Sauvegarde recorded_positions échouée: {e}")

    def import_history(self) -> None:
        """Importe l'historique MT5 des trades fermés (au démarrage).
        Charge d'abord les position_ids persistés pour éviter les doublons."""
        self._load_recorded_positions()
        # Mode batch OnlineLearner : éviter 40+ saves/calibration pendant import
        if hasattr(self.adaptive, "learner") and hasattr(self.adaptive.learner, "batch_mode"):
            self.adaptive.learner.batch_mode(True)
        try:
            since = int(time.time() - cfg.HISTORY_LOOKBACK_DAYS * 86400)
            now_ts = int(time.time())
            # 🔧 FIX 16 Juillet 2026: Utiliser self.mt5.get_history() avec timeout
            # au lieu de mt5.history_deals_get() direct (peut bloquer indéfiniment).
            deals = self.mt5.get_history(since, now_ts) or []
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
                            # 🔧 FIX AUDIT H8: Soustraire offset serveur (~3h) pour trade_age correct.
                            # d.time = temps serveur MT5 (+3h vs UTC local). Sans correction,
                            # trade_age est 3h trop petit → trades 45-48h passent à tort.
                            trade_age = time.time() - trade_dt - 10800  # −3h server offset
                        else:
                            # ⚠️ MT5 peut retourner datetime ou int selon version
                            trade_ts = trade_dt.timestamp() if hasattr(trade_dt, "timestamp") else float(trade_dt)
                            trade_age = time.time() - trade_ts - 10800  # −3h server offset
                        if trade_age > 48 * 3600:
                            continue
                    except Exception as e:
                        logger.warning(f"  [TRACKER] import_history trade_age: {e}")
                        pass
                pos_key = f"{d.position_id}_{d.symbol}"
                if pos_key in self._recorded_position_ids:
                    continue
                self._recorded_position_ids[pos_key] = None
                # 🐛 FIX 28 Juillet 2026: Stocker position_id au lieu du deal ticket.
                # check_closed() ligne 387 vérifie les POSITION tickets dans _recorded_deals.
                # Si on stocke des DEAL tickets (d.ticket), la vérification échoue TOUJOURS
                # car les numéros de deal et de position sont différents.
                # Conséquence: les trades déjà importés ne sont JAMAIS dédoublonnés,
                # et une rafale de trades historiques (166 en 16s) peut contaminer l'OL.
                self._recorded_deals[d.position_id] = None
                # Passer le vrai timestamp MT5 pour que challenge.py puisse filtrer
                # les trades de plus de 48h (évite de polluer WR avec des trades anciens)
                trade_dt = getattr(d, "time", None)
                # 🐛 FIX 10 Août 2026 (Bug #6): direction réelle déduite du DEAL type.
                # DEAL_TYPE_BUY(0)=rachat de SELL → réel=SELL; DEAL_TYPE_SELL(1)=vente de BUY → réel=BUY.
                # Sans cette direction, _check_directional_imbalance restait inerte (buys/sells = 0).
                hist_dir = "BUY" if getattr(d, "type", 1) == 1 else "SELL"
                self.ftmo.record_trade_result(
                    d.symbol, d.profit, historical=True, trade_time=trade_dt, direction=hist_dir
                )
                # ⛔ NE PAS appeler performance_monitor.record_trade() ici !
                # Les trades historiques sont pour le cooldown/consecutive losses, PAS pour les stats.
                # Appeler record_trade() ici corrompt les stats quotidiennes (double-count à chaque restart).
                # Le performance_monitor est mis à jour PAR les trades LIVE (position_tracker.track_new).
                # ✅ OnlineLearner est alimenté ici avec les trades historiques
                # (r_multiple simplifié = ±1, car on n'a pas le SL dans l'historique MT5)
                # ⛔ EURUSD exclu de l'OL : les trades historiques datent de l'ancienne config
                # allow_shorts=false (WR=3.3%), qui contaminerait l'apprentissage en ligne.
                # L'OL apprendra EURUSD uniquement via les trades live (config actuelle).
                # 🔧 FIX 28 Juillet 2026: Les trades HIST ne sont PLUS importés dans l'OnlineLearner.
                # Raison: les données historiques (régime HIST) corrompent l'apprentissage avec
                # des patterns qui ne reflètent pas la configuration actuelle (allow_shorts, lots, etc.).
                # L'OL n'apprend que des vrais trades LIVE exécutés par le robot avec la config réelle.
                # La sauvegarde FTMO challenge est maintenue ci-dessus (record_trade_result).
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

    def track_new(self) -> None:
        our = [p for p in (self.positions_cache.get() or []) if p.magic == cfg.ROBOT_MAGIC]
        for p in our:
            # P3: Filtrer par whitelist — ignorer les symboles inactifs
            if p.symbol not in cfg.SYMBOLS:
                continue
            if p.ticket not in self._position_meta:
                order_type = self.mt5.ORDER_TYPE_BUY if p.type == 0 else self.mt5.ORDER_TYPE_SELL
                r1 = self.mt5.calc_profit(order_type, p.symbol, p.volume, p.price_open, p.sl)
                # Re-traduire le code court (3 lettres) en nom complet de régime
                # Le commentaire MT5 stocke "RAN" pour "RANGING", "DOW" pour "TREND_DOWN", etc.
                REGIME_SHORT_TO_FULL = {
                    "TRE": "TREND_UP",
                    "DOW": "TREND_DOWN",
                    "RAN": "RANGING",
                    "HIG": "HIGH_VOL",
                    "LOW": "LOW_VOL",
                }
                # 🔧 FIX 10 Juil 2026: Plus de fallback "LEGACY" systematique.
                # Avant : comment sans "ADAPT_" → "LEGACY" (polluait l'OL avec donnees pourries)
                # Apres : on essaie de parser le commentaire, sinon on utilise le regime
                # stocke dans _position_meta (mis a jour par add_meta > track_new).
                raw_regime = p.comment.replace("ADAPT_", "") if p.comment.startswith("ADAPT_") else ""
                if raw_regime in REGIME_SHORT_TO_FULL:
                    regime = REGIME_SHORT_TO_FULL[raw_regime]
                elif raw_regime in ("", "LEGACY"):
                    # Fallback: verifier le regime stocke anterieurement
                    existing_meta = self._position_meta.get(p.ticket, {})
                    stored_regime = existing_meta.get("regime", "")
                    if stored_regime and stored_regime != "LEGACY":
                        regime = stored_regime
                    else:
                        # Si on a un signal detecte par strategy.py, l'utiliser
                        # sinon RANGING par defaut (neutre)
                        regime = "RANGING"
                        logger.debug(
                            f"  [TRACK] {p.symbol} #{p.ticket}: fallback regime=RANGING (comment='{p.comment}')"
                        )
                else:
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
                    # Restaurer _meta_extra (sl_atr, tp_atr, atr) sauvegardé par add_meta
                    meta_extra = saved.get("_meta_extra", {})
                    if meta_extra:
                        meta.update(meta_extra)
                        logger.debug(
                            f"  [TRACK] {p.symbol} #{p.ticket}: restauré _meta_extra: {set(meta_extra.keys())}"
                        )
                    restored_keys = set(saved.keys()) & {
                        "_features",
                        "predictions",
                        "feature_adj",
                        "feature_reasons",
                        "_meta_extra",
                    }
                    if restored_keys:
                        logger.debug(f"  [TRACK] {p.symbol} #{p.ticket} restored saved meta: {restored_keys}")
                self._position_meta[p.ticket] = meta
                # Fusionner les champs supplémentaires (sl_atr, tp_atr, atr) stockés par add_meta avant track_new
                extra = self._meta_extra.pop(p.ticket, {})
                if extra:
                    self._position_meta[p.ticket].update(extra)
                    logger.debug(f"  [TRACK] {p.symbol} #{p.ticket}: fusionné extra meta: {set(extra.keys())}")
                logger.debug(f"  [TRACK] {p.symbol} #{p.ticket} regime={regime}")

    def check_closed(self) -> None:
        current = {p.ticket for p in (self.positions_cache.get() or []) if p.magic == cfg.ROBOT_MAGIC}
        try:
            # 🔧 FIX 12 Août 2026: Annuler les retries des tickets réapparus en position.
            # Un ticket mis en file _pending_closures car "fermé sans historique MT5" peut
            # en réalité être un glitch MT5 (position restaurée au cycle suivant). Dans ce
            # cas on ANNULE le retry — ne jamais enregistrer un faux trade.
            if self._pending_closures:
                reappeared = [t for t in self._pending_closures if t in current]
                if reappeared:
                    logger.info(
                        f"  [TRACKER] {len(reappeared)} tickets réapparus en position "
                        f"(glitch MT5) → retry annulé: {reappeared}"
                    )
                    for t in reappeared:
                        self._pending_closures.pop(t, None)
            closed = self._previous_tickets - current
            if closed:
                logger.info(
                    f"  [TRACKER] Closed tickets detected: {closed}, previous={self._previous_tickets}, current={current}"
                )
                # 🔒 CIRCUIT BREAKER 29 Juillet 2026: Si trop de positions ferment en un cycle,
                # c'est probablement un glitch MT5 (get_positions timeout → retourne [])
                # plutôt que de vraies fermetures. On saute l'enregistrement OL pour ces trades
                # pour éviter la contamination (ex: 40 trades d'un coup pour EURUSD).
                # Limite: 5 positions/cycle (même avec MAX_POSITIONS=18, le robot ne peut pas
                # en fermer plus de ~4 simultanément via trailing/TP).
                if len(closed) > 5:
                    logger.warning(
                        f"  [TRACKER] ⚠️ CIRCUIT BREAKER: {len(closed)} positions fermées en 1 cycle "
                        f"(max attendu=5) — probable glitch MT5, skip OL recording"
                    )
                    # Marquer comme traitées (pour éviter les logs répétés) mais NE PAS
                    # enregistrer dans l'OL ni le performance monitor.
                    for t in closed:
                        self._recorded_deals[t] = None
                    return
            pending_this_cycle: set[int] = set()
            # 🔧 FIX 12 Août 2026: tickets fermés sans deal MT5 mis en retry
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
                closing = self._find_closing_deal(ticket)
                if closing is None:
                    # 🔧 FIX 12 Août 2026: Retry des fermetures sans deal MT5 (gap de session).
                    # Avant ce fix, marquer _recorded_deals[ticket]=None ABANDONNAIT définitivement
                    # le PnL (absent de trades_log.csv, daily_pnl_by_date et perf monitor).
                    # Observé le 11/08: 2 XAUUSD (−240.65$) fermées pendant la reprise post-gel
                    # machine S3, deal MT5 pas encore synchronisé → stats fausses.
                    # Désormais: file de retry CLOSE_RETRY_ATTEMPTS cycles (~90s) où l'on retente
                    # la recherche du deal. Si trouvé → enregistrement complet (_record_closed_trade).
                    self._pending_closures[ticket] = self.CLOSE_RETRY_ATTEMPTS
                    pending_this_cycle.add(ticket)
                    logger.info(
                        f"  [TRACKER] Ticket {ticket} ferme sans historique MT5 — "
                        f"retry {self.CLOSE_RETRY_ATTEMPTS} cycles (~90s) avant fallback import_history"
                    )
                    continue
                deal_ts = self._deal_timestamp(closing)
                is_historical = deal_ts > 0 and deal_ts < float(self._start_time)
                self._record_closed_trade(closing, ticket, deal_ts, is_historical)
            # 🔧 FIX 12 Août 2026: Sweep des retries en attente (cycles suivants).
            # Un ticket fermé sans deal MT5 au cycle N reste dans _pending_closures et est
            # ré-interrogé à chaque cycle jusqu'à épuisement des tentatives ou apparition du deal.
            for ticket, remaining in list(self._pending_closures.items()):
                if ticket in pending_this_cycle:
                    continue  # déjà traité ce cycle (enqueued dans la boucle closed)
                if ticket in self._recorded_deals:
                    # Déjà traité (ex: réapparu + re-fermé, ou import) → nettoyer la file
                    self._pending_closures.pop(ticket, None)
                    continue
                closing = self._find_closing_deal(ticket)
                if closing is not None:
                    deal_ts = self._deal_timestamp(closing)
                    is_historical = deal_ts > 0 and deal_ts < float(self._start_time)
                    logger.info(f"  [TRACKER] Retry OK: deal trouvé pour ticket {ticket} → PnL enregistré")
                    self._record_closed_trade(closing, ticket, deal_ts, is_historical)
                    self._pending_closures.pop(ticket, None)
                    continue
                if remaining <= 1:
                    logger.warning(
                        f"  [TRACKER] Ticket {ticket}: deal introuvable après {self.CLOSE_RETRY_ATTEMPTS} cycles "
                        f"de retry — PnL abandonné (sera récupéré par import_history au prochain restart)"
                    )
                    self._recorded_deals[ticket] = None
                    self._pending_closures.pop(ticket, None)
                else:
                    self._pending_closures[ticket] = remaining - 1
        finally:
            # 🔧 FIX 30 Août 2026: TOUJOURS mettre à jour _previous_tickets
            # même si une exception survient dans la boucle. Sans ce try/finally,
            # une exception dans _record_closed_trade ou _find_closing_deal laissait
            # _previous_tickets inchangé → le prochain cycle re-détectait les mêmes
            # fermetures → double-comptage des trades (bug BTCUSD 4× SELL en 5s).
            self._previous_tickets = current


    def _find_closing_deal(self, ticket: int) -> Any:
        """Trouve le deal de fermeture d'un ticket dans l'historique MT5.

        🔧 FIX 12 Août 2026: extrait de check_closed() pour être réutilisable par
        le retry des fermetures sans historique MT5 (_pending_closures).

        🔧 FIX 17 Août 2026 (Log Analyst): agrège TOUS les deals de fermeture
        (closes partielles) du ticket. Avant: on ne prenait que le PREMIER deal
        avec profit != 0 → un partial TP (qui ferme 50% puis le reste en 2-3
        deals OUT) n'enregistrait QUE le profit de la première close, perdant
        le PnL du reste (T4 AUDUSD: 3 closes, 1 seule comptée → stats GR fausses).
        Désormais on somme les profits et volumes de tous les deals OUT du ticket
        (entry == DEAL_ENTRY_OUT=1, profit != 0).
        """
        since = int(time.time() - cfg.HISTORY_LOOKBACK_DAYS * 86400)
        now_ts = int(time.time())
        history = self.mt5.get_history(since, now_ts) or []
        logger.debug(f"  [TRACKER] query history for ticket {ticket}: {len(history)} deals")
        closing_deals = [
            d
            for d in history
            if d.position_id == ticket
            and d.magic == cfg.ROBOT_MAGIC
            and d.profit != 0
            and self._is_out_deal(d)
        ]
        if closing_deals:
            return self._aggregate_closing_deals(closing_deals)
        # Fallback: chercher par position ID directement (plus fiable que time-range)
        try:
            # 🔧 FIX 16 Juillet 2026: Utiliser self.mt5.get_history_by_position() avec timeout
            # au lieu de mt5.history_deals_get(position=...) direct.
            direct = self.mt5.get_history_by_position(ticket)
            if direct and len(direct) > 0:
                direct_closes = [
                    d for d in direct if d.profit != 0 and self._is_out_deal(d)
                ]
                if direct_closes:
                    logger.info(
                        f"  [TRACKER] Found closing deal via direct lookup: "
                        f"{len(direct_closes)} deal(s)"
                    )
                    return self._aggregate_closing_deals(direct_closes)
        except Exception as e:
            logger.debug(f"Direct lookup failed for ticket {ticket}: {e}")
        return None

    @staticmethod
    def _is_out_deal(d: Any) -> bool:
        """True si le deal est une CLÔTURE (DEAL_ENTRY_OUT), pas un swap/rollover.

        🔧 FIX 17 Août 2026 (Log Analyst): MT5 crée des deals OUT (entry=1) pour
        chaque close partielle (partial TP) ET la clôture finale. Les swaps/rollovers
        ont entry=2 (INOUT) ou 0 (IN) — on les EXCLUT pour ne sommer que le vrai PnL.
        Robustesse tests: si entry n'est pas un int (MagicMock, attribut absent),
        on garde le deal (comportement historique).
        """
        entry = getattr(d, "entry", None)
        if not isinstance(entry, int):
            return True  # mock ou attribut absent → compat
        return entry == 1  # DEAL_ENTRY_OUT

    @staticmethod
    def _aggregate_closing_deals(closing_deals: list) -> Any:
        """🔧 FIX 17 Août 2026 (Log Analyst): agrège les closes partielles d'un ticket.

        Un partial TP génère 2-3 deals OUT (50% puis le reste) sur le MÊME
        position_id. On somme profits et volumes, et on garde les attributs du
        DERNIER deal (fermeture finale) pour le prix/timestamp/raison.
        Retourne un objet SimpleNamespace compatible avec le reste du pipeline
        (symbol, position_id, profit, type, volume, price, time, comment, reason).
        """
        from types import SimpleNamespace

        total_profit = sum(d.profit for d in closing_deals)
        total_volume = sum(d.volume for d in closing_deals)
        last = closing_deals[-1]  # fermeture finale (ordre chronologique MT5)
        return SimpleNamespace(
            symbol=closing_deals[0].symbol,
            position_id=closing_deals[0].position_id,
            magic=getattr(closing_deals[0], "magic", cfg.ROBOT_MAGIC),
            profit=total_profit,
            volume=total_volume,
            price=getattr(last, "price", 0.0),
            time=getattr(last, "time", 0),
            type=getattr(last, "type", 0),
            comment=getattr(last, "comment", ""),
            reason=getattr(last, "reason", 0),
        )

    @staticmethod
    def _deal_timestamp(closing: Any) -> float:
        """Convertit le timestamp de fermeture MT5 (datetime | int | float) en epoch float.

        🔧 FIX 9 Juillet 2026: support datetime + int robuste (float(datetime) crashait).
        🔧 FIX 12 Août 2026: factorisé pour le retry des closes sans historique MT5.
        """
        deal_time = getattr(closing, "time", None)
        if deal_time is None:
            deal_time = getattr(closing, "timestamp", 0)
        # Conversion robuste: datetime → timestamp, int/float → float
        if isinstance(deal_time, datetime):
            return deal_time.timestamp()
        if isinstance(deal_time, (int, float)):
            return float(deal_time)
        return 0.0

    def _record_closed_trade(self, closing: Any, ticket: int, deal_ts: float, is_historical: bool) -> None:
        """Enregistre un trade fermé dont le deal MT5 a été trouvé (chemin live).

        🔧 FIX 12 Août 2026: factorisé de check_closed() pour être partagé entre le
        chemin normal et le retry des fermetures sans historique MT5 (_pending_closures).
        Centre de tout ce qui était PERDU quand le deal n'était pas trouvé immédiatement:
        record_trade_result FTMO (cooldown/consecutive), journal CSV, perf monitor,
        AdaptiveParams, audit — plus le PnL dans trades_log.csv et daily_pnl.
        """
        # P3: Whitelist — ignorer les symboles inactifs (contamination EURUSD)
        if closing.symbol not in cfg.SYMBOLS:
            logger.debug(f"  [TRACKER] Skipping {closing.symbol} (not in SYMBOLS whitelist)")
            self._recorded_deals[ticket] = None
            return
        pos_key = f"{closing.position_id}_{closing.symbol}"
        if pos_key in self._recorded_position_ids:
            return
        self._recorded_position_ids[pos_key] = None
        self._recorded_deals[ticket] = None
        logger.info(
            f"  [TRACKER] Found closing deal for {closing.symbol} ticket {ticket}: profit={closing.profit:.2f}"
        )
        # 🔒 Si le trade a été fermé AVANT le démarrage du robot, c'est un replay historique
        # → ne pas incrémenter consecutive_losses (sinon circuit breaker trip au restart)
        # Prendre le vrai timestamp MT5 pour que challenge.py filtre les trades >48h
        trade_dt = getattr(closing, "time", None)
        if isinstance(trade_dt, (int, float)):
            trade_dt = datetime.utcfromtimestamp(trade_dt)
        # 🐛 FIX 10 Août 2026 (Bug #6): direction réelle déduite du DEAL type
        # (convention: DEAL_TYPE_SELL(1)=vente de BUY → réel=BUY).
        closing_dir = "BUY" if closing.type == 1 else "SELL"
        self.ftmo.record_trade_result(
            closing.symbol, closing.profit, historical=is_historical, trade_time=trade_dt, direction=closing_dir
        )
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

            # 🔧 FIX 14 Août 2026: NE PAS alimenter le perf monitor avec des trades
            # historiques rejoués (is_historical=True). Le robot rejoue les deals MT5
            # des 48h au restart : cela importait des centaines de trades (dont SELL
            # bannis et ancienne config) dans recent_trades, POLLUANT les rolling
            # windows (WR 31-45% au lieu de 83% réel) et les stats par symbole.
            # Même principe que le FIX 28 Juillet pour l'OnlineLearner : seuls les
            # vrais trades LIVE exécutés avec la config actuelle alimentent les stats.
            if not is_historical:
                regime = meta.get("regime", "UNKNOWN")
                # MT5: le DEAL type est l'inverse de la position réelle
                # DEAL_TYPE_BUY(0)=rachat de SELL → réel=SELL; DEAL_TYPE_SELL(1)=vente de BUY → réel=BUY
                pos_dir = "BUY" if closing.type == 1 else "SELL"
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

        # 🔧 FIX 29 Juillet 2026: DEAL type ≠ position direction
        # DEAL_TYPE_BUY(0)=rachat de SELL → réel=SELL; DEAL_TYPE_SELL(1)=vente de BUY → réel=BUY
        pos_dir = "BUY" if closing.type == 1 else "SELL"
        # 🐛 FIX 31 Juillet 2026: Calculer la VRAIE raison de sortie au lieu de "closed" codé en dur.
        exit_reason = self._extract_exit_reason(closing)
        # durée réelle du trade: opened_at (epoch local UTC) → timestamp de fermeture MT5
        # 🔧 FIX 28 Août 2026: deal_ts est en temps serveur MT5 (+3h offset vs UTC local).
        #    Soustraire le server_offset pour corriger la durée affichée.
        try:
            opened_ts = float(meta.get("opened_at", 0) or 0)
            server_offset = getattr(self.ftmo, "_server_offset_s", 0.0) or 0.0
            corrected_deal_ts = deal_ts - server_offset if server_offset else deal_ts
            duration_min = int(max(0, (corrected_deal_ts - opened_ts) / 60.0)) if opened_ts > 0 else 0
        except (TypeError, ValueError):
            duration_min = 0
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
                    # 🐛 FIX 03 Aout 2026: timestamp de fermeture = vrai deal MT5 (deal_ts),
                    time_close=str(datetime.fromtimestamp(deal_ts, tz=timezone.utc)) if deal_ts > 0 else str(datetime.now(timezone.utc)),
                    reason=exit_reason,
                    duration_min=duration_min,
                    # 🐛 FIX 4 Juillet 2026: ATR multiples pour analyse post-trade
                    sl_atr=meta.get("sl_atr", ""),
                    tp_atr=meta.get("tp_atr", ""),
                    atr=meta.get("atr", 0.0),
                )
            )
        except Exception as e:
            logger.warning(f"[TRACK] journal.record failed for {closing.symbol}: {e}")
        self.feature_store.delete(ticket)
        regime = meta.get("regime", "UNKNOWN")
        r1 = meta.get("r1_usd", 1)
        r_mul = round(closing.profit / r1, 2) if r1 > 0 else 0
        # 🔧 FIX 28 Juillet 2026: Guard anti-contamination historique
        # is_historical = True quand le trade a été fermé avant le démarrage du robot.
        # Sans ce guard, un cache MT5 vide (timeout) transforme TOUTES les positions
        # en "fermées", et les trades historiques (≤48h mais pré-start) alimentent l'OL
        # avec des centaines de faux trades en une seconde.
        if not is_historical:
            self.adaptive.record_result(
                closing.symbol, r_mul, regime, profit=closing.profit, win=closing.profit > 0
            )
        else:
            logger.debug(
                f"  [TRACKER] {closing.symbol}: skip adaptive.record_result "
                f"(historical trade, profit={closing.profit:.2f})"
            )
        # 🔧 FIX 28 Juillet 2026: Ne PAS alimenter l'OnlineLearner avec des trades historiques.
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
        # 🔧 FIX 28 Août 2026: record_meta_result supprimé (DL code mort)
        # Les prédictions sont déjà enregistrées dans trade_journal et performance_monitor


    # MT5 DEAL_REASON codes (définition Python MT5)
    _DEAL_REASON = {
        0: "client",  # DEAL_REASON_CLIENT
        1: "expert",  # DEAL_REASON_EXPERT (ordre envoyé par le robot)
        2: "dealer",  # DEAL_REASON_DEALER
        3: "sl",  # DEAL_REASON_SL
        4: "tp",  # DEAL_REASON_TP
        5: "stop_out",  # DEAL_REASON_SO
        6: "rollover",  # DEAL_REASON_ROLLOVER
        7: "external",  # DEAL_REASON_EXTERNAL
    }

    def _extract_exit_reason(self, closing: Any) -> str:
        """🐛 FIX 31 Juillet 2026: Détermine la VRAIE raison de sortie d'un trade fermé.

        Sources, par ordre de priorité:
        1. Commentaire MT5 du deal (le robot y met "TIME_STOP", "KILL_SWITCH", etc.)
        2. Code DEAL_REASON (3=SL, 4=TP, 5=stop out, ...)
        3. Fallback "closed" (raison inconnue)

        Avant ce fix, `reason` était TOUJOURS "closed" et duration_h=0,
        rendant impossible l'audit SL/TP/trailing/BE/time_stop sur trades_log.csv.
        """
        # 1. Commentaire MT5 (positions fermées PAR le robot: time_stop, kill_switch, structure...)
        comment = getattr(closing, "comment", None)
        if comment:
            c = str(comment).upper()
            if "TIME_STOP" in c:
                return "time_stop"
            if "KILL" in c or "EMERGENCY" in c:
                return "kill_switch"
            if "STRUCT" in c:
                return "structure"
            if "SL" in c:
                return "sl"
            if "TP" in c:
                return "tp"
            if "PARTIAL" in c:
                return "partial_tp"
            # Commentaire standard MT5 pour ordre du robot — on continue vers reason code
        # 2. Code DEAL_REASON (positions fermées par le serveur: SL/TP/stop out)
        reason_code = getattr(closing, "reason", None)
        try:
            code = int(reason_code)
        except (TypeError, ValueError):
            code = -1
        if code in self._DEAL_REASON:
            return self._DEAL_REASON[code]
        # 3. Fallback
        return "closed"

    def add_meta(self, ticket: int, data: dict[str, Any]) -> None:
        if ticket in self._position_meta:
            # Fusionner avec le meta existant (track_new déjà exécuté)
            self._position_meta[ticket].update(data)
            self._position_meta[ticket]["opened_at"] = time.time()
            logger.debug(f"  [META] #{ticket}: fusionné {set(data.keys())} dans meta existant")
        else:
            # Stocker pour fusion ultérieure quand track_new() créera le meta
            existing = self._meta_extra.get(ticket, {})
            existing.update(data)
            self._meta_extra[ticket] = existing
            logger.debug(f"  [META] #{ticket}: stocké {set(data.keys())} dans _meta_extra en attente de track_new")
        self.feature_store.save(ticket, {"_meta_extra": data})

    def get_active_count(self) -> int:
        return len(self._position_meta)

    def get_symbol_performance(self, symbol: str) -> SymbolPerformance | None:
        """🔧 FIX 16 Juillet 2026: Retourne le SymbolPerformance pour un symbole.
        Méthode ajoutée pour remplacer l'appel inexistant dans symbol_params.py.
        """
        return self._perf(symbol)

    def performance_summary(self) -> dict[str, Any]:
        return {sym: perf.summary() for sym, perf in self.performance.items()}

    def global_summary(self) -> dict[str, Any]:
        total_trades = sum(p.trades for p in self.performance.values())
        total_profit = sum(p.total_profit for p in self.performance.values())
        total_wins = sum(p.wins for p in self.performance.values())
        return {
            "total_trades": total_trades,
            "total_profit": round(total_profit, 2),
            "global_win_rate": round(total_wins / max(total_trades, 1), 3),
            "symbols_tracked": len(self.performance),
        }
