"""Challenge FTMO — tracking et vérification des règles.

Extrait de ftmo_protector.py (P2.3, v4.1.0).
Gère : record_trade_result, consistency, daily loss, drawdown, progress report.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("ftmo")


class ChallengeTracker:
    """Suivi du challenge FTMO 200K — règles de risque et progress tracking."""

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
        self, symbol: str, profit: float, historical: bool = False, trade_time: Optional[datetime] = None
    ) -> None:
        """Enregistre le résultat d'un trade fermé.

        Args:
            symbol: le symbole tradé
            profit: profit en $ (positif=gain, négatif=perte)
            historical: True si appelé depuis import_history() au démarrage.
                        Ne compte PAS dans daily_stats["trades"] pour
                        éviter de bloquer les trades live (MAX_TRADES_PER_DAY).
            trade_time: timestamp réel du trade (datetime). Si None, utilise utcnow().
                        Pour les imports historiques, utiliser le temps réel MT5.
        """
        now = trade_time or datetime.utcnow()

        # 🔒 FIX #2: Pour les trades historiques, ne garder que les 48 dernières heures
        # dans _trade_history pour éviter de polluer le WR avec des trades anciens.
        # Les cooldowns/daily_pnl ne sont pas affectés (historical=True les exclut).
        add_to_history = True
        if historical and trade_time is not None:
            # ⚠️ MT5 peut retourner un int (unix timestamp) ou un datetime selon version
            if isinstance(trade_time, (int, float)):
                trade_dt = datetime.fromtimestamp(trade_time)
            else:
                trade_dt = trade_time
            age = (datetime.utcnow() - trade_dt).total_seconds()
            if age > 48 * 3600:  # plus de 48h
                add_to_history = False

        if add_to_history:
            self._trade_history.append(dict(symbol=symbol, profit=profit, time=now, historical=historical))
        if len(self._trade_history) > 1000:
            self._trade_history[:] = self._trade_history[-1000:]

        if not historical:
            self.daily_stats["trades"] += 1
            self._daily_trades_per_symbol[symbol] = self._daily_trades_per_symbol.get(symbol, 0) + 1
            self.daily_stats["pnl"] += profit
            # 🔧 FIX_SUPREME_COUNCIL: suivi PnL quotidien par symbole
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
                # Cooldown progressif: 15 min normal, 120 min après 3 pertes consécutives
                sym_losses = self._symbol_consecutive_losses.get(symbol, 0) + 1
                self._symbol_consecutive_losses[symbol] = sym_losses
                if sym_losses >= 3:
                    cd_minutes = self.symbol_limits.get(symbol, {}).get(
                        "cooldown_minutes_consecutive",  # configurable par symbole
                        self.config.get("COOLDOWN_MINUTES_CONSECUTIVE", 120),  # global, fallback 120
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
        """FTMO consistency rule: aucun jour ne doit dépasser 30% du profit RÉEL.
        Règle FTMO 1-Step authentique: le meilleur jour ≤ 30% × profit total réalisé.
        Exemple: profit total $1,000 → max $300/jour.

        Corrigé 26 Juin 2026:
          - Reset consistency_violated AVANT l'early return pour éviter
            le blocage à True quand on passe de 2+ jours à 1 jour (recalcul)
          - Le dénominateur est la SOMME DES JOURS POSITIFS (règle FTMO réelle)
          - Le guard utilise le nombre de jours tradés (≥2), pas un seuil $500
          - Évite le bug 763% quand une grosse perte compresse le PnL net"""
        # Reset consistency_violated AVANT tout calcul (peut se résoudre
        # quand le nombre de jours augmente et dilue best_day_pct).
        # Important: doit être AVANT l'early return pour éviter qu'un
        # flag True ne reste bloqué quand on retombe à 1 jour.
        self.consistency_violated = False

        # Pas assez de jours pour juger la consistance
        total_net = sum(self.daily_pnl_by_date.values())
        positive_days = [v for v in self.daily_pnl_by_date.values() if v > 0]
        if len(self.daily_pnl_by_date) < 2 or total_net <= 0:
            return
        best_day = max(positive_days)
        positive_total = sum(positive_days)
        max_per_day = positive_total * self.consistency_max_pct  # FTMO: 30% des GAINS (fix: total_net→positive_total)
        for day, day_pnl in sorted(self.daily_pnl_by_date.items()):
            if day_pnl <= 0:
                continue
            if day_pnl > max_per_day:
                self.consistency_violated = True
                day_pct_of_net = day_pnl / positive_total if positive_total > 0 else 0
                logger.critical(
                    f"FTMO CONSISTENCY VIOLATED: {day} = ${day_pnl:.0f} "
                    f"({day_pct_of_net:.1%} du PnL net ${total_net:.0f}) "
                    f"> max {self.consistency_max_pct:.0%} du PnL net"
                )

    def _check_daily_loss_limit(self, symbol: Optional[str] = None) -> None:
        """Vérifie la daily loss avec coordination et caching.
        Met à jour _daily_loss_violated pour synchronisation avec can_trade().
        """
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

        # Per-symbol override (ex: BTCUSD = 1.5%)
        if symbol:
            sym_cfg = self.symbol_limits.get(symbol, {})
            sym_daily_loss = sym_cfg.get("max_daily_loss_pct_override")
            if sym_daily_loss is not None:
                daily_loss_limit = sym_daily_loss

        self._daily_loss_violated = daily_loss_pct >= daily_loss_limit
        if self._daily_loss_violated:
            # ⚠️ Daily loss violation = BLOCAGE TEMPORAIRE, pas FAILED_DD
            # Ne pas set challenge_status = "FAILED_DD" ici — le daily loss
            # reset chaque jour. FAILED_DD est réservé au max drawdown (10%).
            # Le blocage est géré via _daily_loss_violated dans can_trade().
            logger.warning(f"DAILY LOSS LIMIT: {daily_loss_pct:.1%} — trading bloqué pour aujourd'hui")

    def current_dd_pct(self) -> float:
        """Retourne le drawdown actuel en ratio (0.0 = pas de DD, 1.0 = 100%).
        En cas d'erreur, retourne 1.0 (conservateur : bloque tous les trades)."""
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

        # Règle FTMO réelle: best_day_pct = meilleur jour / somme des jours POSITIFS
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

        # FTMO: dénominateur = somme des jours profitables (pas le PnL net)
        positive_days_total = sum(v for v in self.daily_pnl_by_date.values() if v > 0)
        if best_day <= 0 or positive_days_total <= 0:
            best_day_pct = 0.0
        else:
            best_day_pct = best_day / positive_days_total

        # 🔒 FIX: Sanity cap — best_day_pct > 100% = données contaminées
        # (p. ex. daily_pnl_by_date contient des valeurs equity-based au lieu de realized PnL)
        if best_day_pct > 1.0:
            logger.warning(
                f"best_day_pct={best_day_pct:.1%} invalide (>100%) — "
                f"daily_pnl_by_date probablement contaminé par equity-PnL. "
                f"Cap à 100% (best_day=${best_day:.2f}, positive_sum=${positive_days_total:.2f})"
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
        """Reset l'état du challenge (utile pour comptes practice/Free Trial).
        Ne PAS appeler en cours de vrai challenge FTMO."""
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
            self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": now.date()}
            self._daily_trades_per_symbol = {}
            self._symbol_daily_pnl = {}  # Reset PnL quotidien par symbole
            self._opened_today = 0
            self._daily_profit_reduced = False
            account = self.mt5.get_account_info()
            if account:
                self.daily_start_equity = account.equity
            else:
                self.daily_start_equity = max(self.peak_equity, self.initial_balance)

    # ── Pruning ──────────────────────────────────────────────────────

    def _prune_histories(self) -> None:
        """Nettoie les historiques pour limiter la mémoire.
        Utilise slice assignment pour préserver les aliases FTMOProtector."""
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
        # Cause: daily loss limit set challenge_status="FAILED_DD" (bug avant fix).
        # Un daily loss est temporaire, seul le max drawdown (10%) doit causer FAILED_DD.
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
                pass  # keep FAILED_DD as safe default if MT5 unreachable

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

        # Restore trade_history (for FTMO report WR/total_trades)
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
                        }
                    )
                except (ValueError, TypeError):
                    pass

        # ── Contamination Guard ──────────────────────────────────────────
        # Valide daily_pnl_by_date contre _trade_history (source de vérité).
        # Les valeurs equity-PnL contaminées (ex: daily_pnl_by_date écrit avec
        # equity-based PnL au lieu du realized PnL) sont détectées et corrigées.
        # Voir Session Robot Manager — Juin 2026 (AGENTS.md).
        if self._trade_history:
            # Reconstruire daily_pnl_by_date depuis trade_history (atomique)
            rebuilt: dict[date, float] = {}
            for t in self._trade_history:
                trade_time = t.get("time")
                if isinstance(trade_time, datetime):
                    d = trade_time.date()
                    rebuilt[d] = rebuilt.get(d, 0.0) + t.get("profit", 0.0)

            # ── Recovery: daily_pnl_by_date vide mais trade_history dispo ──
            if not self.daily_pnl_by_date and rebuilt:
                logger.info(
                    f"[RECOVERY] daily_pnl_by_date vide — "
                    f"reconstruction depuis trade_history ({len(rebuilt)} jours, "
                    f"{len(self._trade_history)} trades)"
                )
                self.daily_pnl_by_date = dict(rebuilt)
                # Reconstruire aussi trading_days
                for d in rebuilt:
                    self.trading_days.add(d)

            if self.daily_pnl_by_date and rebuilt:
                # Comparer les dates communes
                common_dates = set(self.daily_pnl_by_date.keys()) & set(rebuilt.keys())
                discrepancies = 0
                for d in common_dates:
                    loaded = self.daily_pnl_by_date[d]
                    truth = rebuilt[d]
                    # Tolérance: 10% ou $1 (arrondis flottants)
                    if abs(loaded - truth) > max(abs(truth) * 0.1, 1.0):
                        discrepancies += 1

                if discrepancies > 0:
                    pct = discrepancies / len(common_dates)
                    if pct > 0.2:  # >20% des dates communes sont divergentes → contamination
                        logger.warning(
                            f"[CONTAMINATION] daily_pnl_by_date: {discrepancies}/{len(common_dates)} "
                            f"dates diffèrent de trade_history ({pct:.1%}) — "
                            f"correction depuis trade_history"
                        )
                        # Corriger: utiliser rebuilt pour les dates communes
                        for d, pnl in rebuilt.items():
                            self.daily_pnl_by_date[d] = pnl
                        # Signaler les dates orphelines (dans daily_pnl_by_date mais pas dans trade_history)
                        orphan_dates = set(self.daily_pnl_by_date.keys()) - set(rebuilt.keys())
                        if orphan_dates:
                            logger.warning(
                                f"[CONTAMINATION] {len(orphan_dates)} date(s) orpheline(s) "
                                f"conservée(s) intacte(s): "
                                f"{', '.join(str(d) for d in sorted(orphan_dates)[:5])}"
                            )
                    else:
                        logger.info(
                            f"[CONTAMINATION] {discrepancies} divergence(s) mineure(s) "
                            f"({pct:.1%} des dates) — sous le seuil de correction, ignoré"
                        )
