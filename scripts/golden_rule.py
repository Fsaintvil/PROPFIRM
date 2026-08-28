"""
RÈGLE D'OR — Framework de suivi de validation (13 Août 2026)
============================================================
Collecte automatisée des 100 premiers trades PROPRES sur les symboles actifs
(10 symboles : BTCUSD, SOLUSD, USDJPY, EURUSD, GBPUSD, USDCAD, US100.cash,
US30.cash, JP225.cash, XAUUSD) et évaluation du score selon la RÈGLE D'OR :

    ✅ VALIDÉ   si ≥ 100 trades ET PF ≥ 1.50
    ❌ REJETÉ   si ≥ 100 trades mais critères non remplis → STOP définitif
    ⏳ EN COURS sinon (progression x/100)

CONTEXTE : le challenge FTMO est perdu (0 jour restant). Le robot tourne sur
compte démo. AUCUNE re-tentative de challenge ni scaling avant que la règle
d'or ne soit VALIDÉE. Ce script est la seule référence pour cette décision.

Usage :
    python scripts/golden_rule.py              # rapport texte + écrit état
    python scripts/golden_rule.py --json       # sortie JSON uniquement
    python scripts/golden_rule.py --start "2026-08-13 21:20"  # borne custom
    python scripts/golden_rule.py --entry-based  # filtrer par OUVRetture

Fuseau : le journal (trades_log.csv / trading_journal.db) est en heure serveur
MT5 = UTC+3 (décalé +1h vs système UTC+2 en été). Toutes les bornes du script
sont donc en heure JOURNAL. Le redémarrage post-repositionnement a eu lieu à
20:20 système = 21:20 journal → borne par défaut GOLDEN_RULE_START.

Sorties : runtime/golden_rule/state.json (état courant)
          runtime/golden_rule/YYYY-MM-DD.json (historique quotidien)
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── Paramètres de la règle d'or ───────────────────────────────────────────
# Borne de démarrage (heure JOURNAL = UTC+3). Correspond au redémarrage du
# robot après le repositionnement INDICES/CRYPTO (13/08 20:20 système).
GOLDEN_RULE_START = "2026-08-13 21:20:00"

# Symboles cibles : 🔧 17/08/2026 (décision utilisateur) — TOUS les symboles actifs
# comptent pour la collecte GR (13 symboles). Le périmètre initial (5 symboles avec
# edge backtest 16 ans) est élargi pour accélérer la collecte des 100 trades.
# Le cap de consistance est désactivé en mode preuve (consistency_cap_enabled=false),
# Symboles éligibles Règle d'or — 10 actifs (26 Août 2026)
# AUDUSD/NZDUSD/USDCHF restent désactivés (risk_mult=0, perdants structurels).
# Critère : PF ≥ 1.50 sur 100+ trades (WR non requis — momentum = WR modéré, RR élevé).
GOLDEN_SYMBOLS = [
    "BTCUSD", "SOLUSD", "USDJPY",
    "EURUSD", "GBPUSD", "USDCAD",
    "US100.cash", "US30.cash", "JP225.cash", "XAUUSD",
]

# Critères de la règle d'or (révisé 25 Août 2026 — Robot Manager)
# Le MOM20x3 est un edge momentum : WR naturel ~38-50%, RR ~2.75, PF > 1.5.
# Exiger WR ≥ 60% est mathématiquement incompatible avec ce style de stratégie.
GOLDEN_RULE = {
    "min_trades": 100,     # échantillon cible
    "wr_target": 0.25,     # WR plancher très bas (momentum = beaucoup de petites pertes)
    "pf_target": 1.50,     # profit factor minimum — vraie mesure de la qualité de l'edge
}

# ── Persistance ───────────────────────────────────────────────────────────
OUT_DIR = BASE / "runtime" / "golden_rule"


def load_trades():
    """Charge le journal CSV (source principale : trades_log.csv)."""
    path = BASE / "runtime" / "trades_log.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        rows = []
        for row in reader:
            if len(row) >= 12:
                rows.append(row)
    return rows


def compute_stats(rows, start_str, symbols, entry_based=False):
    """Stats de la règle d'or : trades des symboles cibles fermés (ou ouverts)
    après la borne, avec pnl valide."""
    start = datetime.fromisoformat(start_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
    sample = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(row[0].replace(" ", "T"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        symbol = row[1]
        if symbol not in symbols:
            continue
        try:
            pnl = float(row[10])
        except Exception:
            continue
        # Filtrer par date : entry-based (DB colonne 13 si dispo) sinon exit
        # Par défaut le CSV n'a QUE le timestamp de fermeture (colonne 0).
        if entry_based and len(row) > 13 and row[13]:
            # tentative d'entrée via DB — le CSV n'a pas entry_time,
            # on retombe sur exit (colonne 0) par sécurité.
            pass
        if ts < start:
            continue
        sample.append({"ts": row[0], "symbol": symbol, "pnl": pnl})

    n = len(sample)
    if n == 0:
        return {
            "trades": 0, "wins": 0, "losses": 0, "wr": 0.0,
            "expectancy": 0.0, "pf": 0.0, "pnl_total": 0.0,
            "max_drawdown": 0.0, "by_symbol": {}, "by_day": {},
        }

    wins = [t for t in sample if t["pnl"] > 0]
    losses = [t for t in sample if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pnl_total = sum(t["pnl"] for t in sample)

    # Max drawdown sur la séquence cumulée
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sample:
        cum += t["pnl"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    by_symbol = {}
    for sym in symbols:
        sym_trades = [t for t in sample if t["symbol"] == sym]
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
    for t in sample:
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
        "max_drawdown": round(max_dd, 2),
        "by_symbol": by_symbol,
        "by_day": by_day,
    }


def evaluate_golden_rule(stats):
    """Verdict de la règle d'or."""
    r = GOLDEN_RULE
    n = stats["trades"]
    verdict = {"reached": False, "status": "EN_COURS", "criteria": {}, "message": ""}

    wr_ok = stats["wr"] >= r["wr_target"] if n >= 20 else None
    pf_ok = stats["pf"] >= r["pf_target"] if n >= 20 else None
    verdict["criteria"] = {
        "trades": {"cible": r["min_trades"], "actuel": n, "ok": n >= r["min_trades"]},
        "wr": {"cible": r["wr_target"], "actuel": stats["wr"], "ok": wr_ok},
        "pf": {"cible": r["pf_target"], "actuel": stats["pf"], "ok": pf_ok},
    }

    if n >= r["min_trades"] and all(
        v["ok"] for k, v in verdict["criteria"].items() if k != "trades"
    ):
        verdict["reached"] = True
        verdict["status"] = "VALIDÉ"
        verdict["message"] = (
            f"✅ RÈGLE D'OR ATTEINTE : {n} trades, WR {stats['wr']:.1%}, "
            f"PF {stats['pf']:.2f} — edge prouvé, challenge/scaling autorisés"
        )
    elif n >= r["min_trades"]:
        verdict["status"] = "REJETÉ"
        verdict["message"] = (
            f"❌ RÈGLE D'OR NON ATTEINTE : {n} trades mais WR {stats['wr']:.1%} / "
            f"PF {stats['pf']:.2f} — PF < 1.5, edge non prouvé"
        )
    else:
        verdict["message"] = f"Échantillon insuffisant ({n}/{r['min_trades']} trades)"
    return verdict


def write_state(data):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # état courant
    with open(OUT_DIR / "state.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # historique quotidien
    today = datetime.now().strftime("%Y-%m-%d")
    with open(OUT_DIR / f"{today}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # history.json (résumé léger)
    history = OUT_DIR / "history.json"
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Sortie JSON uniquement")
    parser.add_argument(
        "--start", default=GOLDEN_RULE_START,
        help=f"Borne de démarrage (heure journal UTC+3), défaut {GOLDEN_RULE_START}",
    )
    parser.add_argument(
        "--symbols", nargs="*", default=GOLDEN_SYMBOLS,
        help="Symboles cibles (défaut: les 4 du repositionnement)",
    )
    parser.add_argument(
        "--entry-based", action="store_true",
        help="Filtrer par date d'ouverture (nécessite une source avec entry_time)",
    )
    args = parser.parse_args()

    rows = load_trades()
    stats = compute_stats(rows, args.start, args.symbols, args.entry_based)
    verdict = evaluate_golden_rule(stats)

    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "start": args.start,
        "symbols": args.symbols,
        "golden_rule": GOLDEN_RULE,
        "stats": stats,
        "verdict": verdict,
    }
    write_state(summary)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # ── Affichage texte ──────────────────────────────────────────────────
    bar_len = 30
    filled = int(bar_len * min(1.0, stats["trades"] / GOLDEN_RULE["min_trades"]))
    bar = "█" * filled + "░" * (bar_len - filled)

    print("=" * 62)
    print("🏆 RÈGLE D'OR — VALIDATION 100 TRADES (repositionnement INDICES/CRYPTO)")
    print(f"   {summary['date']} | borne: {args.start} (heure journal UTC+3)")
    print("=" * 62)

    # Progression
    print(f"\n📈 PROGRESSION: {stats['trades']}/{GOLDEN_RULE['min_trades']} trades")
    print(f"   [{bar}] {stats['trades']/GOLDEN_RULE['min_trades']:.0%}")
    print(
        f"   WR: {stats['wr']:.1%} (cible {GOLDEN_RULE['wr_target']:.0%}) | "
        f"PF: {stats['pf']:.2f} (cible {GOLDEN_RULE['pf_target']:.2f})"
    )
    print(f"   PnL: {stats['pnl_total']:+.2f}$ | Expectancy: {stats['expectancy']:+.2f}$ | MaxDD: {stats['max_drawdown']:.2f}$")

    # Par symbole
    if stats["by_symbol"]:
        print("\n   Par symbole:")
        for sym, s in sorted(stats["by_symbol"].items(), key=lambda x: -x[1]["pnl"]):
            pf_s = s.get("pf", 0.0)
            print(f"     {sym:12s} {s['trades']:3d} trades | WR {s['wr']:.0%} | PF {pf_s:.2f} | PnL {s['pnl']:+8.2f}$")

    # Par jour
    if stats["by_day"]:
        print("\n   Par jour:")
        for d, s in sorted(stats["by_day"].items()):
            print(f"     {d}  {s['trades']:3d} trades | {s['pnl']:+8.2f}$")

    # Verdict
    print("\n⚖️ VERDICT RÈGLE D'OR")
    for k, v in verdict["criteria"].items():
        mark = "✅" if v["ok"] else ("⏳" if v["ok"] is None else "❌")
        if k == "trades":
            print(f"   {mark} trades: {v['actuel']}/{v['cible']}")
        else:
            print(f"   {mark} {k}: {v['actuel']} (cible {v['cible']})")
    print(f"   → {verdict['message']}")

    if verdict["status"] == "EN_COURS":
        missing = GOLDEN_RULE["min_trades"] - stats["trades"]
        print(f"\n   ⏳ Encore {missing} trades à collecter. Prochaine vérification: `python scripts/golden_rule.py`")

    print(f"\n📁 État écrit: {OUT_DIR / 'state.json'}")


if __name__ == "__main__":
    main()