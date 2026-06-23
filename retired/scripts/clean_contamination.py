"""Nettoie trades_log.csv des lignes backtest + vérifie performance_history.json"""
import csv
import json
from pathlib import Path
from collections import defaultdict

RUNTIME = Path(__file__).parent.parent / "runtime"

# ── 1. NETTOYAGE trades_log.csv ──
csv_path = RUNTIME / "trades_log.csv"
backup_path = RUNTIME / "trades_log.csv.contamination_bak"

if csv_path.exists():
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    print(f"Total lignes: {len(rows)}")

    # Critères backtest : prix génériques, symbols inactifs, volume irréaliste
    active_symbols = {"USDCAD", "USDCHF", "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "XAUUSD"}
    backtest_prices = {"1.1", "1.25", "150.0"}

    clean = []
    backtest_count = 0
    for r in rows:
        entry = r.get("entry_price", "")
        symbol = r.get("symbol", "")
        vol = r.get("volume", "0")

        is_backtest = (
            entry in backtest_prices
            or symbol not in active_symbols
            or vol in ("0", "0.0")
            or r.get("direction", "") == ""
        )

        if is_backtest:
            backtest_count += 1
        else:
            clean.append(r)

    print(f"Lignes backtest supprimées: {backtest_count}")
    print(f"Lignes propres conservées: {len(clean)}")

    # Backup + écriture
    if backtest_count > 0:
        import shutil
        shutil.copy2(csv_path, backup_path)
        print(f"Backup créé: {backup_path}")

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(clean)
        print(f"✅ trades_log.csv nettoyé ({len(clean)} lignes conservées)")
    else:
        print("✅ Aucune contamination détectée")
else:
    print("ℹ️ trades_log.csv introuvable")

# ── 2. VÉRIFICATION performance_history.json ──
perf_path = RUNTIME / "performance_history.json"
if perf_path.exists():
    with open(perf_path, "r") as f:
        perf = json.load(f)

    print(f"\nperformance_history.json:")
    print(f"  Keys: {list(perf.keys())[:10]}")

    # Vérifier les daily trades
    daily = perf.get("daily", {})
    total_trades = sum(d["trades"] for d in daily.values())
    print(f"  Jours: {len(daily)}, Trades totaux: {total_trades}")

    # Vérifier les rolling windows
    rolling = perf.get("rolling", {})
    for window, data in rolling.items():
        trades = data.get("trade_history", [])
        print(f"  Rolling {window}: {len(trades)} trades, "
              f"WR={data.get('win_rate', 'N/A')}, "
              f"PnL={data.get('net_profit', 'N/A')}")

    # Vérifier challenge
    challenge = perf.get("challenge", {})
    print(f"  Challenge: {challenge.get('status', 'N/A')} | "
          f"Balance=${challenge.get('balance', 0):.2f} | "
          f"DD={challenge.get('drawdown_pct', 0):.2f}%")

    # Vérifier s'il y a des trades backtest (profit irréaliste)
    all_trades = []
    for d_key in sorted(daily.keys()):
        d = daily[d_key]
        all_trades.extend(d.get("trade_history", []))

    suspicious = [t for t in all_trades if abs(t.get("pnl", 0)) > 200]
    if suspicious:
        print(f"  ⚠️  {len(suspicious)} trades suspects (|PnL| > $200):")
        for t in suspicious[:5]:
            print(f"    {t}")
    else:
        print(f"  ✅ Aucun trade suspect détecté")

print("\n✅ Nettoyage terminé")
