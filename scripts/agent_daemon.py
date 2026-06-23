#!/usr/bin/env python3
"""Agent Daemon — Trading Intelligence Council.
Daemon MAÎTRE : démarre AVANT le robot, le lance comme sous-processus,
et les 11 agents communiquent entre eux en permanence pour surveiller,
analyser et protéger le trading.

Architecture:
  agent_daemon.py (ce processus)
    ├── Agent #1: CIO         (15s) → synthétise TOUS les agents
    ├── Agent #2: RISK        (15s) → règles FTMO, veto
    ├── Agent #3: KILL_SWITCH (15s) → arrêt d'urgence
    ├── Agent #4: SYSTEM      (60s) → mémoire, logs
    ├── Agent #5: DATA        (300s)→ fraîcheur données
    ├── Agent #6: PERF        (300s)→ performance, fuites
    ├── Agent #7: SIGNAL      (300s)→ qualité signaux
    ├── Agent #8: AUTO_FIXER  (60s) → diagnostic erreurs
    ├── Agent #9: ADAPTIVE    (300s)→ pipeline ML
    ├── Agent #10: QUANT      (3600s)→ validation stats
    └── Agent #11: OPTIMIZER  (86400s)→ perf hebdo
    │
    └── main.py (sous-processus) ← lancé par le daemon

Communication inter-agents :
  Chaque agent voit le verdict de TOUS les autres agents via
  ctx["agent_verdicts"] et peut poster des messages via ctx["council_board"].

Usage:
    python scripts/agent_daemon.py          # Démarre daemon + robot
    python scripts/agent_daemon.py --status  # Voir l'état
    python scripts/agent_daemon.py --stop    # Arrêter daemon + robot
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
ROBOT_PID_FILE = RUNTIME / "robot.pid"

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

LEVEL_ORDER = {"GREEN": 0, "ORANGE": 1, "RED": 2}


def _max_level(levels: list[str]) -> str:
    """Retourne le niveau le plus élevé dans une liste."""
    return max(levels, key=lambda x: LEVEL_ORDER.get(x, 0))


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
        self.last_message: str = ""

    def should_run(self) -> bool:
        now = time.time()
        if now - self.last_run >= self.interval_s:
            return True
        return False

    def execute(self, ctx: dict) -> dict:
        """Exécute la check de l'agent.
        ctx contient :
          - agent_verdicts: dict[str, dict] → verdicts de TOUS les agents
          - council_board: list[dict] → messages inter-agents
          - ftmo_report, robot_pid, log_age_s, memory_mb, etc.
        Retourne un dict {level, message, data}.
        """
        raise NotImplementedError

    def run(self, ctx: dict) -> dict:
        try:
            self.last_run = time.time()
            result = self.execute(ctx)
            self.last_verdict = result.get("level", "GREEN")
            self.last_message = result.get("message", "")
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

    def post_message(self, ctx: dict, to: str, msg: str, level: str = "INFO"):
        """Poste un message sur le council board visible par tous les agents."""
        board = ctx.get("council_board", [])
        board.append(
            {
                "from": self.name,
                "to": to,
                "message": msg,
                "level": level,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def read_other_agents(self, ctx: dict) -> dict[str, dict]:
        """Lit les verdicts des autres agents."""
        return ctx.get("agent_verdicts", {})


# ─── Agents Implémentation ──────────────────────────────────────────────


class CIOAgent(Agent):
    """Chief Investment Officer — tous les 15s.
    Synthétise les verdicts de TOUS les agents et coordonne le council."""

    def __init__(self):
        super().__init__("CIO", interval_s=15, description="Coordination du council")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        pid = ctx.get("robot_pid")
        log_age = ctx.get("log_age_s", 999)
        all_verdicts = self.read_other_agents(ctx)

        alerts = []
        level = "GREEN"

        # ── Synthèse inter-agents ──
        agent_levels: list[str] = []
        for name, verdict in all_verdicts.items():
            if name == self.name:
                continue
            v_level = verdict.get("level", "GREEN")
            agent_levels.append(v_level)
            v_msg = verdict.get("message", "")
            if v_level == "RED":
                alerts.append(f"{name}: {v_msg}")
            elif v_level == "ORANGE":
                alerts.append(f"{name}: {v_msg}")

        # Niveau = le plus élevé parmi tous les agents
        if agent_levels:
            level = _max_level(agent_levels)

        # ── Vérifier robot vivant ──
        if not pid:
            alerts.append("ROBOT_ARRET")
            level = "RED"

        # ── Vérifier logs frais ──
        if log_age > 120:
            alerts.append(f"LOGS_FIGES ({log_age}s)")
            level = _max_level([level, "ORANGE"])
        if log_age > 300:
            level = "RED"

        # ── Vérifier métriques FTMO ──
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
            level = _max_level([level, "ORANGE"])

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
            level = _max_level([level, "ORANGE"])

        status = ftmo.get("status", "UNKNOWN")
        if status in ("FAILED_DD", "FAILED_CONSISTENCY"):
            alerts.append(f"FTMO_{status}")
            level = "RED"

        # ── Si plusieurs agents sont RED, poster un message au Kill Switch ──
        red_count = sum(1 for lvl in agent_levels if lvl == "RED")
        if red_count >= 3:
            self.post_message(ctx, "KILL_SWITCH", f"{red_count} agents en ROUGE — risque systémique", "CRITICAL")

        msg = "; ".join(alerts) if alerts else "ALL CLEAR"
        return {"level": level, "message": msg, "data": {"alerts": alerts, "red_count": red_count}}


class SystemMonitorAgent(Agent):
    """System Monitor — toutes les 60s, mémoire, logs, PID."""

    def __init__(self):
        super().__init__("SYSTEM_MONITOR", interval_s=60, description="Surveillance infrastructure")

    def execute(self, ctx: dict) -> dict:
        alerts = []
        level = "GREEN"

        mem = ctx.get("memory_mb", 0)
        if mem > 2000:
            alerts.append(f"MEM={mem}MB > 2000")
            level = "RED"
        elif mem > 1500:
            alerts.append(f"MEM={mem}MB > 1500")
            level = "ORANGE"

        pid = ctx.get("robot_pid")
        if not pid:
            alerts.append("PID_LOCK_MANQUANT")
            level = "RED"

        # Vérifier les autres agents
        other = self.read_other_agents(ctx)
        perf = other.get("PERFORMANCE_ENGINEER", {})
        if perf.get("level") == "RED":
            alerts.append("PERF_ENGINEER_ALERTE")
            level = _max_level([level, "ORANGE"])

        msg = ", ".join(alerts) if alerts else "OK"
        return {"level": level, "message": msg, "data": {"memory_mb": mem}}


class RiskComplianceAgent(Agent):
    """Risk & Compliance — toutes les 15s, vérifie règles FTMO."""

    def __init__(self):
        super().__init__("RISK_COMPLIANCE", interval_s=15, description="Protection capital FTMO")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        other = self.read_other_agents(ctx)
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

        # Si data-manager signale des données périmées, réduire confiance
        data_mgr = other.get("DATA_MANAGER", {})
        if data_mgr.get("level") == "RED":
            alerts.append("DATA_MANAGER_RED — données suspectes, risque accru")
            level = _max_level([level, "ORANGE"])

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

        # Poster un message si erreurs MT5 récurrentes
        if error_types.get("MT5_CONNECT", 0) >= 3:
            self.post_message(
                ctx,
                "SYSTEM_MONITOR",
                f"MT5_CONNECT erreurs x{error_types['MT5_CONNECT']} — vérifier connexion",
                "WARNING",
            )

        return {"level": level, "message": msg, "data": error_types}


class KillSwitchAgent(Agent):
    """Kill Switch — toutes les 15s, arrêt d'urgence.
    Peut être déclenché par les autres agents via le council_board."""

    def __init__(self):
        super().__init__("KILL_SWITCH", interval_s=15, description="Arrêt d'urgence")

    def execute(self, ctx: dict) -> dict:
        ftmo = ctx.get("ftmo_report", {})
        board = ctx.get("council_board", [])
        status = ftmo.get("status", "ACTIVE")
        dd = ftmo.get("dd_from_peak", 0)
        if isinstance(dd, str):
            try:
                dd = float(dd.strip("%"))
            except ValueError:
                dd = 0.0

        alerts = []

        # FTMO challenge rules
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

        # Inter-agent kill requests
        for msg in board:
            if msg.get("to") == "KILL_SWITCH" and msg.get("level") == "CRITICAL":
                alerts.append(f"KILL_REQUEST: {msg.get('from')} — {msg.get('message')}")
                return {
                    "level": "RED",
                    "message": "; ".join(alerts),
                    "data": {"action": "KILL", "source": msg.get("from")},
                }

        return {"level": "GREEN", "message": "Standing by", "data": {}}


class DataManagerAgent(Agent):
    """Data Manager — toutes les 300s, vérifie fraîcheur et intégrité des données."""

    def __init__(self):
        super().__init__("DATA_MANAGER", interval_s=300, description="Qualité des données")

    def execute(self, ctx: dict) -> dict:
        alerts = []
        level = "GREEN"

        # 1. Fraîcheur des logs
        log_age = ctx.get("log_age_s", 999)
        if log_age > 120:
            alerts.append(f"LOGS_STALE ({log_age:.0f}s)")
            level = "ORANGE"
        if log_age > 300:
            level = "RED"

        # 2. Intégrité des fichiers Parquet
        parquet_dir = BASE / "data" / "historical"
        parquet_files: list = []
        if parquet_dir.exists():
            parquet_files = list(parquet_dir.glob("*.parquet"))
            stale_count = 0
            now = time.time()
            for f in parquet_files:
                age_days = (now - f.stat().st_mtime) / 86400
                if age_days > 30:
                    stale_count += 1
            if stale_count > 5:
                alerts.append(f"PARQUET_STALE: {stale_count} fichiers > 30 jours")
                level = max(level, "ORANGE")

        # 3. Taille des fichiers runtime
        if RUNTIME.exists():
            for f in RUNTIME.glob("*.json"):
                size_mb = f.stat().st_size / (1024 * 1024)
                if size_mb > 10:
                    alerts.append(f"RUNTIME_FILE_LARGE: {f.name}={size_mb:.1f}MB")
                    level = max(level, "ORANGE")

        msg = ", ".join(alerts) if alerts else "Données OK"
        return {
            "level": level,
            "message": msg,
            "data": {"parquet_count": len(parquet_files) if parquet_dir.exists() else 0},
        }


class PerformanceEngineerAgent(Agent):
    """Performance Engineer — toutes les 300s, mesure cycle time, mémoire, logs, uptime."""

    def __init__(self):
        super().__init__("PERFORMANCE_ENGINEER", interval_s=300, description="Performance et stabilité")
        self._baseline_memory: float = 0.0
        self._memory_samples: list[float] = []

    def execute(self, ctx: dict) -> dict:
        alerts = []
        level = "GREEN"
        data: dict[str, Any] = {}

        # 1. Mémoire
        mem = ctx.get("memory_mb", 0)
        data["memory_mb"] = round(mem, 1)
        if self._baseline_memory == 0 and mem > 0:
            self._baseline_memory = mem
        self._memory_samples.append(mem)
        if len(self._memory_samples) > 60:
            self._memory_samples.pop(0)

        if mem > 2000:
            alerts.append(f"MEM={mem:.0f}MB > 2000")
            level = "RED"
        elif mem > 1500:
            alerts.append(f"MEM={mem:.0f}MB > 1500")
            level = "ORANGE"
        elif mem > 500 and self._baseline_memory > 0:
            growth_pct = (mem - self._baseline_memory) / self._baseline_memory * 100
            if growth_pct > 50:
                alerts.append(f"MEM_LEAK: +{growth_pct:.0f}% depuis baseline ({self._baseline_memory:.0f}MB)")
                level = "ORANGE"

        # 2. Taille des logs
        total_log_mb = 0.0
        if LOG_DIR.exists():
            for f in LOG_DIR.glob("*.log*"):
                total_log_mb += f.stat().st_size / (1024 * 1024)
        data["logs_mb"] = round(total_log_mb, 1)
        if total_log_mb > 500:
            alerts.append(f"LOGS={total_log_mb:.0f}MB > 500MB")
            level = "ORANGE"
        elif total_log_mb > 1000:
            alerts.append(f"LOGS={total_log_mb:.0f}MB > 1GB")
            level = "RED"

        # 3. Robot vivant ?
        pid = ctx.get("robot_pid")
        if pid:
            data["uptime_h"] = 0
            log_age = ctx.get("log_age_s", 999)
            data["log_age_s"] = log_age
        else:
            alerts.append("ROBOT_DOWN")
            level = "RED"

        # 4. Tendance mémoire
        if len(self._memory_samples) >= 10:
            recent = self._memory_samples[-10:]
            if recent[-1] > recent[0] * 1.1 and recent[-1] - recent[0] > 50:
                alerts.append(f"MEM_DRIFT: +{(recent[-1] - recent[0]) / recent[0] * 100:.0f}% sur 10 échantillons")
                level = max(level, "ORANGE")

        msg = ", ".join(alerts) if alerts else f"OK (mem={data.get('memory_mb', 0)}MB, logs={data.get('logs_mb', 0)}MB)"
        return {"level": level, "message": msg, "data": data}


# ─── Daemon ─────────────────────────────────────────────────────────────


class AgentDaemon:
    """Daemon principal qui orchestre tous les agents.
    Lance le robot comme sous-processus et surveille tout en continu."""

    def __init__(self):
        self.running = False
        self.robot_process: subprocess.Popen | None = None
        self.agent_verdicts: dict[str, dict] = {}  # inter-agent communication
        self.council_board: list[dict] = []  # message board between agents
        self.agents: list[Agent] = [
            CIOAgent(),
            RiskComplianceAgent(),
            KillSwitchAgent(),
            SystemMonitorAgent(),
            DataManagerAgent(),
            PerformanceEngineerAgent(),
            SignalEngineAgent(),
            AutoFixerAgent(),
            AdaptiveEngineAgent(),
            QuantAuditorAgent(),
            OptimizerAgent(),
        ]
        self.cycle_count = 0
        self.last_heartbeat = 0.0
        self.robot_start_attempts = 0

    # ── Gestion du robot sous-processus ──

    def start_robot(self):
        """Lance main.py comme sous-processus."""
        if self.robot_process and self.robot_process.poll() is None:
            logger.info("Robot déjà en cours d'exécution")
            return

        logger.info("Démarrage du robot MOM20x3...")
        try:
            self.robot_process = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=str(BASE),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Écrire le PID dans le fichier (pour compatibilité robot.ps1, main.py)
            ROBOT_PID_FILE.write_text(str(self.robot_process.pid))
            self.robot_start_attempts += 1
            logger.info(f"Robot démarré avec PID {self.robot_process.pid}")
        except Exception as e:
            logger.error(f"ÉCHEC démarrage robot: {e}")
            self.robot_process = None

    def stop_robot(self):
        """Arrête le robot proprement."""
        if self.robot_process and self.robot_process.poll() is None:
            pid = self.robot_process.pid
            logger.info(f"Arrêt du robot PID {pid}...")
            self.robot_process.terminate()
            try:
                self.robot_process.wait(timeout=10)
                logger.info(f"Robot PID {pid} arrêté")
            except subprocess.TimeoutExpired:
                logger.warning(f"Robot PID {pid} ne répond pas — kill")
                self.robot_process.kill()
                self.robot_process.wait(timeout=5)
        self.robot_process = None
        # Nettoyer le PID file
        if ROBOT_PID_FILE.exists():
            ROBOT_PID_FILE.unlink()

    def is_robot_alive(self) -> bool:
        """Vérifie si le robot est toujours en vie."""
        if self.robot_process and self.robot_process.poll() is None:
            return True
        # Vérifier via PID file
        try:
            if ROBOT_PID_FILE.exists():
                pid_str = ROBOT_PID_FILE.read_text().strip()
                if pid_str:
                    pid = int(pid_str)
                    os.kill(pid, 0)
                    return True
        except (OSError, ValueError):
            pass
        return False

    # ── Lecture des métriques ──

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
        # D'abord depuis le sous-processus
        if self.robot_process and self.robot_process.poll() is None:
            return self.robot_process.pid
        # Fallback fichier
        try:
            if ROBOT_PID_FILE.exists():
                pid_str = ROBOT_PID_FILE.read_text().strip()
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
                return True
        except Exception:
            pass
        return True

    # ── Sauvegarde d'état ──

    def save_status(self, results: list[dict]):
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": self.cycle_count,
            "agents": {},
            "global_level": "GREEN",
            "robot_alive": self.is_robot_alive(),
            "council_board_count": len(self.council_board),
        }
        max_level = 0
        for agent, result in zip(self.agents, results):
            data["agents"][agent.name] = {
                "level": result["level"],
                "message": result["message"],
                "status": agent.status,
                "silences": agent.consecutive_silences,
                "last_verdict": agent.last_verdict,
            }
            max_level = max(max_level, LEVEL_ORDER.get(result["level"], 0))
        data["global_level"] = ["GREEN", "ORANGE", "RED"][max_level]
        data["council_board"] = self.council_board[-20:]  # 20 derniers messages
        STATUS_FILE.write_text(json.dumps(data, indent=2, default=str))

    def log_council(self, results: list[dict]):
        """Écrit le résumé du cycle dans council_log.md avec synthèse inter-agents."""
        try:
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            lines = [
                f"## Cycle {self.cycle_count} — {now} UTC\n",
                f"Robot: {'✅ EN VIE' if self.is_robot_alive() else '❌ ARRÊTÉ'}\n",
            ]
            for agent, result in zip(self.agents, results):
                icon = {"GREEN": "✅", "ORANGE": "🟡", "RED": "🔴"}.get(result["level"], "⚪")
                lines.append(f"- {icon} **{agent.name}**: {result['message']}")

            # Ajouter les messages inter-agents récents
            recent_msgs = [m for m in self.council_board if m.get("level") in ("WARNING", "CRITICAL")]
            if recent_msgs:
                lines.append("\n### Messages inter-agents\n")
                for m in recent_msgs[-5:]:
                    lines.append(f"- `{m.get('from')}` → `{m.get('to')}`: {m.get('message')}")

            lines.append("")
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

    # ── Boucle principale ──

    def run(self):
        """Boucle principale du daemon.
        1. Démarre les agents
        2. Lance le robot comme sous-processus
        3. Chaque cycle : exécute les agents, surveille le robot, communique
        """
        self.running = True
        logger.info("=" * 60)
        logger.info("AGENT DAEMON DÉMARRÉ — Mode Maître")
        logger.info(f"Agents: {[a.name for a in self.agents]}")
        logger.info("=" * 60)

        # Marquer le PID du daemon
        PID_FILE.write_text(str(os.getpid()))

        # Handler pour arrêt propre
        def shutdown(sig, frame):
            logger.info("Signal reçu — arrêt complet (daemon + robot)")
            self.running = False
            self.stop_robot()
            if PID_FILE.exists():
                PID_FILE.unlink()
            sys.exit(0)

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

        # ═══ Démarrer le robot AVANT les cycles ═══
        logger.info("Phase d'initialisation — démarrage du robot...")
        self.start_robot()
        time.sleep(3)  # Laisser le temps au robot de s'initialiser

        while self.running:
            self.cycle_count += 1

            # ── Vérifier la santé du robot ──
            if not self.is_robot_alive():
                logger.warning(f"Robot mort (PID fichier ou processus) — redémarrage...")
                self.start_robot()
                # Attendre un peu pour éviter un redémarrage boucle
                time.sleep(5)

            # ── Construire le contexte avec inter-agent communication ──
            ctx = {
                "ftmo_report": self.read_ftmo_report(),
                "performance_history": self.read_performance_history(),
                "robot_pid": self.read_robot_pid(),
                "log_age_s": self.get_log_age(),
                "memory_mb": self.get_memory_mb(),
                "log_tail": self.read_log_tail(100),
                "log_errors_100": 0,
                "lgb_available": self.check_lgb_available(),
                "meta_active": self.check_meta_active(),
                # ── Inter-agent communication ──
                "agent_verdicts": dict(self.agent_verdicts),
                "council_board": self.council_board,
            }
            ctx["log_errors_100"] = self.count_log_errors(ctx["log_tail"])

            # ── Exécuter les agents ──
            results = []
            for agent in self.agents:
                if agent.should_run():
                    result = agent.run(ctx)
                    results.append(result)
                    # Mettre à jour le verdict partagé
                    self.agent_verdicts[agent.name] = {
                        "level": result["level"],
                        "message": result["message"],
                        "status": agent.status,
                    }
                else:
                    results.append({"level": agent.last_verdict, "message": "", "data": {}})

            # ── Sauvegarder et logger ──
            self.save_status(results)
            self.log_council(results)

            # ── Nettoyer le board des vieux messages (garder les 50 derniers) ──
            if len(self.council_board) > 100:
                self.council_board = self.council_board[-50:]

            # ── Logger les résultats ──
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

            # Heartbeat toutes les 5 min
            if time.time() - self.last_heartbeat > 300:
                self.last_heartbeat = time.time()
                status = STATUS_FILE.read_text() if STATUS_FILE.exists() else "{}"
                logger.info(
                    f"[HEARTBEAT] Cycle {self.cycle_count} | Agents: {len(self.agents)} | "
                    f"Robot: {'ALIVE' if self.is_robot_alive() else 'DEAD'} | "
                    f"Status: {status[:120]}"
                )

            time.sleep(5)


# ─── Interface CLI ──────────────────────────────────────────────────────


def show_status():
    """Affiche l'état actuel du daemon avec les messages inter-agents."""
    if STATUS_FILE.exists():
        data = json.loads(STATUS_FILE.read_text())
        print(f"=== AGENT DAEMON STATUS ===")
        print(f"Dernier cycle: {data.get('cycle', 'N/A')}")
        print(f"Timestamp: {data.get('timestamp', 'N/A')}")
        print(f"Niveau global: {data.get('global_level', 'N/A')}")
        print(f"Robot en vie: {'✅ OUI' if data.get('robot_alive') else '❌ NON'}")
        print()
        for name, agent in data.get("agents", {}).items():
            icon = {"GREEN": "✅", "ORANGE": "🟡", "RED": "🔴"}.get(agent.get("level", ""), "⚪")
            print(f"  {icon} {name}: {agent.get('message', '')}")
        # Messages inter-agents
        board = data.get("council_board", [])
        if board:
            print(f"\n--- Council Board ({len(board)} messages) ---")
            for m in board[-5:]:
                print(f"  [{m.get('from')} → {m.get('to')}] {m.get('message')}")
    else:
        print("Agent daemon ne tourne pas.")

    # Vérifier le PID
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            print(f"\nPID {pid} — EN VIE")
        except OSError:
            print(f"\nPID {pid} — MORT (nettoyer le PID file)")
    else:
        print("\nPID file absent — daemon arrêté")


def stop_daemon():
    """Arrête le daemon + robot proprement."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Signal SIGTERM envoyé à PID {pid} (daemon + robot arrêtés)")
            time.sleep(3)
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
