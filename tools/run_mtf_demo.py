"""
Demo runner: load OHLCV CSV, compute MTF signals, run backtester and
write a small report.

Usage: run from repo root: `python tools/run_mtf_demo.py path/to/ohlcv.csv`
"""
import sys
import pandas as pd
from MT5_FTMO_IA.scripts.signal_mtf import generate_signals
from MT5_FTMO_IA.scripts.realtime_backtester import SimpleRealtimeBacktester


def main(path: str):
    df = pd.read_csv(path, parse_dates=True, index_col=0)
    # ensure standard columns
    for c in ["open", "high", "low", "close", "volume"]:
        if c not in df.columns:
            raise SystemExit(f"CSV must contain column: {c}")
    # Contrainte opérationnelle: convergence MTF doit se faire sur M5
    signals = generate_signals(df, higher_tf="5min")
    # run backtester: feed row-by-row merged df
    br = SimpleRealtimeBacktester()
    bars = (df.iloc[i] for i in range(len(df)))
    # run stream (backtester records trades internally)
    _ = list(br.run_stream(bars))
    report = {
        "initial_cash": 10000.0,
        "final_cash": br.cash,
        "position": br.position,
        "n_signals": int(signals["signal"].abs().sum()),
        "n_trades": len(br.trades),
        "trades": br.trades,
    }
    out_path = "mtf_demo_report.json"
    pd.Series(report).to_json(out_path)
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/run_mtf_demo.py path/to/ohlcv.csv")
        raise SystemExit(2)
    main(sys.argv[1])
