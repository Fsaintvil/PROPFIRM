#!/usr/bin/env python3
"""Cleanup: déduplication et purge des données contaminées.

Usage:
    python scripts/cleanup_data.py          # Nettoyer robot_state.json + performance_history.json
    python scripts/cleanup_data.py --dry    # Simulation sans écriture
    python scripts/cleanup_data.py --force  # Forcer même si robot en cours
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

BASE = Path(__file__).resolve().parent.parent
RUNTIME = BASE / "runtime"


def log(msg):
    print(f"[CLEANUP] {msg}")


def check_robot_running():
    """Vérifie si le robot tourne (PID lock + process)."""
    pid_file = RUNTIME / "robot.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if os.name == "nt":
                import ctypes

                PROCESS_QUERY_INFORMATION = 0x0400
                h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    return True
            else:
                # Unix
                try:
                    os.kill(pid, 0)
                    return True
                except OSError:
                    pass
        except (ValueError, OSError):
            pass
    return False


def dedup_trade_history(trades):
    """Déduplique une liste de trades par contenu JSON complet."""
    seen = set()
    unique = []
    dups = 0
    for t in trades:
        key = json.dumps(t, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(t)
        else:
            dups += 1
    log(f"Déduplication: {len(trades)} → {len(unique)} ({dups} duplicats supprimés)")
    return unique


def clean_robot_state(dry=False):
    """Nettoie robot_state.json: déduplication + réparation."""
    path = RUNTIME / "robot_state.json"
    if not path.exists():
        log("robot_state.json introuvable, skip")
        return False

    with open(path) as f:
        data = json.load(f)

    changed = False

    # 1. Dédupliquer trade_history
    if "trade_history" in data:
        old_len = len(data["trade_history"])
        data["trade_history"] = dedup_trade_history(data["trade_history"])
        if len(data["trade_history"]) != old_len:
            changed = True

    # 2. Recalculer daily_pnl_by_date à partir des trades uniques
    if "trade_history" in data and len(data["trade_history"]) > 0:
        from collections import defaultdict

        daily = defaultdict(float)
        for t in data["trade_history"]:
            time_val = t.get("time", "")
            if time_val:
                try:
                    time_str = str(time_val)
                    # Gérer les formats ISO et natif
                    if "T" in time_str:
                        dt = datetime.fromisoformat(time_str)
                    else:
                        dt = datetime.strptime(time_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    day = dt.strftime("%Y-%m-%d")
                    daily[day] += t.get("profit", 0)
                except (ValueError, IndexError):
                    pass
        data["daily_pnl_by_date"] = dict(daily)
        changed = True
        log(f"Daily PnL recalculé: {len(daily)} jours")

    # 3. Nettoyer les entrées vides/null
    if "trade_history" in data:
        clean = [t for t in data["trade_history"] if t.get("symbol") and t.get("profit") is not None]
        if len(clean) != len(data["trade_history"]):
            log(f"Entrées vides supprimées: {len(data['trade_history']) - len(clean)}")
            data["trade_history"] = clean
            changed = True

    if changed and not dry:
        backup = path.with_suffix(".json.cleanup_bak")
        with open(backup, "w") as f:
            json.dump(data, f, indent=2, default=str)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        log(f"robot_state.json sauvegardé → {backup.name}")
        log("robot_state.json nettoyé ✅")
    elif changed and dry:
        log("[DRY] robot_state.json serait nettoyé")
    else:
        log("robot_state.json: aucun changement nécessaire")
    return changed


def clean_performance_history(dry=False):
    """Nettoie performance_history.json: détection et purge des trades contaminés."""
    path = RUNTIME / "performance_history.json"
    if not path.exists():
        log("performance_history.json introuvable, skip")
        return False

    with open(path) as f:
        data = json.load(f)

    changed = False

    # 1. Nettoyer recent_trades: supprimer les patterns suspects
    if "recent_trades" in data and len(data["recent_trades"]) > 0:
        trades = data["recent_trades"]

        # Détection: profits identiques répétés (contamination seed)
        profit_patterns = Counter((t.get("symbol", "?"), round(t.get("profit", 0), 2)) for t in trades)
        suspicious_patterns = {k for k, v in profit_patterns.items() if v >= 3}

        before = len(trades)
        if suspicious_patterns:
            clean_trades = []
            removed = 0
            # Ne garder que 1 trade par pattern suspect
            pattern_count = {}
            for t in trades:
                key = (t.get("symbol", "?"), round(t.get("profit", 0), 2))
                if key in suspicious_patterns:
                    pattern_count[key] = pattern_count.get(key, 0) + 1
                    if pattern_count[key] > 1:
                        removed += 1
                        continue  # Skip duplicate of suspicious pattern
                clean_trades.append(t)

            if removed > 0:
                data["recent_trades"] = clean_trades
                changed = True
                log(f"Trades contaminés supprimés de recent_trades: {removed} (patterns suspects)")
                patterns_str = ", ".join(
                    f"{s} ${p}×{c}" for (s, p), c in sorted(profit_patterns.items(), key=lambda x: -x[1]) if c >= 3
                )
                log(f"  Patterns: {patterns_str}")

        # Supprimer les entrées invalides (profit=None ou symbol=?)
        valid = [t for t in data["recent_trades"] if t.get("profit") is not None and t.get("symbol")]
        if len(valid) != len(data["recent_trades"]):
            log(f"Entrées invalides supprimées: {len(data['recent_trades']) - len(valid)}")
            data["recent_trades"] = valid
            changed = True

    # 2. Recalculer daily stats si changed
    if changed and data.get("recent_trades"):
        trades = data["recent_trades"]
        from collections import defaultdict

        daily = defaultdict(
            lambda: {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "symbols": {},
            }
        )
        for t in trades:
            pnl = t.get("profit", 0) or 0
            ts = t.get("ts", t.get("timestamp", ""))
            day = ts[:10] if ts else "unknown"
            sym = t.get("symbol", "?")
            d = daily[day]
            d["trades"] += 1
            d["pnl"] += pnl
            if pnl > 0:
                d["wins"] += 1
                d["gross_profit"] += pnl
            elif pnl < 0:
                d["losses"] += 1
                d["gross_loss"] += abs(pnl)
            # Par symbole
            if sym not in d["symbols"]:
                d["symbols"][sym] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            sd = d["symbols"][sym]
            sd["trades"] += 1
            sd["pnl"] += pnl
            if pnl > 0:
                sd["wins"] += 1
            elif pnl < 0:
                sd["losses"] += 1

        data["daily"] = dict(daily)
        log(f"Daily stats recalculées: {len(daily)} jours")

        # 3. Recalculer rolling windows
        rolling = {}
        for window in [20, 50, 100, 200]:
            recent = trades[-window:]
            wins = sum(1 for t in recent if (t.get("profit", 0) or 0) > 0)
            total_pnl = sum((t.get("profit", 0) or 0) for t in recent)
            rolling[f"last_{window}"] = {
                "trades": len(recent),
                "wins": wins,
                "losses": len(recent) - wins,
                "pnl": round(total_pnl, 2),
                "wr": round(wins / len(recent) * 100, 1) if recent else 0,
                "avg": round(total_pnl / len(recent), 2) if recent else 0,
            }
        data["rolling"] = rolling
        log("Rolling windows recalculées")

        # 4. Recalculer symbol stats
        symbols = defaultdict(
            lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
        )
        for t in trades:
            sym = t.get("symbol", "?")
            pnl = t.get("profit", 0) or 0
            s = symbols[sym]
            s["trades"] += 1
            s["pnl"] += pnl
            if pnl > 0:
                s["wins"] += 1
                s["gross_profit"] += pnl
            elif pnl < 0:
                s["losses"] += 1
                s["gross_loss"] += abs(pnl)
        data["symbols"] = dict(symbols)
        log(f"Symbol stats recalculées: {len(symbols)} symboles")

    if changed and not dry:
        backup = path.with_suffix(".json.cleanup_bak")
        with open(backup, "w") as f:
            json.dump(data, f, indent=2, default=str)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        log(f"performance_history.json sauvegardé → {backup.name}")
        log("performance_history.json nettoyé ✅")
    elif changed and dry:
        log("[DRY] performance_history.json serait nettoyé")
    else:
        log("performance_history.json: aucun changement nécessaire")
    return changed


def main():
    dry = "--dry" in sys.argv
    force = "--force" in sys.argv

    if not force and check_robot_running():
        log("⚠️  Robot en cours d'exécution! Utilisez --force pour forcer ou arrêtez d'abord.")
        log("   Conseil: arrêter le robot d'abord pour éviter la corruption.")
        sys.exit(1)

    log("=== NETTOYAGE DES DONNÉES ===")
    if dry:
        log("🧪 Mode DRY — aucune écriture")

    c1 = clean_robot_state(dry)
    c2 = clean_performance_history(dry)

    if dry:
        log(
            f"\nRésultat simulé: robot_state={'MODIFIÉ' if c1 else 'OK'}, performance_history={'MODIFIÉ' if c2 else 'OK'}"
        )
    else:
        log(f"\nRésultat: robot_state={'NETTOYÉ' if c1 else 'OK'}, performance_history={'NETTOYÉ' if c2 else 'OK'}")

    log("=== TERMINÉ ===")


if __name__ == "__main__":
    main()
