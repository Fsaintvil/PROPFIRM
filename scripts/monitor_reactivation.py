#!/usr/bin/env python3
"""
Moniteur de réactivation BTCUSD + suivi des nouveaux symboles.
Vérifie les conditions pour réactiver BTCUSD:
  - momentum > threshold sur la dernière bougie
  - WR > 55% sur les 20 derniers trades BTCUSD

Usage:
    python scripts/monitor_reactivation.py          # Rapport complet
    python scripts/monitor_reactivation.py --watch   # Mode continu (15s)
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("monitor_reactivation")

RUNTIME = Path("runtime")
LOGS = Path("logs")
CONDITIONS_FILE = RUNTIME / "btc_reactivation_conditions.json"


def check_btc_conditions():
    """Vérifie les conditions de réactivation BTCUSD."""
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "btcusd": {
            "momentum_above_threshold": False,
            "wr_above_55": False,
            "both_conditions_met": False,
            "details": {},
        },
        "new_symbols": {},
        "challenge": {},
    }

    # 1. Vérifier les logs pour le momentum BTCUSD récent
    log_file = LOGS / "simple_robot.log"
    if log_file.exists():
        with open(log_file) as f:
            lines = f.readlines()

        # Chercher les dernières lignes MOM20x3 BTCUSD
        btc_mom_lines = [l for l in lines if "[MOM20x3] BTCUSD" in l]
        if btc_mom_lines:
            last = btc_mom_lines[-1]
            try:
                # Parse: [MOM20x3] SELL BTCUSD | mom=-675.32000 thresh=592.07747 ...
                parts = last.split("|")
                mom_part = [p for p in parts if "mom=" in p]
                thresh_part = [p for p in parts if "thresh=" in p]
                if mom_part and thresh_part:
                    mom = float(mom_part[0].split("mom=")[1].strip().split()[0])
                    thresh = float(thresh_part[0].split("thresh=")[1].strip().split()[0])
                    result["btcusd"]["details"]["momentum"] = abs(mom)
                    result["btcusd"]["details"]["threshold"] = thresh
                    result["btcusd"]["momentum_above_threshold"] = abs(mom) > thresh
            except (ValueError, IndexError):
                pass

        # Chercher WR BTCUSD dans les logs [PHASE 3]
        btc_perf_lines = [l for l in lines if "[PHASE 3] BTCUSD" in l]
        if btc_perf_lines:
            last = btc_perf_lines[-1]
            try:
                # Parse: [PHASE 3] BTCUSD: 62 trades, WR=32.3%, PF=0.71
                wr_part = last.split("WR=")[1].split("%")[0]
                trades_part = last.split(":")[1].strip().split(",")[0].split()[0]
                wr = float(wr_part)
                trades = int(trades_part)
                result["btcusd"]["details"]["wr"] = wr
                result["btcusd"]["details"]["trades"] = trades
                result["btcusd"]["wr_above_55"] = wr > 55.0
            except (ValueError, IndexError):
                pass

    # 2. Vérifier les nouveaux symboles (trades count)
    state_file = RUNTIME / "robot_state.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)

            trade_history = state.get("trade_history", [])
            sym_trades = {}
            for t in trade_history:
                sym = t.get("symbol", "?")
                sym_trades[sym] = sym_trades.get(sym, 0) + 1

            for sym in ["USDJPY", "GBPUSD", "AUDUSD", "USDCAD"]:
                result["new_symbols"][sym] = {
                    "trades": sym_trades.get(sym, 0),
                    "awaiting_optimization": sym_trades.get(sym, 0) < 50,
                }
        except (json.JSONDecodeError, KeyError):
            pass

    # 3. Challenge status
    ftmo_file = RUNTIME / "ftmo_report.json"
    if ftmo_file.exists():
        try:
            with open(ftmo_file) as f:
                ftmo = json.load(f)
            result["challenge"] = {
                "balance": ftmo.get("balance", 0),
                "equity": ftmo.get("equity", 0),
                "pnl": ftmo.get("pnl", 0),
                "dd_peak": ftmo.get("dd_from_peak", "N/A"),
                "wr": ftmo.get("win_rate", "N/A"),
                "days": ftmo.get("trading_days", 0),
                "consecutive_losses": ftmo.get("consecutive_losses", 0),
            }
        except (json.JSONDecodeError, KeyError):
            pass

    # Conditions combinées
    result["btcusd"]["both_conditions_met"] = (
        result["btcusd"]["momentum_above_threshold"] and result["btcusd"]["wr_above_55"]
    )

    # Sauvegarder
    with open(CONDITIONS_FILE, "w") as f:
        json.dump(result, f, indent=2)

    return result


def print_report(r):
    """Affiche un rapport lisible."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'=' * 60}")
    print(f"  MONITEUR RÉACTIVATION — {now}")
    print(f"{'=' * 60}")

    # BTCUSD
    b = r["btcusd"]
    print(f"\n🔶 BTCUSD:")
    print(
        f"  Momentum: {b['details'].get('momentum', 'N/A'):.2f} | "
        f"Threshold: {b['details'].get('threshold', 'N/A'):.2f} | "
        f"Status: {'✅ OK' if b['momentum_above_threshold'] else '❌ Trop faible'}"
    )
    print(
        f"  WR: {b['details'].get('wr', 'N/A')}% | "
        f"Trades: {b['details'].get('trades', 'N/A')} | "
        f"Status: {'✅ OK' if b['wr_above_55'] else '❌ <55%'}"
    )
    if b["both_conditions_met"]:
        print(f"  ⚡ CONDITION RÉACTIVATION REMPLIE !")
    else:
        print(f"  ⏳ En attente des conditions (besoin momentum+WR)")

    # Nouveaux symboles
    print(f"\n🆕 Nouveaux symboles:")
    for sym, data in r["new_symbols"].items():
        status = "✅" if not data["awaiting_optimization"] else "⏳"
        print(f"  {sym}: {data['trades']} trades {status}")

    # Challenge
    c = r["challenge"]
    print(f"\n🏆 Challenge FTMO:")
    print(f"  Balance: ${c.get('balance', 0):,.2f} | Equity: ${c.get('equity', 0):,.2f}")
    print(f"  PnL: ${c.get('pnl', 0):,.2f} | DD: {c.get('dd_peak', 'N/A')}")
    print(f"  WR: {c.get('wr', 'N/A')} | Days: {c.get('days', 0)}/10")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        logger.info("Mode surveillance continu (Ctrl+C pour quitter)")
        try:
            while True:
                r = check_btc_conditions()
                print_report(r)
                time.sleep(15)
        except KeyboardInterrupt:
            logger.info("Arrêt")
    else:
        r = check_btc_conditions()
        print_report(r)

        # Sauvegarder
        with open(CONDITIONS_FILE, "w") as f:
            json.dump(r, f, indent=2)
        print(f"Rapport sauvegardé: {CONDITIONS_FILE}")
