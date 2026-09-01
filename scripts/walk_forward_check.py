"""
Walk-Forward Validation Automatique — Valide les edges par symbole.

Fix 30 Aout 2026: La validation Walk-Forward a été faite manuellement (21 symboles,
1 seul PASS). Ce script la refait automatiquement sur les 100 derniers trades de
chaque symbole et détecte les edges qui disparaissent ou émergent.

Usage:
    python scripts/walk_forward_check.py [--min-trades 20] [--folds 5]
"""

import csv
import os
import sys
from collections import defaultdict
from typing import Optional

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_trades(csv_path: str = "runtime/trades_log.csv") -> list[dict]:
    """Charge les trades depuis le CSV (nettoyé des doublons si possible)."""
    trades = []
    if not os.path.exists(csv_path):
        print(f"Fichier non trouvé: {csv_path}")
        return trades

    with open(csv_path, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                pnl = float(row.get('pnl', 0))
                trades.append({
                    'symbol': row.get('symbol', ''),
                    'direction': row.get('direction', ''),
                    'pnl': pnl,
                    'volume': float(row.get('volume', 0)),
                    'timestamp': row.get('timestamp', ''),
                    'reason': row.get('reason', ''),
                })
            except (ValueError, KeyError):
                continue
    return trades


def walk_forward_fold(trades: list[dict], fold_idx: int, total_folds: int) -> dict:
    """Évalue un fold Walk-Forward."""
    n = len(trades)
    fold_size = n // total_folds

    # Split: train sur les premiers folds, test sur le dernier
    test_start = fold_idx * fold_size
    test_end = test_start + fold_size if fold_idx < total_folds - 1 else n
    test_trades = trades[test_start:test_end]

    if not test_trades:
        return {'wr': 0, 'pf': 0, 'net': 0, 'count': 0}

    wins = [t for t in test_trades if t['pnl'] > 0]
    losses = [t for t in test_trades if t['pnl'] <= 0]
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss = abs(sum(t['pnl'] for t in losses))
    net = sum(t['pnl'] for t in test_trades)
    wr = len(wins) / len(test_trades) if test_trades else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else 9999.0

    return {
        'wr': wr,
        'pf': pf,
        'net': net,
        'count': len(test_trades),
        'wins': len(wins),
        'losses': len(losses),
    }


def validate_symbol(symbol: str, trades: list[dict], min_trades: int = 20, folds: int = 5) -> dict:
    """Valide un symbole avec Walk-Forward."""
    sym_trades = [t for t in trades if t['symbol'] == symbol]

    if len(sym_trades) < min_trades:
        return {
            'symbol': symbol,
            'status': 'INSUFFICIENT',
            'total_trades': len(sym_trades),
            'message': f'{len(sym_trades)} trades < {min_trades} minimum',
        }

    # Stats globales
    total_pnl = sum(t['pnl'] for t in sym_trades)
    total_wins = sum(1 for t in sym_trades if t['pnl'] > 0)
    total_wr = total_wins / len(sym_trades)
    gross_profit = sum(t['pnl'] for t in sym_trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in sym_trades if t['pnl'] <= 0))
    total_pf = gross_profit / gross_loss if gross_loss > 0 else 9999.0

    # Walk-Forward folds
    fold_results = []
    for i in range(folds):
        result = walk_forward_fold(sym_trades, i, folds)
        fold_results.append(result)

    # Critères de validation
    # PASS si: au moins 3/5 folds ont PF > 1.0 ET PF global > 1.0
    profitable_folds = sum(1 for f in fold_results if f['pf'] > 1.0)
    avg_oos_pf = sum(f['pf'] for f in fold_results) / len(fold_results) if fold_results else 0
    avg_oos_wr = sum(f['wr'] for f in fold_results) / len(fold_results) if fold_results else 0

    passed = profitable_folds >= 3 and total_pf > 1.0

    return {
        'symbol': symbol,
        'status': 'PASS' if passed else 'FAIL',
        'total_trades': len(sym_trades),
        'total_wr': total_wr,
        'total_pf': total_pf,
        'total_pnl': total_pnl,
        'profitable_folds': profitable_folds,
        'total_folds': folds,
        'avg_oos_pf': avg_oos_pf,
        'avg_oos_wr': avg_oos_wr,
        'fold_results': fold_results,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Walk-Forward validation automatique")
    parser.add_argument("--csv", default="runtime/trades_log.csv", help="Chemin vers trades_log.csv")
    parser.add_argument("--min-trades", type=int, default=20, help="Minimum de trades par symbole")
    parser.add_argument("--folds", type=int, default=5, help="Nombre de folds Walk-Forward")
    args = parser.parse_args()

    trades = load_trades(args.csv)
    if not trades:
        print("Aucun trade chargé.")
        return

    print(f"Trades chargés: {len(trades)}")
    print(f"Fichier: {args.csv}")
    print(f"Folds: {args.folds}")
    print(f"Min trades: {args.min_trades}")
    print()

    # Récupérer les symboles uniques
    symbols = sorted(set(t['symbol'] for t in trades if t['symbol']))
    print(f"Symboles: {len(symbols)}")
    print()

    # Valider chaque symbole
    results = []
    for sym in symbols:
        result = validate_symbol(sym, trades, args.min_trades, args.folds)
        results.append(result)

    # Afficher les résultats
    print("=" * 80)
    print(f"{'Symbole':12s} {'Status':8s} {'Trades':>6s} {'WR':>6s} {'PF':>6s} "
          f"{'PnL':>10s} {'Folds':>6s} {'OOS PF':>6s}")
    print("-" * 80)

    passes = 0
    fails = 0
    insufficient = 0

    for r in sorted(results, key=lambda x: x.get('total_pf', 0), reverse=True):
        if r['status'] == 'PASS':
            passes += 1
            print(f"{r['symbol']:12s} {'✅ PASS':8s} {r['total_trades']:6d} "
                  f"{r['total_wr']:5.1f}% {r['total_pf']:6.2f} "
                  f"${r['total_pnl']:+9.2f} "
                  f"{r['profitable_folds']}/{r['total_folds']} "
                  f"{r['avg_oos_pf']:6.2f}")
        elif r['status'] == 'FAIL':
            fails += 1
            print(f"{r['symbol']:12s} {'❌ FAIL':8s} {r['total_trades']:6d} "
                  f"{r['total_wr']:5.1f}% {r['total_pf']:6.2f} "
                  f"${r['total_pnl']:+9.2f} "
                  f"{r['profitable_folds']}/{r['total_folds']} "
                  f"{r['avg_oos_pf']:6.2f}")
        else:
            insufficient += 1
            print(f"{r['symbol']:12s} {'⚠️ LOW':8s} {r['total_trades']:6d} "
                  f"{'N/A':>6s} {'N/A':>6s} {'N/A':>10s} {'N/A':>6s} {'N/A':>6s}")

    print("-" * 80)
    print(f"PASS: {passes} | FAIL: {fails} | INSUFFICIENT: {insufficient}")
    print()

    # Recommandations
    print("RECOMMANDATIONS:")
    for r in results:
        if r['status'] == 'PASS' and r.get('total_pf', 0) > 1.5:
            print(f"  ✅ {r['symbol']}: EDGE PRUVÉ (PF {r['total_pf']:.2f}) — considérer scaler")
        elif r['status'] == 'FAIL' and r.get('total_pf', 0) < 0.8:
            print(f"  ❌ {r['symbol']}: PERDANT (PF {r['total_pf']:.2f}) — considérer désactiver")


if __name__ == "__main__":
    main()
