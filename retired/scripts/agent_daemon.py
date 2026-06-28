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

    def propose(
        self,
        ctx: dict,
        title: str,
        description: str,
        priority: str = "MEDIUM",
        impact: str = "",
        effort: str = "MOYEN",
        rationale: str = "",
        proposal_type: str = "optimization",
    ):
        """Poste une proposition structurée sur le proposal_board.

        Types: optimization, alert, fix, monitoring, parameter_tuning, risk_control
        Priorités: CRITICAL, HIGH, MEDIUM, LOW
        Effort: FAIBLE, MOYEN, ÉLEVÉ
        """
        board = ctx.get("proposal_board", [])
        # Éviter les doublons (même titre dans les dernières 24h)
        now = time.time()
        for existing in board:
            if existing.get("title") == title and existing.get("agent") == self.name:
                age_h = (now - existing.get("_timestamp_s", 0)) / 3600
                if age_h < 24 and existing.get("status") in ("OPEN", "IN_PROGRESS"):
                    return  # déjà proposé récemment
        proposal = {
            "id": f"PROP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{len(board) + 1:03d}",
            "agent": self.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_timestamp_s": now,
            "type": proposal_type,
            "title": title,
            "description": description,
            "priority": priority,
            "impact": impact,
            "effort": effort,
            "rationale": rationale,
            "status": "OPEN",
            "status_history": [{"status": "OPEN", "timestamp": datetime.now(timezone.utc).isoformat()}],
        }
        board.append(proposal)
        logger.info(f"[{self.name}] 📋 PROPOSITION {proposal['id']}: {title}")


# ─── Agents Implémentation ──────────────────────────────────────────────


class CIOAgent(Agent):
    """Chief Investment Officer — tous les 15s.
    Synthétise les verdicts de TOUS les agents, priorise les propositions
    et coordonne le council."""

    def __init__(self):
        super().__init__("CIO", interval_s=15, description="Coordination du council")

    def _synthesize_proposals(self, ctx: dict) -> list[dict]:
        """Synthétise et priorise les propositions du conseil.
        Retourne les propositions classées par priorité."""
        board = ctx.get("proposal_board", [])
        if not board:
            return []

        # Marquer comme obsolètes les propositions OPEN > 7 jours
        now = time.time()
        for p in board:
            age_s = now - p.get("_timestamp_s", 0)
            if p.get("status") == "OPEN" and age_s > 7 * 86400:
                p["status"] = "OBSOLETE"
                p["status_history"].append({"status": "OBSOLETE", "timestamp": datetime.now(timezone.utc).isoformat()})

        # Classer par priorité
        priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        open_props = [p for p in board if p.get("status") == "OPEN"]
        open_props.sort(key=lambda p: priority_order.get(p.get("priority", "MEDIUM"), 99))

        # Proposer au CIO de recommander les CRITICAL/HIGH au robot
        for p in open_props[:3]:  # top 3
            if p["priority"] in ("CRITICAL", "HIGH"):
                self.post_message(
                    ctx,
                    "AUTO_FIXER",
                    f"Proposition prioritaire: {p['id']} — {p['title']} ({p['agent']})",
                    "WARNING",
                )

        return open_props

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

        # ── Synthèse des propositions ──
        top_proposals = self._synthesize_proposals(ctx)
        if top_proposals:
            alert_msgs = []
            for p in top_proposals[:3]:
                alert_msgs.append(f"[{p['priority']}] {p['agent']}: {p['title']}")
            if alert_msgs:
                alerts.append("PROPOSITIONS: " + " | ".join(alert_msgs))

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

        # ── Propositions ──
        if mem > 1500:
            self.propose(
                ctx,
                title=f"Mémoire élevée ({mem:.0f}MB) — rotation des logs recommandée",
                description=f"La mémoire du daemon atteint {mem:.0f}MB. Proposer une rotation des logs agressive (max 50MB) et un redémarrage programmé du daemon toutes les 24h.",
                priority="HIGH" if mem > 2000 else "MEDIUM",
                impact=f"Réduit la consommation mémoire de {mem:.0f}MB à <500MB",
                effort="FAIBLE",
                rationale=f"MEM={mem:.0f}MB {'> 2000' if mem > 2000 else '> 1500'}, risque de OOM ou de swap excessif.",
                proposal_type="monitoring",
            )
        if not pid:
            self.propose(
                ctx,
                title="PID lock manquant — vérifier l'intégrité du démarrage",
                description="Le fichier robot.pid est absent ou le processus robot est mort. Activer le heartbeat monitoring avec alerte Telegram/email.",
                priority="CRITICAL",
                impact="Évite les instances dupliquées et les arrêts non détectés",
                effort="MOYEN",
                rationale="PID lock manquant = risque d'instances dupliquées ou de robot arrêté sans alerte.",
                proposal_type="fix",
            )

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

        # ── Propositions ──
        if dd > 5.0:
            self.propose(
                ctx,
                title=f"Drawdown {dd:.1f}% — réduire le risque par symbole",
                description=f"DD à {dd:.1f}% du peak. Proposer de réduire risk_mult de 20% sur les symboles les plus corrélés et activer le mode protection renforcée.",
                priority="HIGH" if dd > 6.0 else "MEDIUM",
                impact=f"Réduit le risque de violation FTMO de {dd:.0f}% à <5%",
                effort="FAIBLE",
                rationale=f"DD={dd:.1f}% approche la zone de danger (8%). Réduction préventive du risque.",
                proposal_type="risk_control",
            )
        if consistency:
            self.propose(
                ctx,
                title="Consistency FTMO violée — lisser les gains quotidiens",
                description=f"Un jour représente {best_day_pct:.0f}% du profit total. Proposer de plafonner le gain journalier à 20% via un trailing plus agressif sur les gros trades gagnants.",
                priority="CRITICAL",
                impact="Évite le FAIL_CONSISTENCY et sauvegarde le challenge",
                effort="MOYEN",
                rationale=f"best_day_pct={best_day_pct:.0f}% > 30%, risque de violation FTMO immédiat.",
                proposal_type="risk_control",
            )

        msg = ", ".join(alerts) if alerts else "PASS"
        return {"level": level, "message": msg, "data": {"consistency": consistency, "dd": dd}}


class SignalEngineAgent(Agent):
    """Signal Engine — toutes les 300s, analyse qualité des signaux."""

    def __init__(self):
        super().__init__("SIGNAL_ENGINE", interval_s=300, description="Analyse qualité signaux")

    def execute(self, ctx: dict) -> dict:
        log_errors = ctx.get("log_errors_100", 0)
        level = "GREEN" if log_errors < 3 else "ORANGE" if log_errors < 10 else "RED"
        msg = f"{log_errors} erreurs dans les 100 dernières lignes"

        # ── Propositions ──
        if log_errors >= 5:
            self.propose(
                ctx,
                title=f"{log_errors} erreurs dans les logs — audit des signaux recommandé",
                description=f"{log_errors} erreurs détectées dans les 100 dernières lignes. Vérifier les rejets d'ordres, les timeouts MT5 et les symboles problématiques. Envisager de réduire le nombre de symboles actifs.",
                priority="HIGH" if log_errors >= 10 else "MEDIUM",
                impact=f"Réduit les erreurs de trading de {log_errors} à <3 / 100 lignes",
                effort="MOYEN",
                rationale=f"{log_errors} erreurs/100 lignes indique un problème récurrent dans la boucle de trading.",
                proposal_type="fix",
            )

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

        # ── Propositions ──
        if not lgb_loaded:
            self.propose(
                ctx,
                title="LightGBM désactivé — réactivation planifiée",
                description="Le pipeline LightGBM est down. Planifier une réactivation après 500 trades propres avec re-calibration sur les données réelles. Activer d'abord le mode collecte de features.",
                priority="LOW",
                impact="Amélioration potentielle du Win Rate de +3-5%",
                effort="ÉLEVÉ",
                rationale="LightGBM peut améliorer la sélection de signaux mais nécessite des données non contaminées.",
                proposal_type="optimization",
            )
        if not meta_active:
            self.propose(
                ctx,
                title="Meta-Learner désactivé — diagnostic nécessaire",
                description="Le Meta-Learner n'est pas actif. Vérifier le fichier meta_learner.json et la calibration des 3 trackers. Proposer une réinitialisation des poids si le déséquilibre persiste.",
                priority="MEDIUM",
                impact="Meilleure allocation entre les stratégies MOM20x3 et les prédicteurs ML",
                effort="MOYEN",
                rationale="Meta-Learner = arbitrage intelligent entre les signaux. Son absence réduit l'edge.",
                proposal_type="fix",
            )

        return {"level": level, "message": msg, "data": {"lgb": lgb_loaded, "meta": meta_active}}


class QuantAuditorAgent(Agent):
    """Quant Auditor — toutes les 3600s, valide les performances et propose des ajustements."""

    def __init__(self):
        super().__init__("QUANT_AUDITOR", interval_s=3600, description="Validation statistique")

    def execute(self, ctx: dict) -> dict:
        ph = ctx.get("performance_history", {})
        rolling = ph.get("rolling", {})
        alerts = []
        level = "GREEN"
        worst_pf = 1.0
        worst_window = ""
        for window, data in rolling.items():
            pf = data.get("pf", 1.0)
            wr = data.get("wr", 0.5)
            if pf < worst_pf:
                worst_pf = pf
                worst_window = window
            if pf < 1.0:
                alerts.append(f"{window}: PF={pf:.2f}<1.0")
                level = "RED"
            elif pf < 1.2:
                alerts.append(f"{window}: PF={pf:.2f}<1.2")
                level = "ORANGE"
            if wr and wr < 0.40:
                alerts.append(f"{window}: WR={wr:.1%}")
                level = "RED"

        # ── Propositions ──
        if worst_pf < 1.0:
            self.propose(
                ctx,
                title=f"Profit Factor {worst_pf:.2f} sur {worst_window} — réduction de risque recommandée",
                description=f"Le PF sur {worst_window} trades est de {worst_pf:.2f} (sous le seuil de 1.0). Proposer de réduire risk_mult de 30% et de désactiver temporairement les symboles les moins performants jusqu'à retour à PF>1.2.",
                priority="CRITICAL",
                impact="Évite un drawdown prolongé et préserve le capital FTMO",
                effort="FAIBLE",
                rationale=f"PF={worst_pf:.2f} < 1.0 sur {worst_window} trades = système en perte nette. Action immédiate requise.",
                proposal_type="risk_control",
            )
        elif worst_pf < 1.2:
            self.propose(
                ctx,
                title=f"Profit Factor {worst_pf:.2f} sur {worst_window} — surveiller la tendance",
                description=f"PF à {worst_pf:.2f} sur {worst_window} trades. Surveiller l'évolution sur les 50 prochains trades. Si la tendance se dégrade, réduire le risque de 15%.",
                priority="MEDIUM",
                impact="Maintient le PF au-dessus de 1.2",
                effort="FAIBLE",
                rationale=f"PF={worst_pf:.2f} < 1.2 = système en zone de fragilité statistique.",
                proposal_type="monitoring",
            )

        msg = ", ".join(alerts) if alerts else "Tout vert"
        return {"level": level, "message": msg, "data": {"rolling": rolling, "worst_pf": worst_pf}}


class OptimizerAgent(Agent):
    """Optimizer — toutes les 86400s (24h), tendances performance.
    Force de proposition : suggère des optimisations paramétriques concrètes."""

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
        dd = ftmo.get("dd_from_peak", 0)
        if isinstance(dd, str):
            try:
                dd = float(dd.strip("%"))
            except ValueError:
                dd = 0.0
        profit_progress = ftmo.get("profit_progress", "0%")
        if isinstance(profit_progress, str):
            try:
                profit_progress = float(profit_progress.strip("%"))
            except ValueError:
                profit_progress = 0.0

        level = "GREEN"
        if win_rate < 45:
            level = "RED"
        elif win_rate < 50:
            level = "ORANGE"

        # ── Propositions d'optimisation ──
        # 1. Si WR bas mais PnL positif → ajuster les seuils de take-profit
        if win_rate < 50 and pnl > 0:
            self.propose(
                ctx,
                title=f"WR={win_rate:.0f}% bas mais PnL positif — augmenter le ratio RR cible",
                description=f"WR de {win_rate:.0f}% avec PnL de +${pnl:.0f} suggère que les trades gagnants compensent largement les perdants. Proposer d'augmenter le ratio RR minimum à 2.5 et d'élargir les take-profit de 0.5×ATR pour capturer plus de mouvement.",
                priority="MEDIUM",
                impact=f"Peut améliorer le PnL de +15-25% et faire passer le WR à >55%",
                effort="MOYEN",
                rationale=f"WR={win_rate:.0f}% < 50% mais PnL positif = les gros trades gagnants portent le système. Optimiser le TP peut décupler cet avantage.",
                proposal_type="optimization",
            )

        # 2. Si DD > 3% → proposer ajustement trailing
        if dd > 3.0:
            self.propose(
                ctx,
                title=f"Drawdown {dd:.1f}% — renforcer le trailing stop",
                description=f"Le drawdown atteint {dd:.1f}% du peak. Proposer de réduire les intervalles de trailing (passer de 0.50/0.35/0.20/0.10 à 0.40/0.25/0.15/0.08 ×ATR) pour verrouiller les gains plus tôt.",
                priority="HIGH" if dd > 5.0 else "MEDIUM",
                impact=f"Limite le DD maximum à {min(dd + 1, 8):.0f}% au lieu de {dd + 5:.0f}% potentiel",
                effort="FAIBLE",
                rationale=f"DD={dd:.1f}% en hausse, le trailing actuel est trop large pour le régime en cours.",
                proposal_type="parameter_tuning",
            )

        # 3. Si profit_progress < 1% après 10 jours → proposer augmentation risque
        if profit_progress < 1.0 and total_trades > 50:
            self.propose(
                ctx,
                title=f"Progression {profit_progress:.1f}% seulement — augmenter le risque contrôlé",
                description=f"Seulement {profit_progress:.1f}% de progression après {total_trades} trades. Avec un DD de {dd:.1f}% et WR de {win_rate:.0f}%, proposer d'augmenter RISK_PER_TRADE de 0.004 à 0.005 (+25%) sur les symboles les plus performants uniquement.",
                priority="MEDIUM",
                impact=f"Augmente la progression mensuelle de {profit_progress:.0f}% à {profit_progress * 1.25:.0f}%",
                effort="FAIBLE",
                rationale=f"Progression trop lente pour atteindre le target FTMO dans les délais. Marge de sécurité suffisante (DD={dd:.1f}%).",
                proposal_type="parameter_tuning",
            )

        msg = f"{total_trades} trades, WR={win_rate:.1f}%, PnL={pnl:+.2f}"
        return {"level": level, "message": msg, "data": {"trades": total_trades, "wr": win_rate, "pnl": pnl, "dd": dd}}


class AutoFixerAgent(Agent):
    """Auto Fixer — toutes les 60s, détecte et diagnostique les erreurs.
    Force de proposition : suggère des correctifs concrets."""

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

        # ── Propositions de correction ──
        if error_types.get("MT5_CONNECT", 0) >= 3:
            self.propose(
                ctx,
                title=f"MT5_CONNECT : {error_types['MT5_CONNECT']} erreurs — reconnexion forcée recommandée",
                description=f"{error_types['MT5_CONNECT']} erreurs de connexion MT5 détectées. Proposer un redémarrage de la connexion MT5 avec timeout=60000ms et réinitialisation du terminal via MT5Terminal.exe.",
                priority="CRITICAL",
                impact="Rétablit la connexion MT5 et évite les trades manqués",
                effort="MOYEN",
                rationale=f"{error_types['MT5_CONNECT']} échecs de connexion MT5 = risque de trading à l'aveugle ou de positions non surveillées.",
                proposal_type="fix",
            )
            self.post_message(
                ctx,
                "SYSTEM_MONITOR",
                f"MT5_CONNECT erreurs x{error_types['MT5_CONNECT']} — vérifier connexion",
                "WARNING",
            )

        if error_types.get("ORDER_REJECT", 0) >= 3:
            self.propose(
                ctx,
                title=f"Ordres rejetés ×{error_types['ORDER_REJECT']} — vérifier les paramètres de trading",
                description=f"{error_types['ORDER_REJECT']} ordres rejetés. Causes possibles : spread trop large, lots trop petits, symole non tradable. Proposer d'augmenter MAX_SPREAD_POINTS et vérifier les horaires de trading de chaque symbole.",
                priority="HIGH",
                impact="Élimine les rejets d'ordres qui consomment des cycles CPU inutilement",
                effort="FAIBLE",
                rationale=f"{error_types['ORDER_REJECT']} rejets = paramètres inadaptés au marché actuel.",
                proposal_type="fix",
            )

        if error_types.get("EXCEPTION", 0) >= 3:
            self.propose(
                ctx,
                title=f"Exceptions Python ×{error_types['EXCEPTION']} — ajouter des guards",
                description=f"{error_types['EXCEPTION']} exceptions Python non gérées. Analyser les stack traces complètes et ajouter des try/except spécifiques autour des appels MT5 et des calculs numpy/pandas.",
                priority="HIGH",
                impact="Élimine les crashes silencieux et stabilise le robot",
                effort="MOYEN",
                rationale=f"{error_types['EXCEPTION']} exceptions = code fragile qui peut planter à tout moment.",
                proposal_type="fix",
            )

        return {"level": level, "message": msg, "data": error_types}


class KillSwitchAgent(Agent):
    """Kill Switch — toutes les 15s, arrêt d'urgence.
    Peut être déclenché par les autres agents via le council_board.
    Force de proposition : suggère des mesures préventives avant l'urgence."""

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

        # ── Propositions préventives ──
        if dd > 7.0:
            self.propose(
                ctx,
                title=f"DD={dd:.1f}% proche du seuil critique — réduire positions agressivement",
                description=f"DD à {dd:.1f}% (seuil d'arrêt à 9.5%). Proposer de fermer 50% des positions les moins performantes et de passer en mode survie (risk_mult ×0.3) jusqu'à retour sous 5%.",
                priority="CRITICAL",
                impact="Évite l'arrêt automatique et préserve le challenge FTMO",
                effort="FAIBLE",
                rationale=f"DD={dd:.1f}% > 7%, seuil d'arrêt à 9.5%. Marge de sécurité insuffisante pour absorber un choc.",
                proposal_type="risk_control",
            )
        elif dd > 5.0:
            self.propose(
                ctx,
                title=f"DD={dd:.1f}% en zone orange — plan de contingence",
                description=f"DD à {dd:.1f}%. Activer le plan de contingence : réduire le risque de 30%, n'ouvrir que des trades avec RR>3.0, désactiver les symboles les plus volatils.",
                priority="HIGH",
                impact="Maintient le DD sous 7% et évite la zone rouge",
                effort="FAIBLE",
                rationale=f"DD={dd:.1f}% en zone orange, anticiper plutôt que subir.",
                proposal_type="risk_control",
            )

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
        stale_count = 0
        if parquet_dir.exists():
            parquet_files = list(parquet_dir.glob("*.parquet"))
            now = time.time()
            for f in parquet_files:
                age_days = (now - f.stat().st_mtime) / 86400
                if age_days > 30:
                    stale_count += 1
            if stale_count > 5:
                alerts.append(f"PARQUET_STALE: {stale_count} fichiers > 30 jours")
                level = max(level, "ORANGE")

        # 3. Taille des fichiers runtime
        large_files = []
        if RUNTIME.exists():
            for f in RUNTIME.glob("*.json"):
                size_mb = f.stat().st_size / (1024 * 1024)
                if size_mb > 10:
                    large_files.append(f.name)
                    alerts.append(f"RUNTIME_FILE_LARGE: {f.name}={size_mb:.1f}MB")
                    level = max(level, "ORANGE")

        # ── Propositions ──
        if log_age > 180:
            self.propose(
                ctx,
                title=f"Logs figés depuis {log_age:.0f}s — vérifier le heartbeat du robot",
                description=f"Les logs n'ont pas été mis à jour depuis {log_age:.0f} secondes. Activer un heartbeat forcé toutes les 30s et une alerte immédiate si >60s sans écriture.",
                priority="CRITICAL" if log_age > 300 else "HIGH",
                impact="Détecte les arrêts du robot en <60s au lieu de minutes",
                effort="FAIBLE",
                rationale=f"Logs figés depuis {log_age:.0f}s = robot probablement bloqué ou crashé.",
                proposal_type="monitoring",
            )

        if stale_count > 5:
            self.propose(
                ctx,
                title=f"{stale_count} fichiers Parquet obsolètes — rafraîchir les données historiques",
                description=f"{stale_count} fichiers de données historiques ont plus de 30 jours. Lancer un téléchargement MT5 pour mettre à jour les données H1/H4/D1 des 15 symboles.",
                priority="LOW",
                impact="Améliore la qualité des backtests et de la détection de régime",
                effort="MOYEN",
                rationale=f"{stale_count} fichiers > 30 jours = données historiques potentiellement dépassées pour l'analyse de régime.",
                proposal_type="optimization",
            )

        if large_files:
            self.propose(
                ctx,
                title=f"Fichiers runtime volumineux ({', '.join(large_files)}) — rotation nécessaire",
                description=f"Les fichiers runtime suivants dépassent 10MB : {', '.join(large_files)}. Mettre en place une rotation automatique (conserver 7 jours, supprimer les plus anciens).",
                priority="MEDIUM",
                impact="Libère de l'espace disque et accélère les lectures/écritures JSON",
                effort="FAIBLE",
                rationale=f"Fichiers runtime > 10MB = lectures/écritures JSON ralenties et risque de corruption.",
                proposal_type="monitoring",
            )

        msg = ", ".join(alerts) if alerts else "Données OK"
        return {
            "level": level,
            "message": msg,
            "data": {"parquet_count": len(parquet_files) if parquet_dir.exists() else 0, "stale_count": stale_count},
        }


class PerformanceEngineerAgent(Agent):
    """Performance Engineer — toutes les 300s, mesure cycle time, mémoire, logs, uptime.
    Force de proposition : suggère des optimisations de performance concrètes."""

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

        has_mem_leak = False
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
                has_mem_leak = True

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
        mem_drift = False
        if len(self._memory_samples) >= 10:
            recent = self._memory_samples[-10:]
            if recent[-1] > recent[0] * 1.1 and recent[-1] - recent[0] > 50:
                alerts.append(f"MEM_DRIFT: +{(recent[-1] - recent[0]) / recent[0] * 100:.0f}% sur 10 échantillons")
                level = max(level, "ORANGE")
                mem_drift = True

        # ── Propositions ──
        if has_mem_leak or mem_drift:
            self.propose(
                ctx,
                title=f"Fuite mémoire détectée — diagnostic GC/python requis",
                description=f"La mémoire du daemon augmente de façon anormale (baseline={self._baseline_memory:.0f}MB, actuel={mem:.0f}MB). Vérifier les références circulaires, les callbacks non libérés, et les fichiers JSON qui s'accumulent. Ajouter gc.collect() tous les 100 cycles.",
                priority="HIGH",
                impact=f"Stabilise la mémoire à {self._baseline_memory:.0f}MB et évite le OOM",
                effort="MOYEN",
                rationale=f"MEM drift de +{(mem / self._baseline_memory - 1) * 100:.0f}% = fuite probable. Risque de crash mémoire dans les prochaines heures.",
                proposal_type="fix",
            )

        if total_log_mb > 500:
            self.propose(
                ctx,
                title=f"Logs volumineux ({total_log_mb:.0f}MB) — rotation urgente",
                description=f"Les logs occupent {total_log_mb:.0f}MB. Mettre en place une rotation quotidienne avec compression gzip, conserver 7 jours max, et réduire le niveau de log de DEBUG à INFO pour les modules les plus bavards (feature_pipeline, strategy).",
                priority="HIGH" if total_log_mb > 1000 else "MEDIUM",
                impact=f"Réduit l'espace disque logs de {total_log_mb:.0f}MB à <100MB",
                effort="FAIBLE",
                rationale=f"{total_log_mb:.0f}MB de logs = espace disque gaspillé et lectures ralenties.",
                proposal_type="monitoring",
            )

        if not pid:
            self.propose(
                ctx,
                title="Robot arrêté — redémarrage immédiat recommandé",
                description="Le robot n'est pas en cours d'exécution (PID manquant). Lancer un redémarrage immédiat et activer le monitoring heartbeat toutes les 15s.",
                priority="CRITICAL",
                impact="Rétablit le trading et évite les opportunités manquées",
                effort="FAIBLE",
                rationale="Robot down = pas de trading = perte d'opportunités et risque de positions orphelines.",
                proposal_type="fix",
            )

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
        self.proposal_board: list[dict] = []  # proposal board — force de proposition
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
        self._load_proposals()

    # ── Gestion du robot sous-processus ──

    def start_robot(self):
        """Lance main.py comme sous-processus."""
        if self.robot_process and self.robot_process.poll() is None:
            logger.info("Robot déjà en cours d'exécution")
            return

        # Nettoyer toute référence morte + PID stale avant de démarrer
        self.robot_process = None
        self._clean_stale_pid()

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

    def _close_mt5_positions(self):
        """Ferme toutes les positions MT5 du robot directement.
        Utilisé en dernier recours quand le robot ne répond plus."""
        try:
            import MetaTrader5 as _mt5
            import config_simple as _cfg

            if not _mt5.initialize(timeout=5000, portable=True):
                logger.error("[KILL] Impossible d'initialiser MT5 pour fermeture positions")
                return

            magic = _cfg.ROBOT_MAGIC
            positions = _mt5.positions_get()
            if positions is None:
                logger.warning("[KILL] Aucune position MT5 trouvée")
                _mt5.shutdown()
                return

            closed = 0
            for pos in positions:
                if pos.magic != magic:
                    continue
                close_type = _mt5.ORDER_TYPE_BUY if pos.type == 0 else _mt5.ORDER_TYPE_SELL
                tick = _mt5.symbol_info_tick(pos.symbol)
                if tick is None:
                    logger.error(f"[KILL] Tick indisponible pour {pos.symbol} — fermeture impossible")
                    continue
                price = tick.ask if close_type == _mt5.ORDER_TYPE_BUY else tick.bid
                req = {
                    "action": _mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": close_type,
                    "position": pos.ticket,
                    "price": price,
                    "deviation": 100,
                    "magic": magic,
                    "comment": "KILL_SWITCH",
                    "type_time": _mt5.ORDER_TIME_GTC,
                    "type_filling": _mt5.ORDER_FILLING_IOC,
                }
                result = _mt5.order_send(req)
                if result and result.retcode == 10009:
                    logger.info(f"[KILL] #{pos.ticket} {pos.symbol} fermée OK")
                    closed += 1
                else:
                    retcode = result.retcode if result else "NO_RESULT"
                    logger.error(f"[KILL] Échec fermeture #{pos.ticket} {pos.symbol}: retcode={retcode}")

            logger.info(f"[KILL] {closed} position(s) fermée(s) avant arrêt robot")
            _mt5.shutdown()
        except Exception as e:
            logger.error(f"[KILL] Erreur fermeture positions MT5: {e}")

    def stop_robot(self):
        """Arrête le robot proprement — ferme d'abord les positions MT5."""
        # 1. Fermer les positions MT5 directement (même si le robot ne répond plus)
        logger.info("[STOP] Fermeture des positions MT5 avant arrêt...")
        self._close_mt5_positions()

        # 2. Tuer le processus robot
        if self.robot_process and self.robot_process.poll() is None:
            pid = self.robot_process.pid
            logger.info(f"[STOP] Arrêt du robot PID {pid}...")
            self.robot_process.terminate()
            try:
                self.robot_process.wait(timeout=10)
                logger.info(f"[STOP] Robot PID {pid} arrêté")
            except subprocess.TimeoutExpired:
                logger.warning(f"[STOP] Robot PID {pid} ne répond pas — kill")
                self.robot_process.kill()
                self.robot_process.wait(timeout=5)
        self.robot_process = None

        # 3. Nettoyer le PID file
        if ROBOT_PID_FILE.exists():
            ROBOT_PID_FILE.unlink()

    def is_robot_alive(self) -> bool:
        """Vérifie si le robot est toujours en vie."""
        # 1) Vérifier via le subprocess Popen (source de confiance)
        if self.robot_process and self.robot_process.poll() is None:
            return True
        # Si le subprocess est mort, nettoyer la référence pour éviter les fuites
        if self.robot_process and self.robot_process.poll() is not None:
            self.robot_process = None

        # 2) Fallback: vérifier via le PID file (utile après redémarrage du daemon)
        try:
            if ROBOT_PID_FILE.exists():
                pid_str = ROBOT_PID_FILE.read_text().strip()
                if pid_str:
                    pid = int(pid_str)
                    # os.kill(pid, 0) vérifie l'existence sans envoyer de signal
                    os.kill(pid, 0)
                    # Vérifier que le process est bien un Python qui tourne depuis assez longtemps
                    # pour éviter de confondre avec un PID recyclé
                    return True
        except (OSError, ValueError, PermissionError):
            # PID inexistant ou invalide → fichier stale, le nettoyer
            self._clean_stale_pid()
        return False

    def _clean_stale_pid(self):
        """Supprime le fichier PID s'il est obsolète (processus mort)."""
        try:
            if ROBOT_PID_FILE.exists():
                pid_str = ROBOT_PID_FILE.read_text().strip()
                if pid_str:
                    try:
                        pid = int(pid_str)
                        os.kill(pid, 0)  # Vérifie si le process existe
                        # Le process existe → ne pas supprimer
                        return
                    except (OSError, ValueError):
                        # Processus mort → fichier stale, supprimer
                        pass
                ROBOT_PID_FILE.unlink()
                logger.info(f"PID file supprimé (stale: {pid_str})")
        except Exception as e:
            logger.debug(f"Nettoyage PID file échoué: {e}")

    # ── Lecture des métriques ──

    def read_ftmo_report(self) -> dict:
        try:
            fp = RUNTIME / "ftmo_report.json"
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    # ── Persistance des propositions ──

    PROPOSAL_FILE = RUNTIME / "council_proposals.json"

    def _load_proposals(self):
        """Charge les propositions existantes depuis le fichier."""
        try:
            if self.PROPOSAL_FILE.exists():
                data = json.loads(self.PROPOSAL_FILE.read_text(encoding="utf-8"))
                self.proposal_board = data.get("proposals", [])
                logger.info(f"📋 {len(self.proposal_board)} propositions chargées")
        except Exception as e:
            logger.debug(f"Erreur chargement propositions: {e}")
            self.proposal_board = []

    def _save_proposals(self):
        """Sauvegarde les propositions dans le fichier."""
        try:
            # Nettoyer les _timestamp_s (interne, non sérialisable)
            clean = []
            for p in self.proposal_board:
                entry = {k: v for k, v in p.items() if k != "_timestamp_s"}
                clean.append(entry)
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_proposals": len(clean),
                "open_count": sum(1 for p in clean if p.get("status") == "OPEN"),
                "proposals": clean,
            }
            self.PROPOSAL_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.error(f"Erreur sauvegarde propositions: {e}")

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
                logger.warning(
                    f"Robot mort — redémarrage... "
                    f"(process={self.robot_process is not None}, "
                    f"pid_file={ROBOT_PID_FILE.exists()})"
                )
                # Forcer le nettoyage de toute référence morte
                self.robot_process = None
                self._clean_stale_pid()
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
                # ── Proposal board : force de proposition ──
                "proposal_board": self.proposal_board,
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

            # ── Vérifier si un agent a déclenché un KILL (bug C1 fix) ──
            for agent, result in zip(self.agents, results):
                if result.get("data", {}).get("action") == "KILL":
                    logger.critical(
                        f"🚨 KILL SWITCH DECLENCHÉ par {agent.name}: "
                        f"{result['data'].get('message', '')}"
                    )
                    # Fermer positions + tuer robot + sortir
                    self.stop_robot()
                    self.running = False
                    break

            if not self.running:
                break

            # ── Sauvegarder et logger ──
            self.save_status(results)
            self.log_council(results)
            self._save_proposals()  # ← Persistance des propositions

            # ── Nettoyer le board des vieux messages (garder les 50 derniers) ──
            if len(self.council_board) > 100:
                self.council_board = self.council_board[-50:]

            # ── Nettoyer le proposal_board des propositions obsolètes (>14 jours) ──
            if len(self.proposal_board) > 200:
                now = time.time()
                fresh = [
                    p
                    for p in self.proposal_board
                    if p.get("status") == "OPEN" or (now - p.get("_timestamp_s", 0)) < 14 * 86400
                ]
                self.proposal_board = fresh[-100:]  # garder max 100 récentes

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


def show_proposals():
    """Affiche toutes les propositions en cours du council."""
    prop_file = RUNTIME / "council_proposals.json"
    if not prop_file.exists():
        print("📋 Aucune proposition — le daemon n'a pas encore tourné avec le nouveau système.")
        print("   Redémarrez le daemon pour activer le système de propositions.")
        return

    data = json.loads(prop_file.read_text(encoding="utf-8"))
    proposals = data.get("proposals", [])
    print(f"\n╔══════════════════════════════════════════════════════════╗")
    print(f"║    C O U N C I L   —   F O R C E   D E   P R O P O S I T I O N  ║")
    print(f"╚══════════════════════════════════════════════════════════╝")
    print(f"  Total: {data.get('total_proposals', 0)} | Ouvertes: {data.get('open_count', 0)}")
    print(f"  Dernière mise à jour: {data.get('timestamp', 'N/A')}")
    print()

    # Filtrer par statut
    open_props = [p for p in proposals if p.get("status") == "OPEN"]
    other_props = [p for p in proposals if p.get("status") != "OPEN"]

    if open_props:
        print(f"─── PROPOSITIONS ACTIVES ({len(open_props)}) ───")
        print()
        priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        open_props.sort(key=lambda p: priority_order.get(p.get("priority", "MEDIUM"), 99))
        for i, p in enumerate(open_props, 1):
            icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}.get(p.get("priority", "MEDIUM"), "⚪")
            print(f"  {icon} #{i} [{p['priority']}] {p['title']}")
            print(
                f"     Agent: {p.get('agent', '?')}  |  Type: {p.get('type', '?')}  |  Effort: {p.get('effort', '?')}"
            )
            print(f"     ID: {p.get('id', '?')}")
            print(f"     {p.get('description', '')}")
            if p.get("impact"):
                print(f"     → Impact: {p['impact']}")
            if p.get("rationale"):
                print(f"     → Raison: {p['rationale']}")
            print()

    if other_props:
        print(f"─── HISTORIQUE ({len(other_props)} fermées/obsolètes) ───")
        for p in other_props[-5:]:
            print(f"  [{p.get('status', '?')}] {p.get('title')} — {p.get('agent')}")
        print()

    # Résumé par agent
    print(f"─── RÉSUMÉ PAR AGENT ───")
    by_agent: dict[str, list] = {}
    for p in proposals:
        by_agent.setdefault(p.get("agent", "?"), []).append(p)
    for agent, props in sorted(by_agent.items()):
        open_ct = sum(1 for p in props if p.get("status") == "OPEN")
        total_ct = len(props)
        print(f"  {agent}: {open_ct} ouvertes / {total_ct} total")


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
    if "--proposals" in sys.argv:
        show_proposals()
    elif "--status" in sys.argv:
        show_status()
    elif "--stop" in sys.argv:
        stop_daemon()
    else:
        daemon = AgentDaemon()
        daemon.run()
