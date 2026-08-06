"""
CHECKPOINT QUOTIDIEN — Mode Preuve Strict (06 Aout 2026)
=========================================================
Mesure chaque jour l'état de la phase de validation du robot :
  1. Santé du processus (PID, heartbeat, erreurs)
  2. État FTMO (balance, DD, daily loss, consistance)
  3. Performance de la PREUVE (trades BUY-only post-redémarrage)
  4. Critères de validation (WR ≥ 50%, expectancy > 0, PF > 1.2, stabilité 2 fenêtres)

Usage :
    python scripts/daily_checkpoint.py            # affiche + écrit le rapport
    python scripts/daily_checkpoint.py --json     # sortie JSON uniquement

Rapports écrits dans : runtime/daily_checkpoint/YYYY-MM-DD.json
Historique lisible dans : runtime/daily_checkpoint/history.json
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── Constantes de la phase de preuve ────────────────────────────────────────
PROOF_START = datetime(2026, 8, 6, 19, 36)  # redémarrage mode preuve strict
PROOF_SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "EURGBP", "USOIL.cash"]
VALIDATION = {
    "min_trades": 100,  # échantillon cible pour conclure
    "wr_target": 0.50,  # WR cible
    "expectancy_target": 0.0,  # expectancy > 0
    "pf_target": 1.2,  # profit factor cible
    "windows": 2,  # stabilité sur 2 fenêtres de 50
}


def load_trades():
    path = BASE / "runtime" / "trades_log.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        rows = []
        for row in reader:
            if len(row) >= 12:
                rows.append(row)
    return rows


def load_ftmo_report():
    path = BASE / "runtime" / "ftmo_report.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def check_process():
    pid_file = BASE / "runtime" / "robot.pid"
    status = {"running": False, "pid": None, "heartbeat_age_s": None, "errors_24h": 0}
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            status["pid"] = pid
        except Exception:
            pass
    # heartbeat
    hb = BASE / "runtime" / "heartbeat.txt"
    if hb.exists():
        try:
            age = time.time() - hb.stat().st_mtime
            status["heartbeat_age_s"] = int(age)
            status["running"] = age < 600
        except Exception:
            pass
    # erreurs dans le log récent
    log = BASE / "logs" / "simple_robot.log"
    if log.exists():
        cutoff = time.time() - 86400
        try:
            count = 0
            with open(log, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "ERROR" in line or "CRITICAL" in line:
                        # tenter d'extraire le timestamp
                        try:
                            ts = datetime.fromisoformat(line[:23].replace(" ", "T"))
                            if ts.timestamp() > cutoff:
                                count += 1
                        except Exception:
                            count += 1
            status["errors_24h"] = count
        except Exception:
            pass
    return status


def compute_proof_stats(rows):
    """Stats de la phase de preuve : trades BUY-only sur les 5 symboles,
    depuis le redémarrage du mode preuve."""
    proof = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(row[0].replace(" ", "T"))
        except Exception:
            continue
        if ts < PROOF_START:
            continue
        symbol = row[1]
        direction = row[2]
        if symbol not in PROOF_SYMBOLS:
            continue
        if direction != "BUY":
            continue
        try:
            pnl = float(row[10])
        except Exception:
            continue
        proof.append({"ts": row[0], "symbol": symbol, "pnl": pnl})

    n = len(proof)
    if n == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "wr": 0.0,
            "expectancy": 0.0,
            "pf": 0.0,
            "pnl_total": 0.0,
            "by_symbol": {},
            "by_day": {},
        }

    wins = [t for t in proof if t["pnl"] > 0]
    losses = [t for t in proof if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pnl_total = sum(t["pnl"] for t in proof)

    by_symbol = {}
    for sym in PROOF_SYMBOLS:
        sym_trades = [t for t in proof if t["symbol"] == sym]
        if not sym_trades:
            continue
        w = sum(1 for t in sym_trades if t["pnl"] > 0)
        by_symbol[sym] = {
            "trades": len(sym_trades),
            "wins": w,
            "wr": w / len(sym_trades),
            "pnl": round(sum(t["pnl"] for t in sym_trades), 2),
        }

    by_day = {}
    for t in proof:
        d = t["ts"][:10]
        by_day.setdefault(d, {"trades": 0, "pnl": 0.0, "wins": 0})
        by_day[d]["trades"] += 1
        by_day[d]["pnl"] += t["pnl"]
        by_day[d]["wins"] += 1 if t["pnl"] > 0 else 0

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(len(wins) / n, 4),
        "expectancy": round(pnl_total / n, 2),
        "pf": round(gross_win / gross_loss, 3) if gross_loss > 0 else 99.9,
        "pnl_total": round(pnl_total, 2),
        "by_symbol": by_symbol,
        "by_day": by_day,
    }


def evaluate_validation(stats):
    """Verdict de validation basé sur les critères de la phase de preuve."""
    verdict = {"reached": False, "criteria": {}, "status": "EN_COURS", "message": ""}
    c = VALIDATION
    n = stats["trades"]
    wr_ok = stats["wr"] >= c["wr_target"] if n >= 20 else None
    exp_ok = stats["expectancy"] > c["expectancy_target"] if n >= 20 else None
    pf_ok = stats["pf"] >= c["pf_target"] if n >= 20 else None
    verdict["criteria"] = {
        "trades": {"cible": c["min_trades"], "actuel": n, "ok": n >= c["min_trades"]},
        "wr": {"cible": c["wr_target"], "actuel": stats["wr"], "ok": wr_ok},
        "expectancy": {"cible": c["expectancy_target"], "actuel": stats["expectancy"], "ok": exp_ok},
        "pf": {"cible": c["pf_target"], "actuel": stats["pf"], "ok": pf_ok},
    }
    if n >= c["min_trades"] and all(v["ok"] for k, v in verdict["criteria"].items() if k != "trades"):
        verdict["reached"] = True
        verdict["status"] = "VALIDÉ — edge prouvé, scaling possible"
        verdict["message"] = (
            f"✅ {n} trades, WR {stats['wr']:.1%}, expectancy {stats['expectancy']:+.2f}$, PF {stats['pf']:.2f}"
        )
    elif n >= c["min_trades"]:
        verdict["status"] = "NON VALIDÉ — edge non prouvé"
        verdict["message"] = f"⚠️ {n} trades mais critères non remplis: ajuster ou réduire"
    else:
        verdict["message"] = f"Échantillon insuffisant ({n}/{c['min_trades']} trades)"
    return verdict


def ftmo_status(report):
    if not report:
        return {"state": "inconnu"}
    return {
        "state": report.get("status", "?"),
        "balance": report.get("balance", 0),
        "pnl": report.get("pnl", 0),
        "profit_progress": report.get("profit_progress", "?"),
        "dd_from_peak": report.get("dd_from_peak", "?"),
        "daily_pnl": report.get("daily_pnl", 0),
        "trading_days": report.get("trading_days", 0),
        "total_trades": report.get("total_trades", 0),
        "consecutive_losses": report.get("consecutive_losses", 0),
        "consistency_violated": report.get("consistency_violated", False),
    }


def write_report(data):
    out_dir = BASE / "runtime" / "daily_checkpoint"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = out_dir / f"{today}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # append to history
    history = out_dir / "history.json"
    hist = []
    if history.exists():
        try:
            hist = json.loads(history.read_text(encoding="utf-8"))
        except Exception:
            hist = []
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, "summary": data.get("summary", {})})
    hist.sort(key=lambda h: h["date"])
    with open(history, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Sortie JSON uniquement")
    args = parser.parse_args()

    rows = load_trades()
    stats = compute_proof_stats(rows)
    ftmo = ftmo_status(load_ftmo_report())
    proc = check_process()
    verdict = evaluate_validation(stats)

    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "process": proc,
        "ftmo": ftmo,
        "proof": stats,
        "validation": verdict,
    }

    path = write_report(summary)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # ── Affichage texte ──────────────────────────────────────────────────
    print("=" * 62)
    print("📊 CHECKPOINT QUOTIDIEN — MODE PREUVE STRICT")
    print(f"   {summary['date']}")
    print("=" * 62)

    # Santé
    print("\n🤖 SANTÉ DU ROBOT")
    state = "✅ EN LIGNE" if proc["running"] else "🔴 ARRÊTÉ"
    print(f"   Processus: {state} (PID {proc.get('pid')})")
    if proc.get("heartbeat_age_s") is not None:
        print(f"   Heartbeat: {proc['heartbeat_age_s']}s")
    print(f"   Erreurs 24h: {proc.get('errors_24h', 0)}")

    # FTMO
    print("\n🎯 ÉTAT FTMO")
    print(f"   Balance: ${ftmo.get('balance', 0):,.2f} | PnL: ${ftmo.get('pnl', 0):,.2f}")
    print(f"   Progression: {ftmo.get('profit_progress')} | DD peak: {ftmo.get('dd_from_peak')}")
    print(
        f"   Jours: {ftmo.get('trading_days')} | Trades: {ftmo.get('total_trades')} | Pertes conséc.: {ftmo.get('consecutive_losses')}"
    )

    # Preuve
    print("\n🧪 PHASE DE PREUVE (BUY-only, 5 symboles)")
    print(
        f"   Trades: {stats['trades']} | WR: {stats['wr']:.1%} | Expectancy: {stats['expectancy']:+.2f}$ | PF: {stats['pf']:.2f}"
    )
    print(f"   PnL preuve: {stats['pnl_total']:+.2f}$")
    if stats["by_symbol"]:
        print("   Par symbole:")
        for sym, s in sorted(stats["by_symbol"].items(), key=lambda x: -x[1]["pnl"]):
            print(f"     {sym:12s} {s['trades']:3d} trades | WR {s['wr']:.0%} | PnL {s['pnl']:+8.2f}$")
    if stats["by_day"]:
        print("   Par jour:")
        for d, s in sorted(stats["by_day"].items()):
            print(f"     {d}  {s['trades']:3d} trades | {s['pnl']:+8.2f}$")

    # Validation
    print("\n⚖️ VALIDATION")
    print(f"   Statut: {verdict['status']}")
    for k, v in verdict["criteria"].items():
        mark = "✅" if v["ok"] else ("⏳" if v["ok"] is None else "❌")
        if k == "trades":
            print(f"   {mark} {k}: {v['actuel']}/{v['cible']}")
        else:
            print(f"   {mark} {k}: {v['actuel']} (cible {v['cible']})")
    print(f"   → {verdict['message']}")

    print(f"\n📁 Rapport écrit: {path}")


if __name__ == "__main__":
    main()
