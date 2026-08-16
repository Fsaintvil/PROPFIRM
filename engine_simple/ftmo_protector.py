from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import MetaTrader5 as mt5
import numpy as np

import config_simple as cfg
from engine_simple.ftmo_config import (
    ATR_CACHE_TTL,
    BE_BUFFER_BY_REGIME,
    FIRST_LOCK_ATR,
    MAX_TOTAL_LOTS,
    TRAILING_BY_REGIME,
    RISK_MULT_CAP,
    DD_REDUCE_THRESHOLD,
    DD_CRITICAL_THRESHOLD,
    DD_AUTODISABLE_THRESHOLD,
    get_trailing_for_symbol,
    get_be_buffer_for_symbol,
)
from engine_simple.news_filter import is_news_blocked
from engine_simple.signal_validator import SignalValidator
from engine_simple.structure_analyzer import structure_exit_signal
from engine_simple.trailer import Trailer

logger = logging.getLogger("ftmo")


class ChallengeTracker:
    """Suivi du challenge FTMO 200K — règles de risque et progress tracking.

    Déplacé de engine_simple/challenge.py (Phase 2, 21 Juillet 2026).
    """

    def __init__(self, mt5: Any, config: dict) -> None:
        self.mt5 = mt5
        self.config: dict = config  # stocké pour accès ultérieur (cooldown, etc.)

        # ── Règles FTMO ──────────────────────────────────────────────
        self.initial_balance = config.get("INITIAL_BALANCE", 200000)
        self.max_dd_pct = config.get("MAX_DD_PCT", 0.10)
        self.max_daily_loss_pct = config.get("MAX_DAILY_LOSS_PCT", 0.02)
        self.profit_target_pct = config.get("PROFIT_TARGET_PCT", 0.10)
        self.consistency_max_pct = config.get("CONSISTENCY_MAX_PCT", 0.30)
        self.min_trading_days = config.get("MIN_TRADING_DAYS", 10)
        self.symbol_limits = config.get("SYMBOL_LIMITS", {})

        # ── État challenge ───────────────────────────────────────────
        self.peak_equity = self.initial_balance
        self.daily_start_equity = self.initial_balance
        self.challenge_status = "ACTIVE"  # ACTIVE | PASSED | FAILED_DD
        self.consistency_violated = False
        self._daily_loss_violated = False

        # ── Stats quotidiennes ───────────────────────────────────────
        self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": datetime.utcnow().date()}
        self._daily_trades_per_symbol: dict[str, int] = {}
        self._opened_today = 0
        self._daily_profit_reduced = False
        self.daily_pnl_by_date: dict[date, float] = {}  # date -> realized PnL

        # ── Historiques ──────────────────────────────────────────────
        self.trading_days: set = set()
        self.consecutive_losses = 0
        self._symbol_consecutive_losses: dict[str, int] = {}
        self.cooldowns: dict[str, datetime] = {}
        self.global_cooldown_until: datetime | None = None
        self._trade_history: list[dict] = []
        self._symbol_trade_history: dict[str, list[dict]] = {}
        # 🔧 FIX_SUPREME_COUNCIL 2 Juillet 2026: suivi PnL quotidien par symbole
        self._symbol_daily_pnl: dict[str, float] = {}

    # ── Trade recording ──────────────────────────────────────────────

    def record_trade_result(
        self,
        symbol: str,
        profit: float,
        historical: bool = False,
        trade_time: Optional[datetime] = None,
        direction: Optional[str] = None,
    ) -> None:
        """Enregistre le résultat d'un trade fermé."""
        now = trade_time or datetime.utcnow()

        # 🔒 FIX #2: Pour les trades historiques, ne garder que les 48 dernières heures
        add_to_history = True
        if historical and trade_time is not None:
            if isinstance(trade_time, (int, float)):
                trade_dt = datetime.fromtimestamp(trade_time)
            else:
                trade_dt = trade_time
            age = (datetime.utcnow() - trade_dt).total_seconds()
            if age > 48 * 3600:  # plus de 48h
                add_to_history = False

        if add_to_history:
            # 🐛 FIX 10 Août 2026 (Bug #6): stocker la direction réelle ("BUY"/"SELL")
            # dans trade_history. Sans elle, _check_directional_imbalance restait
            # inerte (buys/sells toujours à 0) et la protection ne s'appliquait jamais.
            self._trade_history.append(
                dict(
                    symbol=symbol,
                    profit=profit,
                    time=now,
                    historical=historical,
                    action=direction,
                )
            )
        if len(self._trade_history) > 1000:
            self._trade_history[:] = self._trade_history[-1000:]

        if not historical:
            self.daily_stats["trades"] += 1
            self._daily_trades_per_symbol[symbol] = self._daily_trades_per_symbol.get(symbol, 0) + 1
            self.daily_stats["pnl"] += profit
            self._symbol_daily_pnl[symbol] = self._symbol_daily_pnl.get(symbol, 0) + profit
            today = datetime.utcnow().date()
            self.trading_days.add(today)
            self.daily_pnl_by_date[today] = self.daily_pnl_by_date.get(today, 0) + profit
            # Per-symbol trade history (rolling window 50 trades)
            if symbol not in self._symbol_trade_history:
                self._symbol_trade_history[symbol] = []
            self._symbol_trade_history[symbol].append(dict(profit=profit, time=datetime.utcnow()))
            if len(self._symbol_trade_history[symbol]) > 50:
                self._symbol_trade_history[symbol] = self._symbol_trade_history[symbol][-50:]

            if profit < 0:
                self.daily_stats["losses"] += 1
                self.consecutive_losses += 1
                sym_losses = self._symbol_consecutive_losses.get(symbol, 0) + 1
                self._symbol_consecutive_losses[symbol] = sym_losses
                if sym_losses >= 3:
                    cd_minutes = self.symbol_limits.get(symbol, {}).get(
                        "cooldown_minutes_consecutive",
                        self.config.get("COOLDOWN_MINUTES_CONSECUTIVE", 120),
                    )
                else:
                    cd_minutes = self.symbol_limits.get(symbol, {}).get(
                        "cooldown_minutes", getattr(self, "cooldown_minutes", 15)
                    )
                self.cooldowns[symbol] = datetime.utcnow() + timedelta(minutes=cd_minutes)
                logger.info(f"  [COOLDOWN] {symbol}: {sym_losses} perte(s) consecutive(s) → {cd_minutes}min")
            elif profit > 0:
                self.consecutive_losses = 0
                self._symbol_consecutive_losses[symbol] = 0

            self._check_consistency()
            self._check_daily_loss_limit(symbol=symbol)
            self._check_drawdown_limit()

        self._prune_histories()

    # ── Règles FTMO ─────────────────────────────────────────────────

    def _check_consistency(self) -> None:
        """FTMO consistency rule: aucun jour ne doit dépasser 30% du profit RÉEL."""
        self.consistency_violated = False
        total_net = sum(self.daily_pnl_by_date.values())
        positive_days = [v for v in self.daily_pnl_by_date.values() if v > 0]
        if len(self.daily_pnl_by_date) < 2 or total_net <= 0:
            return
        # 🔧 FIX 24 Juillet 2026: Besoin d'au moins 2 jours POSITIFS
        # pour que la règle de consistance ait un sens.
        # Avec 1 seul jour positif, best_day = positive_total = 100% toujours → faux positif.
        if len(positive_days) < 2:
            return
        # 🔧 FIX 24 Juillet 2026: PnL total positif trop faible → consistency non applicable
        # FTMO conçoit la règle pour des profits significatifs. < $100 = bruit statistique.
        if sum(positive_days) < 100:
            return
        positive_total = sum(positive_days)
        max_per_day = positive_total * self.consistency_max_pct
        for day, day_pnl in sorted(self.daily_pnl_by_date.items()):
            if day_pnl <= 0:
                continue
            if day_pnl > max_per_day:
                self.consistency_violated = True
                day_pct_of_net = day_pnl / positive_total if positive_total > 0 else 0
                logger.warning(
                    f"FTMO CONSISTENCY VIOLATED: {day} = ${day_pnl:.0f} "
                    f"({day_pct_of_net:.1%} du PnL net ${total_net:.0f}) "
                    f"> max {self.consistency_max_pct:.0%} du PnL net — flag info, trading continue"
                )

    def _check_daily_loss_limit(self, symbol: Optional[str] = None) -> None:
        """Vérifie la daily loss avec coordination et caching."""
        self._reset_daily()
        try:
            account = self.mt5.get_account_info()
            equity_val = getattr(account, "equity", None)
            if equity_val is not None and isinstance(equity_val, (int, float)):
                daily_equity_change = equity_val - self.daily_start_equity
            else:
                daily_equity_change = self.daily_stats["pnl"]
        except (AttributeError, RuntimeError, OSError):
            daily_equity_change = self.daily_stats["pnl"]

        daily_loss_pct = max(0, -daily_equity_change) / max(self.initial_balance, 1)
        daily_loss_limit = self.max_daily_loss_pct

        if symbol:
            sym_cfg = self.symbol_limits.get(symbol, {})
            sym_daily_loss = sym_cfg.get("max_daily_loss_pct_override")
            if sym_daily_loss is not None:
                daily_loss_limit = sym_daily_loss

        if daily_loss_pct >= daily_loss_limit and self._opened_today == 0 and daily_loss_pct > 0:
            # 🐛 FIX 10 Août 2026 (Bug #5): AUTO-HEAL SUPPRIMÉ.
            # L'ancien code (lignes 200-215) recalait daily_start_equity sur l'equity
            # courante quand la perte dépassait le seuil SANS trade ouvert aujourd'hui.
            # Or ce cas correspond à une perte HÉRITÉE de positions ouvertes avant minuit
            # UTC, et un simple bruit de tick entre deux appels get_account_info()
            # (écartés de quelques secondes seulement) suffisait à faire passer
            # _retry_pct < daily_loss_pct → le DSE était recalé vers le bas → la perte
            # réelle était MASQUÉE et le daily loss limit ne bloquait JAMAIS le trading.
            # Le DSE ne doit être recalculé que par _reset_daily() à minuit UTC.
            logger.warning(
                f"DAILY LOSS LIMIT: {daily_loss_pct:.2%} >= {daily_loss_limit:.2%} "
                f"sans trade ouvert aujourd'hui — perte héritée de positions d'avant "
                f"minuit UTC. Trading bloqué pour aujourd'hui."
            )

        # 🐛 FIX 16 Août 2026 (Audit A3): LATCh — une fois le daily loss dépassé,
        # le flag reste True jusqu'au _reset_daily() de minuit UTC. FTMO applique
        # la règle sur le pire niveau du jour : une remontée d'equity intraday
        # ne doit PAS ré-autoriser le trading.
        if not self._daily_loss_violated:
            self._daily_loss_violated = daily_loss_pct >= daily_loss_limit
        if self._daily_loss_violated:
            logger.warning(f"DAILY LOSS LIMIT: {daily_loss_pct:.1%} — trading bloqué pour aujourd'hui")

    def current_dd_pct(self) -> float:
        """Retourne le drawdown actuel en ratio (0.0 = pas de DD, 1.0 = 100%)."""
        try:
            account = self.mt5.get_account_info()
            if not account:
                logger.warning("[DD] get_account_info() returned None — returning 1.0")
                return 1.0
            eq = account.equity
            peak = self.peak_equity
            return (peak - eq) / max(peak, 1) if peak > 0 else 0.0
        except Exception as e:
            logger.error(f"[DD] current_dd_pct() FAILED: {e} — returning 1.0")
            return 1.0

    def _check_drawdown_limit(self) -> None:
        """Vérifie le drawdown max (10% FTMO)."""
        try:
            account = self.mt5.get_account_info()
            if account:
                dd_pct = (self.peak_equity - account.equity) / max(self.peak_equity, 1)
                if dd_pct >= self.max_dd_pct:
                    self.challenge_status = "FAILED_DD"
                    logger.warning(f"MAX DRAWDOWN: {dd_pct:.1%} - STOPPING")
        except Exception as e:
            logger.warning(f"Drawdown check failed: {e}")

    # ── Progress report ──────────────────────────────────────────────

    def get_progress_report(self) -> dict:
        """Génère le rapport de progression du challenge."""
        account = self.mt5.get_account_info()
        equity = account.equity if account else self.peak_equity
        balance = account.balance if account else self.initial_balance

        realized_pnl = sum(self.daily_pnl_by_date.values()) if self.daily_pnl_by_date else 0
        current_pnl = equity - self.initial_balance
        if realized_pnl == 0 and self._trade_history:
            realized_pnl = sum(t.get("profit", 0) for t in self._trade_history)

        profit_progress = current_pnl / max(self.initial_balance * self.profit_target_pct, 1e-6)
        dd_init = max(0, (self.initial_balance - equity) / self.initial_balance)
        dd_peak = max(0, (self.peak_equity - equity) / max(self.peak_equity, 1))

        winners = sum(1 for t in self._trade_history if t.get("profit", 0) > 0)
        wr = winners / max(len(self._trade_history), 1)

        best_day = max(self.daily_pnl_by_date.values()) if self.daily_pnl_by_date else 0
        if best_day == 0 and self._trade_history and current_pnl > 0:
            temp_daily = {}
            for t in self._trade_history:
                t_time = t.get("time")
                d = t_time.date() if isinstance(t_time, datetime) else None
                if d is None:
                    continue
                temp_daily[d] = temp_daily.get(d, 0) + t.get("profit", 0)
            if temp_daily:
                best_day = max(temp_daily.values())

        positive_days_total = sum(v for v in self.daily_pnl_by_date.values() if v > 0)
        if best_day <= 0 or positive_days_total <= 0:
            best_day_pct = 0.0
        else:
            best_day_pct = best_day / positive_days_total

        if best_day_pct > 1.0:
            logger.warning(
                f"best_day_pct={best_day_pct:.1%} invalide (>100%) — "
                f"daily_pnl_by_date probablement contaminé. Cap à 100%"
            )
            best_day_pct = min(best_day_pct, 1.0)

        return dict(
            balance=balance,
            equity=equity,
            pnl=current_pnl,
            status=self.challenge_status,
            consistency_violated=self.consistency_violated,
            best_day_pct=f"{best_day_pct:.1%}",
            profit_progress=f"{profit_progress:.1%}",
            profit_remaining=f"${max(0, self.initial_balance * self.profit_target_pct - current_pnl):.0f}",
            dd_from_initial=f"{dd_init:.1%}",
            dd_from_peak=f"{dd_peak:.1%}",
            trading_days=len(self.trading_days),
            days_remaining=max(0, self.min_trading_days - len(self.trading_days)),
            total_trades=len(self._trade_history),
            win_rate=f"{wr:.0%}",
            daily_pnl=f"${self.daily_stats['pnl']:.0f}",
            daily_equity_pnl=f"${equity - self.daily_start_equity:.0f}",
            peak_equity=self.peak_equity,
            consecutive_losses=self.consecutive_losses,
        )

    # ── Reset ────────────────────────────────────────────────────────

    def reset_challenge(self, new_initial_balance: Optional[float] = None) -> None:
        """Reset l'état du challenge (utile pour comptes practice/Free Trial)."""
        self.challenge_status = "ACTIVE"
        self.consistency_violated = False
        self.consecutive_losses = 0
        self._symbol_consecutive_losses = {}
        self.global_cooldown_until = None
        self.cooldowns = {}
        self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": datetime.utcnow().date()}
        self._daily_trades_per_symbol = {}
        self._opened_today = 0
        self._trade_history = []
        self._symbol_trade_history = {}
        self.daily_pnl_by_date = {}
        self.trading_days = set()
        self.trading_days.add(datetime.utcnow().date())
        self._daily_profit_reduced = False
        if new_initial_balance is not None:
            self.initial_balance = new_initial_balance
        account = self.mt5.get_account_info()
        if account:
            self.peak_equity = account.equity
            self.daily_start_equity = account.equity
        logger.warning(
            f"[CHALLENGE RESET] Status={self.challenge_status}, "
            f"balance=${self.initial_balance:.2f}, peak=${self.peak_equity:.2f}"
        )

    def _reset_daily(self) -> None:
        """Reset les stats quotidiennes à minuit UTC."""
        now = datetime.utcnow()
        if now.date() != self.daily_stats.get("day"):
            old_dse = self.daily_start_equity
            self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": now.date()}
            self._daily_trades_per_symbol = {}
            self._symbol_daily_pnl = {}
            self._opened_today = 0
            self._daily_profit_reduced = False
            # 🐛 FIX 16 Août 2026 (Audit A3): reset du latch daily loss au minuit
            # UTC (le latch est arme en intraday par _check_daily_loss_limit).
            self._daily_loss_violated = False
            account = self.mt5.get_account_info()
            if account:
                self.daily_start_equity = account.equity
            else:
                self.daily_start_equity = old_dse if old_dse > 0 else self.initial_balance

    # ── Pruning ──────────────────────────────────────────────────────

    def _prune_histories(self) -> None:
        """Nettoie les historiques pour limiter la mémoire."""
        if len(self._trade_history) > 1000:
            self._trade_history[:] = self._trade_history[-1000:]

    # ── State sync helpers (for FTMOProtector) ───────────────────────

    def get_state_dict(self) -> dict:
        """Retourne un dict des champs persistés dans robot_state.json."""
        return {
            "consecutive_losses": self.consecutive_losses,
            "cooldowns": {k: v.isoformat() for k, v in self.cooldowns.items()},
            "symbol_consecutive_losses": dict(self._symbol_consecutive_losses),
            "trading_days_list": sorted(d.isoformat() for d in self.trading_days),
            "daily_pnl_by_date": {k.isoformat(): v for k, v in self.daily_pnl_by_date.items()},
            "challenge_status": self.challenge_status,
            "consistency_violated": self.consistency_violated,
            "daily_stats": self.daily_stats,
            "daily_start_equity": self.daily_start_equity,
            "peak_equity": self.peak_equity,
            "trade_history": [
                {
                    "symbol": t["symbol"],
                    "profit": t["profit"],
                    "time": t["time"].isoformat()
                    if isinstance(t["time"], datetime)
                    else datetime.fromtimestamp(t["time"]).isoformat()
                    if isinstance(t["time"], (int, float))
                    else str(t["time"]),
                    "historical": t.get("historical", False),
                    "action": t.get("action"),  # 🐛 FIX Bug #6: direction réelle (BUY/SELL)
                }
                for t in self._trade_history[-200:]  # last 200 trades
            ],
        }

    def load_state_dict(self, state: dict):
        """Restaure l'état depuis robot_state.json."""
        self.consecutive_losses = state.get("consecutive_losses", 0)
        self._symbol_consecutive_losses = state.get("symbol_consecutive_losses", {})
        self.challenge_status = state.get("challenge_status", "ACTIVE")
        self.consistency_violated = state.get("consistency_violated", False)
        self.daily_stats = state.get("daily_stats", self.daily_stats)
        self.daily_start_equity = state.get("daily_start_equity", self.initial_balance)
        self.peak_equity = state.get("peak_equity", self.initial_balance)
        self._daily_loss_violated = False

        # 🔧 FIX 7 Juillet 2026: Sanity check — si FAILED_DD mais DD réel < 10%, reset
        if self.challenge_status == "FAILED_DD":
            try:
                account = self.mt5.get_account_info()
                if account:
                    current_dd = (self.peak_equity - account.equity) / max(self.peak_equity, 1)
                    if current_dd < self.max_dd_pct:
                        logger.info(
                            f"[CHALLENGE RESET] FAILED_DD chargé depuis state mais DD réel={current_dd:.2%} "
                            f"< {self.max_dd_pct:.0%} — reset à ACTIVE"
                        )
                        self.challenge_status = "ACTIVE"
            except Exception:
                pass

        # Restore cooldowns
        cd = state.get("cooldowns", {})
        self.cooldowns = {}
        for k, v in cd.items():
            try:
                self.cooldowns[k] = datetime.fromisoformat(v)
            except (ValueError, TypeError):
                pass

        # Restore trading days
        td = state.get("trading_days_list", [])
        self.trading_days = set()
        for d in td:
            try:
                self.trading_days.add(datetime.fromisoformat(d).date())
            except (ValueError, TypeError):
                pass

        # Restore daily_pnl_by_date
        dp = state.get("daily_pnl_by_date", {})
        self.daily_pnl_by_date = {}
        for k, v in dp.items():
            try:
                self.daily_pnl_by_date[datetime.fromisoformat(k).date()] = v
            except (ValueError, TypeError):
                pass

        # Restore trade_history
        th = state.get("trade_history", [])
        if th:
            self._trade_history = []
            for t in th:
                try:
                    time_val = t.get("time", "")
                    if isinstance(time_val, (int, float)):
                        time_val = datetime.fromtimestamp(time_val)
                    elif isinstance(time_val, str):
                        time_val = datetime.fromisoformat(time_val)
                    self._trade_history.append(
                        {
                            "symbol": t.get("symbol", ""),
                            "profit": t.get("profit", 0),
                            "time": time_val,
                            "historical": t.get("historical", True),
                            "action": t.get("action"),  # 🐛 FIX Bug #6: direction réelle (BUY/SELL)
                        }
                    )
                except (ValueError, TypeError):
                    pass

            # 🔧 FIX 05 Août 2026: Dédup des doublons d'historique.
            # Les imports historiques re-joués à chaque restart (position_tracker)
            # ajoutent des COPIES du même trade (même symbole/profit à la même
            # seconde, flag historical=true) → gonfle total_trades, fausse win_rate
            # et contamine le rebuild de daily_pnl_by_date.
            dedup: dict[tuple, dict] = {}
            for _t in self._trade_history:
                _key = (_t.get("symbol"), _t.get("profit"), str(_t.get("time")))
                # Priorité au flag non-historique (le vrai trade live)
                if _key not in dedup or not _t.get("historical"):
                    dedup[_key] = _t
            if len(dedup) != len(self._trade_history):
                logger.info(
                    f"[DEDUP] trade_history: {len(self._trade_history)} → "
                    f"{len(dedup)} entrées uniques (doublons supprimés)"
                )
                self._trade_history = list(dedup.values())

        # ── Contamination Guard ──
        if self._trade_history:
            rebuilt: dict[date, float] = {}
            for t in self._trade_history:
                trade_time = t.get("time")
                if isinstance(trade_time, datetime):
                    d = trade_time.date()
                    rebuilt[d] = rebuilt.get(d, 0.0) + t.get("profit", 0.0)

            if not self.daily_pnl_by_date and rebuilt:
                logger.info(
                    f"[RECOVERY] daily_pnl_by_date vide — "
                    f"reconstruction depuis trade_history ({len(rebuilt)} jours, "
                    f"{len(self._trade_history)} trades)"
                )
                self.daily_pnl_by_date = dict(rebuilt)
                for d in rebuilt:
                    self.trading_days.add(d)

            if self.daily_pnl_by_date and rebuilt:
                common_dates = set(self.daily_pnl_by_date.keys()) & set(rebuilt.keys())
                discrepancies = 0
                for d in common_dates:
                    loaded = self.daily_pnl_by_date[d]
                    truth = rebuilt[d]
                    if abs(loaded - truth) > max(abs(truth) * 0.1, 1.0):
                        discrepancies += 1

                if discrepancies > 0:
                    pct = discrepancies / len(common_dates)
                    if pct > 0.2:
                        logger.warning(
                            f"[CONTAMINATION] daily_pnl_by_date: {discrepancies}/{len(common_dates)} "
                            f"dates diffèrent de trade_history ({pct:.1%}) — correction"
                        )
                        for d, pnl in rebuilt.items():
                            self.daily_pnl_by_date[d] = pnl
                        orphan_dates = set(self.daily_pnl_by_date.keys()) - set(rebuilt.keys())
                        if orphan_dates:
                            logger.warning(
                                f"[CONTAMINATION] {len(orphan_dates)} date(s) orpheline(s) "
                                f"conservée(s): {', '.join(str(d) for d in sorted(orphan_dates)[:5])}"
                            )
                    else:
                        logger.info(
                            f"[CONTAMINATION] {discrepancies} divergence(s) mineure(s) "
                            f"({pct:.1%} des dates) — sous le seuil, ignoré"
                        )


class FTMOProtector:
    def __init__(self, mt5: Any, config: dict[str, Any]) -> None:
        self.mt5 = mt5
        self.config = config

        self.initial_balance = config.get("INITIAL_BALANCE", 200000)
        self.max_dd_pct = config.get("MAX_DD_PCT", 0.10)
        self.max_daily_loss_pct = config.get("MAX_DAILY_LOSS_PCT", 0.02)
        self.profit_target_pct = config.get("PROFIT_TARGET_PCT", 0.10)
        self.consistency_max_pct = config.get("CONSISTENCY_MAX_PCT", 0.30)
        self.min_trading_days = config.get("MIN_TRADING_DAYS", 10)
        self.max_trading_days = config.get("MAX_TRADING_DAYS", 0)
        self.max_spread_points = config.get("MAX_SPREAD_POINTS", 30)
        self.cooldown_minutes = config.get("COOLDOWN_MINUTES", 15)
        self.symbol_limits = config.get("SYMBOL_LIMITS", {})
        self.max_risk_amount = config.get("MAX_RISK_AMOUNT", 0)
        self._symbol_auto_disable_wr_threshold = 0.20

        # ── ChallengeTracker (P2.3) ──────────────────────────────────
        self.challenge = ChallengeTracker(mt5, config)
        # Expose state aliases for backward compat with Trailer / tests
        self.peak_equity = self.challenge.peak_equity
        self.daily_start_equity = self.challenge.daily_start_equity
        self.daily_stats = self.challenge.daily_stats
        self.consecutive_losses = self.challenge.consecutive_losses
        self.cooldowns = self.challenge.cooldowns
        self._symbol_consecutive_losses = self.challenge._symbol_consecutive_losses
        self._trade_history = self.challenge._trade_history
        self.trading_days = self.challenge.trading_days
        self.daily_pnl_by_date = self.challenge.daily_pnl_by_date
        self.consistency_violated = self.challenge.consistency_violated
        self.challenge_status = self.challenge.challenge_status
        self._daily_loss_violated = self.challenge._daily_loss_violated
        self._daily_trades_per_symbol = self.challenge._daily_trades_per_symbol
        self._symbol_daily_pnl = self.challenge._symbol_daily_pnl
        self._opened_today = self.challenge._opened_today
        self._daily_profit_reduced = self.challenge._daily_profit_reduced
        self._symbol_trade_history = self.challenge._symbol_trade_history
        self.global_cooldown_until = None  # trading control, not challenge tracking
        # 🐛 FIX 10 Août 2026 (Bug #4): Mémorise le palier de circuit breaker déjà servi.
        # Sans lui, l'escalade 3→5→10 ne s'accumule jamais : chaque palier resetait
        # consecutive_losses à 0 à l'expiration du cooldown, donc le HARD STOP à 10
        # pertes n'était JAMAIS atteint. Ce compteur ne descend QUE sur une victoire.
        self._circuit_stage_served = 0  # 0=none, 1=SOFT, 2=AUTO_PAUSE, 3=HARD_STOP

        # 🔧 FIX 22 Juillet 2026: Mode conservation — activé quand le challenge
        # FTMO est mathématiquement impossible à atteindre. Réduit les risques
        # et filtre les signaux faibles pour protéger le capital.
        self._conservation_mode = False
        self._conservation_mode_logged = False  # log unique pour éviter le spam

        # ── Position tracking (still in FTMOProtector) ───────────────
        # 🔧 FIX 16 Juillet 2026: Shared RLock protège les 6 dicts partagés
        # entre FTMOProtector et Trailer contre les race conditions.
        # Même si le robot est monothreadé, le ThreadPoolExecutor de MT5
        # peut causer des entrelacements lors des callbacks timeout.
        self._shared_lock = threading.RLock()
        self.position_open_times = {}
        self.partial_closed = set()
        self.peak_profit = {}
        self.trailing_peaks = {}
        self.position_regime = {}
        self.position_meta = {}
        self._time_stop_cooldown = {}
        self._atr_cache = {}
        self._rates_cache = {}

        # ── Correlation ──────────────────────────────────────────────
        self._position_cache_ttl = 60
        self._last_position_fetch = 0.0

        # ── ADX market filter ────────────────────────────────────────
        self._adx_cache_ts = 0.0
        self._adx_cache_mult = 1.0
        self._adx_cache_ttl = 900

        # ── Profile cache (for _get_profile) ─────────────────────────
        self._profile_cache = {}

        # ── Auto-stop (ranging market) ──────────────────────────────
        self._auto_stop_paused = False
        self._auto_stop_until = None

        # ── Trailer (delegated) ──────────────────────────────────────
        self.trailer = Trailer(mt5, config, shared_lock=self._shared_lock)
        self.trailer.partial_closed = self.partial_closed
        self.trailer.trailing_peaks = self.trailing_peaks

        # ── SignalValidator (validation des signaux) ──────────────────
        self.signal_validator = SignalValidator(
            mt5=mt5,
            trailer=self.trailer,
            symbol_limits=self.symbol_limits,
            symbol_trade_history=self._symbol_trade_history,
            staleness_check_fn=self.check_price_staleness,
        )
        self.trailer.position_regime = self.position_regime
        self.trailer.position_meta = self.position_meta
        self.trailer.position_open_times = self.position_open_times
        self.trailer.peak_profit = self.peak_profit

    def check_price_staleness(self, symbol: str, max_age: int = 60) -> bool:
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return False
        tick_time = getattr(tick, "time", None)
        if tick_time is None:
            return False
        try:
            age = time.time() - float(tick_time)
        except (TypeError, ValueError):
            # Si on ne peut pas déterminer l'âge, CONSERVATEUR : considérer comme stale
            return False
        if age > max_age:
            logger.warning(f"  [STALE] {symbol}: tick age={age:.0f}s > {max_age}s")
            return False
        return True

    def _reconcile_positions(self, positions: Any) -> None:
        open_tickets = {str(p.ticket) for p in positions}
        for p in positions:
            ticket_key = str(p.ticket)
            if ticket_key not in self.position_open_times:
                raw = getattr(p, "time", None)
                if raw is None:
                    open_time = datetime.utcnow()
                elif isinstance(raw, (int, float)):
                    open_time = datetime.utcfromtimestamp(raw)
                else:
                    open_time = raw
                self.position_open_times[ticket_key] = {"open_time": open_time, "symbol": p.symbol}
            if ticket_key not in self.position_regime:
                comment = getattr(p, "comment", "") or ""
                self._parse_comment_regime(comment, ticket_key)
        # Nettoyer les entrées obsolètes (positions fermées)
        for t in list(self.trailing_peaks.keys()):
            if t not in open_tickets:
                del self.trailing_peaks[t]
        for t in list(self.position_open_times.keys()):
            if t not in open_tickets:
                del self.position_open_times[t]
        for t in list(self.position_regime.keys()):
            if t not in open_tickets:
                del self.position_regime[t]
        for t in list(self.peak_profit.keys()):
            if t not in open_tickets:
                del self.peak_profit[t]
        self.partial_closed &= open_tickets

    def _parse_comment_regime(self, comment: str, ticket_key: str) -> None:
        m = re.match(r"ADAPT_(\w{3})", comment)
        short = m.group(1) if m else "RAN"
        self.position_regime[ticket_key] = self.REGIME_FROM_COMMENT.get(short, "RANGING")

    # Corrélations RÉACTIVÉES — 25 Juin 2026 (Risk & Compliance Officer)
    # Vérifications de corrélation via portfolio_controller.py (groupes de corrélation).
    # Max 3 trades/groupe, max 2/direction. Limite les pertes simultanées.

    def _get_profile(self, symbol: str) -> Any:
        """Retourne le profil institutionnel du symbole (caché)."""
        if symbol not in self._profile_cache:
            try:
                from engine_simple.symbol_profile import get_profile

                self._profile_cache[symbol] = get_profile(symbol)
            except (ImportError, RuntimeError, KeyError):
                self._profile_cache[symbol] = None
        return self._profile_cache[symbol]

    def can_trade(
        self,
        symbol: str,
        signal: Optional[dict[str, Any]] = None,
        positions: Any = None,
        check_danger_hours: bool = True,
    ) -> tuple[bool, str | None]:
        """Vérifie si un trade est autorisé pour le symbole.

        Pipeline de vérification (chaque étape peut bloquer) :
          0. _check_auto_stop         — pause auto-ranging (MarketMemory)
          1. _check_symbol_health    — auto-disable si WR < 20%
          2. _check_spread           — spread > max ou > 10% ATR
          3. _check_global_cooldown  — pause 30min après N pertes consécutives
          4. _check_daily_limits     — max trades/jour (ouverts + fermés)
          5. _check_signal_valid     — direction, corrélation, score, SL/TP
          6. _check_risk_state       — volatility spike, DD, daily loss zones, auto-pause
          7. _check_profile          — ranging restriction, DL required, ATR scaling
          8. _check_session          — news, danger hours, session, preferred hours, weekend
          9. _check_ftmo_status      — expiry, profit target, consistency

        Args:
            symbol: symbole à trader
            signal: dict du signal MOM20x3 (optionnel, requis pour bypass DANGER_HOURS)
            positions: liste des positions actuelles (optionnel)
            check_danger_hours: False pour la pré-vérification sans signal (main.py première passe)
        """
        # _reset_daily() est appelé dans _scan_signals() (main.py) — une fois par cycle

        for check in (
            lambda: self._check_auto_stop(),
            lambda: self._check_symbol_health(symbol, signal),
            lambda: self._check_spread(symbol),
            lambda: self._check_global_cooldown(),
            lambda: self._check_symbol_daily_loss(symbol),  # 🔧 FIX_SUPREME_COUNCIL
            lambda: self._check_daily_limits(),
            lambda: self._check_signal_valid(symbol, signal, positions),
            lambda: self._check_risk_state(symbol, signal),
            lambda: self._check_profile(symbol, signal),
            lambda: self._check_session(symbol, signal, check_danger_hours),
            lambda: self._check_fomc_protection(symbol, signal),  # 🔒 Ajouté 28 Juillet 2026
            lambda: self._check_directional_imbalance(symbol, signal),  # 🔒 Ajouté 28 Juillet 2026
            lambda: (
                self._check_consistency_cap()
            ),  # 🔧 Réactivé 16 Juillet 2026 — FIX: guard `positive_days < 2` au lieu de `len(...) < 2`
            lambda: self._check_conservation_mode(symbol, signal),  # 🔧 FIX 22 Juillet 2026
            lambda: self._check_ftmo_status(),
        ):
            ok, reason = check()
            if not ok:
                return ok, reason

        return True, "OK"

    # ── Sub-checks (extraites de can_trade pour lisibilité) ─────────

    def _check_auto_stop(self) -> tuple[bool, str | None]:
        """🔒 AUTO-STOP : pause auto-ranging basée sur ADX moyen des symboles.

        🐛 FIX #9 (3 Juillet): Si RATIO_STOP >= 1.0, AUTO_STOP est désactivé
        par l'utilisateur (ne veut plus de pause). On court-circuite directement
        pour éviter d'appeler decision() à chaque cycle (code mort).
        """
        try:
            from engine_simple.auto_stop import RATIO_STOP

            # Désactivé par l'utilisateur (FIX #6, 2 Juillet)
            if RATIO_STOP >= 1.0:
                self._auto_stop_paused = False
                self._auto_stop_until = None
                return True, None

            from engine_simple.auto_stop import decision

            # 🐛 FIX 26 Juin 2026: passer self.mt5 au lieu de laisser auto_stop
            # créer sa propre connexion (MT5Connector() sans arguments plantait)
            verdict, state = decision(mt5_connector=self.mt5)
            if verdict == "STOP":
                self._auto_stop_paused = True
                self._auto_stop_until = state.get("auto_paused_until")
                return False, f"AUTO_STOP: Trading paused (ranging market, until {self._auto_stop_until})"
            elif verdict == "RESUME":
                self._auto_stop_paused = False
                self._auto_stop_until = None
            elif verdict == "WAIT" and self._auto_stop_paused:
                # 🐛 FIX 26 Juin 2026: utiliser state.get() au lieu de self._auto_stop_until
                # car ce dernier n'est pas mis à jour par les prolongations de pause (15min).
                actual_until = state.get("auto_paused_until", self._auto_stop_until)
                self._auto_stop_until = actual_until  # sync pour prochains cycles
                return False, f"AUTO_STOP: Still paused until {actual_until}"
        except ImportError:
            pass  # auto_stop module non disponible
        except Exception as e:
            logger.debug(f"  [AUTO-STOP] erreur: {e}")
        return True, None

    def _check_symbol_health(self, symbol: str, signal: Optional[dict[str, Any]]) -> tuple[bool, str | None]:
        """🔒 AUTO-DISABLE : symbole avec WR < 20% sur les 20 derniers trades."""
        if signal is None:
            return True, None
        sym_history = self._symbol_trade_history.get(symbol, [])
        last20 = [t for t in sym_history[-20:] if t.get("profit", 0) != 0]
        if len(last20) >= 10:
            wins = sum(1 for t in last20 if t["profit"] > 0)
            wr = wins / len(last20)
            if wr < DD_AUTODISABLE_THRESHOLD:
                return False, (
                    f"[AUTO-DISABLE] {symbol} WR={wr:.0%} sur {len(last20)} trades "
                    f"< {self._symbol_auto_disable_wr_threshold:.0%}"
                )
        return True, None

    def _check_spread(self, symbol: str) -> tuple[bool, str | None]:
        """Spread check — points absolus + ratio ATR (max 10% de l'ATR)."""
        info = self.mt5.get_symbol_info(symbol)
        if not (info and hasattr(info, "point") and info.point > 0):
            return False, f"Cannot get symbol info for {symbol}"
        tick = self.mt5.get_tick(symbol)
        if tick is None:
            return False, f"No tick data for {symbol} — spread check impossible"
        spread = tick.ask - tick.bid
        sym_cfg = self.symbol_limits.get(symbol, {})
        max_sp = sym_cfg.get("max_spread_points", self.max_spread_points)
        spread_pts_ok = spread < max_sp * info.point * 1.05
        atr_val = self.trailer._get_atr(symbol)
        atr_ok = True
        if atr_val and atr_val > 0:
            # 🐛 FIX #16 (3 Juillet): Seuil ATR configurable par symbole
            # EURGBP: ATR très bas → spread 0.5pip = 12.9% ATR → dépassait le 10% fixe
            max_atr_ratio = sym_cfg.get(
                "max_spread_atr_ratio", 0.15
            )  # 🔧 Global default 15% (was 10% — trop strict pour crosses)
            if spread / atr_val > max_atr_ratio:
                atr_ok = False
        if not spread_pts_ok or not atr_ok:
            return False, (
                f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f}, ATR ratio={spread / atr_val:.1%})"
                if atr_val
                else f"Spread too high: {spread:.5f} (limit={max_sp * info.point:.5f})"
            )
        return True, None

    def _check_global_cooldown(self) -> tuple[bool, str | None]:
        """🔒 Global cooldown: pause après AUTO_PAUSE_LOSSES pertes consécutives."""
        if self.global_cooldown_until is None:
            return True, None
        now = datetime.utcnow()
        if now < self.global_cooldown_until:
            remaining = int((self.global_cooldown_until - now).total_seconds() // 60)
            return False, f"Global cooldown: {remaining}min (after {self.consecutive_losses} consecutive losses)"
        # 🐛 FIX 10 Août 2026 (Bug #4): Cooldown expiré → on vide le cooldown MAIS
        # on NE RESET PLUS consecutive_losses à 0. Ce reset (avec celui de chaque
        # palier) empêchait l'escalade 3→5→10 du circuit breaker : le compteur
        # repartait à 0 à chaque expiration, donc le HARD STOP à 10 pertes n'était
        # jamais atteint. Le compteur ne descend QUE sur une victoire.
        self.global_cooldown_until = None
        return True, None

    def _check_daily_limits(self) -> tuple[bool, str | None]:
        """Max trades/jour (fermés + ouverts)."""
        max_trades = self.config.get("MAX_TRADES_PER_DAY", 200)
        if self.daily_stats["trades"] >= max_trades:
            return False, f"Daily trade limit (closed: {self.daily_stats['trades']}/{max_trades})"
        if self._opened_today >= max_trades:
            logger.debug(
                f"_check_daily_limits: _opened_today={self._opened_today} (bloque {max_trades}) — daily_stats={self.daily_stats}"
            )
            return False, f"Daily trade limit (opened: {self._opened_today}/{max_trades})"
        return True, None

    # 🔧 FIX_SUPREME_COUNCIL 2 Juillet 2026: Per-Symbol Daily Loss Limit
    def _check_symbol_daily_loss(self, symbol: str) -> tuple[bool, str | None]:
        """Bloque un symbole si sa perte quotidienne dépasse le seuil (0.5% du capital par défaut).
        Les autres symboles ne sont pas affectés.

        🐛 FIX #8 (3 Juillet): Seuil en % du capital au lieu de $200 dur.
        """
        daily_pnl = self._symbol_daily_pnl.get(symbol, 0)
        symbol_loss_pct = self.config.get("SYMBOL_DAILY_LOSS_PCT", 0.005)
        symbol_loss_limit = self.initial_balance * symbol_loss_pct
        symbol_warn_pct = self.config.get("SYMBOL_DAILY_WARN_PCT", 0.0025)
        symbol_warn_limit = self.initial_balance * symbol_warn_pct
        if daily_pnl < -symbol_loss_limit:
            return False, (
                f"{symbol}: Perte quotidienne ${daily_pnl:.0f} < -${symbol_loss_limit:.0f} "
                f"({symbol_loss_pct:.1%}) → bloque pour aujourd'hui"
            )
        if daily_pnl < -symbol_warn_limit:
            logger.warning(
                f"  [SYMBOL DAILY LOSS] {symbol}: ${daily_pnl:.0f} aujourd'hui "
                f"(alerte à -${symbol_warn_limit:.0f}, limite à -${symbol_loss_limit:.0f})"
            )
        return True, None

    def _check_signal_valid(
        self, symbol: str, signal: Optional[dict[str, Any]], positions: Any
    ) -> tuple[bool, str | None]:
        """Direction restrictions, corrélation, score minimum, SL/TP obligatoire.

        Délégué à SignalValidator (signal_validator.py).
        """
        return self.signal_validator.check(symbol, signal, positions)

    def _check_risk_state(self, symbol: str, signal: Optional[dict[str, Any]]) -> tuple[bool, str | None]:
        """Volatility spike, DD circuit breaker, daily loss zones, auto-pause, cooldown."""
        # Volatility spike
        atr_pct = signal.get("atr_pct", 0) if signal else 0
        if atr_pct > 0:
            atr_median = signal.get("atr_median_14", atr_pct)
            if atr_median > 0 and atr_pct / atr_median > 3.0:
                return False, f"Volatility spike: ATR%={atr_pct:.3f} vs median={atr_median:.3f} (>3x)"

        # Account info
        account = self.mt5.get_account_info()
        if account is None:
            return False, "Cannot get account info"
        current_equity = account.equity

        # Daily profit limit → risk reduction mode
        daily_equity_change = current_equity - self.daily_start_equity
        daily_pnl_pct = daily_equity_change / max(self.initial_balance, 1)
        profit_limit = self.config.get("DAILY_PROFIT_LIMIT_PCT", 0.008)
        if daily_pnl_pct >= profit_limit:
            self._daily_profit_reduced = True
            logger.info(
                f"  [PROFIT LIMIT] daily PnL ${self.daily_stats['pnl']:.0f} "
                f"({daily_pnl_pct:.3%}) >= {profit_limit:.3%} — risk reduit a 25%"
            )
        else:
            self._daily_profit_reduced = False
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self.challenge.peak_equity = current_equity  # Sync challenge tracker

        # Daily loss flag
        self._check_daily_loss_limit(symbol=symbol)

        # DD from peak
        dd_peak = (self.peak_equity - current_equity) / max(self.peak_equity, 1)

        # Circuit breaker progressif (3 niveaux)
        cb_threshold = self.config.get("CIRCUIT_BREAKER_DD_PCT", 0.08)
        sym_cfg_cb = self.symbol_limits.get(symbol, {})
        sym_cb_override = sym_cfg_cb.get("circuit_breaker_dd_pct_override")
        if sym_cb_override is not None:
            cb_threshold = sym_cb_override
        # Niveau 1: DD > seuil_warn → risk reduction 50% (toutes directions)
        # seuil = 75% du circuit_breaker threshold (ex: cb=8% → warn=6%)
        dd_warn = cb_threshold * 0.75
        if dd_peak > dd_warn and dd_peak <= cb_threshold:
            if signal:
                signal["risk_mult"] = signal.get("risk_mult", 1.0) * 0.5
        # Niveau 2: DD > seuil cb → TOUS les trades bloqués
        if dd_peak > cb_threshold:
            return False, f"Circuit breaker: DD {dd_peak:.1%} > {cb_threshold:.0%}, all trades blocked"

        # FTMO daily loss limit
        daily_loss = max(0, -daily_equity_change) / self.initial_balance
        if self._daily_loss_violated:
            return False, f"FTMO daily loss limit: {daily_loss:.1%}"

        # Zone 3: >1.5% daily loss → STOP TOTAL (global_cooldown)
        zone3 = self.config.get("ZONE3_LOSS_PCT", 0.015)
        # 🐛 FIX C2: Supprimé `and self.daily_stats["losses"] > 0` qui bypassait
        # le hard stop quand une seule position ouverte faisait perdre 2%+ (gap/news)
        # sans avoir encore de `losses` comptabilisé (losses incrémenté APRÈS fermeture).
        if daily_loss >= zone3:
            # 🔧 13 Juillet 2026: Global cooldown jusqu'à la fin de la journée
            now = datetime.utcnow()
            end_of_day = now.replace(hour=23, minute=59, second=59)
            remaining = (end_of_day - now).total_seconds() / 60
            self.global_cooldown_until = now + timedelta(minutes=max(remaining, 1))
            logger.warning(
                f"  🛑 HARD STOP: daily loss {daily_loss:.2%} >= {zone3:.1%}, "
                f"cooldown until {self.global_cooldown_until.strftime('%H:%M')} (fin de journée)"
            )
            return False, f"ZONE 3: daily DD {daily_loss:.1%} >= {zone3:.1%}, STOP total"

        # Per-symbol auto-pause progressif après pertes consécutives
        # 🔧 RECALIBRATION 31 Juillet 2026: STOP → réduction progressive
        #   - 3 pertes: soft cooldown 5min, lot réduit (via _adaptive_lot_mult)
        #   - 5 pertes: cooldown 15min, lot fortement réduit
        #   - 10 pertes: hard stop 60min (circuit breaker final)
        from engine_simple.strategy import get_symbol_full_config as _get_sym_full_cfg

        sym_full_cfg = _get_sym_full_cfg(symbol)
        global_auto_pause = self.config.get("auto_pause_losses", self.config.get("AUTO_PAUSE_LOSSES"))
        sym_auto_pause = (
            global_auto_pause if global_auto_pause is not None else sym_full_cfg.get("auto_pause_losses", 5)
        )
        global_cooldown = self.config.get("cooldown_minutes", self.config.get("COOLDOWN_MINUTES"))
        sym_cooldown_minutes = (
            global_cooldown if global_cooldown is not None else sym_full_cfg.get("cooldown_minutes", 15)
        )
        # 🔧 31 Juillet 2026: Palier progressif — court cooldown à 3 pertes, stop dur à 10+
        # 🐛 FIX 10 Août 2026 (Bug #4): L'escalade 3→5→10 ne s'accumulait JAMAIS car
        # chaque palier resetait consecutive_losses à 0 à l'expiration de son cooldown.
        # Conséquence : le HARD STOP à 10 pertes n'était jamais atteint (le compteur
        # repartait à zéro après chaque pause). Correction : le compteur ne descend
        # QUE sur une victoire (record_trade_result). On mémorise le palier déjà servi
        # (_circuit_stage_served) pour ne pas re-déclencher le même palier en boucle,
        # et on n'escalade que lorsque consecutive_losses franchit le seuil supérieur.
        consec = self.consecutive_losses
        now = datetime.utcnow()

        # Déterminer le palier courant selon le nombre de pertes consécutives
        if consec >= 10:
            stage = 3
            stage_cooldown = max(60, sym_cooldown_minutes * 4)
        elif consec >= sym_auto_pause:
            stage = 2
            stage_cooldown = sym_cooldown_minutes
        elif consec >= 3:
            stage = 1
            stage_cooldown = min(5, sym_cooldown_minutes)
        else:
            stage = 0
            stage_cooldown = 0

        if stage > 0:
            # Un cooldown global est déjà actif (ex: ZONE 3, autre symbole) → bloquer
            if self.global_cooldown_until is not None and now < self.global_cooldown_until:
                remaining = int((self.global_cooldown_until - now).total_seconds() // 60)
                return False, f"Cooldown global: {remaining}min restantes ({consec} pertes)"
            # Nouveau palier atteint (escalade) → armer le cooldown de CE palier
            if stage > self._circuit_stage_served:
                self._circuit_stage_served = stage
                self.global_cooldown_until = now + timedelta(minutes=stage_cooldown)
                if stage == 3:
                    logger.critical(
                        f"HARD STOP ({symbol}): {consec} pertes consécutives! "
                        f"Cooldown {stage_cooldown}min — vérifier le marché"
                    )
                elif stage == 2:
                    logger.warning(
                        f"AUTO PAUSE ({symbol}): {consec} pertes >= {sym_auto_pause}, "
                        f"cooldown {stage_cooldown}min (lot réduit 50%)"
                    )
                else:
                    logger.info(
                        f"SOFT PAUSE ({symbol}): {consec} pertes, cooldown {stage_cooldown}min (réduction lot active)"
                    )
                return False, f"Cooldown: {stage_cooldown}min ({consec} pertes consécutives)"
            # Palier déjà servi + cooldown expiré → on autorise le trading
            # (le compteur consecutive_losses EST CONSERVÉ pour l'escalade :
            # une nouvelle perte le fera grimper vers le palier supérieur)
            self.global_cooldown_until = None

        # Per-symbol cooldown
        if symbol in self.cooldowns and datetime.utcnow() < self.cooldowns[symbol]:
            remaining = (self.cooldowns[symbol] - datetime.utcnow()).seconds // 60
            return False, f"Cooldown: {remaining}min"

        return True, None

    def _check_profile(self, symbol: str, signal: Optional[dict[str, Any]]) -> tuple[bool, str | None]:
        """Institutional profile: ranging restriction, DL required, ATR scaling."""
        if signal is None:
            return True, None
        profile = self._get_profile(symbol)
        if not profile:
            return True, None
        sym_cfg = self.symbol_limits.get(symbol, {})

        # Ranging restriction
        if signal.get("_is_ranging", True) is not False:
            allow_ranging = sym_cfg.get("allow_ranging")
            if allow_ranging is False and signal.get("_regime") == "RANGING":
                return False, f"{symbol}: ranging trades not allowed (per-symbol config)"

        # DL required
        if sym_cfg.get("dl_required", False) and signal.get("_ml_agrees") is not True:
            return False, f"{symbol}: DL agreement required but not confirmed"

        # Per-symbol max daily trades
        max_daily = sym_cfg.get("max_daily_trades")
        if max_daily is not None:
            sym_daily = self._daily_trades_per_symbol.get(symbol, 0)
            if sym_daily >= max_daily:
                return False, f"{symbol}: daily limit ({max_daily})"

        # Profile-based ATR validation
        atr_val = signal.get("atr", 0)
        if atr_val > 0:
            from engine_simple.symbol_profile import get_atr_scaling

            scaling = get_atr_scaling(symbol, atr_val)
            if scaling < 0.75:
                return False, f"{symbol}: ATR {atr_val:.5f} trop eleve pour profil"

        return True, None

    def _check_session(
        self, symbol: str, signal: Optional[dict[str, Any]], check_danger_hours: bool
    ) -> tuple[bool, str | None]:
        """News, danger hours, session block, preferred hours, weekend block."""
        # News filter — returns (blocked: bool, reason: str)
        news_blocked, news_reason = is_news_blocked(symbol=symbol)
        if news_blocked:
            return False, f"News: {news_reason}"

        utc_hour = datetime.utcnow().hour

        # 🔧 FIX #5: DANGER_HOURS — PLUS AUCUN BYPASS
        # Le bypass par score≥0.80+ADX≥15 est supprimé.
        # Les heures dangereuses (WR historique 0-35%) bloquent TOUS les trades.
        danger_hours = self.config.get("DANGER_HOURS", [])
        if utc_hour in danger_hours and check_danger_hours:
            return False, f"Danger hour: {utc_hour}h UTC (0% WR historique sur ce créneau — bypass supprimé)"

        # Session block
        start_hour = self.config.get("TRADING_START_HOUR", 0)
        end_hour = self.config.get("TRADING_END_HOUR", 24)
        if not (start_hour <= utc_hour < end_hour):
            return False, f"Session block: {utc_hour}h UTC (trade only {start_hour}-{end_hour}h UTC)"

        # Per-symbol preferred hours
        if signal is not None:
            pref_hours = self.symbol_limits.get(symbol, {}).get("preferred_hours")
            if pref_hours is not None and len(pref_hours) > 0 and utc_hour not in pref_hours:
                return False, f"{symbol}: not in preferred hours {pref_hours}h UTC"
            elif pref_hours is not None and len(pref_hours) == 0:
                return False, f"{symbol}: preferred_hours empty — trading bloqué"

        # Per-symbol weekend block (XAUUSD = 24/5, BTC/ETH = 24/7)
        weekend_ok = self.symbol_limits.get(symbol, {}).get("weekend_trading", True)
        if not weekend_ok and datetime.utcnow().weekday() >= 5:
            return False, f"{symbol}: weekend block (24/5 — pas de trading samedi/dimanche)"

        return True, None

    def _check_consistency_cap(self) -> tuple[bool, str | None]:
        """🔒 CONSISTENCY CAP: PRÉVENTIF — Réactivé 16 Juillet 2026.
        Bloque les trades si le PnL du jour dépasse déjà le seuil de consistance FTMO
        (30% du total des jours positifs).

        Règle FTMO : best_day ≤ 30% × sum(jours positifs)
        Si aujourd'hui dépasse déjà ce seuil, tout trade supplémentaire
        (même gagnant) ne ferait qu'aggraver la situation.

        🔧 FIX 16 Juillet 2026:
        - Ancien guard: `len(self.daily_pnl_by_date) < 2` (vérifiait TOUS les jours, même négatifs)
        - Nouveau guard: `positive_days < 2` (ne compte que les jours AVEC PnL positif)
        - Problème: si 1 seul jour positif = aujourd'hui, ratio = 100% → toujours bloqué.

        🔧 FIX 22 Juillet 2026:
        - Ajout guard `total_positive < MIN_POSITIVE_FOR_CONSISTENCY` ($100)
        - Évite le deadlock quand le PnL total positif est négligeable (< 0.05% du compte)
        - FTMO conçoit la règle de consistance pour des profits significatifs
        """
        total_positive = sum(v for v in self.daily_pnl_by_date.values() if v > 0)
        positive_days = sum(1 for v in self.daily_pnl_by_date.values() if v > 0)
        if positive_days < 2 or total_positive <= 0:
            return True, None

        # 🛡️ Guard: PnL positif total trop faible → consistency cap non applicable
        # FTMO conçoit cette règle pour des profits significatifs. Avec < $100,
        # tout jour positif dépasse mathématiquement 30%, créant un deadlock.
        MIN_POSITIVE_FOR_CONSISTENCY = 100  # $100 = 0.05% d'un compte $200k
        if total_positive < MIN_POSITIVE_FOR_CONSISTENCY:
            logger.debug(
                f"  [CONSISTENCY SKIP] total_positive=${total_positive:.0f} < "
                f"${MIN_POSITIVE_FOR_CONSISTENCY} — PnL trop faible, cap désactivé"
            )
            return True, None

        today = datetime.utcnow().date()
        today_pnl = self.daily_pnl_by_date.get(today, 0)

        if today_pnl <= 0:
            return True, None

        ratio = today_pnl / total_positive
        if ratio >= self.consistency_max_pct:
            return False, (
                f"Consistency cap: today PnL ${today_pnl:.0f} = "
                f"{ratio:.1%} du total positif ${total_positive:.0f} "
                f">= {self.consistency_max_pct:.0%} — trades bloqués préventivement"
            )

        # Alerte si proche du seuil (> 75% du max)
        if ratio >= self.consistency_max_pct * 0.75:
            logger.warning(
                f"  [CONSISTENCY WARN] today PnL ${today_pnl:.0f} = {ratio:.1%} "
                f"du total positif (seuil à {self.consistency_max_pct:.0%})"
            )

        return True, None

    def _check_ftmo_status(self) -> tuple[bool, str | None]:
        """Challenge expiry, profit target, consistency."""
        # 🐛 FIX 16 Août 2026 (Audit A2): FAILED_DD / FAILED_EXPIRY / FAILED_CONSISTENCY
        # doivent bloquer définitivement can_trade. Avant ce fix, un challenge perdu
        # (DD dépassé) pouvait continuer à trader tant que le DD courant < seuil.
        if self.challenge_status in ("FAILED_DD", "FAILED_EXPIRY", "FAILED_CONSISTENCY", "PASSED"):
            return False, f"FTMO: challenge terminé ({self.challenge_status}) — trading bloqué"

        # Max trading days
        if self.max_trading_days > 0 and len(self.trading_days) >= self.max_trading_days:
            self.challenge_status = "FAILED_EXPIRY"
            return False, f"FTMO: maximum trading days ({self.max_trading_days}) atteint — challenge expiré"

        # Profit target (realized PnL only)
        current_pnl = sum(self.daily_pnl_by_date.values())
        profit_target_amount = self.initial_balance * self.profit_target_pct
        if current_pnl >= profit_target_amount:
            if self.consistency_violated:
                self.challenge_status = "FAILED_CONSISTENCY"
                return False, "FTMO FAILED: consistency violated (>30% daily)"
            if len(self.trading_days) < self.min_trading_days:
                return False, (
                    f"FTMO: target atteint (${current_pnl:.0f}) mais {len(self.trading_days)}/"
                    f"{self.min_trading_days} jours de trading"
                )
            self.challenge_status = "PASSED"
            return False, "FTMO PASSED: target + 10 days + consistency OK"

        # Consistency violated → stop avant target (80% seuil de sécurité)
        # Permet la dilution : le PnL total continue de croître, ce qui réduit
        # progressivement best_day_pct jusqu'à repasser sous 30%.
        if self.consistency_violated and current_pnl >= profit_target_amount * 0.8:
            self.challenge_status = "FAILED_CONSISTENCY"
            return False, "FTMO FAILED: consistency violated — stop avant target"

        return True, None

    # ── Conservation mode ─────────────────────────────────────────────────

    def _check_conservation_mode(self, symbol: str, signal: Optional[dict[str, Any]]) -> tuple[bool, str | None]:
        """🔒 MODE CONSERVATION : Quand le challenge FTMO est mathématiquement
        impossible à atteindre, le robot passe en mode conservation de capital.

        Conditions d'activation :
          1. Profit progress < 5% ET trading_days restants <= 3
          2. OU consistency déjà violée (le PASS est impossible)
          3. OU WR global < 40% sur les 50 derniers trades

        Effets :
          - risk_mult divisé par 2 sur le signal
          - Seuls les signaux avec confidence >= 0.80 passent
          - Log unique pour éviter le spam dans les logs
        """
        if signal is None:
            return True, None

        # 🔧 07 Août 2026 (mode preuve): flag configurable pour désactiver le
        # mode conservation. Quand il est désactivé, les trades passent même si
        # le challenge est mathématiquement perdu — nécessaire pour collecter
        # les 100+ trades de preuve (décision utilisateur).
        if not self.config.get("CONSERVATION_MODE_ENABLED", True):
            if self._conservation_mode:
                logger.info("  🛡️ MODE CONSERVATION DÉSACTIVÉ (config CONSERVATION_MODE_ENABLED=false)")
                self._conservation_mode = False
                self._conservation_mode_logged = False
            return True, None

        # Calculer l'état du challenge
        current_pnl = sum(self.daily_pnl_by_date.values())
        profit_progress = current_pnl / max(self.initial_balance * self.profit_target_pct, 1e-6)
        days_remaining = max(0, self.min_trading_days - len(self.trading_days))

        # WR global sur les 50 derniers trades
        recent = self._trade_history[-50:] if len(self._trade_history) >= 50 else self._trade_history
        global_wr = 0.0
        if len(recent) >= 10:
            wins = sum(1 for t in recent if t.get("profit", 0) > 0)
            global_wr = wins / len(recent)

        # Décision: activer le mode conservation ?
        # 🛡️ Guard: ne pas activer sans assez de trades (évite déclenchement sur robot frais/démo)
        # 🐛 FIX 29 Juillet 2026: WR catastrophique nécessite 100+ trades ET 5+ jours de trading
        # pour éviter activation prématurée (ex: WR=35% sur 145 trades mais seulement 2 jours)
        enough_trades = len(self._trade_history) >= 20
        enough_days = len(self.trading_days) >= 5  # Au moins 5 jours de trading
        enough_recent = len(recent) >= 100  # Échantillon significatif
        should_conserve = enough_trades and (
            (profit_progress < 0.05 and days_remaining <= 3)  # Trop peu de temps pour atteindre le target
            or (self.consistency_violated and profit_progress < 0.80)  # Consistency violée + pas encore au target
            or (global_wr < 0.40 and enough_recent and enough_days)  # WR catastrophique sur échantillon significatif
        )

        if should_conserve:
            self._conservation_mode = True
            if not self._conservation_mode_logged:
                logger.warning(
                    f"  🛡️ MODE CONSERVATION ACTIF : profit_progress={profit_progress:.1%}, "
                    f"days_remaining={days_remaining}, WR_50={global_wr:.0%}, "
                    f"consistency_violated={self.consistency_violated} "
                    f"— risque réduit 50%, seuls signaux haute confiance"
                )
                self._conservation_mode_logged = True

            # Effet 1: Réduire risk_mult de moitié
            current_rm = signal.get("risk_mult", 1.0)
            signal["risk_mult"] = current_rm * 0.50
            logger.debug(f"  [CONSERVATION] {symbol}: risk_mult {current_rm:.2f}→{signal['risk_mult']:.2f} (×0.50)")

            # Effet 2: Bloquer les signaux à faible confiance (< 0.80)
            sig_conf = signal.get("confidence", 0.0)
            if sig_conf < 0.80:
                return False, (
                    f"Conservation mode: {symbol} confidence={sig_conf:.2f} < 0.80, "
                    f"seuls signaux haute confiance acceptés"
                )
        else:
            if self._conservation_mode:
                logger.info("  🛡️ MODE CONSERVATION DÉSACTIVÉ — conditions améliorées")
                self._conservation_mode_logged = False
            self._conservation_mode = False

        return True, None

    # ── Directional imbalance protection ──────────────────────────────────

    def _check_directional_imbalance(self, symbol: str, signal: Optional[dict[str, Any]]) -> tuple[bool, str | None]:
        """🔒 DIRECTIONAL IMBALANCE : bloque trades si >70% des trades récents
        sont dans la même direction. Évite le biais directionnel mortel.

        Contexte : 28 Juillet 2026 — 174 SELL / 0 BUY signalés.
        Le marché est baissier et le robot capture bien cette tendance.
        MAIS un retournement (ex: FOMC 29 Juillet) détruirait le compte.
        Cette règle force un équilibre : impossible d'ouvrir >70% dans une seule direction.

        La règle s'applique au niveau du compte (tous symboles confondus).
        """
        if signal is None:
            return True, None
        if self._trade_history is None:
            return True, None

        signal_action = signal.get("action", "")
        if signal_action not in ("BUY", "SELL"):
            return True, None

        # 🐛 FIX 10 Août 2026 (Bug #6): Ne pas appliquer la règle aux symboles
        # UNIDIRECTIONNELS (allow_shorts=false en MODE PREUVE strict BUY-only).
        # Avec le stockage de la direction désormais actif, un symbole 100% BUY
        # (biais VOLONTAIRE et configuré, pas accidentel) aurait un buy_ratio=100%
        # > 70% → la règle bloquerait la SEULE direction possible → robot figé.
        # La règle ne protège que lorsque les DEUX directions sont possibles.
        sym_cfg = self.symbol_limits.get(symbol, {})
        allows_shorts = sym_cfg.get("allow_shorts", True)
        allows_buys = sym_cfg.get("allow_buys", True)
        if not allows_shorts or not allows_buys:
            return True, None  # symbole unidirectionnel → règle sans objet

        # Analyser les N derniers trades fermés
        recent = self._trade_history[-30:] if len(self._trade_history) >= 30 else self._trade_history
        if len(recent) < 10:
            return True, None  # pas assez de données

        buys = sum(1 for t in recent if t.get("action") == "BUY" or t.get("direction") == "BUY")
        sells = sum(1 for t in recent if t.get("action") == "SELL" or t.get("direction") == "SELL")
        total = buys + sells
        if total < 10:
            return True, None

        buy_ratio = buys / total
        sell_ratio = sells / total

        # Si > 70% dans une direction, on bloque les trades dans CETTE direction
        imbalance_threshold = 0.70
        if buy_ratio > imbalance_threshold and signal_action == "BUY":
            return False, (
                f"Directional imbalance: {buys}/{total} BUY ({buy_ratio:.0%}) "
                f"> {imbalance_threshold:.0%} — BUY bloqué préventivement"
            )
        if sell_ratio > imbalance_threshold and signal_action == "SELL":
            return False, (
                f"Directional imbalance: {sells}/{total} SELL ({sell_ratio:.0%}) "
                f"> {imbalance_threshold:.0%} — SELL bloqué préventivement"
            )

        # Alerte si proche du seuil (> 60%)
        warn_threshold = 0.60
        if buy_ratio > warn_threshold and signal_action == "BUY":
            logger.warning(
                f"  [DIR IMBALANCE WARN] {buys}/{total} BUY ({buy_ratio:.0%}) "
                f"— approche seuil {imbalance_threshold:.0%}"
            )
        if sell_ratio > warn_threshold and signal_action == "SELL":
            logger.warning(
                f"  [DIR IMBALANCE WARN] {sells}/{total} SELL ({sell_ratio:.0%}) "
                f"— approche seuil {imbalance_threshold:.0%}"
            )

        return True, None

    # ── FOMC / Macro event protection ────────────────────────────────────

    def _check_fomc_protection(self, symbol: str, signal: Optional[dict[str, Any]]) -> tuple[bool, str | None]:
        """🔒 FOMC PROTECTION : avant une décision FOMC, on réduit le risque.

        Contexte : FOMC 29 Juillet 2026. Le robot est 100% SELL (biais baissier).
        Une surprise hawkish/dovish peut causer un retournement violent.
        On force une réduction du risque 24h avant + blocage 2h avant.

        Fonctionnement :
        - J-1 (28 Juillet) : risk_mult × 0.5, alerte
        - J (29 Juillet, 2h avant) : tous trades bloqués
        - J (après annonce) : retour à la normale après 30min
        """
        if signal is None:
            return True, None

        # Dates FOMC 2026 (source: Federal Reserve calendar)
        # Prochaines dates: 29 Juillet 2026, puis septembre, novembre, décembre
        FOMC_DATES_2026 = [
            (2026, 1, 28),  # déjà passé
            (2026, 3, 18),  # déjà passé
            (2026, 5, 6),  # déjà passé
            (2026, 6, 17),  # déjà passé
            (2026, 7, 29),  # ⚠️ PROCHAIN — dans 1 jour
            (2026, 9, 16),
            (2026, 11, 4),
            (2026, 12, 16),
        ]

        now = datetime.utcnow()
        today = now.date()
        current_hour = now.hour

        for year, month, day in FOMC_DATES_2026:
            fomc_date = datetime(year, month, day).date()
            days_until = (fomc_date - today).days

            # FOMC day: blocage 2h avant (FOMC annonce à 19:00 UTC)
            if days_until == 0:
                fomc_hour = 19  # 19:00 UTC = 14:00 ET
                if current_hour >= fomc_hour - 2 and current_hour < fomc_hour + 1:
                    return False, (
                        f"FOMC PROTECTION: décision FOMC aujourd'hui à {fomc_hour}:00 UTC"
                        f" — trades bloqués de {fomc_hour - 2}h à {fomc_hour + 1}h UTC"
                    )
                # Après l'annonce : 30min de calme
                if current_hour >= fomc_hour + 1 and current_hour < fomc_hour + 1.5:
                    return False, (f"FOMC PROTECTION: post-FOMC calm period (30min après annonce)")

            # J-1 : risk réduit de moitié
            if days_until == 1:
                current_rm = signal.get("risk_mult", 1.0)
                signal["risk_mult"] = current_rm * 0.50
                logger.warning(
                    f"  [FOMC PROTECTION] FOMC demain ({fomc_date}) — "
                    f"risk_mult {current_rm:.2f}→{signal['risk_mult']:.2f} (×0.50)"
                )

        return True, None

    def reset_challenge(self, new_initial_balance: Optional[float] = None) -> None:
        """Reset challenge state. Delegates to ChallengeTracker."""
        self.challenge.reset_challenge(new_initial_balance)
        # Sync aliases
        self.peak_equity = self.challenge.peak_equity
        self.daily_start_equity = self.challenge.daily_start_equity
        self.consecutive_losses = self.challenge.consecutive_losses
        self.challenge_status = self.challenge.challenge_status
        # 🐛 FIX 10 Août 2026 (Bug #4): Un reset de challenge remet aussi l'escalade
        # du circuit breaker à zéro (nouveau départ, compteur de pertes réinitialisé).
        self._circuit_stage_served = 0

    def _adaptive_lot_mult(self) -> float:
        """Multiplicateur adaptatif (0.30-1.0) basé sur performance récente.
        Augmente les lots quand le robot performe bien, les réduit en cas de difficulté.
        """
        mult = 1.0

        # 1. Win rate récent (max 20 derniers trades)
        recent = self._trade_history[-20:] if len(self._trade_history) >= 20 else self._trade_history
        if len(recent) >= 5:
            wins = sum(1 for t in recent if t.get("profit", 0) > 0)
            wr = wins / len(recent)
            if wr > 0.65:
                mult *= 1.25  # Bon WR → lots +25%
            elif wr > 0.55:
                mult *= 1.05  # WR correct → lots +5%
            elif wr < 0.40:
                mult *= 0.70  # Mauvais WR → lots -30%
            elif wr < 0.50:
                mult *= 0.85  # WR médiocre → lots -15%

        # 2. Drawdown progressif
        account = self.mt5.get_account_info()
        if account:
            dd = (self.peak_equity - account.equity) / max(self.peak_equity, 1)
            if dd > 0.07:
                mult *= 0.50  # DD > 7% → -50% (fix P4: était 0.80, trop permissif)
            elif dd > 0.05:
                mult *= 0.75  # DD > 5% → -25%
            elif dd > 0.03:
                mult *= 0.90  # DD > 3% → -10%

        # 3. Pertes consécutives — réduction progressive (RECALIBRATION 31 Juillet 2026)
        # 🔧 31 Juillet 2026: Plus de paliers pour réduire le lot en douceur
        # avant d'atteindre le cooldown. Évite le STOP brutal.
        consec = self.consecutive_losses
        if consec >= 7:
            mult *= 0.25  # 7+ pertes → risque quasi nul, cooldown imminent
        elif consec >= 5:
            mult *= 0.40  # 5 pertes → pause imminente, lot fortement réduit
        elif consec >= 4:
            mult *= 0.60  # 4 pertes → réduction sérieuse
        elif consec >= 3:
            mult *= 0.70  # 3 pertes → alerte renforcée
        elif consec >= 2:
            mult *= 0.80  # 2 pertes → pré-alerte, réduction légère

        # 4. Challenge progress (confiance croissante)
        report = self.get_progress_report()
        progress_str = report.get("profit_progress", "0%")
        try:
            progress = float(progress_str.strip().rstrip("%"))
        except (ValueError, AttributeError):
            progress = 0
        if progress > 70:
            mult *= 1.30
        elif progress > 40:
            mult *= 1.10

        return max(0.30, min(1.0, mult))

    def _adx_market_risk_mult(self) -> float:
        """🔒 ADX Market Filter — DÉPRÉCIÉ (6 Juillet 2026).
        Remplacé par le per-symbol regime detection (MarketRegime + OnlineLearner)
        qui est plus granulaire et précis. Cette fonction itérait 27 symboles × ADX
        tous les 15 min pour un bénéfice marginal.
        Conservée comme stub pour compatibilité ascendante."""
        return 1.0

    def _get_symbol_perf_risk_mult(self, symbol: str) -> float:
        """Multiplicateur de risque par symbole basé sur WR + RR des 20 derniers trades.

        Principe : chaque symbole a sa propre dynamique. Au lieu d'un risk_mult
        fixe dans la config, on l'ajuste dynamiquement selon le WR récent
        ET le RR réalisé (ratio gain/perte moyen).

        Fenêtre : 20 derniers trades du symbole (ou moins si pas assez de données).

        Règles WR :
          WR > 70% → x1.35
          WR > 60% → x1.10
          WR 50-60% → x1.00
          WR 40-50% → x0.80
          WR < 40% → x0.50
          < 5 trades → x1.00 (pas assez de données, neutre)

        Règles RR (appliquées APRÈS le WR, multiplicateur composé) :
          RR < 0.6 → ×0.50 (pertes 2x + grosses que gains → risk divisé par 2)
          RR < 0.8 → ×0.70 (pertes 25% + grosses → risk -30%)
          RR < 1.0 → ×0.85 (pertes plus grosses que gains → risk -15%)
          RR > 2.0 → ×1.15 (gains 2x + gros que pertes → bonus +15%)
          Sinon → ×1.00 (RR neutre, pas d'ajustement)
        """
        sym_trades = self._symbol_trade_history.get(symbol, [])
        if len(sym_trades) < 5:
            return 1.0

        # Derniers 20 trades du symbole
        recent = sym_trades[-20:] if len(sym_trades) >= 20 else sym_trades
        wins = sum(1 for t in recent if t.get("profit", 0) > 0)
        wr = wins / len(recent)

        if wr > 0.70:
            mult = 1.35
        elif wr > 0.60:
            mult = 1.10
        elif wr > 0.50:
            mult = 1.00
        elif wr > 0.40:
            mult = 0.80
        else:
            mult = 0.50

        # 🔧 FIX 29 Juillet 2026: RR-based penalty
        # Un symbole avec bon WR mais mauvais RR (pertes > gains) DOIT être pénalisé.
        # Ex: XAUUSD WR=55% mais RR=0.44 → les pertes sont 2.3× plus grosses que les gains.
        if len(recent) >= 5:
            gross_profit = sum(t["profit"] for t in recent if t.get("profit", 0) > 0)
            gross_loss = abs(sum(t["profit"] for t in recent if t.get("profit", 0) < 0))
            losses = len(recent) - wins
            if wins > 0 and losses > 0 and gross_profit > 0 and gross_loss > 0:
                avg_win = gross_profit / wins
                avg_loss = gross_loss / losses
                realized_rr = avg_win / avg_loss if avg_loss > 0 else 1.0

                if realized_rr < 0.6:
                    mult *= 0.50
                    logger.debug(
                        f"  [RR-PENALTY] {symbol}: RR={realized_rr:.2f} < 0.6 → mult ×0.50 (pertes {1 / realized_rr:.1f}× > gains)"
                    )
                elif realized_rr < 0.8:
                    mult *= 0.70
                    logger.debug(f"  [RR-PENALTY] {symbol}: RR={realized_rr:.2f} < 0.8 → mult ×0.70")
                elif realized_rr < 1.0:
                    mult *= 0.85
                    logger.debug(f"  [RR-PENALTY] {symbol}: RR={realized_rr:.2f} < 1.0 → mult ×0.85")
                elif realized_rr > 2.0:
                    mult *= 1.15
                    logger.debug(
                        f"  [RR-BONUS] {symbol}: RR={realized_rr:.2f} > 2.0 → mult ×1.15 (gains {realized_rr:.1f}× > pertes)"
                    )

        logger.debug(f"  [SYM-PERF] {symbol}: {wins}/{len(recent)} WR={wr:.0%} → risk_mult={mult:.3f}")
        return mult

    def _get_wr_based_max_lot(self, symbol: str) -> float:
        """Calcule le max_lot — DÉSACTIVÉ (16 Juillet 2026).

        Le système de lot progressif (WR→lot) est désactivé car il a causé
        des pertes catastrophiques sur XAUUSD (lot 0.20 au lieu de 0.01).
        Les lots de 0.20 ont transformé des petites pertes normales en
        désastres de -$627.80 (5 pires trades = 65% des pertes totales).

        Retourne toujours le max_lot de la config YAML (fixe, pas de WR).
        """
        cfg_max_lot = self.symbol_limits.get(symbol, {}).get("max_lot", 0.01)
        logger.debug(f"  [LOT] {symbol}: lot progressif désactivé → max_lot config={cfg_max_lot}")
        return cfg_max_lot

    def calculate_lot(
        self,
        symbol: str,
        entry: float,
        sl: float,
        quality: float = 1.0,
        direction: int = 0,
        signal_risk_mult: Optional[float] = None,
    ) -> float:
        account = self.mt5.get_account_info()
        if account is None:
            # 🐛 FIX 16 Août 2026 (Audit M-EX1): retournait 0.05 fixe (trade SANS
            # contrôle de risque si MT5 down). Désormais REFUS (0.0) — un trade
            # ne doit jamais partir sans compte valide.
            logger.warning(f"[LOT] {symbol}: account=None (MT5 down?) → lot=0 (refus)")
            return 0.0
        current_equity = account.equity

        # 🔒 GARDE-FOU MAX TOTAL LOTS: refuse tout trade si le volume total
        # de toutes les positions dépasse MAX_TOTAL_LOTS (anti-runaway).
        # Évite la répétition du scénario tuple bug (91 positions, 1.20 lots XAUUSD).
        try:
            all_pos = self.mt5.get_positions()
            total_vol = sum(getattr(p, "volume", 0) or 0 for p in all_pos) if all_pos else 0
            if total_vol >= MAX_TOTAL_LOTS:
                # 🐛 FIX 16 Août 2026 (Audit M-EX3): retournait min_lot au lieu de
                # refuser → le volume total CONTINUAIT de croître (anti-runaway
                # inopérant). Retourne 0.0 = refus clair.
                logger.warning(
                    f"[LOT SAFETY] Volume total {total_vol:.2f} >= MAX_TOTAL_LOTS={MAX_TOTAL_LOTS} "
                    f"— refus nouveau trade {symbol} (lot=0)"
                )
                return 0.0
        except Exception as e:
            logger.debug(f"[LOT SAFETY] Volume check failed: {e}")

        # Base risk from RISK_PER_TRADE, ajusté par direction
        base_risk = self.config.get("RISK_PER_TRADE", 0.004)
        short_mult = self.config.get("RISK_SHORT_MULT", 1.0)
        dir_mult = 1.0 if direction == 0 else short_mult
        risk_amount = current_equity * base_risk * dir_mult
        dd_peak = (self.peak_equity - current_equity) / max(self.peak_equity, 1)
        if dd_peak > DD_REDUCE_THRESHOLD:
            risk_amount *= 1 - dd_peak
        # 🔒 CRITIQUE: réduction agressive au-delà de 7% DD (proche du max 10% FTMO)
        if dd_peak > DD_CRITICAL_THRESHOLD:
            risk_amount *= 0.20  # ×0.20 au lieu de ×0.93 → 80% de réduction
            logger.warning(f"  [DD CRITICAL] {symbol}: DD peak {dd_peak:.1%} > 7% → risk ×0.20")
        risk_amount *= quality

        # 🐛 FIX 16 Août 2026 (Audit M-EX4): _daily_profit_reduced était posé
        # (log "risk reduit a 25%") mais JAMAIS lu → la réduction annoncée
        # n'avait aucun effet sur le lot. Appliquée ici : risk × 0.25 quand
        # le profit journalier dépasse DAILY_PROFIT_LIMIT_PCT.
        if getattr(self, "_daily_profit_reduced", False):
            risk_amount *= 0.25
            logger.debug(
                f"  [PROFIT LIMIT] {symbol}: _daily_profit_reduced=True → risk ×0.25 "
                f"(risk_amount={risk_amount:.2f})"
            )

        # Friday risk reduction SUPPRIMÉE — mode 24/7

        # 🔒 FIX C1: Utiliser le risk_mult du signal (OL→Adaptive→Anticipation→Kelly)
        # au lieu de la config statique. Cap par symbole pour éviter le sure-sizing.
        # Per-symbol risk_mult cap: XAUUSD=1.25, BTCUSD=1.00, US500.cash=1.15, ETHUSD=1.00
        # 🐛 FIX 28 Juillet 2026: risk_mult=0.0 = GEL ABSOLU (n'était PAS respecté car
        # la condition `signal_risk_mult > 0` excluait 0.0, qui tombait dans le else
        # avec risk_mult=1.0 de la config statique → trade ouvert malgré le gel).
        if signal_risk_mult is not None:
            if signal_risk_mult <= 0:
                # risk_mult ≤ 0 = symbole gelé, pas de trade
                logger.info(f"  [GEL] {symbol}: risk_mult={signal_risk_mult} ≤ 0 → lot=0 (symbole gelé)")
                return 0.0
            # signal_risk_mult > 0: utiliser le risk_mult du signal, capé par symbole
            sym_cap = RISK_MULT_CAP.get(symbol, 1.0)
            final_rm = max(0.1, min(signal_risk_mult, sym_cap))
            if final_rm != signal_risk_mult:
                logger.debug(
                    f"  [RISK] {symbol}: signal_risk_mult={signal_risk_mult:.3f} capé à {sym_cap} → {final_rm:.3f}"
                )
            risk_amount *= final_rm
            logger.debug(
                f"  [RISK] {symbol}: base=${risk_amount / final_rm:.2f} × risk_mult={final_rm:.3f} = ${risk_amount:.2f}"
            )
        else:
            # Fallback: config statique si signal_risk_mult non fourni
            risk_amount *= self.symbol_limits.get(symbol, {}).get("risk_mult", 1.0)

        # Per-symbol performance multiplier (rolling WR tracker)
        perf_mult = self._get_symbol_perf_risk_mult(symbol)
        risk_amount *= perf_mult

        # Absolute max risk cap (if configured)
        if self.max_risk_amount > 0:
            risk_amount = min(risk_amount, self.max_risk_amount)

        # Zone 2: >1% daily loss → risk × 0.75 (uniquement sur pertes réelles, pas sur gains)
        daily_loss_amt = max(0, -self.daily_stats["pnl"])
        zone2 = self.config.get("ZONE2_LOSS_PCT", 0.01)
        if daily_loss_amt > 0 and (daily_loss_amt / max(self.initial_balance, 1)) >= zone2:
            risk_amount *= 0.75
            logger.debug(f"  [ZONE 2] daily loss {daily_loss_amt / self.initial_balance:.2%} >= {zone2:.1%}, risk 75%")

        sym_cfg = self.symbol_limits.get(symbol, {})
        min_lot = sym_cfg.get("min_lot", 0.05)
        # WR-based max_lot progressif (remplace le max_lot statique de la config)
        max_lot = self._get_wr_based_max_lot(symbol)
        lot_size = self.config.get("LOT_SIZE", 0.05)

        order_type = self.mt5.ORDER_TYPE_BUY if direction == 0 else self.mt5.ORDER_TYPE_SELL
        # 🐛 FIX 05 Août 2026: label de log trompeur. calc_profit renvoie un PnL
        # NÉGATIF au niveau du SL (c'est une perte — comportement normal), puis
        # abs() le transforme en risque positif. L'ancien log affichait sl_profit
        # brut (négatif) sous le label "risk_per_01", faussant le diagnostic.
        # On log désormais les DEUX valeurs explicitement.
        sl_profit = self.mt5.calc_profit(order_type, symbol, 0.1, entry, sl)
        risk_per_01 = None
        if sl_profit is not None and sl_profit < 0:
            risk_per_01 = abs(sl_profit)
            if risk_per_01 < 1.0:
                logger.warning(f"  [RISK] {symbol}: risk_per_01=${risk_per_01:.2f} < $1.0 → fallback lot={lot_size}")
                lot = lot_size  # fallback (marché fermé ou SL trop serré)
            else:
                lot = (risk_amount / risk_per_01) * 0.1
        else:
            # sl_profit None ou ≥ 0 (SL du mauvais côté / donnée invalide) → fallback sûr
            logger.warning(
                f"  [RISK] {symbol}: calc_profit renvoyé sl_profit={sl_profit} "
                f"(None ou non-négatif = SL invalide) → fallback lot={lot_size}"
            )
            lot = lot_size

        # Adaptive lot multiplier (performance-based)
        adaptive_mult = self._adaptive_lot_mult()
        lot *= adaptive_mult
        logger.debug(
            f"  [ADAPTIVE LOT] {symbol}: lot pré-clamp={lot:.3f} "
            f"(sl_profit brut=${sl_profit if sl_profit is not None else 0:.2f} → "
            f"risk_per_01=${risk_per_01 if risk_per_01 is not None else 0:.2f})"
        )

        # 🔧 FIX 23 Juillet 2026: Suppression du safety clamp lot > 3×max_lot → lot_size
        # L'ancien code forçait lot=lot_size (0.01) quand lot dépassait 3×max_lot, ce qui
        # cassait la chaîne de risque : risk_amount=$300 → lot=1.75 → clamped à 0.01 → trade à 0.02 lot.
        # Désormais le clamp naturel à max_lot (ligne ci-dessous) suffit.
        # En cas de valeur aberrante (> 10× max_lot), on force max_lot comme garde-fou.
        MAX_LOT_ABSURDITY_FACTOR = 10
        if lot > max_lot * MAX_LOT_ABSURDITY_FACTOR:
            logger.warning(
                f"[LOT SAFETY] {symbol}: lot={lot:.3f} > {MAX_LOT_ABSURDITY_FACTOR}×max_lot={max_lot} "
                f"(anomalie) → force max_lot"
            )
            lot = max_lot

        # Clamp between min_lot and max_lot from symbol config
        lot = max(min_lot, min(lot, max_lot))
        return round(lot, 2)

    REGIME_FROM_COMMENT = {
        "TRE": "TREND_UP",
        "DOW": "TREND_DOWN",
        "RAN": "RANGING",
        "HIG": "HIGH_VOL",
        "LOW": "LOW_VOL",
    }

    def register_open_trade(self, symbol: Optional[str] = None) -> None:
        """Enregistre un trade qui VIENT d'être ouvert.
        Permet au MAX_TRADES_PER_DAY de compter aussi les trades ouverts,
        pas seulement les fermés (était la cause des 222 trades/jour)."""
        # 🐛 FIX 16 Août 2026 (Audit M-EX7): _reset_daily() AVANT l'incrément.
        # L'ancien ordre (incrément puis reset) perdait le trade quand le jour
        # UTC changeait entre les deux → MAX_TRADES_PER_DAY non compté à minuit.
        self._reset_daily()  # reset si jour a changé
        self._opened_today += 1

    def refresh_symbol_limits(self) -> None:
        """Recharge les symbol_limits depuis la config globale.
        Charge DIRECTEMENT depuis les YAML (contourne le mtime check buggé)."""
        try:
            # Charge frais depuis YAML en contournant le cache/hot-reload
            from config.schema import _load_yaml, _interpolate, _deep_merge, ConfigSchema
            from pathlib import Path

            # Vide le cache LRU de _load_yaml pour garantir des données fraîches
            _load_yaml.cache_clear()
            logger.debug("[CONFIG] cache YAML vidé pour rechargement frais")

            config_dir = Path(__file__).parent.parent / "config"
            default_path = config_dir / "default.yaml"
            env_path = config_dir / "production.yaml"

            raw = _load_yaml(default_path)
            raw = _interpolate(raw)
            if env_path.exists():
                raw = _deep_merge(raw, _interpolate(_load_yaml(env_path)))
            cfg = ConfigSchema(**raw)

            # Met à jour symbol_limits depuis le frais
            fresh_limits = {sym: lim.model_dump(exclude_none=True) for sym, lim in cfg.symbol_limits.items()}
            # 🐛 FIX 29 Juillet 2026: update IN-PLACE pour que SignalValidator et
            # autres références voient les changements (au lieu de remplacer le dict)
            self.symbol_limits.clear()
            self.symbol_limits.update(fresh_limits)
            logger.info(f"[CONFIG] symbol_limits rechargées (frais): {len(fresh_limits)} symboles")

            # Met à jour DANGER_HOURS depuis cfg
            self.config["DANGER_HOURS"] = cfg.trading.danger_hours
        except Exception as e:
            logger.warning(f"[CONFIG] refresh_symbol_limits failed: {e}")
            import traceback

            logger.warning(traceback.format_exc())

    def check_invariants(self, position: Any) -> None:
        with self._shared_lock:
            ticket_key = str(position.ticket)
            if ticket_key not in self.position_open_times:
                open_time = getattr(position, "time", None) or datetime.utcnow()
                self.position_open_times[ticket_key] = {"open_time": open_time, "symbol": position.symbol}
            if ticket_key not in self.position_regime:
                comment = getattr(position, "comment", "") or ""
                self._parse_comment_regime(comment, ticket_key)
            self._prune_position_times()
        # Chaque sous-vérification est protégée individuellement :
        # le "readonly attribute" d'MT5 survient si la position a été modifiée
        # entre la lecture et l'envoi (ex: partial TP puis trailing dans le même cycle)
        subs = [
            ("time_stop", self.trailer._check_time_stop),
            # 🔧 30 Juillet 2026: BE progressif AVANT partial TP et trailing.
            # Sécurise un profit minimal (entry ou entry+0.15×ATR) dès 0.50×ATR,
            # avant même que le trailing N1 (1.20×ATR) ne s'active.
            ("progressive_be", self.trailer._check_progressive_be),
            ("partial_tp", self.trailer._check_partial_tp),
            ("step_trail", self.trailer._check_step_trailing),
            ("structure", self.trailer._check_structure_exit),
        ]
        for name, fn in subs:
            try:
                fn(position)
            except AttributeError as e:
                if "readonly" in str(e).lower() or "attribute" in str(e).lower():
                    logger.debug(f"[GUARD] {position.symbol} ticket={ticket_key}: {name} skip (position locked)")
                else:
                    logger.warning(f"[GUARD] {position.symbol} ticket={ticket_key}: {name} attr err: {e}")
            except Exception as e:
                logger.warning(f"[GUARD] {position.symbol} ticket={ticket_key}: {name} err: {e}")

    def set_position_regime(self, ticket: int, regime: str) -> None:
        with self._shared_lock:
            self.position_regime[str(ticket)] = regime

    def _prune_position_times(self) -> None:
        with self._shared_lock:
            if len(self.position_open_times) > 200:
                try:
                    old = sorted(
                        self.position_open_times.keys(), key=lambda k: self.position_open_times[k]["open_time"]
                    )[:-150]
                    for k in old:
                        del self.position_open_times[k]
                except Exception as e:
                    logger.warning(f"Prune failed: {e}")
                    self.position_open_times = dict(list(self.position_open_times.items())[-150:])

    def record_trade_result(
        self,
        symbol: str,
        profit: float,
        historical: bool = False,
        trade_time: Any = None,
        direction: Optional[str] = None,
    ) -> None:
        """Enregistre le résultat d'un trade fermé. Delegates to ChallengeTracker."""
        self.challenge.record_trade_result(symbol, profit, historical, trade_time=trade_time, direction=direction)
        # Sync aliases
        self.consecutive_losses = self.challenge.consecutive_losses
        self.challenge_status = self.challenge.challenge_status
        # 🐛 FIX 10 Août 2026 (Bug #4): Le palier de circuit breaker (_circuit_stage_served)
        # ne descend QUE sur une victoire réelle. Sans ce reset, une fois un palier déclenché
        # (ex: HARD STOP), le robot resterait au palier max à vie — l'escalade serait bloquée
        # à la baisse comme à la hausse. Une victoire remet l'escalade à zéro proprement.
        if profit > 0 and not historical:
            self._circuit_stage_served = 0

    def _check_consistency(self) -> None:
        """FTMO consistency rule. Delegates to ChallengeTracker."""
        # Sync state in case tests/code reassigned aliases
        self.challenge.daily_pnl_by_date = self.daily_pnl_by_date
        self.challenge._check_consistency()
        self.consistency_violated = self.challenge.consistency_violated

    def _check_daily_loss_limit(self, symbol: Optional[str] = None) -> None:
        """Daily loss limit check. Delegates to ChallengeTracker."""
        self.challenge._check_daily_loss_limit(symbol)
        self._daily_loss_violated = self.challenge._daily_loss_violated
        self.challenge_status = self.challenge.challenge_status

    def current_dd_pct(self) -> float:
        """Current drawdown %. Delegates to ChallengeTracker."""
        return self.challenge.current_dd_pct()

    def _check_drawdown_limit(self) -> None:
        """Drawdown limit check. Delegates to ChallengeTracker."""
        self.challenge._check_drawdown_limit()
        self.challenge_status = self.challenge.challenge_status

    def _prune_histories(self) -> None:
        """Prune both challenge and position histories."""
        # Sync in case alias was broken by reassignment
        self.challenge._trade_history = self._trade_history
        self.challenge._prune_histories()
        # Re-sync after pruning (may have been replaced)
        self._trade_history = self.challenge._trade_history
        # Position-level pruning (stays in FTMOProtector)
        with self._shared_lock:
            if len(self.partial_closed) > 500:
                self.partial_closed = set(list(self.partial_closed)[-300:])
            if len(self.peak_profit) > 500:
                old = sorted(self.peak_profit.keys(), key=lambda k: int(k))[:-300]
                for k in old:
                    del self.peak_profit[k]
            if len(self.trailing_peaks) > 500:
                old = sorted(self.trailing_peaks.keys(), key=lambda k: int(k))[:-300]
                for k in old:
                    del self.trailing_peaks[k]
            if len(self.position_regime) > 500:
                old = sorted(self.position_regime.keys(), key=lambda k: int(k))[:-300]
                for k in old:
                    del self.position_regime[k]

    def get_progress_report(self) -> dict[str, Any]:
        """Progress report. Delegates to ChallengeTracker."""
        return self.challenge.get_progress_report()

    # ── Trailing & Exit — delegated to Trailer (shared state) ─────────

    def _pip_offset(self, symbol: str, pips: int = 10) -> float:
        return self.trailer._pip_offset(symbol, pips)

    def _check_partial_tp(self, position: Any) -> Any:
        return self.trailer._check_partial_tp(position)

    def _check_time_stop(self, position: Any) -> Any:
        return self.trailer._check_time_stop(position)

    def _get_atr(self, symbol: str, period: int = 14) -> Any:
        return self.trailer._get_atr(symbol, period)

    def _check_step_trailing(self, position: Any) -> Any:
        return self.trailer._check_step_trailing(position)

    def _reconstruct_peak(self, position: Any) -> Any:
        return self.trailer._reconstruct_peak(position)

    def _check_structure_exit(self, position: Any) -> Any:
        return self.trailer._check_structure_exit(position)

    def _calc_sl_tp(
        self,
        symbol: str,
        entry: float,
        direction: int,
        atr_val: Optional[float] = None,
        sl_mult: float = 1.8,  # 🔧 24 Juil: 2.0→1.8 (W/L ratio)
        tp_mult: float = 5.0,  # 🔧 24 Juil: 4.0→5.0 (W/L ratio)
    ) -> Any:
        return self.trailer.calc_sl_tp(symbol, entry, direction, atr_val, sl_mult, tp_mult)

    def _reset_daily(self) -> None:
        """Reset daily stats. Delegates to ChallengeTracker.

        🔧 FIX 6 Juillet 2026: sync _opened_today après reset challenge
        (l'alias int est cassé par l'immutabilité Python — doit être ré-syncé explicitement)
        """
        self.challenge._reset_daily()
        self.daily_stats = self.challenge.daily_stats
        self.daily_start_equity = self.challenge.daily_start_equity
        self._opened_today = self.challenge._opened_today
