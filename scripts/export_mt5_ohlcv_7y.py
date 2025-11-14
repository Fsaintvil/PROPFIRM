#!/usr/bin/env python3
"""
Exporte OHLCV 15 minutes sur 7 ans depuis MetaTrader5 pour une liste de symboles.
Utilise les credentials MT5 de .env via config.unified_settings.
Sortie: data/ohlcv/<SYMBOL>_15m.csv (colonnes: time,open,high,low,close,volume)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


def _connect_mt5():
    try:
        import MetaTrader5 as mt5
    except Exception as e:
        raise RuntimeError(f"MetaTrader5 indisponible: {e}")

    # Charger credentials depuis config unifiée
    from config.unified_settings import CONFIG

    # Initialiser terminal (avec path si fourni)
    if not mt5.initialize(path=CONFIG.mt5.terminal_path):
        # Essayer sans path (au cas où)
        if not mt5.initialize():
            raise RuntimeError(f"Échec initialize MT5: {mt5.last_error()}")

    login = int(CONFIG.mt5.login) if CONFIG.mt5.login else None
    pwd = CONFIG.mt5.password
    server = CONFIG.mt5.server
    if not login or not pwd or not server:
        raise RuntimeError("Variables MT5_LOGIN/MT5_PWD/MT5_SERVER manquantes")

    if not mt5.login(login=login, password=pwd, server=server):
        err = mt5.last_error()
        mt5.shutdown()
        raise RuntimeError(f"Login MT5 échoué: {err}")

    return mt5


def export_symbol_7y_15m(mt5, symbol: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / f"{symbol}_15m.csv"

    # Sélection symbole
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Impossible de sélectionner symbole: {symbol}")

    # Fenêtre 7 ans
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * 7 + 10)  # marge

    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Aucune donnée renvoyée pour {symbol}")

    df = pd.DataFrame(rates)
    # time -> datetime
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    # Uniformiser colonnes et volume
    cols_map = {
        "time": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "volume",
    }
    df = df[list(cols_map.keys())].rename(columns=cols_map).sort_values("time")
    df.to_csv(out_csv, index=False)
    return out_csv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--symbols",
        default=("BTCUSD,EURUSD,XAUUSD,USDJPY,ETHUSD,USDCAD,AUDNZD,"
                 "EURJPY,GBPCHF,NZDJPY,EURAUD,GBPUSD")
    )
    ap.add_argument("--out", default="data/ohlcv")
    ap.add_argument("--dry-run", action="store_true", help="Ne pas se connecter à MT5; créer un fichier CSV minimal pour test.")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out)

    if args.dry_run:
        # Création d'un CSV minimal sans connexion MT5 pour les tests
        out_dir.mkdir(parents=True, exist_ok=True)
        for sym in symbols:
            csv_path = out_dir / f"{sym}_15m.csv"
            # écrire un en-tête minimal
            import pandas as _pd
            df = _pd.DataFrame([{
                'time': datetime.now(timezone.utc).isoformat(),
                'open': 0.0,
                'high': 0.0,
                'low': 0.0,
                'close': 0.0,
                'volume': 0
            }])
            df.to_csv(csv_path, index=False)
            print(f"(dry-run) CSV créé: {csv_path}")
        return

    mt5 = _connect_mt5()
    try:
        for sym in symbols:
            path = export_symbol_7y_15m(mt5, sym, out_dir)
            print(f"✅ Export {sym}: {path}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
