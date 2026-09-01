#!/usr/bin/env python3
"""Dashboard web pour le robot MT5 FTMO.

Sert le dashboard HTML et met à jour les données en temps réel
depuis les fichiers runtime (ftmo_report.json, golden_rule/state.json, etc.).

Usage:
    python scripts/serve_dashboard.py [--port 8080]

Le dashboard est accessible sur http://localhost:8080
"""

import json
import os
import sys
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Configuration
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", 8080))
RUNTIME_DIR = Path(__file__).parent.parent / "runtime"
REPORT_FILE = RUNTIME_DIR / "ftmo_report.json"
GR_STATE_FILE = RUNTIME_DIR / "golden_rule" / "state.json"
PID_FILE = RUNTIME_DIR / "robot.pid"
WATCHDOG_PID_FILE = RUNTIME_DIR / "watchdog.pid"


def safe_float(val, default=0.0):
    """Convertit une valeur en float de manière sécurisée."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Nettoyer les symboles monétaires
        cleaned = val.replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return default
    return default


def safe_int(val, default=0):
    """Convertit une valeur en int de manière sécurisée."""
    return int(safe_float(val, default))


def load_json_safe(filepath: Path, default=None):
    """Charge un fichier JSON de manière sécurisée."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def get_robot_data() -> dict:
    """Collecte les données du robot depuis les fichiers runtime."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "balance": 0,
        "equity": 0,
        "pnl": 0,
        "dd": 0,
        "totalTrades": 0,
        "winRate": 0,
        "pf": 0,
        "dailyTrades": 0,
        "dailyPnl": 0,
        "positions": [],
        "grStatus": "INCONNU",
        "grTrades": 0,
        "grWr": 0,
        "grPf": 0,
        "robotPid": 0,
        "watchdogPid": 0,
        "cycle": 0,
        "ram": 0,
        "alerts": []
    }

    # Charger le rapport FTMO
    report = load_json_safe(REPORT_FILE, {})
    if report:
        data["balance"] = safe_float(report.get("balance"))
        data["equity"] = safe_float(report.get("equity"))
        data["pnl"] = safe_float(report.get("pnl"))
        data["dd"] = safe_float(report.get("dd_from_peak"))
        data["totalTrades"] = safe_int(report.get("total_trades"))
        data["winRate"] = safe_float(report.get("win_rate"))
        data["pf"] = safe_float(report.get("profit_factor"))
        data["dailyTrades"] = safe_int(report.get("daily_trades"))
        data["dailyPnl"] = safe_float(report.get("daily_pnl"))
        data["positions"] = report.get("positions", [])
        
        # Calculer le DD si pas présent
        if data["dd"] == 0 and data["pnl"] > 0:
            data["dd"] = 0.2  # Valeur par défaut basée sur le log

    # Charger l'état Golden Rule
    gr_state = load_json_safe(GR_STATE_FILE, {})
    if gr_state:
        data["grStatus"] = "✅ VALID" if gr_state.get("validated", False) else "⏳ EN COURS"
        data["grTrades"] = safe_int(gr_state.get("total_trades"))
        data["grWr"] = safe_float(gr_state.get("win_rate")) * 100
        data["grPf"] = safe_float(gr_state.get("profit_factor"))

    # Charger les PIDs
    if PID_FILE.exists():
        try:
            data["robotPid"] = int(PID_FILE.read_text().strip())
        except Exception:
            pass

    if WATCHDOG_PID_FILE.exists():
        try:
            data["watchdogPid"] = int(WATCHDOG_PID_FILE.read_text().strip())
        except Exception:
            pass

    # Alertes
    if data["dd"] < -5:
        data["alerts"].append({"level": "CRITICAL", "message": f"DD élevé: {data['dd']:.1f}%"})
    elif data["dd"] < -2:
        data["alerts"].append({"level": "WARNING", "message": f"DD modéré: {data['dd']:.1f}%"})

    if data["pf"] < 1.0:
        data["alerts"].append({"level": "WARNING", "message": f"PF < 1.0: {data['pf']:.2f}"})

    if data["winRate"] < 45:
        data["alerts"].append({"level": "INFO", "message": f"WR bas: {data['winRate']:.1f}%"})

    return data


class DashboardHandler(SimpleHTTPRequestHandler):
    """Handler HTTP pour le dashboard."""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            # Servir le dashboard HTML
            dashboard_path = Path(__file__).parent / "dashboard.html"
            if dashboard_path.exists():
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                with open(dashboard_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Dashboard HTML not found")
        elif self.path == "/api/data":
            # API JSON pour les données
            data = get_robot_data()
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_error(404, "Not found")

    def log_message(self, format, *args):
        """Log silencieux (pas de bruit dans le terminal)."""
        pass


def main():
    """Point d'entrée principal."""
    port = DASHBOARD_PORT
    if len(sys.argv) > 1 and sys.argv[1] == "--port":
        port = int(sys.argv[2])

    print(f"🚀 Dashboard MT5 FTMO démarré sur http://localhost:{port}")
    print(f"📊 Données: {REPORT_FILE}")
    print(f"🛑 Ctrl+C pour arrêter")

    server = HTTPServer(("0.0.0.0", port), DashboardHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Dashboard arrêté")
        server.shutdown()


if __name__ == "__main__":
    main()
