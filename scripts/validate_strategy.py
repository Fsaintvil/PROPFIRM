"""
Validation statistique du MOM20x3 — FIX #1 et #4 de la Haute Cour d'Audit.

Usage:
    python scripts/validate_strategy.py                     # Validation complète
    python scripts/validate_strategy.py --symbol USDCAD     # Un seul symbole
    python scripts/validate_strategy.py --walk-forward      # Walk-forward uniquement
    python scripts/validate_strategy.py --csv trades_log.csv # Depuis un fichier CSV

Ce script :
  1. Calcule le Win Rate, Profit Factor, Expectancy sur les trades réels
  2. Teste la significativité statistique (p-value du WR vs 50%)
  3. Walk-Forward Validation : divise les données en fenêtres train/test
  4. Rapport de confiance : intervalle à 95% sur le WR réel
"""
import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Constantes ──
WR_NULL_HYPOTHESIS = 0.50  # H0: le WR = 50% (pas d'edge)
Z_95 = 1.96  # Score Z pour intervalle de confiance 95%


def load_trades_from_backtest_pickle(path: str = "runtime/trades_backtest.pkl") -> list[dict]:
    """Charge les trades depuis le pickle du backtest multi-TF (format dict)."""
    try:
        import pickle
        with open(path, "rb") as f:
            raw = pickle.load(f)
    except (FileNotFoundError, pickle.UnpicklingError, OSError) as e:
        print(f"❌ Erreur chargement pickle: {e}")
        return []

    trades = []
    for sym_tf_key, trade_list in raw.items():
        for t in trade_list:
            pnl = t.get("profit_usd", 0)
            if pnl == 0:
                continue
            trades.append({
                "symbol": t.get("symbol", sym_tf_key.split("_")[0] if "_" in sym_tf_key else sym_tf_key),
                "direction": "buy" if t.get("action", "").upper() in ("BUY", "LONG") else "sell",
                "pnl": pnl,
                "reason": t.get("result", "?"),
                "ts": t.get("open_time", ""),
                "timeframe": t.get("timeframe", sym_tf_key.split("_")[1] if "_" in sym_tf_key else ""),
            })
    return trades


def load_trades_from_csv(path: str) -> list[dict]:
    """Charge les trades depuis le CSV du robot."""
    trades = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                pnl = float(row["pnl"])
                if pnl == 0:
                    continue  # ignorer les BE
                trades.append({
                    "symbol": row["symbol"],
                    "direction": row["direction"],
                    "pnl": pnl,
                    "reason": row.get("reason", "?"),
                    "ts": row.get("timestamp", ""),
                })
            except (ValueError, KeyError):
                continue
    return trades


def load_trades_from_log() -> list[dict]:
    """Charge les trades depuis le state JSON du robot."""
    state_file = Path("runtime/robot_state.json")
    if not state_file.exists():
        return []
    try:
        data = json.loads(state_file.read_text())
        return data.get("trade_history", [])
    except (json.JSONDecodeError, OSError):
        return []


def compute_metrics(trades: list[dict]) -> dict:
    """Calcule les métriques de performance avec intervalles de confiance."""
    n = len(trades)
    if n == 0:
        return {"error": "Aucun trade à analyser", "n": 0}

    wins = sum(1 for t in trades if t.get("profit", t.get("pnl", 0)) > 0)
    losses = n - wins
    total_pnl = sum(t.get("profit", t.get("pnl", 0)) for t in trades)
    gross_profit = sum(max(0, t.get("profit", t.get("pnl", 0))) for t in trades)
    gross_loss = sum(abs(min(0, t.get("profit", t.get("pnl", 0)))) for t in trades)

    wr = wins / n if n > 0 else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # ── P-value (test binomial exact approximé par normale) ──
    # H0: WR = 50%. Z-score = (WR_obs - 0.5) / sqrt(0.5 * 0.5 / n)
    if n >= 5:
        se = math.sqrt(WR_NULL_HYPOTHESIS * (1 - WR_NULL_HYPOTHESIS) / n)
        z_score = (wr - WR_NULL_HYPOTHESIS) / se if se > 0 else 0
        p_value = 2 * (1 - _norm_cdf(abs(z_score)))
    else:
        z_score = 0
        p_value = 1.0

    # ── Intervalle de confiance à 95% du WR ──
    if n >= 5:
        ci_margin = Z_95 * math.sqrt(wr * (1 - wr) / n)
        ci_lower = max(0, wr - ci_margin)
        ci_upper = min(1, wr + ci_margin)
    else:
        ci_lower, ci_upper = 0, 1

    # ── Expectancy ──
    avg_win = gross_profit / wins if wins > 0 else 0
    avg_loss = gross_loss / losses if losses > 0 else 0
    expectancy = (wr * avg_win) - ((1 - wr) * avg_loss) if losses > 0 else float("inf")

    # ── Taux de significativité ──
    if p_value < 0.001:
        significance = "*** Très hautement significatif (p<0.001)"
    elif p_value < 0.01:
        significance = "** Hautement significatif (p<0.01)"
    elif p_value < 0.05:
        significance = "* Significatif (p<0.05)"
    else:
        significance = f"ns Non significatif (p={p_value:.4f})"

    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 4),
        "win_rate_pct": f"{wr:.1%}",
        "ci_95": f"[{ci_lower:.1%}, {ci_upper:.1%}]",
        "pnl": round(total_pnl, 2),
        "profit_factor": round(pf, 2) if isinstance(pf, float) else "∞",
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "z_score": round(z_score, 4),
        "p_value": round(p_value, 4),
        "significance": significance,
        "significant_at_95": p_value < 0.05,
    }


def walk_forward_validation(trades: list[dict], n_splits: int = 5) -> dict:
    """Walk-Forward: divise les données en n_splits fenêtres train/test.
    
    Pour chaque split, les premières 80% servent de train, les 20% suivantes de test.
    Mesure la stabilité du WR entre train et test.
    """
    n = len(trades)
    if n < 50:
        return {"error": f"Échantillon trop petit pour walk-forward: {n} trades (min 50)"}

    # Trier par timestamp si disponible
    sorted_trades = sorted(
        trades,
        key=lambda t: t.get("ts", t.get("time", "")),
    )

    results = []
    window_size = n // n_splits

    for i in range(n_splits):
        test_start = i * window_size
        test_end = (i + 1) * window_size if i < n_splits - 1 else n
        train_end = test_start

        train = sorted_trades[:train_end]
        test = sorted_trades[test_start:test_end]

        if len(train) < 10 or len(test) < 5:
            continue

        train_metrics = compute_metrics(train)
        test_metrics = compute_metrics(test)

        wr_diff = abs(
            (train_metrics.get("win_rate", 0) - test_metrics.get("win_rate", 0))
        )

        results.append({
            "split": i + 1,
            "train_size": len(train),
            "test_size": len(test),
            "train_wr": train_metrics.get("win_rate_pct", "N/A"),
            "test_wr": test_metrics.get("win_rate_pct", "N/A"),
            "wr_diff": round(wr_diff, 4),
            "test_pnl": test_metrics.get("pnl", 0),
            "test_pf": test_metrics.get("profit_factor", 0),
        })

    if not results:
        return {"error": "Aucun split valide"}

    # Agrégation
    avg_wr_diff = sum(r["wr_diff"] for r in results) / len(results)
    avg_test_pf = sum(
        r.get("test_pf", 0) if isinstance(r.get("test_pf"), (int, float)) else 0
        for r in results
    ) / len(results)

    return {
        "n_splits": len(results),
        "avg_wr_diff_train_test": round(avg_wr_diff, 4),
        "avg_test_profit_factor": round(avg_test_pf, 2),
        "splits": results,
        "overfitting_risk": "HIGH" if avg_wr_diff > 0.15 else "MEDIUM" if avg_wr_diff > 0.08 else "LOW",
    }


def by_symbol_analysis(trades: list[dict]) -> dict:
    """Analyse détaillée par symbole."""
    by_sym = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", "?")
        by_sym[sym].append(t)

    results = {}
    for sym, sym_trades in sorted(by_sym.items()):
        metrics = compute_metrics(sym_trades)
        results[sym] = {
            "trades": metrics["n"],
            "win_rate": metrics.get("win_rate_pct", "N/A"),
            "pnl": metrics.get("pnl", 0),
            "pf": metrics.get("profit_factor", 0),
            "expectancy": metrics.get("expectancy", 0),
            "significant": metrics.get("significant_at_95", False),
        }
    return results


def _norm_cdf(x):
    """Approximation de la fonction de répartition normale standard."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def print_report(metrics: dict, wf: dict = None, by_sym: dict = None):
    """Affiche le rapport formaté."""
    print("=" * 70)
    print("  VALIDATION STATISTIQUE DU MOM20x3 — Haute Cour d'Audit")
    print(f"  {datetime.utcnow().strftime('%d %B %Y %H:%M UTC')}")
    print("=" * 70)

    if "error" in metrics:
        print(f"\n❌ {metrics['error']}")
        return

    m = metrics
    print(f"\n📊 MÉTRIQUES GLOBALES")
    print(f"  Trades:          {m['n']}")
    print(f"  Win Rate:        {m['win_rate_pct']}  (IC 95%: {m['ci_95']})")
    print(f"  PnL:             ${m['pnl']:+,.2f}")
    print(f"  Profit Factor:   {m['profit_factor']}")
    print(f"  Expectancy:      ${m['expectancy']:+,.2f}/trade")
    print(f"  Avg Win:         ${m['avg_win']:+,.2f}")
    print(f"  Avg Loss:        ${m['avg_loss']:+,.2f}")

    print(f"\n🧪 TEST DE SIGNIFICATIVITÉ")
    print(f"  H0: Le WR réel = 50% (pas d'edge)")
    print(f"  Z-score:         {m['z_score']:.4f}")
    print(f"  P-value:         {m['p_value']:.6f}")
    print(f"  Conclusion:      {m['significance']}")
    if m.get("significant_at_95"):
        print(f"  ✅ WR statistiquement différent de 50%")
    else:
        print(f"  ❌ Pas de preuve d'edge (p > 0.05)")

    if wf and "error" not in wf:
        print(f"\n🔄 WALK-FORWARD VALIDATION ({wf['n_splits']} splits)")
        print(f"  Différence WR train/test: {wf['avg_wr_diff_train_test']:.2%}")
        print(f"  Risque sur-apprentissage: {wf['overfitting_risk']}")
        for s in wf["splits"]:
            status = "✓" if s["wr_diff"] < 0.10 else "⚠️" if s["wr_diff"] < 0.20 else "❌"
            print(f"    Split {s['split']}: train WR={s['train_wr']} "
                  f"test WR={s['test_wr']} (diff={s['wr_diff']:.1%}) {status}")

    if by_sym:
        print(f"\n💰 PAR SYMBOLE")
        print(f"  {'Symbole':12s} {'Trades':>7s} {'WR':>8s} {'PnL':>10s} "
              f"{'PF':>6s} {'Signif.':>8s}")
        print(f"  {'-'*54}")
        for sym, data in sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True):
            sig = "✅" if data.get("significant") else "❌"
            pf_val = data['pf']
            if isinstance(pf_val, str):
                pf_str = pf_val
            else:
                pf_str = f"{pf_val:.2f}"
            print(f"  {sym:12s} {data['trades']:7d} {data['win_rate']:>8s} "
                  f"${data['pnl']:>+7.2f} {pf_str:>6s} {sig:>8s}")

    # ── Verdict ──
    print(f"\n{'='*70}")
    print(f"  VERDICT STATISTIQUE")
    if m.get("significant_at_95") and wf and wf.get("overfitting_risk") == "LOW":
        print(f"  ✅ EDGE CONFIRMÉ — WR significatif et stable hors échantillon")
    elif m.get("significant_at_95"):
        print(f"  ⚠️  EDGE POTENTIEL — WR significatif mais instable (sur-apprentissage possible)")
    else:
        print(f"  ❌ PAS D'EDGE DÉMONTRÉ — WR non significatif (p={m['p_value']:.4f} > 0.05)")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Validation statistique MOM20x3")
    parser.add_argument("--symbol", type=str, default=None, help="Filtrer par symbole")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward validation uniquement")
    parser.add_argument("--csv", type=str, default=None, help="Fichier CSV de trades")
    parser.add_argument("--backtest", action="store_true", help="Charger depuis le pickle du backtest multi-TF")
    parser.add_argument("--tf", type=str, default=None, help="Filtrer par timeframe (H1/H4/D1)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args()

    # Chargement des trades
    if args.backtest:
        trades = load_trades_from_backtest_pickle()
    elif args.csv:
        trades = load_trades_from_csv(args.csv)
    else:
        trades = load_trades_from_log()

    if not trades:
        print("❌ Aucun trade trouvé. Lancez d'abord le robot ou fournissez --csv")
        sys.exit(1)

    # Filtre par symbole
    if args.symbol:
        trades = [t for t in trades if t.get("symbol") == args.symbol]
        print(f"Filtre symbole: {args.symbol} → {len(trades)} trades")
    if args.tf:
        trades = [t for t in trades if t.get("timeframe") == args.tf]
        print(f"Filtre timeframe: {args.tf} → {len(trades)} trades")

    print(f"Analyse de {len(trades)} trades")

    # Métriques globales
    metrics = compute_metrics(trades)

    # Walk-forward
    wf = walk_forward_validation(trades) if not args.walk_forward else None

    # Par symbole
    by_sym = by_symbol_analysis(trades)

    if args.json:
        report = {"metrics": metrics, "walk_forward": wf, "by_symbol": by_sym}
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(metrics, wf, by_sym)

    # Code de sortie: 0 si edge significatif, 1 sinon
    if metrics.get("significant_at_95") and wf and wf.get("overfitting_risk") != "HIGH":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
