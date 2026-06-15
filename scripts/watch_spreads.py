"""
Watch Spreads — surveillance en direct des spreads MT5 pour tous les symboles.

Usage:
    python scripts/watch_spreads.py                    # Mode continu (rafraîchit toutes les 10s)
    python scripts/watch_spreads.py --once              # Un seul scan
    python scripts/watch_spreads.py --interval 5        # Rafraîchir toutes les 5s
    python scripts/watch_spreads.py --symbols USDCAD,USDCHF  # Symboles spécifiques

Alerte visuelle quand le spread dépasse le seuil configuré (max_spread_points).
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config_simple as cfg

# ── Couleurs ANSI ───────────────────────────────────────────────────
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
CLEAR = "\033[2J\033[H"


def colorize_spread(spread_pts, max_pts):
    """Colorie le spread selon son ratio par rapport au max."""
    ratio = spread_pts / max_pts if max_pts > 0 else 1
    if ratio >= 0.9:
        return RED
    if ratio >= 0.6:
        return YELLOW
    return GREEN


def watch_spreads(symbols, interval, once=False, show_all=False):
    """Boucle de surveillance des spreads."""
    # Si pas de symboles fournis, prendre ceux du config + actifs
    if not symbols:
        symbols = list(cfg.SYMBOL_LIMITS.keys())
        # --all : ne PAS filtrer, afficher tous les symboles meme désactivés
        if not show_all:
            active = []
            for sym in symbols:
                lim = cfg.SYMBOL_LIMITS.get(sym, {})
                if lim.get("allow_buys", True) or lim.get("allow_shorts", True):
                    active.append(sym)
            if active:
                symbols = active

    # Connexion MT5
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(f"{RED}MetaTrader5 non installé. Ce script nécessite MT5.{RESET}")
        sys.exit(1)

    initialized = mt5.initialize()
    if not initialized:
        print(f"{RED}Échec de connexion MT5. Vérifie que le terminal MT5 est lancé.{RESET}")
        sys.exit(1)

    print(f"{CYAN}Connecté à MT5. Surveillance des spreads...{RESET}\n")

    cycle = 0
    try:
        while True:
            now = datetime.utcnow()
            trading = True  # 24/5 — toujours en session trading (weekend bloqué par FTMO)
            day_name = now.strftime("%A")
            weekend = day_name in ("Saturday", "Sunday")

            if not once:
                print(CLEAR, end="")

            # En-tête
            header = (f"{BOLD}{'Symbole':<10} {'Bid':>10} {'Ask':>10} "
                      f"{'Spread':>7} {'pts':>6} {'Seuil':>6} {'Ratio':>7} {'Statut':>10}{RESET}")
            print(f"{CYAN}═══ Spread Watch — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                  f"{'(TRADING)' if trading else '(HORS SESSION)'} "
                  f"{'(WEEKEND)' if weekend else ''} ═══{RESET}")
            print(f"{CYAN}─── {len(symbols)} symboles | "
                  f"cycle {cycle} | intervalle {interval}s "
                  f"{'(--once)' if once else ''} ───{RESET}")
            print(header)
            print(f"{'─'*70}")

            alerts = []
            for sym in symbols:
                tick = mt5.symbol_info_tick(sym)
                info = mt5.symbol_info(sym)
                if tick is None or info is None:
                    print(f"{sym:<10} {YELLOW}Pas de données{RESET}")
                    continue

                bid = tick.bid
                ask = tick.ask
                spread_raw = ask - bid
                point = info.point or 0.0001
                spread_pts = spread_raw / point
                max_pts = cfg.SYMBOL_LIMITS.get(sym, {}).get("max_spread_points", 50)
                ratio = spread_pts / max_pts if max_pts > 0 else 1

                # Couleur
                color = colorize_spread(spread_pts, max_pts)

                # Statut
                if spread_pts >= max_pts:
                    status = f"{RED}⚠ BLOQUÉ{RESET}"
                    alerts.append(f"{RED}⚠ {sym}: spread {spread_pts:.0f}pts >= max {max_pts}pts{RESET}")
                elif ratio >= 0.6:
                    status = f"{YELLOW}⚠ ÉLEVÉ{RESET}"
                else:
                    status = f"{GREEN}OK{RESET}"

                print(f"{sym:<10} {bid:>10.5f} {ask:>10.5f} "
                      f"{spread_raw:>7.5f} {color}{spread_pts:>6.0f}{RESET} "
                      f"{max_pts:>6d} {color}{ratio:>6.1%}{RESET} {status}")

            # Alertes
            if alerts:
                print(f"\n{RED}{BOLD}⚠ ALERTES SPREAD{RESET}")
                for a in alerts:
                    print(f"  {a}")

            # Résumé
            print(f"\n{CYAN}─── Fin cycle {cycle} ───{RESET}")

            if once:
                break

            time.sleep(interval)
            cycle += 1

    except KeyboardInterrupt:
        print(f"\n{YELLOW}Arrêté par l'utilisateur.{RESET}")
    finally:
        mt5.shutdown()
        print(f"{GREEN}Déconnecté de MT5.{RESET}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Surveillance des spreads MT5 en direct")
    parser.add_argument("--once", action="store_true", help="Un seul scan, ne pas boucler")
    parser.add_argument("--interval", type=int, default=10, help="Intervalle en secondes (défaut: 10)")
    parser.add_argument("--symbols", type=str, default="",
                        help="Symboles séparés par des virgules (défaut: tous les symboles config)")
    parser.add_argument("--all", action="store_true", help="Afficher aussi les symboles désactivés")
    args = parser.parse_args()

    sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    watch_spreads(sym_list, args.interval, once=args.once, show_all=args.all)
