"""Challenge FTMO — tracking et vérification des règles.

Extrait de ftmo_protector.py (P2.3, v4.1.0).
Gère : record_trade_result, consistency, daily loss, drawdown, progress report.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("ftmo")


class ChallengeTracker:
    """Suivi du challenge FTMO 200K — règles de risque et progress tracking."""

    def __init__(self, mt5, config):
        self.mt5 = mt5

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
        self.daily_pnl_by_date: dict[datetime, float] = {}  # date -> realized PnL

        # ── Historiques ──────────────────────────────────────────────
        self.trading_days: set = set()
        self.consecutive_losses = 0
        self._symbol_consecutive_losses: dict[str, int] = {}
        self.cooldowns: dict[str, datetime] = {}
        self.global_cooldown_until: datetime | None = None
        self._trade_history: list[dict] = []
        self._symbol_trade_history: dict[str, list[dict]] = {}

    # ── Trade recording ──────────────────────────────────────────────

    def record_trade_result(self, symbol, profit, historical=False, trade_time=None):
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
            self._trade_history.append(dict(symbol=symbol, profit=profit, time=now))
        if len(self._trade_history) > 1000:
            self._trade_history[:] = self._trade_history[-1000:]

        if not historical:
            self.daily_stats["trades"] += 1
            self._daily_trades_per_symbol[symbol] = self._daily_trades_per_symbol.get(symbol, 0) + 1
            self.daily_stats["pnl"] += profit
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
                # Cooldown progressif: 5 min pour 1 perte, 10 min pour 2+ consécutives
                sym_losses = self._symbol_consecutive_losses.get(symbol, 0) + 1
                self._symbol_consecutive_losses[symbol] = sym_losses
                cd_minutes = 5 if sym_losses <= 1 else 10
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

    def _check_consistency(self):
        """FTMO consistency rule: aucun jour ne doit dépasser 30% du profit TARGET.
        Règle FTMO 1-Step: le meilleur jour ≤ 30% × (capital × profit_target_pct).
        Exemple: 200K × 10% = $20K target → 30% = $6K max/jour.

        Vérification EN CONTINU dès que le PnL total dépasse $500."""
        total_pnl = sum(self.daily_pnl_by_date.values())
        if total_pnl < 500 or total_pnl <= 0:
            return
        profit_target_amount = self.initial_balance * self.profit_target_pct
        max_per_day = profit_target_amount * self.consistency_max_pct  # ex: $20K × 30% = $6K
        # Reset consistency_violated avant recalcul (peut se résoudre)
        self.consistency_violated = False
        for day, day_pnl in sorted(self.daily_pnl_by_date.items()):
            if day_pnl <= 0:
                continue
            if day_pnl > max_per_day:
                self.consistency_violated = True
                day_pct_of_target = day_pnl / profit_target_amount
                logger.critical(
                    f"FTMO CONSISTENCY VIOLATED: {day} = ${day_pnl:.0f} "
                    f"({day_pct_of_target:.1%} du target ${profit_target_amount:.0f}) "
                    f"> max {self.consistency_max_pct:.0%} × target"
                )

    def _check_daily_loss_limit(self, symbol=None):
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
            self.challenge_status = "FAILED_DD"
            logger.warning(f"DAILY LOSS LIMIT: {daily_loss_pct:.1%}")

    def current_dd_pct(self):
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

    def _check_drawdown_limit(self):
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

    def get_progress_report(self):
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
                d = t.get("time").date() if isinstance(t.get("time"), datetime) else None
                if d is None:
                    continue
                temp_daily[d] = temp_daily.get(d, 0) + t.get("profit", 0)
            if temp_daily:
                best_day = max(temp_daily.values())

        best_day_pct = best_day / realized_pnl if realized_pnl > 0 and best_day > 0 else 0.0

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

    def reset_challenge(self, new_initial_balance=None):
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

    def _reset_daily(self):
        """Reset les stats quotidiennes à minuit UTC."""
        now = datetime.utcnow()
        if now.date() != self.daily_stats.get("day"):
            self.daily_stats = {"trades": 0, "losses": 0, "pnl": 0, "day": now.date()}
            self._daily_trades_per_symbol = {}
            self._opened_today = 0
            self._daily_profit_reduced = False
            account = self.mt5.get_account_info()
            if account:
                self.daily_start_equity = account.equity
            else:
                self.daily_start_equity = max(self.peak_equity, self.initial_balance)

    # ── Pruning ──────────────────────────────────────────────────────

    def _prune_histories(self):
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
                    "time": t["time"].isoformat() if isinstance(t["time"], datetime) else str(t["time"]),
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
                    if isinstance(time_val, str):
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
