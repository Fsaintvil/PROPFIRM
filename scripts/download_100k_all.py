"""
Télécharge ~100K barres H1 pour TOUS les symboles actifs (25, XAGUSD exclu).
Sauvegarde dans data/historical/ au format parquet (compatible download_historical_data.py).

Usage:
    python scripts/download_100k_all.py
    python scripts/download_100k_all.py --force          # re-télécharge tout
    python scripts/download_100k_all.py --tf H1,H4,D1     # timeframes cibles
    python scripts/download_100k_all.py --max-candles 150000

⚠️  Lecture seule MT5 — compatible avec le robot qui tourne.
    Les appels copy_rates_from_pos sont read-only et ne perturbent pas l'exécution.
"""

import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5

# 25 symboles actifs (XAGUSD exclu — désactivé 31 Juil 2026)
ACTIVE_SYMBOLS = [
    # Forex majors
    "EURUSD",
    "GBPUSD",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "USDJPY",
    # Forex crosses
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "AUDJPY",
    # Crypto
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "BNBUSD",
    # Indices
    "US500.cash",
    "US30.cash",
    "US100.cash",
    "JP225.cash",
    "GER40.cash",
    "UK100.cash",
    # Commodities
    "XAUUSD",
    "USOIL.cash",
    "UKOIL.cash",
    "NATGAS.cash",
]

TF_MAP = {
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

BATCH_SIZE = 50000
SLEEP_BETWEEN = 0.2
OUT_DIR = Path("data/historical")


def download_symbol_tf(symbol: str, tf_name: str, max_candles: int, force: bool):
    """Télécharge l'historique d'un symbole/timeframe par lots jusqu'à max_candles."""
    out_path = OUT_DIR / f"{symbol}_{tf_name}.parquet"

    # 🔧 FIX 31 Juil 2026: réutiliser uniquement si le fichier est assez récent
    # (le bug d'origine ne contrôlait que le NOMBRE de barres → les 11 paires forex
    # à 100K barres étaient réutilisées indéfiniment, figées au 4-22 juin 2026).
    if not force and out_path.exists():
        try:
            df = pd.read_parquet(out_path)
            last_ts = pd.Timestamp(df["timestamp"].max())
            age_days = (datetime.utcnow() - last_ts.to_pydatetime()).total_seconds() / 86400
            if len(df) >= max_candles * 0.9 and age_days <= 3:
                return (df, None)
            if age_days > 3:
                print(f"    ↳ périmé ({age_days:.0f}j) → re-téléchargement")
        except Exception:
            pass

    tf = TF_MAP[tf_name]
    all_rates = []
    offset = 0

    while offset < max_candles:
        try:
            rates = mt5.copy_rates_from_pos(symbol, tf, offset, BATCH_SIZE)
        except Exception as e:
            return (None, f"erreur API: {e}")
        if rates is None or len(rates) == 0:
            break
        all_rates.extend(rates)
        n = len(rates)
        offset += n
        if n < BATCH_SIZE:
            break
        time.sleep(SLEEP_BETWEEN)

    if not all_rates:
        return (None, "aucune donnée")

    result = []
    for r in all_rates:
        result.append(
            {
                "timestamp": datetime.fromtimestamp(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": int(r[5]),
                "spread": int(r[6]),
                "symbol": symbol,
            }
        )

    df = pd.DataFrame(result)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(out_path, index=False)
    return (df, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tf", type=str, default="H1", help="Timeframes séparés par virgules (défaut: H1)")
    parser.add_argument("--max-candles", type=int, default=100000)
    parser.add_argument("--symbols", type=str, default=None, help="Symboles séparés par virgules (défaut: tous actifs)")
    args = parser.parse_args()

    symbols = ACTIVE_SYMBOLS if not args.symbols else [s.strip() for s in args.symbols.split(",")]
    timeframes = [t.strip().upper() for t in args.tf.split(",")]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    acc = mt5.account_info()
    print(f"MT5 connecté: login={acc.login} balance={acc.balance}")

    summary = {}
    for symbol in symbols:
        for tf_name in timeframes:
            start = time.time()
            (df, err) = download_symbol_tf(symbol, tf_name, args.max_candles, args.force)
            elapsed = time.time() - start
            if err:
                print(f"  {symbol:>14s}_{tf_name}: ✗ {err}")
            elif df is None:
                print(f"  {symbol:>14s}_{tf_name}: ✗ échec")
            else:
                t_min = str(df["timestamp"].min())[:10]
                t_max = str(df["timestamp"].max())[:10]
                print(f"  {symbol:>14s}_{tf_name}: {len(df):>7d} barres | {t_min} -> {t_max} | {elapsed:.0f}s")
                summary[f"{symbol}_{tf_name}"] = len(df)
            time.sleep(0.1)

    mt5.shutdown()

    # Résumé
    print("\n=== RÉSUMÉ ===")
    missing = [k for k, v in summary.items() if v < args.max_candles * 0.5]
    ok = sum(1 for v in summary.values() if v >= args.max_candles * 0.5)
    print(f"Symboles/TF avec >=50% de {args.max_candles} barres: {ok}/{len(summary)}")
    if missing:
        print(f"En dessous de 50%: {missing}")


if __name__ == "__main__":
    main()
