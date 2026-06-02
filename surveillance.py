#!/usr/bin/env python3
"""Surveillance en temps réel du robot FTMO — lit runtime/ftmo_report.json"""
import json
import logging
import os
import sys
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SURVEILLANCE")

RUNTIME_DIR = "runtime"
STATE_FILE = os.path.join(RUNTIME_DIR, "robot_state.json")
REPORT_FILE = os.path.join(RUNTIME_DIR, "ftmo_report.json")
HEARTBEAT = os.path.join(RUNTIME_DIR, "heartbeat.txt")


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def get_heartbeat():
    try:
        with open(HEARTBEAT) as f:
            data = f.read().strip()
            return data if data else None
    except Exception:
        return None


def format_status():
    report = read_json(REPORT_FILE)
    state = read_json(STATE_FILE)
    hb = get_heartbeat()

    logger.info(f"{'='*80}")

    if report:
        logger.info(f"COMPTE: Balance=${report.get('balance',0):.0f} | "
                    f"Equity=${report.get('equity',0):.0f} | "
                    f"P&L=${report.get('pnl',0):+.0f}")
        logger.info(f"STATUT: {report.get('status','?')} | "
                    f"Consistance={'OK' if not report.get('consistency_violated') else 'VIOLATED'} | "
                    f"Meilleur jour={report.get('best_day_pct','?')} | "
                    f"Progress={report.get('profit_progress','?')}")
        logger.info(f"DD init={report.get('dd_from_initial','?')} | "
                    f"DD peak={report.get('dd_from_peak','?')} | "
                    f"Jours={report.get('trading_days',0)}/{report.get('days_remaining',0)}")
        logger.info(f"TRADES: Total={report.get('total_trades',0)} | "
                    f"WR={report.get('win_rate','?')} | "
                    f"Daily P&L={report.get('daily_pnl','$0')}")
    elif state:
        logger.info(f"STATE: peak_equity=${state.get('peak_equity',0):.0f} | "
                    f"pertes_cons={state.get('consecutive_losses',0)} | "
                    f"daily_profit_reduced={state.get('daily_profit_reduced',False)}")
    else:
        logger.info("Aucun fichier d'état trouvé (robot ne tourne pas ?)")

    if hb:
        dt = datetime.fromisoformat(hb)
        age = (datetime.now() - dt).total_seconds()
        logger.info(f"Heartbeat: {hb} (age={age:.0f}s)")
        if age > 120:
            logger.warning(f"WATCHDOG: heartbeat age={age:.0f}s — possible freeze!")

    logger.info(f"{'='*80}")


def main():
    logger.info("Surveillance activée (1 mise à jour /10s)")
    cycle = 0
    while True:
        try:
            cycle += 1
            logger.info(f"\n[CYCLE {cycle}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            format_status()
            time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Arrêt demandé")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Erreur: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
