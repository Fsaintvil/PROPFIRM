#!/usr/bin/env python3
"""Wrapper safe pour séquencer le démarrage production.

Usage:
  - Lance d'abord `start_production.py` pour un petit sous-ensemble de symbols
  - Optionnellement attend jusqu'à un horodatage pour lancer les symbols restants

Ce script invoque le lanceur canonique `start_production.py` via subprocess
afin de conserver le flow de santé/locks/confirmations existant.

IMPORTANT: ce script exécute réellement le lanceur. Passez --dry-run au
        lanceur si vous voulez seulement vérifier (il fera health checks).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import os
from datetime import datetime, timezone
from pathlib import Path


def run_start_production(
    symbols: str,
    yes: bool = False,
    dry_run: bool = False,
    extra_args: list[str] | None = None,
) -> int:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "start_production.py"),
    ]
    cmd += ["--symbols", symbols]
    if yes:
        cmd.append("--yes")
    if dry_run:
        cmd.append("--dry-run")
    if extra_args:
        cmd += extra_args

    print("Lancement:", " ".join(cmd))
    p = subprocess.run(cmd)
    print("Process finished with exit code", p.returncode)
    return p.returncode


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run production sequence: first subset then remaining "
            "when scheduled"
        )
    )
    p.add_argument(
        "--now-symbols",
        type=str,
        required=True,
        help="Comma separated symbols to start immediately (ex: BTCUSD,ETHUSD)",
    )
    p.add_argument(
        "--later-symbols",
        type=str,
        default="",
        help="Comma separated symbols to start later (ex: EURUSD,USDJPY,...)",
    )
    p.add_argument(
        "--later-start",
        type=str,
        default=None,
        help=(
            "ISO datetime (UTC) to start later batch, ex: 2025-11-10T01:00:00Z"
        ),
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Pass --yes to start_production (skip interactive confirmation)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to start_production (health checks only)",
    )
    p.add_argument(
        "--extra",
        type=str,
        default="",
        help=(
            "Extra args to forward to start_production, quoted as a single string"
        ),
    )
    return p.parse_args()


def wait_until(dt_str: str) -> None:
    # Expect ISO8601-like string with optional Z; assume UTC
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1]
    target = datetime.fromisoformat(dt_str)
    if target.tzinfo is None:
        # assume UTC
        target = target.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    sec = (target - now).total_seconds()
    if sec <= 0:
        print("Target time already passed; continuing immediately")
        return
    print(f"Attente de {int(sec)} secondes jusqu'à {target.isoformat()} UTC")
    try:
        while sec > 0:
            sleep = min(60, sec)
            time.sleep(sleep)
            sec -= sleep
    except KeyboardInterrupt:
        print("Attente interrompue par utilisateur")


def main():
    args = parse_args()

    extra = []
    if args.extra:
        extra = args.extra.split()

    # Respect OPERATIONAL_RULES: require environment flags before live sends
    required_env = [
        "ALLOW_MT5_SEND",
        "AUTO_APPLY",
        "AUTO_DEPLOY",
        "AUTO_LEARN",
        "AUTO_ADAPT",
        "AUTO_ENRICH",
    ]
    missing = [v for v in required_env if os.environ.get(v, "0") != "1"]
    if missing:
        print(
            "OPERATIONAL_RULES: variables d'environnement obligatoires manquantes",
            missing,
        )
        print(
            "Conformément à OPERATIONAL_RULES.md, définissez ces variables à '1' "
            "avant d'exécuter en mode live, ou utilisez --dry-run pour health checks."
        )
        if not args.dry_run:
            print(
                "Arrêt: passez --dry-run pour exécuter les health checks sans envoyer "
                "d'ordres, ou exportez les variables requises puis relancez."
            )
            return 2

    # 1) lancer le batch immédiat
    rc = run_start_production(
        args.now_symbols, yes=args.yes, dry_run=args.dry_run, extra_args=extra
    )
    if rc != 0:
        print("Premier batch terminé avec code non-nul; arrêter la suite.")
        sys.exit(rc)

    # 2) si aucun later-symbols fourni, on arrête ici
    if not args.later_symbols:
        print(
            "Aucun second batch configuré. Lancez manuellement pour les symbols "
            "restants lorsque le marché est ouvert."
        )
        print(
            "Ex: python tools/run_production_sequence.py --now-symbols BTCUSD,ETHUSD "
            "--later-symbols EURUSD,USDJPY,... --later-start 2025-11-10T01:00:00Z --yes"
        )
        return

    # 3) attendre si requested
    if args.later_start:
        wait_until(args.later_start)
    else:
        # If later-start is not provided, refuse to auto-start the second batch
        # during potential Forex closed windows (weekends). Require explicit scheduling
        # or manual launch to honor OPERATIONAL_RULES.
        print(
            "Aucun horaire fourni pour second batch. Par sécurité (OPERATIONAL_RULES),"
        )
        print(
            "Fournissez --later-start (UTC ISO) ou lancez manuellement lorsque le "
            "marché est ouvert."
        )
        print("Second batch symbols:", args.later_symbols)
        return

    # 4) lancer second batch
    rc2 = run_start_production(
        args.later_symbols, yes=args.yes, dry_run=args.dry_run, extra_args=extra
    )
    sys.exit(rc2)


if __name__ == "__main__":
    main()
