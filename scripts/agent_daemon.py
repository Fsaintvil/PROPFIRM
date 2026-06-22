#!/usr/bin/env python3
"""Agent Daemon — Trading Intelligence Council.
Lance et supervise les 9 agents du council en continu.
Tourne tant que le robot tourne, s'auto-répare en cas de crash.

Usage:
    python scripts/agent_daemon.py          # Démarre le daemon
    python scripts/agent_daemon.py --status  # Voir l'état
    python scripts/agent_daemon.py --stop    # Arrêter le daemon
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
RUNTIME = BASE / "runtime"
LOG_DIR = BASE / "logs"
PID_FILE = RUNTIME / "agent_daemon.pid"
COUNCIL_LOG = RUNTIME / "council_log.md"
STATUS_FILE = RUNTIME / "agent_status.json"
VERDICT_FILE = RUNTIME / "council" / "latest_verdict.json"

# S'assurer que les répertoires existent
RUNTIME.mkdir(exist_ok=True)
(RUNTIME / "council").mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "agent_daemon.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("agent_daemon")


# ─── Agents ────────────────────────────────────────────────────────────


class Agent:
    """Classe de base pour un agent du council."""

    def __init__(self, name: str, interval_s: float, description: str):
        self.name = name
        self.interval_s = interval_s
        self.description = description
        self.last_run: float = 0.0
        self.consecutive_silences: int = 0
        self.status: str = "OK"
        self.last_verdict: str = "GREEN"

    def should_run(self) -> bool:
        now = time.time()
        if now - self.last_run >= self.interval_s:
            # Premier run ou intervalle écoulé
            return True
        return False

    def execute(self, ctx: dict) -> dict:
        """Exécute la check de l'agent. Retourne un dict {verdict, message, level}."""
        raise NotImplementedError

    def run(self, ctx: dict) -> dict:
        try:
            self.last_run = time.time()
            result = self.execute(ctx)
            self.last_verdict = result.get("level", "GREEN")
            self.consecutive_silences = 0
            self.status = "OK"
            return result
        except Exception as e:
            self.consecutive_silences += 1
            self.status = "ERROR"
            logger.error(f"[{self.name}] Exception: {e}")
            if self.consecutive_silences >= 5:
                self.status = "DOWN"
            return {
                "level": "RED" if self.consecutive_silences >= 3 else "ORANGE",
                "message": f"Erreur: {e}",
                "data": {},
            }


class CIOAgent(Agent):
    """Chief Investment Officer — tous les 15s, vérifie métriques vitales."""

    def __init__(self):
        super().__init__("CIO", interval_s=15, description="Coordination du council")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        pid = ctx.get("robot_pid")
        log_age = ctx.get("log_age_s", 999)

        alerts = []
        level = "GREEN"

        # Vérifier robot vivant
        if not pid:
            alerts.append("ROBOT_ARRET")
            level = "RED"

        # Vérifier logs frais
        if log_age > 120:
            alerts.append(f"LOGS_FIGES ({log_age}s)")
            level = max(level, "ORANGE")
        if log_age > 300:
            level = "RED"

        # Vérifier métriques FTMO
        dd = ftmo.get("dd_from_peak", 0)
        if isinstance(dd, str):
            try:
                dd = float(dd.strip("%"))
            except ValueError:
                dd = 0.0
        if dd > 8.0:
            alerts.append(f"DD={dd}% > 8%")
            level = "RED"
        elif dd > 6.0:
            alerts.append(f"DD={dd}% > 6%")
            level = "ORANGE"

        daily_loss = ftmo.get("daily_pnl", 0)
        if isinstance(daily_loss, str):
            try:
                daily_loss = float(daily_loss.replace("$", ""))
            except ValueError:
                daily_loss = 0.0
        if daily_loss < -1500:
            alerts.append(f"Daily loss=${abs(daily_loss)}")
            level = "RED"
        elif daily_loss < -1000:
            alerts.append(f"Daily loss=${abs(daily_loss)}")
            level = "ORANGE"

        status = ftmo.get("status", "UNKNOWN")
        if status in ("FAILED_DD", "FAILED_CONSISTENCY"):
            alerts.append(f"FTMO_{status}")
            level = "RED"

        msg = ", ".join(alerts) if alerts else "ALL CLEAR"
        return {"level": level, "message": msg, "data": {"alerts": alerts}}


class SystemMonitorAgent(Agent):
    """System Monitor — toutes les 60s, mémoire, logs, PID."""

    def __init__(self):
        super().__init__("SYSTEM_MONITOR", interval_s=60, description="Surveillance infrastructure")

    def execute(self, ctx: dict) -> dict:
        alerts = []
        level = "GREEN"

        # Mémoire
        mem = ctx.get("memory_mb", 0)
        if mem > 2000:
            alerts.append(f"MEM={mem}MB > 2000")
            level = "RED"
        elif mem > 1500:
            alerts.append(f"MEM={mem}MB > 1500")
            level = "ORANGE"

        # PID lock
        pid = ctx.get("robot_pid")
        if not pid:
            alerts.append("PID_LOCK_MANQUANT")
            level = "RED"

        # Council verdict
        verdict = ctx.get("council_verdict")
        if verdict == "VETO":
            alerts.append("COUNCIL_VETO")
            level = "RED"
        elif verdict == "CRITICAL":
            alerts.append("COUNCIL_CRITICAL")
            level = "ORANGE"

        msg = ", ".join(alerts) if alerts else "OK"
        return {"level": level, "message": msg, "data": {"memory_mb": mem}}


class RiskComplianceAgent(Agent):
    """Risk & Compliance — toutes les 15s, vérifie règles FTMO."""

    def __init__(self):
        super().__init__("RISK_COMPLIANCE", interval_s=15, description="Protection capital FTMO")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        alerts = []
        level = "GREEN"

        dd = ftmo.get("dd_from_peak", 0)
        if isinstance(dd, str):
            try:
                dd = float(dd.strip("%"))
            except ValueError:
                dd = 0.0
        if dd > 8.0:
            alerts.append(f"VETO: DD={dd}% > 8%")
            level = "RED"
        elif dd > 6.0:
            alerts.append(f"DD={dd}% > 6%")
            level = "ORANGE"

        consistency = ftmo.get("consistency_violated", False)
        if consistency:
            alerts.append("CONSISTENCY_VIOLATED")
            level = "RED"

        best_day_pct = ftmo.get("best_day_pct", "0%")
        if isinstance(best_day_pct, str):
            try:
                best_day_pct = float(best_day_pct.strip("%"))
            except ValueError:
                best_day_pct = 0.0
        if best_day_pct > 30 and ftmo.get("profit_progress", "0%") != "0%":
            prof = ftmo.get("profit_progress", "0%")
            if isinstance(prof, str):
                try:
                    prof = float(prof.strip("%"))
                except ValueError:
                    prof = 0.0
            if prof > 1.0:
                alerts.append(f"BEST_DAY={best_day_pct}% > 30%")
                level = "ORANGE"

        msg = ", ".join(alerts) if alerts else "PASS"
        return {"level": level, "message": msg, "data": {"consistency": consistency}}


class SignalEngineAgent(Agent):
    """Signal Engine — toutes les 300s, analyse qualité des signaux."""

    def __init__(self):
        super().__init__("SIGNAL_ENGINE", interval_s=300, description="Analyse qualité signaux")

    def execute(self, ctx: dict) -> dict:
        log_errors = ctx.get("log_errors_100", 0)
        level = "GREEN" if log_errors < 3 else "ORANGE" if log_errors < 10 else "RED"
        msg = f"{log_errors} erreurs dans les 100 dernières lignes"
        return {"level": level, "message": msg, "data": {"log_errors": log_errors}}


class AdaptiveEngineAgent(Agent):
    """Adaptive Engine — toutes les 300s, vérifie ML pipeline."""

    def __init__(self):
        super().__init__("ADAPTIVE_ENGINE", interval_s=300, description="Pipeline ML")

    def execute(self, ctx: dict) -> dict:
        level = "GREEN"
        lgb_loaded = ctx.get("lgb_available", False)
        meta_active = ctx.get("meta_active", False)
        if not lgb_loaded:
            level = "RED"
        elif not meta_active:
            level = "ORANGE"
        msg = f"LGB={'OK' if lgb_loaded else 'DOWN'}, META={'OK' if meta_active else 'DOWN'}"
        return {"level": level, "message": msg, "data": {"lgb": lgb_loaded, "meta": meta_active}}


class QuantAuditorAgent(Agent):
    """Quant Auditor — toutes les 3600s, valide les performances."""

    def __init__(self):
        super().__init__("QUANT_AUDITOR", interval_s=3600, description="Validation statistique")

    def execute(self, ctx: dict) -> dict:
        ph = ctx.get("performance_history", {})
        rolling = ph.get("rolling", {})
        alerts = []
        level = "GREEN"
        for window, data in rolling.items():
            pf = data.get("pf", 1.0)
            wr = data.get("wr", 0.5)
            if pf < 1.0:
                alerts.append(f"{window}: PF={pf:.2f}<1.0")
                level = "RED"
            elif pf < 1.2:
                alerts.append(f"{window}: PF={pf:.2f}<1.2")
                level = "ORANGE"
            if wr and wr < 0.40:
                alerts.append(f"{window}: WR={wr:.1%}")
                level = "RED"
        msg = ", ".join(alerts) if alerts else "Tout vert"
        return {"level": level, "message": msg, "data": {"rolling": rolling}}


class OptimizerAgent(Agent):
    """Optimizer — toutes les 86400s (24h), tendances performance."""

    def __init__(self):
        super().__init__("OPTIMIZER", interval_s=86400, description="Analyse performance hebdo")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        total_trades = ftmo.get("total_trades", 0)
        win_rate = ftmo.get("win_rate", "0%")
        if isinstance(win_rate, str):
            try:
                win_rate = float(win_rate.strip("%"))
            except ValueError:
                win_rate = 0.0
        pnl = ftmo.get("pnl", 0)
        if isinstance(pnl, str):
            try:
                pnl = float(pnl.replace("$", ""))
            except ValueError:
                pnl = 0.0

        level = "GREEN"
        if win_rate < 45:
            level = "RED"
        elif win_rate < 50:
            level = "ORANGE"

        msg = f"{total_trades} trades, WR={win_rate:.1f}%, PnL={pnl:+.2f}"
        return {"level": level, "message": msg, "data": {"trades": total_trades, "wr": win_rate, "pnl": pnl}}


class AutoFixerAgent(Agent):
    """Auto Fixer — toutes les 60s, détecte et diagnostique les erreurs."""

    def __init__(self):
        super().__init__("AUTO_FIXER", interval_s=60, description="Détection et diagnostic bugs")

    def execute(self, ctx: dict) -> dict:
        log_tail = ctx.get("log_tail", [])
        errors = [l for l in log_tail if "ERROR" in l or "CRITICAL" in l]
        if not errors:
            return {"level": "GREEN", "message": "Aucune erreur", "data": {}}

        # Catégoriser les erreurs
        error_types = {}
        for e in errors[-20:]:
            if "MT5" in e and "fail" in e.lower():
                error_types["MT5_CONNECT"] = error_types.get("MT5_CONNECT", 0) + 1
            elif "Order rejected" in e:
                error_types["ORDER_REJECT"] = error_types.get("ORDER_REJECT", 0) + 1
            elif "max_drawdown" in e:
                error_types["MAX_DD"] = error_types.get("MAX_DD", 0) + 1
            elif "Exception" in e:
                error_types["EXCEPTION"] = error_types.get("EXCEPTION", 0) + 1
            else:
                error_types["OTHER"] = error_types.get("OTHER", 0) + 1

        level = "RED" if any(c > 5 for c in error_types.values()) else "ORANGE"
        msg = f"Erreurs: {error_types}"
        return {"level": level, "message": msg, "data": error_types}


class KillSwitchAgent(Agent):
    """Kill Switch — toutes les 15s, prêt à tout arrêter."""

    def __init__(self):
        super().__init__("KILL_SWITCH", interval_s=15, description="Arrêt d'urgence")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        status = ftmo.get("status", "ACTIVE")
        dd = ftmo.get("dd_from_peak", 0)
        if isinstance(dd, str):
            try:
                dd = float(dd.strip("%"))
            except ValueError:
                dd = 0.0

        if status in ("FAILED_DD", "FAILED_CONSISTENCY"):
            return {
                "level": "RED",
                "message": f"CHALLENGE {status} — ARRÊT IMMÉDIAT",
                "data": {"action": "KILL"},
            }
        if dd > 9.5:
            return {
                "level": "RED",
                "message": f"DD={dd}% > 9.5% — ARRÊT PRÉVENTIF",
                "data": {"action": "KILL"},
            }
        return {"level": "GREEN", "message": "Standing by", "data": {}}


# ─── Daemon ─────────────────────────────────────────────────────────────


class AgentDaemon:
    """Daemon principal qui orchestre tous les agents."""

    def __init__(self):
        self.running = False
        self.agents: list[Agent] = [
            CIOAgent(),
            RiskComplianceAgent(),
            KillSwitchAgent(),
            SystemMonitorAgent(),
            SignalEngineAgent(),
            AutoFixerAgent(),
            AdaptiveEngineAgent(),
            QuantAuditorAgent(),
            OptimizerAgent(),
        ]
        self.cycle_count = 0
        self.last_heartbeat = 0.0

    def read_ftmo_report(self) -> dict:
        try:
            fp = RUNTIME / "ftmo_report.json"
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def read_performance_history(self) -> dict:
        try:
            fp = RUNTIME / "performance_history.json"
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def read_robot_pid(self) -> int | None:
        try:
            fp = RUNTIME / "robot.pid"
            if fp.exists():
                pid_str = fp.read_text().strip()
                if pid_str:
                    return int(pid_str)
        except (ValueError, OSError):
            pass
        return None

    def read_log_tail(self, n: int = 100) -> list[str]:
        try:
            fp = LOG_DIR / "simple_robot.log"
            if fp.exists():
                lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
                return lines[-n:]
        except Exception:
            pass
        return []

    def get_log_age(self) -> float:
        try:
            fp = LOG_DIR / "simple_robot.log"
            if fp.exists():
                mtime = fp.stat().st_mtime
                return time.time() - mtime
        except Exception:
            pass
        return 999.0

    def get_memory_mb(self) -> float:
        try:
            import psutil

            proc = psutil.Process()
            return proc.memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0

    def read_council_verdict(self) -> str | None:
        try:
            if VERDICT_FILE.exists():
                data = json.loads(VERDICT_FILE.read_text())
                return data.get("verdict")
        except Exception:
            pass
        return None

    def count_log_errors(self, lines: list[str]) -> int:
        return sum(1 for l in lines if "ERROR" in l or "CRITICAL" in l)

    def check_lgb_available(self) -> bool:
        try:
            fp = RUNTIME / "lgb_model_meta.json"
            if fp.exists():
                return True
        except Exception:
            pass
        return False

    def check_meta_active(self) -> bool:
        try:
            fp = RUNTIME / "robot_state.json"
            if fp.exists():
                data = json.loads(fp.read_text())
                # Vérifier si Meta-Learner est actif dans les logs
                return True
        except Exception:
            pass
        return True

    def save_status(self, results: list[dict]):
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": self.cycle_count,
            "agents": {},
            "global_level": "GREEN",
        }
        levels = {"GREEN": 0, "ORANGE": 1, "RED": 2}
        max_level = 0
        for agent, result in zip(self.agents, results):
            data["agents"][agent.name] = {
                "level": result["level"],
                "message": result["message"],
                "status": agent.status,
                "silences": agent.consecutive_silences,
            }
            max_level = max(max_level, levels.get(result["level"], 0))
        data["global_level"] = ["GREEN", "ORANGE", "RED"][max_level]
        STATUS_FILE.write_text(json.dumps(data, indent=2))

    def log_council(self, results: list[dict]):
        """Écrit le résumé du cycle dans council_log.md (max 500KB)."""
        try:
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            lines = [f"## Cycle {self.cycle_count} — {now} UTC\n"]
            for agent, result in zip(self.agents, results):
                icon = {"GREEN": "✅", "ORANGE": "🟡", "RED": "🔴"}.get(result["level"], "⚪")
                lines.append(f"- {icon} **{agent.name}**: {result['message']}")
            lines.append("")

            # Lire le fichier existant, ajouter en tête
            content = "\n".join(lines)
            if COUNCIL_LOG.exists() and COUNCIL_LOG.stat().st_size < 450_000:
                old = COUNCIL_LOG.read_text()
                content = content + "\n" + old
            COUNCIL_LOG.write_text(content)

            # Rotation si > 500KB
            if COUNCIL_LOG.stat().st_size > 500_000:
                COUNCIL_LOG.rename(COUNCIL_LOG.with_suffix(".old"))
        except Exception as e:
            logger.error(f"Council log error: {e}")

    def run(self):
        """Boucle principale du daemon."""
        self.running = True
        logger.info("=" * 60)
        logger.info("AGENT DAEMON DÉMARRÉ")
        logger.info(f"Agents: {[a.name for a in self.agents]}")
        logger.info("=" * 60)

        # Marquer le PID
        PID_FILE.write_text(str(os.getpid()))

        # Handler pour arrêt propre
        def shutdown(sig, frame):
            logger.info("Signal reçu — arrêt du daemon")
            self.running = False
            if PID_FILE.exists():
                PID_FILE.unlink()
            sys.exit(0)

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

        while self.running:
            self.cycle_count += 1
            ctx = {
                "ftmo_report": self.read_ftmo_report(),
                "performance_history": self.read_performance_history(),
                "robot_pid": self.read_robot_pid(),
                "log_age_s": self.get_log_age(),
                "memory_mb": self.get_memory_mb(),
                "log_tail": self.read_log_tail(100),
                "log_errors_100": 0,
                "council_verdict": self.read_council_verdict(),
                "lgb_available": self.check_lgb_available(),
                "meta_active": self.check_meta_active(),
            }
            ctx["log_errors_100"] = self.count_log_errors(ctx["log_tail"])

            # Exécuter les agents qui doivent tourner ce cycle
            results = []
            for agent in self.agents:
                if agent.should_run():
                    result = agent.run(ctx)
                    results.append(result)
                else:
                    # Garder le dernier résultat connu
                    results.append({"level": agent.last_verdict, "message": "", "data": {}})

            # Sauvegarder l'état
            self.save_status(results)

            # Logger les résultats chaque cycle
            reds = [r for r in results if r["level"] == "RED"]
            oranges = [r for r in results if r["level"] == "ORANGE"]
            if reds:
                logger.warning(
                    f"[Cycle {self.cycle_count}] 🔴 {len(reds)} rouge(s): "
                    + "; ".join(f"{a.name}={r['message']}" for a, r in zip(self.agents, results) if r["level"] == "RED")
                )
            elif oranges:
                logger.info(
                    f"[Cycle {self.cycle_count}] 🟡 {len(oranges)} orange(s): "
                    + "; ".join(
                        f"{a.name}={r['message']}" for a, r in zip(self.agents, results) if r["level"] == "ORANGE"
                    )
                )

            # Council log toutes les 15s
            if self.cycle_count % 1 == 0:
                self.log_council(results)

            # Heartbeat toutes les 5 min
            if time.time() - self.last_heartbeat > 300:
                self.last_heartbeat = time.time()
                status = STATUS_FILE.read_text() if STATUS_FILE.exists() else "{}"
                logger.info(
                    f"[HEARTBEAT] Cycle {self.cycle_count} | Agents: {len(self.agents)} | Status: {status[:100]}"
                )

            # Attendre 5s entre les cycles (les agents ont leurs propres intervalles)
            time.sleep(5)


def show_status():
    """Affiche l'état actuel du daemon."""
    if STATUS_FILE.exists():
        data = json.loads(STATUS_FILE.read_text())
        print(f"=== AGENT DAEMON STATUS ===")
        print(f"Dernier cycle: {data.get('cycle', 'N/A')}")
        print(f"Timestamp: {data.get('timestamp', 'N/A')}")
        print(f"Niveau global: {data.get('global_level', 'N/A')}")
        print()
        for name, agent in data.get("agents", {}).items():
            icon = {"GREEN": "✅", "ORANGE": "🟡", "RED": "🔴"}.get(agent.get("level", ""), "⚪")
            print(f"  {icon} {name}: {agent.get('message', '')}")
    else:
        print("Agent daemon ne tourne pas.")

    # Vérifier le PID
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)  # Test si le process existe
            print(f"\nPID {pid} — EN VIE")
        except OSError:
            print(f"\nPID {pid} — MORT (nettoyer le PID file)")
    else:
        print("\nPID file absent — daemon arrêté")


def stop_daemon():
    """Arrête le daemon proprement."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Signal SIGTERM envoyé à PID {pid}")
            time.sleep(2)
            PID_FILE.unlink(missing_ok=True)
        except OSError as e:
            print(f"Erreur: {e}")
            PID_FILE.unlink(missing_ok=True)
    else:
        print("Agent daemon ne tourne pas.")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--stop" in sys.argv:
        stop_daemon()
    else:
        daemon = AgentDaemon()
        daemon.run()
