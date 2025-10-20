#!/usr/bin/env python3
"""
Construit un dataset cohérent 7 ans MTF:
- OHLCV 15m
- 7 indicateurs techniques sur (1D,4H,1H,30m,15m,5m) alignés à 15m
- 7 fondamentaux alignés à 15m (forward-fill)
Sortie: artifacts/datasets/<symbol>_mtf_15m.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from src.pipeline.mtf_features import build_mtf_technical
from src.pipeline.fundamentals import load_fundamentals_csv, build_7_fundamentals


def load_ohlcv_15m(symbol: str) -> pd.DataFrame:
    """Charge ou télécharge 7 ans de données 15m. Placeholder: attend un CSV local.
    Attendu: data/ohlcv/<symbol>_15m.csv avec colonnes: time,open,high,low,close,volume"""
    csv = Path(f"data/ohlcv/{symbol}_15m.csv")
    if not csv.exists():
        raise FileNotFoundError(f"Manque {csv}. Fournissez historique 7 ans 15m.")
    df = pd.read_csv(csv)
    df["time"] = pd.to_datetime(df["time"])  # assure format
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="Symbole ex: EURUSD, XAUUSD, BTCUSD")
    ap.add_argument("--out", default=None, help="Chemin sortie parquet/csv")
    ap.add_argument("--fundas", default="data/fundamentals", help="Dossier CSV fondamentaux")
    args = ap.parse_args()

    df = load_ohlcv_15m(args.symbol)
    df = df.sort_values("time")
    df = df.set_index(pd.to_datetime(df["time"]))

    tech = build_mtf_technical(df)

    fundas_map = load_fundamentals_csv(args.fundas)
    funda = build_7_fundamentals(tech.index, fundas_map)

    merged = pd.concat([df[["open", "high", "low", "close", "volume"]], tech, funda], axis=1)
    merged = merged.dropna(how="any").copy()

    out_dir = Path("artifacts/datasets")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"{args.symbol}_mtf_15m.parquet"
    if out_path.suffix.lower() in (".parquet", ".pq"):
        try:
            merged.to_parquet(out_path)
        except Exception:
            # Fallback CSV si pyarrow/fastparquet absent
            out_path = out_dir / f"{args.symbol}_mtf_15m.csv"
            merged.to_csv(out_path, index=True)
    else:
        merged.to_csv(out_path, index=True)
    print(f"✅ Dataset MTF écrit: {out_path} | shape={merged.shape}")


if __name__ == "__main__":
    main()
