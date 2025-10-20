#!/usr/bin/env python3
"""
Backtest 7 ans sur dataset MTF 15m généré:
- Règles simples: RSI<30 => buy, RSI>70 => sell; filtre MACD
- Coûts: fee_pct=0.0001, slippage_pct=0.0002
Sortie: KPIs et CSV des trades (optionnel)
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd


def backtest(df: pd.DataFrame, fee_pct=0.0001, slippage_pct=0.0002):
    df = df.copy()
    # Utilise indicateurs 15T par défaut
    rsi = df.get("tech_15T_rsi14")
    macd = df.get("tech_15T_macd")
    macd_sig = df.get("tech_15T_macd_signal")
    close = df["close"]

    pos = 0  # -1,0,1
    entry = 0.0
    pnl = 0.0
    wins = 0
    losses = 0
    trades = 0
    # capital initial utilisé pour le ratio de retour

    for t, price in close.items():
        if pos == 0:
            if rsi.loc[t] is not None and macd.loc[t] is not None and macd_sig.loc[t] is not None:
                if rsi.loc[t] < 30 and macd.loc[t] > macd_sig.loc[t]:
                    pos = 1
                    trades += 1
                    entry = price * (1 + fee_pct + slippage_pct)
                elif rsi.loc[t] > 70 and macd.loc[t] < macd_sig.loc[t]:
                    pos = -1
                    trades += 1
                    entry = price * (1 - fee_pct - slippage_pct)
        else:
            # Sortie sur signal inverse ou retour à neutre
            if pos == 1 and (rsi.loc[t] > 55 or macd.loc[t] < macd_sig.loc[t]):
                exit_p = price * (1 - fee_pct - slippage_pct)
                trade_pnl = exit_p - entry
                pnl += trade_pnl
                wins += 1 if trade_pnl > 0 else 0
                losses += 1 if trade_pnl <= 0 else 0
                pos = 0
            elif pos == -1 and (rsi.loc[t] < 45 or macd.loc[t] > macd_sig.loc[t]):
                exit_p = price * (1 + fee_pct + slippage_pct)
                trade_pnl = entry - exit_p
                pnl += trade_pnl
                wins += 1 if trade_pnl > 0 else 0
                losses += 1 if trade_pnl <= 0 else 0
                pos = 0

    net = pnl
    ret = net / 10000.0
    win_rate = wins / trades if trades else 0.0
    print("=== BACKTEST 7Y (baseline) ===")
    print(f"Trades: {trades}")
    print(f"Win rate: {win_rate:.2%}")
    print(f"Net PnL: {net:.2f}")
    print(f"Return: {ret:.2%}")
    return {"trades": trades, "win_rate": win_rate, "net_pnl": net, "return": ret}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", help="artifacts/datasets/<symbol>_mtf_15m.parquet|csv")
    args = ap.parse_args()
    path = Path(args.dataset)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
    backtest(df)


if __name__ == "__main__":
    main()
