"""
CHECKPOINT QUOTIDIEN — Robot MOM20x3 (16 Août 2026)
====================================================
Vue d'ensemble quotidienne de l'état du robot :
  1. Santé du processus (PID, heartbeat, erreurs)
  2. État du compte (compte DÉMO — challenge FTMO perdu, 0 jour restant)
  3. 🏆 RÈGLE D'OR (13 Août 2026) : état chargé depuis runtime/golden_rule/state.json
     — la logique de calcul vit dans scripts/golden_rule.py, ce script ne la duplique PAS
  4. Historique ARCHIVÉ de l'ancienne phase de preuve (06→13 Août, 5 symboles BUY-only,
     critères de validation/scaling) — conservé pour la compatibilité des clés JSON
     (proof / validation / scaling), il ne fait plus foi depuis la RÈGLE D'OR.

Usage :
    python scripts/daily_checkpoint.py            # affiche + écrit le rapport
    python scripts/daily_checkpoint.py --json     # sortie JSON uniquement

Rapports écrits dans : runtime/daily_checkpoint/YYYY-MM-DD.json
Historique lisible dans : runtime/daily_checkpoint/history.json
"""

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))  # pour importer golden_rule

logger = logging.getLogger("daily_checkpoint")

# 🏆 RÈGLE D'OR (13 Août 2026) — import du framework de validation 100 trades
import golden_rule  # noqa: E402

# ── Constantes de l'ANCIENNE phase de preuve (ARCHIVÉE le 13/08/2026) ──────
# Conservées pour la compatibilité des clés JSON de sortie (proof/validation/
# scaling). La référence de validation est désormais la RÈGLE D'OR
# (scripts/golden_rule.py) — les symboles ci-dessous ont été retirés le 13/08.
PROOF_START = datetime(2026, 8, 6, 19, 36)  # redémarrage mode preuve strict (06 Août, ARCHIVÉ)
PROOF_SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "EURGBP", "USOIL.cash"]
VALIDATION = {
    "min_trades": 100,  # échantillon cible pour conclure
    "wr_target": 0.50,  # WR cible
    "expectancy_target": 0.0,  # expectancy > 0
    "pf_target": 1.2,  # profit factor cible
    "windows": 2,  # stabilité sur 2 fenêtres de 50
}

# ── Critère de scaling par symbole (ARCHIVÉ — phase de preuve du 06→13 Août) ─
# Un symbole devenait "éligible au scaling" quand il prouvait son edge sur un
# échantillon suffisant : PF ≥ 1.5 ET WR ≥ 50% ET ≥ 30 trades BUY-only.
# ⚠️ DEPUIS LA RÈGLE D'OR (13 Août) : AUCUN scaling avant 100 trades propres —
# cette section est conservée pour la compatibilité des clés JSON uniquement.
SCALING = {
    "min_trades": 30,  # échantillon minimum par symbole
    "pf_target": 1.5,  # profit factor cible par symbole
    "wr_target": 0.50,  # WR cible par symbole
    "priority_symbols": ["XAUUSD"],  # moteur principal — signalé en premier
    "suggested_lot": {  # lot proposé après validation (décision utilisateur)
        "XAUUSD": 0.10,
        "EURUSD": 0.08,
        "EURGBP": 0.08,
        "USDJPY": 0.05,
        "USOIL.cash": 0.05,
    },
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


def load_golden_rule_state():
    """État RÈGLE D'OR persisté par scripts/golden_rule.py.

    Source de vérité : runtime/golden_rule/state.json (calcul fait par
    golden_rule.py au checkpoint quotidien / à la demande). Ce script charge
    l'état existant au lieu de dupliquer la logique de calcul.
    Retourne {} si le fichier est absent ou illisible.
    """
    path = BASE / "runtime" / "golden_rule" / "state.json"
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
    """Stats de l'ANCIENNE phase de preuve (ARCHIVÉE) : trades BUY-only sur
    les 5 symboles, depuis le redémarrage du mode preuve (06 Août).
    Conservée pour la compatibilité des clés JSON — n'est plus une référence
    depuis la RÈGLE D'OR (13 Août)."""
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
        gw = sum(max(0, t["pnl"]) for t in sym_trades)
        gl = sum(max(0, -t["pnl"]) for t in sym_trades)
        by_symbol[sym] = {
            "trades": len(sym_trades),
            "wins": w,
            "wr": round(w / len(sym_trades), 4),
            "pnl": round(sum(t["pnl"] for t in sym_trades), 2),
            "expectancy": round(sum(t["pnl"] for t in sym_trades) / len(sym_trades), 2),
            "pf": round(gw / gl, 3) if gl > 0 else (99.9 if gw > 0 else 0.0),
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
    """Verdict de l'ANCIENNE validation de preuve (ARCHIVÉE le 13/08).

    La référence de validation est désormais la RÈGLE D'OR
    (scripts/golden_rule.py) — ce verdict est conservé pour la compatibilité
    des clés JSON de sortie uniquement."""
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


def evaluate_scaling(stats):
    """Éligibilité au scaling par symbole — ANCIEN critère (ARCHIVÉ le 13/08).

    Chaque symbole était évalué sur son propre échantillon BUY-only :
        - ≥ 30 trades (échantillon suffisant)
        - PF ≥ 1.5
        - WR ≥ 50%
    ⚠️ Depuis la RÈGLE D'OR : AUCUN scaling avant 100 trades propres
    (WR ≥ 60% ET PF ≥ 1.1 sur US100/US30/JP225/SOLUSD/BTCUSD). Cette section
    est conservée pour la compatibilité des clés JSON de sortie uniquement.
    Le checkpoint SIGNALE — la décision d'augmenter le lot appartient à
    l'utilisateur. Ne modifie jamais les lots automatiquement.
    """
    sc = SCALING
    result = {"eligible": [], "in_progress": [], "symbols": {}}

    by_symbol = stats.get("by_symbol", {})
    # Priorité : les symboles moteurs d'abord (XAUUSD), puis les autres
    ordered = [s for s in sc["priority_symbols"] if s in by_symbol]
    ordered += [s for s in PROOF_SYMBOLS if s in by_symbol and s not in ordered]

    for sym in ordered:
        s = by_symbol[sym]
        n = s["trades"]
        pf = s.get("pf", 0.0)
        wr = s.get("wr", 0.0)
        eligible = n >= sc["min_trades"] and pf >= sc["pf_target"] and wr >= sc["wr_target"]

        suggested = sc["suggested_lot"].get(sym, 0.05)
        entry = {
            "trades": n,
            "wr": wr,
            "pf": pf,
            "expectancy": s.get("expectancy", 0.0),
            "pnl": s.get("pnl", 0.0),
            "eligible": eligible,
            "suggested_lot": suggested,
            "missing_trades": max(0, sc["min_trades"] - n),
        }
        result["symbols"][sym] = entry
        if eligible:
            result["eligible"].append(sym)
        elif n > 0:
            result["in_progress"].append(sym)
    return result


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


def build_gr_stall(rows, start, symbols):
    """Détecte les symboles GR sans trade fermé depuis >24h (depuis la borne GR).
    Observationnel — n'affecte aucune décision. Retourne {"stalled": [...], "ok": [...]}."""
    now = datetime.now()
    stall_hours = 24.0
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace(" ", "T"))
    last = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row[0].replace(" ", "T"))
        except Exception:
            continue
        if ts < start:
            continue
        sym = row[1]
        if sym not in symbols:
            continue
        if sym not in last or ts > last[sym]:
            last[sym] = ts
    stalled = []
    ok = []
    for sym in symbols:
        if sym not in last:
            stalled.append({"symbol": sym, "hours_since": None, "note": "aucun trade depuis la borne GR"})
            continue
        hours = (now - last[sym]).total_seconds() / 3600
        entry = {"symbol": sym, "hours_since": round(hours, 1)}
        if hours > stall_hours:
            entry["note"] = "stalled >24h"
            stalled.append(entry)
        else:
            ok.append(entry)
    return {"threshold_h": stall_hours, "stalled": stalled, "ok": ok}


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
    scaling = evaluate_scaling(stats)

    # 🏆 RÈGLE D'OR — source de vérité runtime/golden_rule/state.json.
    # 🐛 FIX 16 Août 2026 (Rapport hebdo Optimizer): le checkpoint RECALCULE et
    # PERSISTE l'état via golden_rule.write_state() — avant, il se contentait de
    # charger state.json (qui restait figé au dernier `golden_rule.py` manuel).
    # Conséquence du bug: l'état officiel affichait 3/100 WR 100% alors que le
    # réel était 6/100 WR 50% PF 0.72 (données trompeuses pour toute décision).
    golden_state = load_golden_rule_state()
    golden_start = golden_state.get("start", golden_rule.GOLDEN_RULE_START)
    golden_symbols = golden_state.get("symbols", golden_rule.GOLDEN_SYMBOLS)
    golden_stats = golden_rule.compute_stats(rows, golden_start, golden_symbols)
    golden_verdict = golden_rule.evaluate_golden_rule(golden_stats)
    if golden_state.get("stats") != golden_stats:
        # L'état a changé → persister le recalcul (source de vérité à jour).
        # golden_rule.write_state attend le même format que golden_rule.main().
        golden_summary = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "start": golden_start,
            "symbols": golden_symbols,
            "golden_rule": golden_rule.GOLDEN_RULE,
            "stats": golden_stats,
            "verdict": golden_verdict,
        }
        try:
            golden_rule.write_state(golden_summary)
            logger.info(f"[CHECKPOINT] RÈGLE D'OR persistée: {golden_stats['trades']} trades, "
                        f"WR {golden_stats['wr']:.1%}, PF {golden_stats['pf']:.2f}")
        except Exception as e:
            logger.warning(f"[CHECKPOINT] Persistance RÈGLE D'OR échouée: {e}")

    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "process": proc,
        "ftmo": ftmo,
        "proof": stats,
        "validation": verdict,
        "scaling": scaling,
        "golden_rule": {
            "start": golden_start,
            "symbols": golden_symbols,
            "stats": golden_stats,
            "verdict": golden_verdict,
            "source": "recalcul+persistance (FIX 16 Août 2026)",
        },
        "gr_stall": build_gr_stall(rows, golden_start, golden_symbols),
    }

    path = write_report(summary)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # ── Affichage texte ──────────────────────────────────────────────────
    print("=" * 62)
    print("📊 CHECKPOINT QUOTIDIEN — 13 SYMBOLES ACTIFS + RÈGLE D'OR")
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
    print("\n🎯 ÉTAT DU COMPTE (DÉMO — challenge FTMO perdu, 0 jour restant)")
    print("   ⚠️ Les règles du challenge (consistance, jours min, profit target) ne s'appliquent plus.")
    print(f"   Balance: ${ftmo.get('balance', 0):,.2f} | PnL: ${ftmo.get('pnl', 0):,.2f}")
    print(f"   Progression: {ftmo.get('profit_progress')} | DD peak: {ftmo.get('dd_from_peak')}")
    print(
        f"   Jours: {ftmo.get('trading_days')} | Trades: {ftmo.get('total_trades')} | Pertes conséc.: {ftmo.get('consecutive_losses')}"
    )

    # Preuve (ARCHIVÉE)
    print("\n🧪 ARCHIVE — ANCIENNE PHASE DE PREUVE (BUY-only, 5 symboles, retirés le 13/08)")
    print("   ⚠️ Ne fait plus foi — la référence de validation est la RÈGLE D'OR en bas.")
    print(
        f"   Trades: {stats['trades']} | WR: {stats['wr']:.1%} | Expectancy: {stats['expectancy']:+.2f}$ | PF: {stats['pf']:.2f}"
    )
    print(f"   PnL preuve: {stats['pnl_total']:+.2f}$")
    if stats["by_symbol"]:
        print("   Par symbole:")
        for sym, s in sorted(stats["by_symbol"].items(), key=lambda x: -x[1]["pnl"]):
            pf_s = s.get("pf", 0.0)
            print(f"     {sym:12s} {s['trades']:3d} trades | WR {s['wr']:.0%} | PF {pf_s:.2f} | PnL {s['pnl']:+8.2f}$")
    if stats["by_day"]:
        print("   Par jour:")
        for d, s in sorted(stats["by_day"].items()):
            print(f"     {d}  {s['trades']:3d} trades | {s['pnl']:+8.2f}$")

    # Validation (ARCHIVÉE)
    print("\n⚖️ ARCHIVE — ANCIENNE VALIDATION DE PREUVE (obsolète, la RÈGLE D'OR fait foi)")
    print(f"   Statut: {verdict['status']}")
    for k, v in verdict["criteria"].items():
        mark = "✅" if v["ok"] else ("⏳" if v["ok"] is None else "❌")
        if k == "trades":
            print(f"   {mark} {k}: {v['actuel']}/{v['cible']}")
        else:
            print(f"   {mark} {k}: {v['actuel']} (cible {v['cible']})")
    print(f"   → {verdict['message']}")

    # Scaling par symbole (ARCHIVÉ)
    print("\n🚀 ARCHIVE — ANCIEN CRITÈRE DE SCALING (≥30 trades + PF≥1.5 + WR≥50%)")
    print("   ⚠️ Depuis la RÈGLE D'OR : AUCUN scaling avant 100 trades propres (WR≥60% + PF≥1.1).")
    if scaling["eligible"]:
        print("   🟢 ÉLIGIBLE(S) AU SCALING:")
        for sym in scaling["eligible"]:
            s = scaling["symbols"][sym]
            print(
                f"     ✅ {sym:12s} {s['trades']} trades | PF {s['pf']:.2f} | WR {s['wr']:.0%} | "
                f"lot suggéré: {s['suggested_lot']} (décision utilisateur requise)"
            )
    for sym in scaling["in_progress"]:
        s = scaling["symbols"][sym]
        pf_ok = "✅" if s["pf"] >= SCALING["pf_target"] else "⏳"
        wr_ok = "✅" if s["wr"] >= SCALING["wr_target"] else "⏳"
        n_ok = "✅" if s["trades"] >= SCALING["min_trades"] else f"⏳ {s['missing_trades']} à faire"
        print(f"     🟡 {sym:12s} {s['trades']} trades ({n_ok}) | PF {s['pf']:.2f} {pf_ok} | WR {s['wr']:.0%} {wr_ok}")
    if not scaling["eligible"] and not scaling["in_progress"]:
        print("     ⏳ Aucun trade de preuve accumulé pour l'instant — la phase démarre.")

    # 🏆 RÈGLE D'OR
    print("\n🏆 RÈGLE D'OR (100 trades propres — repositionnement INDICES/CRYPTO)")
    if golden_state:
        print("   (état chargé depuis runtime/golden_rule/state.json — calcul: scripts/golden_rule.py)")
    else:
        print("   (⚠️ state.json absent — état recalculé depuis le journal, lancez scripts/golden_rule.py)")
    gs = golden_stats
    gv = golden_verdict
    bar_len = 30
    filled = int(bar_len * min(1.0, gs["trades"] / golden_rule.GOLDEN_RULE["min_trades"]))
    bar = "█" * filled + "░" * (bar_len - filled)
    print(
        f"   Progression: {gs['trades']}/{golden_rule.GOLDEN_RULE['min_trades']} [{bar}]"
    )
    print(
        f"   WR: {gs['wr']:.1%} (cible {golden_rule.GOLDEN_RULE['wr_target']:.0%}) | "
        f"PF: {gs['pf']:.2f} (cible {golden_rule.GOLDEN_RULE['pf_target']:.2f}) | "
        f"PnL: {gs['pnl_total']:+.2f}$"
    )
    print(f"   Verdict: {gv['status']} — {gv['message']}")

    # 🔍 Surveillance GR — symboles sans trade depuis >24h (observationnel)
    gr_stall = summary.get("gr_stall", {})
    if gr_stall.get("stalled"):
        print("   🔍 GR STALL (0 trade >24h depuis la borne):")
        for s in gr_stall["stalled"]:
            if s["hours_since"] is None:
                print(f"     ⚠️ {s['symbol']:12s} AUCUN trade depuis la borne GR")
            else:
                print(f"     ⚠️ {s['symbol']:12s} {s['hours_since']:.0f}h sans trade ({s['note']})")

    print(f"\n📁 Rapport écrit: {path}")


if __name__ == "__main__":
    main()
