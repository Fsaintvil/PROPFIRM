"""
Performance Monitor — Suivi autonome des métriques du robot MT5 FTMO.
Tracke daily PnL, WR, PF par symbole, tendances glissantes,
progression du challenge FTMO, et génère des alertes.

Usage:
    from engine_simple.performance_monitor import PerformanceMonitor
    pm = PerformanceMonitor()
    pm.record_trade(symbol, profit, regime, direction)
    report = pm.generate_report()
    alerts = pm.check_alerts()
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("perf_monitor")

RUNTIME_DIR = Path(__file__).parent.parent / "runtime"
HISTORY_FILE = RUNTIME_DIR / "performance_history.json"
REPORT_FILE = RUNTIME_DIR / "daily_report.json"

# Fenêtres glissantes pour les tendances
ROLLING_WINDOWS = [20, 50, 100, 200]

# Seuils d'alerte
ALERT_THRESHOLDS = {
    "wr_50_drop": 15,  # -15 points de WR sur 50 trades = alerte (WR stocké en %)
    "pf_below": 1.0,  # PF < 1.0 = rouge
    "pf_warning": 1.2,  # PF < 1.2 = attention
    "daily_loss_pct": 0.02,  # 2% daily loss max
    "dd_pct": 0.08,  # 8% drawdown = avertissement
    "consistency_pct": 0.25,  # 25% jour/total = attention
    "consecutive_losses": 3,  # 3 pertes consécutives
    "low_volume_trades": 5,  # Moins de 5 trades/symbole = échantillon insuffisant
}


class PerformanceMonitor:
    """Surveille les performances en continu et détecte les tendances."""

    def __init__(self) -> None:
        self.history = self._load_history()
        self._ensure_structure()
        self._lock = (
            threading.RLock()
        )  # C-02: protection race condition (RLock pour réentrance _save() dans record_trade())
        self._import_from_csv()  # Sync CSV → performance_history
        self._last_alert_time = {}  # Déduplication: metric → last timestamp

    def _ensure_structure(self) -> None:
        """Crée la structure si le fichier est vide ou corrompu."""
        required = ["daily", "rolling", "symbols", "alerts", "challenge"]
        for key in required:
            if key not in self.history:
                self.history[key] = {} if key != "alerts" else []
        if "rolling" not in self.history:
            self.history["rolling"] = {}
        if "symbols" not in self.history:
            self.history["symbols"] = {}
        if "recent_trades" not in self.history:
            self.history["recent_trades"] = []
        if "challenge" not in self.history:
            self.history["challenge"] = {
                "start_balance": 0,
                "peak_equity": 0,
                "trading_days": 0,
                "last_status": "UNKNOWN",
            }

    def _import_from_csv(self) -> None:
        """Importe les trades depuis trades_log.csv si performance_history est vide.
        Résout le désync entre CSV (8 trades) et performance_history (3 trades)
        causé par MT5 history expiration + restarts.
        Ne s'exécute QUE si recent_trades est vide (premier lancement)
        ET que HISTORY_FILE est dans le RUNTIME_DIR par défaut (pas un test)."""
        if self.history.get("recent_trades"):
            return  # Already has data, skip CSV import
        # Skip if HISTORY_FILE was patched to a different directory (test environment)
        if HISTORY_FILE.parent != RUNTIME_DIR:
            return
        csv_file = RUNTIME_DIR / "trades_log.csv"
        if not csv_file.exists():
            return
        try:
            import csv as csv_mod

            with open(csv_file, "r") as f:
                reader = csv_mod.DictReader(f)
                csv_trades = list(reader)
            if not csv_trades:
                return
            imported = 0
            for row in csv_trades:
                symbol = row.get("symbol", "")
                pnl = float(row.get("pnl", 0))
                direction = row.get("direction", "BUY")
                ts = row.get("timestamp", "")
                if pnl == 0 or not symbol:
                    continue
                # Manually add to recent_trades (bypass record_trade to avoid daily stats)
                self.history["recent_trades"].append(
                    {
                        "profit": pnl,
                        "symbol": symbol,
                        "regime": "IMPORT",
                        "direction": direction,
                        "ts": ts,
                    }
                )
                # Also update symbol stats
                s = self.history["symbols"]
                if symbol not in s:
                    s[symbol] = {
                        "trades": 0,
                        "wins": 0,
                        "losses": 0,
                        "pnl": 0.0,
                        "gross_profit": 0.0,
                        "gross_loss": 0.0,
                        "regime_stats": {},
                        "direction_stats": {},
                    }
                sd = s[symbol]
                sd["trades"] += 1
                sd["pnl"] += pnl
                if pnl > 0:
                    sd["wins"] += 1
                    sd["gross_profit"] += pnl
                else:
                    sd["losses"] += 1
                    sd["gross_loss"] += abs(pnl)
                imported += 1
            if imported > 0:
                logger.info(f"[PERF] CSV import: {imported} trades ajoutés à performance_history")
                self._save()
        except Exception as e:
            logger.debug(f"[PERF] CSV import skip: {e}")

    def _load_history(self) -> dict[str, Any]:
        """Charge l'historique depuis le fichier JSON avec validation.
        Rejette les données contaminées (trades sans timestamp = backtest).
        Applique une migration pour garantir la présence de toutes les clés."""
        default_history = {
            "daily": {},
            "rolling": {},
            "symbols": {},
            "alerts": [],
            "challenge": {},
            "recent_trades": [],
        }
        try:
            if HISTORY_FILE.exists() and HISTORY_FILE.stat().st_size > 10:
                with open(HISTORY_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "daily" in data:
                        # Validation: rejeter si recent_trades contient des trades sans timestamp
                        recent = data.get("recent_trades", [])
                        if len(recent) > 0:
                            has_ts = sum(1 for t in recent if "ts" in t)
                            has_no_ts = len(recent) - has_ts
                            if has_no_ts > has_ts:
                                logger.warning(
                                    f"Historique contaminé: {len(recent)} trades dont "
                                    f"{has_no_ts} sans timestamp — réinitialisation"
                                )
                                return default_history
                        # Validation: détection de profits identiques répétés (contamination seed/pattern)
                        # Ex: 26 trades EURUSD $50.0 + XAUUSD $150.0 = contamination
                        if len(recent) >= 10:
                            from collections import Counter

                            profit_counts = Counter(
                                (t.get("symbol", "?"), round(t.get("profit", 0), 2)) for t in recent
                            )
                            suspicious = [(k, v) for k, v in profit_counts.items() if v >= 5]
                            total_suspicious = sum(v for _, v in suspicious)
                            if total_suspicious >= 10 and total_suspicious >= len(recent) * 0.15:
                                logger.warning(
                                    f"Historique contaminé: {len(recent)} trades dont "
                                    f"{total_suspicious} avec profits identiques répétés "
                                    f"({', '.join(f'{p[0]} ${p[1]}×{c}' for p, c in suspicious[:5])})"
                                    f" — réinitialisation"
                                )
                                return default_history
                        # Migration: s'assurer que tous les symboles ont les clés requises
                        for sym, sdata in data.get("symbols", {}).items():
                            if "gross_profit" not in sdata:
                                sdata["gross_profit"] = 0.0
                            if "gross_loss" not in sdata:
                                sdata["gross_loss"] = 0.0
                            if "regime_stats" not in sdata:
                                sdata["regime_stats"] = {}
                            if "direction_stats" not in sdata:
                                sdata["direction_stats"] = {
                                    "BUY": {"wins": 0, "losses": 0, "pnl": 0.0},
                                    "SELL": {"wins": 0, "losses": 0, "pnl": 0.0},
                                }
                        # Migration: s'assurer que les clés top-level existent
                        for key, default in default_history.items():
                            if key not in data:
                                data[key] = default
                        return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Historique corrompu, réinitialisation: {e}")
        return default_history

    def _save(self) -> None:
        """Sauvegarde l'historique (thread-safe via _lock)."""
        with self._lock:
            # 🔧 Dédoublonnage systématique des alertes avant sauvegarde
            alerts = self.history.get("alerts", [])
            seen = set()
            deduped = []
            for a in alerts:
                key = (a.get("date"), a.get("metric"), a.get("symbol", ""))
                if key not in seen:
                    seen.add(key)
                    deduped.append(a)
            if len(deduped) != len(alerts):
                logger.info(f"[PERF] Dédoublonnage: {len(alerts)} → {len(deduped)} alertes")
            self.history["alerts"] = deduped[-30:]  # garde max 30 alertes récentes
            try:
                RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                tmp = HISTORY_FILE.with_suffix(".tmp")  # écriture atomique
                with open(tmp, "w") as f:
                    json.dump(self.history, f, indent=2, default=str)
                tmp.replace(HISTORY_FILE)
            except OSError as e:
                logger.error(f"Impossible de sauvegarder l'historique: {e}")

    def record_trade(self, symbol: str, profit: float, regime: str = "UNKNOWN", direction: str = "BUY") -> None:
        """Enregistre un trade fermé dans l'historique."""
        with self._lock:
            today = datetime.utcnow().strftime("%Y-%m-%d")

            # === DAILY ===
            if today not in self.history["daily"]:
                self.history["daily"][today] = {
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "pnl": 0.0,
                    "gross_profit": 0.0,
                    "gross_loss": 0.0,
                    "symbols": {},
                }
            d = self.history["daily"][today]
            d["trades"] += 1
            d["pnl"] += profit
            if profit > 0:
                d["wins"] += 1
                d["gross_profit"] += profit
            elif profit < 0:
                d["losses"] += 1
                d["gross_loss"] += abs(profit)

            # Par symbole dans la journée
            if symbol not in d["symbols"]:
                d["symbols"][symbol] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            sd = d["symbols"][symbol]
            sd["trades"] += 1
            sd["pnl"] += profit
            if profit > 0:
                sd["wins"] += 1
            elif profit < 0:
                sd["losses"] += 1

            # === SYMBOLS (cumulatif) ===
            if symbol not in self.history["symbols"]:
                self.history["symbols"][symbol] = {
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "pnl": 0.0,
                    "gross_profit": 0.0,
                    "gross_loss": 0.0,
                    "regime_stats": {},
                    "direction_stats": {
                        "BUY": {"wins": 0, "losses": 0, "pnl": 0.0},
                        "SELL": {"wins": 0, "losses": 0, "pnl": 0.0},
                    },
                }
            s = self.history["symbols"][symbol]
            s["trades"] = s.get("trades", 0) + 1
            s["pnl"] = s.get("pnl", 0.0) + profit
            if profit > 0:
                s["wins"] = s.get("wins", 0) + 1
                s["gross_profit"] = s.get("gross_profit", 0.0) + profit
            elif profit < 0:
                s["losses"] = s.get("losses", 0) + 1
                s["gross_loss"] = s.get("gross_loss", 0.0) + abs(profit)

            # Par régime
            if regime not in s["regime_stats"]:
                s["regime_stats"][regime] = {"trades": 0, "wins": 0, "pnl": 0.0}
            rs = s["regime_stats"][regime]
            rs["trades"] += 1
            rs["pnl"] += profit
            if profit > 0:
                rs["wins"] += 1

            # Par direction
            ds = s["direction_stats"].get(direction, {"wins": 0, "losses": 0, "pnl": 0.0})
            ds["pnl"] += profit
            if profit > 0:
                ds["wins"] += 1
            elif profit < 0:
                ds["losses"] += 1
            s["direction_stats"][direction] = ds

            # === RECENT_TRADES (pour rolling windows exactes) ===
            if "recent_trades" not in self.history:
                self.history["recent_trades"] = []
            self.history["recent_trades"].append(
                {
                    "profit": profit,
                    "symbol": symbol,
                    "regime": regime,
                    "direction": direction,
                    "ts": datetime.utcnow().isoformat(),
                }
            )
            MAX_RECENT = 500
            if len(self.history["recent_trades"]) > MAX_RECENT:
                self.history["recent_trades"] = self.history["recent_trades"][-MAX_RECENT:]

            # Nettoyage : garder max 365 jours
            daily_keys = sorted(self.history["daily"].keys())
            while len(daily_keys) > 365:
                del self.history["daily"][daily_keys.pop(0)]
                daily_keys = sorted(self.history["daily"].keys())

            # Mise à jour des rolling windows
            self._update_rolling()

            self._save()

    def _update_rolling(self) -> None:
        """Met à jour les métriques glissantes (20, 50, 100, 200 trades).
        Utilise la liste exacte des trades individuels (recent_trades).
        Nettoie les fenêtres obsolètes si données insuffisantes."""
        recent = self.history.get("recent_trades", [])
        n_trades = len(recent)

        for window in ROLLING_WINDOWS:
            key = f"last_{window}"
            if n_trades < window:
                # Nettoyer les données périmées de sessions précédentes
                if key in self.history["rolling"]:
                    del self.history["rolling"][key]
                continue

            # Prendre les N trades les plus récents
            subset = recent[-window:]

            wins = sum(1 for t in subset if t["profit"] > 0)
            losses = sum(1 for t in subset if t["profit"] <= 0)
            pnl = sum(t["profit"] for t in subset)
            total = wins + losses

            key = f"last_{window}"
            self.history["rolling"][key] = {
                "trades": total,
                "wins": wins,
                "losses": losses,
                "pnl": round(pnl, 2),
                "wr": round(wins / total * 100, 1) if total > 0 else 0,
                "avg": round(pnl / total, 2) if total > 0 else 0,
            }

    def record_challenge(self, ftmo_data: dict[str, Any]) -> None:
        """Met à jour les métriques du challenge FTMO.

        ftmo_data: dict avec balance, equity, peak_equity, drawdown, status, etc.
        """
        with self._lock:
            c = self.history["challenge"]
            c["last_update"] = datetime.utcnow().isoformat()
            for key in [
                "balance",
                "equity",
                "peak_equity",
                "dd_from_initial",
                "dd_from_peak",
                "profit_progress",
                "profit_remaining",
                "trading_days",
                "days_remaining",
                "total_trades",
                "status",
                "daily_pnl",
                "win_rate",
            ]:
                if key in ftmo_data:
                    c[key] = ftmo_data[key]

            # Progression estimée
            if "profit_progress" in ftmo_data:
                pp_str = str(ftmo_data["profit_progress"]).replace("%", "").replace("+", "")
                try:
                    c["profit_progress_pct"] = float(pp_str)
                except ValueError:
                    c["profit_progress_pct"] = 0.0

            self._save()

    def _dedup_alert(self, metric_key: str) -> bool:
        """Déduplication: une alerte par jour maximum par (type, symbole).

        metric_key format: "TYPE_SYMBOL_YYYY-MM-DD" (ex: "SYMBOL_LOSING_AUDUSD_2026-07-03")
        ou "TYPE_YYYY-MM-DD" pour les alertes globales.
        Le cache mémoire évite de rescanner le JSON à chaque cycle."""
        now = datetime.utcnow().timestamp()
        last = self._last_alert_time.get(metric_key, 0)
        if now - last < 86400:  # 24h de cooldown — une alerte par jour maximum
            return False

        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Extraire le type d'alerte (avant le premier symbole underscore)
        # metric_key = "SYMBOL_LOSING_AUDUSD_2026-07-03" → type="SYMBOL_LOSING"
        parts = metric_key.split("_")
        # Le type est composé des mots avant le symbole (ex: SYMBOL + LOSING)
        # On cherche le pattern: TYPE_DATE ou TYPE_SYMBOL_DATE
        if metric_key.endswith(f"_{today}"):
            base = metric_key[: -len(f"_{today}")]  # retire le suffixe date
            # base = "SYMBOL_LOSING_AUDUSD" ou "CHALLENGE_BEHIND"
            # Le type d'alerte stocké est le préfixe avant le symbole
            # On extrait le type en retirant le symbole si présent
            # Pour les alertes sans symbole: type = base
            # Pour les alertes avec symbole: type = base sans le dernier segment
            pass

        for existing in self.history.get("alerts", []):
            if existing.get("date") != today:
                continue

            existing_type = existing.get("metric", "")
            existing_symbol = existing.get("symbol", "")

            # Vérifier si le type correspond : le metric_key commence par le type stocké
            # ex: "SYMBOL_LOSING_AUDUSD_2026-07-03" commence par "SYMBOL_LOSING" ✅
            if metric_key.startswith(existing_type):
                # Si l'alerte a un symbole, vérifier qu'il correspond aussi
                if existing_symbol:
                    # Extraire le symbole du metric_key: "SYMBOL_LOSING_AUDUSD_..."
                    # Le symbole est entre le type et la date
                    sym_start = len(existing_type) + 1  # +1 pour l'underscore
                    date_part = f"_{today}"
                    if metric_key[sym_start:].endswith(date_part):
                        key_symbol = metric_key[sym_start : -len(date_part)]
                        if key_symbol == existing_symbol:
                            return False  # Même type + même symbole + même date → skip
                else:
                    return False  # Même type global + même date → skip

        self._last_alert_time[metric_key] = now
        return True

    def check_alerts(self) -> list[dict[str, Any]]:
        """Vérifie les seuils d'alerte et retourne les alertes actives."""
        alerts = []
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # 1. WR decline sur 50 trades
        r50 = self.history["rolling"].get("last_50", {})
        r100 = self.history["rolling"].get("last_100", {})
        if r50 and r100 and r100.get("trades", 0) >= 50:
            wr_diff = r100.get("wr", 0) - r50.get("wr", 0)
            if wr_diff > ALERT_THRESHOLDS["wr_50_drop"]:
                metric_key = f"WR_DECLINE_{today}"
                if self._dedup_alert(metric_key):
                    alerts.append(
                        {
                            "level": "WARNING",
                            "metric": "WR_DECLINE",
                            "message": f"WR en baisse de {wr_diff:.1f}% sur 50 trades "
                            f"(était {r100['wr']:.1f}% → {r50['wr']:.1f}%)",
                            "value": wr_diff,
                            "threshold": ALERT_THRESHOLDS["wr_50_drop"],
                            "date": today,
                        }
                    )

        # 2. Profit factor
        for window in [50, 100]:
            key = f"last_{window}"
            r = self.history["rolling"].get(key, {})
            if r.get("trades", 0) >= window and r.get("losses", 0) > 0:
                subset = self._recent_series(window)
                gw = sum(t["profit"] for t in subset if t.get("profit", 0) > 0)
                gl = sum(abs(t["profit"]) for t in subset if t.get("profit", 0) < 0)
                pf = gw / gl if gl > 0 else float("inf")
                if pf < ALERT_THRESHOLDS["pf_below"]:
                    metric_key = f"PF_BELOW_1_{window}_{today}"
                    if self._dedup_alert(metric_key):
                        alerts.append(
                            {
                                "level": "CRITICAL",
                                "metric": "PF_BELOW_1",
                                "message": f"Profit factor {pf:.2f} < 1.0 sur les {window} trades",
                                "value": pf,
                                "threshold": ALERT_THRESHOLDS["pf_below"],
                                "date": today,
                            }
                        )
                elif pf < ALERT_THRESHOLDS["pf_warning"]:
                    metric_key = f"PF_LOW_{window}_{today}"
                    if self._dedup_alert(metric_key):
                        alerts.append(
                            {
                                "level": "WARNING",
                                "metric": "PF_LOW",
                                "message": f"Profit factor {pf:.2f} < 1.2 sur les {window} trades",
                                "value": pf,
                                "threshold": ALERT_THRESHOLDS["pf_warning"],
                                "date": today,
                            }
                        )

        # 3. Symbole problématique
        for sym, sdata in self.history["symbols"].items():
            trades = sdata.get("trades", 0)
            if trades < ALERT_THRESHOLDS["low_volume_trades"]:
                continue
            wins = sdata.get("wins", 0)
            pnl = sdata.get("pnl", 0.0)
            gross_profit = sdata.get("gross_profit", 0.0)
            gross_loss = sdata.get("gross_loss", 0.0)
            wr = wins / trades * 100 if trades > 0 else 0
            pf_sym = gross_profit / gross_loss if gross_loss > 0 else float("inf")
            if pnl < -50 and wr < 40:
                metric_key = f"SYMBOL_LOSING_{sym}_{today}"
                if self._dedup_alert(metric_key):
                    alerts.append(
                        {
                            "level": "WARNING",
                            "metric": "SYMBOL_LOSING",
                            "message": f"{sym}: ${sdata['pnl']:.0f} sur {sdata['trades']} trades "
                            f"(WR {wr:.1f}%, PF {pf_sym:.2f})",
                            "value": sdata["pnl"],
                            "threshold": -50,
                            "symbol": sym,
                            "date": today,
                        }
                    )

        # 4. Challenge progress faible à J+15
        c = self.history["challenge"]
        td = c.get("trading_days", 0)
        pp = c.get("profit_progress_pct", 0)
        if td >= 15 and pp < 30:
            alerts.append(
                {
                    "level": "WARNING",
                    "metric": "CHALLENGE_BEHIND",
                    "message": f"Challenge à J+{td} mais seulement {pp:.1f}% du target "
                    f"— risque de non-respect des 30 jours",
                    "value": pp,
                    "threshold": 30,
                    "date": today,
                }
            )

        # Stocker les alertes — REMPLACE (pas append) pour éviter les doublons
        # 🐛 FIX 3 Juillet: on retire les anciennes alertes du même jour avant d'ajouter les nouvelles
        active = [a for a in alerts if a.get("date") == today]
        if active:
            new_keys = {(a.get("metric", ""), a.get("symbol", "")) for a in active}
            # Garder les alertes des jours précédents + les nouvelles du jour (sans doublons)
            kept = [
                a
                for a in self.history["alerts"]
                if a.get("date") != today or (a.get("metric", ""), a.get("symbol", "")) not in new_keys
            ]
            self.history["alerts"] = kept[-50:] + active
            self._save()

        return alerts

    def _recent_series(self, n_trades: int) -> list[dict[str, Any]]:
        """Retourne les N trades les plus récents (liste individuelle)."""
        recent = self.history.get("recent_trades", [])
        return recent[-n_trades:]

    def generate_report(self) -> dict[str, Any]:
        """Génère un rapport complet de performance."""
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "challenge": self._challenge_summary(),
            "rolling": self._rolling_summary(),
            "symbols": self._symbol_summary(),
            "daily_recent": self._daily_recent(7),
            "alerts": self.check_alerts(),
            "trend": self._trend_analysis(),
            "recommendations": [],
        }

        # Générer des recommandations basées sur l'analyse
        report["recommendations"] = self._generate_recommendations(report)

        # Sauvegarder le rapport
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            with open(REPORT_FILE, "w") as f:
                json.dump(report, f, indent=2, default=str)
        except OSError as e:
            logger.error(f"Impossible de sauvegarder le rapport: {e}")

        return report

    def _challenge_summary(self) -> dict[str, Any]:
        """Résumé de l'avancement du challenge FTMO."""
        c = self.history["challenge"]
        pp = c.get("profit_progress_pct", 0)
        td = c.get("trading_days", 0)
        dr = c.get("days_remaining", 30)
        dd = c.get("dd_from_peak", "0%")

        # Estimation du rythme actuel
        avg_daily_pnl = 0
        daily_data = self.history["daily"]
        if daily_data:
            recent_days = list(daily_data.values())[-10:]
            if recent_days:
                total_pnl = sum(d["pnl"] for d in recent_days)
                n_days = len(recent_days)
                avg_daily_pnl = total_pnl / n_days if n_days > 0 else 0

        # Jours estimés restants pour atteindre le target
        target_remaining = c.get("profit_remaining", 0)
        if isinstance(target_remaining, str):
            try:
                target_remaining = float(target_remaining.replace("$", ""))
            except ValueError:
                target_remaining = 20000
        else:
            target_remaining = float(target_remaining) if target_remaining else 20000

        estimated_days = "∞"
        if avg_daily_pnl > 0:
            est = target_remaining / avg_daily_pnl
            estimated_days = f"{est:.0f}" if est < 365 else ">1 an"

        return {
            "status": c.get("status", "UNKNOWN"),
            "trading_days": td,
            "days_remaining": dr,
            "profit_progress_pct": pp,
            "profit_remaining": target_remaining,
            "drawdown": dd,
            "avg_daily_pnl": round(avg_daily_pnl, 2),
            "estimated_days_to_target": estimated_days,
            "on_track": avg_daily_pnl > 0 and (pp >= 0 or td <= 5),
        }

    def _rolling_summary(self) -> dict[str, Any]:
        """Résumé des métriques glissantes."""
        summary = {}
        for window in ROLLING_WINDOWS:
            key = f"last_{window}"
            if key in self.history["rolling"]:
                r = self.history["rolling"][key]
                pf = "N/A"
                if r["losses"] > 0 and r["trades"] >= window:
                    subset = self._recent_series(window)
                    gw = sum(t["profit"] for t in subset if t.get("profit", 0) > 0)
                    gl = sum(abs(t["profit"]) for t in subset if t.get("profit", 0) < 0)
                    pf = round(gw / gl, 2) if gl > 0 else "∞"
                summary[key] = {
                    "trades": r["trades"],
                    "wr": r["wr"],
                    "pnl": r["pnl"],
                    "avg": r["avg"],
                    "pf": pf,
                }
        return summary

    def _symbol_summary(self) -> dict[str, Any]:
        """Résumé par symbole."""
        summary = {}
        for sym, sdata in sorted(self.history["symbols"].items()):
            if sdata.get("trades", 0) == 0:
                continue
            trades = sdata.get("trades", 0)
            wins = sdata.get("wins", 0)
            pnl = sdata.get("pnl", 0.0)
            gross_profit = sdata.get("gross_profit", 0.0)
            gross_loss = sdata.get("gross_loss", 0.0)
            wr = wins / trades * 100 if trades > 0 else 0
            pf = gross_profit / gross_loss if gross_loss > 0 else "∞"
            summary[sym] = {
                "trades": trades,
                "pnl": round(pnl, 2),
                "wr": round(wr, 1),
                "pf": pf,
                "avg": round(pnl / trades, 2) if trades > 0 else 0,
            }
        return summary

    def _daily_recent(self, n_days: int = 7) -> list[dict[str, Any]]:
        """Derniers N jours de trading."""
        recent = []
        for date_str in sorted(self.history["daily"].keys()):
            d = self.history["daily"][date_str]
            if d["trades"] == 0:
                continue
            wr = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
            recent.append(
                {
                    "date": date_str,
                    "trades": d["trades"],
                    "pnl": round(d["pnl"], 2),
                    "wr": round(wr, 1),
                }
            )
        return recent[-n_days:]

    def _trend_analysis(self) -> dict[str, Any]:
        """Analyse les tendances sur plusieurs fenêtres."""
        trends = {}

        # WR trend: comparer 100 trades récents vs 100 plus anciens
        for window in [50, 100]:
            key = f"last_{window}"
            if key in self.history["rolling"]:
                r = self.history["rolling"][key]
                trends[f"wr_{window}"] = r["wr"]
                trends[f"pnl_{window}"] = r["pnl"]

        # Évolution WR sur 200→100→50 (rétrécissement)
        wr_200 = self.history["rolling"].get("last_200", {}).get("wr", 0)
        wr_100 = self.history["rolling"].get("last_100", {}).get("wr", 0)
        wr_50 = self.history["rolling"].get("last_50", {}).get("wr", 0)
        wr_20 = self.history["rolling"].get("last_20", {}).get("wr", 0)

        trend_direction = "stable"
        if wr_50 < wr_100 and wr_20 < wr_50:
            trend_direction = "declining"
        elif wr_50 > wr_100 and wr_20 > wr_50:
            trend_direction = "improving"

        trends["direction"] = trend_direction
        trends["wr_evolution"] = {"200": wr_200, "100": wr_100, "50": wr_50, "20": wr_20}

        return trends

    def _generate_recommendations(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """Génère des recommandations basées sur l'analyse."""
        recs = []

        # Challenge
        c = report["challenge"]
        if c["estimated_days_to_target"] != "∞" and c["estimated_days_to_target"] != ">1 an":
            try:
                est = int(c["estimated_days_to_target"])
                if est > c["days_remaining"]:
                    recs.append(
                        {
                            "priority": "HIGH",
                            "action": f"Rythme insuffisant: ~{est} jours estimés vs {c['days_remaining']} restants. "
                            f"Envisager d'augmenter risk_mult sur les symboles gagnants.",
                        }
                    )
            except ValueError as e:
                logger.warning(f"PerfMonitor progress estimate failed: {e}")

        # Symboles problématiques
        for sym, sdata in report["symbols"].items():
            pf_val = sdata["pf"]
            if isinstance(pf_val, (int, float)):
                if sdata["trades"] >= 10 and pf_val < 0.8:
                    recs.append(
                        {
                            "priority": "HIGH",
                            "action": f"{sym}: PF {pf_val:.2f} — réduire risk_mult ou désactiver.",
                        }
                    )
                elif sdata["trades"] >= 20 and pf_val < 1.0:
                    recs.append(
                        {
                            "priority": "MEDIUM",
                            "action": f"{sym}: PF {pf_val:.2f} sur {sdata['trades']} trades — surveiller.",
                        }
                    )

        # Tendance WR
        trend = report.get("trend", {})
        if trend.get("direction") == "declining":
            recs.append(
                {
                    "priority": "MEDIUM",
                    "action": f"WR en baisse ({trend.get('wr_evolution', {})}) — "
                    f"surveiller les 50 prochains trades, ajuster les seuils si nécessaire.",
                }
            )

        return recs

    def get_daily_pnl(self, date_str: str | None = None) -> dict[str, Any]:
        """Récupère le PnL d'un jour spécifique ou d'aujourd'hui."""
        if date_str is None:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
        d = self.history["daily"].get(date_str, {})
        return {
            "pnl": d.get("pnl", 0),
            "trades": d.get("trades", 0),
            "wins": d.get("wins", 0),
            "losses": d.get("losses", 0),
        }

    def summary_text(self, detailed: bool = False) -> str:
        """Retourne un texte formaté du rapport."""
        report = self.generate_report()
        c = report["challenge"]
        lines = []
        lines.append("=" * 60)
        lines.append("  📊 RAPPORT DE PERFORMANCE — Robot MT5 FTMO")
        lines.append(f"  {datetime.utcnow().strftime('%d %B %Y %H:%M UTC')}")
        lines.append("=" * 60)

        # Challenge
        lines.append(f"\n🏆 CHALLENGE FTMO: {c['status']}")
        lines.append(f"  Progression: {c['profit_progress_pct']:.1f}% du target")
        lines.append(f"  Restant: ${c['profit_remaining']:,.0f}")
        lines.append(f"  Drawdown: {c['drawdown']}")
        lines.append(f"  Jours: {c['trading_days']} tradés / {c['days_remaining']} restants")
        lines.append(f"  Rythme: ${c['avg_daily_pnl']:.0f}/jour → ~{c['estimated_days_to_target']} jours estimés")

        # Rolling
        lines.append(f"\n📈 TENDANCES GLISSANTES")
        for key, r in sorted(report["rolling"].items()):
            lines.append(f"  {key}: {r['wr']}% WR | ${r['pnl']:+.0f} PnL | PF {r['pf']} | {r['trades']} trades")

        # Symbols
        lines.append(f"\n💰 PERFORMANCE PAR SYMBOLE")
        for sym, sdata in sorted(report["symbols"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            lines.append(
                f"  {sym:10s}: ${sdata['pnl']:>+8.2f} | {sdata['trades']:4d} trades | "
                f"WR {sdata['wr']:5.1f}% | PF {str(sdata['pf']):>5s}"
            )

        # Alerts
        alerts = report["alerts"]
        if alerts:
            lines.append(f"\n⚠️  ALERTES ({len(alerts)})")
            for a in alerts:
                icon = "🔴" if a["level"] == "CRITICAL" else "🟡"
                lines.append(f"  {icon} [{a['metric']}] {a['message']}")

        # Recommendations
        recs = report["recommendations"]
        if recs:
            lines.append(f"\n💡 RECOMMANDATIONS")
            for r in recs:
                lines.append(f"  [{r['priority']}] {r['action']}")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)


# Instance singleton pour usage global (thread-safe)
_monitor = None
_monitor_lock = threading.Lock()


def get_monitor() -> PerformanceMonitor:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            # Double-check après acquisition du lock
            if _monitor is None:
                _monitor = PerformanceMonitor()
    return _monitor


# Fonction appelable depuis main.py pour enregistrer un trade
def record_trade(symbol: str, profit: float, regime: str = "UNKNOWN", direction: str = "BUY") -> None:
    """Enregistre un trade dans le monitoring. Utilisable depuis main.py."""
    try:
        import traceback

        stack = traceback.extract_stack()
        caller = stack[-3] if len(stack) >= 3 else stack[-1]
        logger.info(
            f"[PERF:D] record_trade({symbol}, {profit}, {regime}, {direction}) "
            f"appelé depuis {caller.filename}:{caller.lineno} in {caller.name}"
        )
        pm = get_monitor()
        pm.record_trade(symbol, profit, regime, direction)
    except Exception as e:
        logger.error(f"PerfMonitor record_trade échoué: {e}")


# Fonction appelable depuis main.py pour mettre à jour le challenge
def update_challenge(ftmo_data: dict[str, Any]) -> None:
    """Met à jour les métriques du challenge."""
    try:
        pm = get_monitor()
        pm.record_challenge(ftmo_data)
    except Exception as e:
        logger.exception(f"PerfMonitor update_challenge échoué: {e}")


if __name__ == "__main__":
    # Test — génère un rapport
    pm = PerformanceMonitor()
    report = pm.generate_report()
    print(pm.summary_text(detailed=True))
    print(f"\n✅ Rapport sauvegardé dans {REPORT_FILE}")
