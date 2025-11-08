#!/usr/bin/env python3
"""Run simulated validation cycles across all engine symbols.
Usage: python scripts/run_simulated_cycles.py --cycles 150

This script instantiates the `LiveTradingEngine` in local simulation/fallback
mode and runs N cycles per symbol, calling the AI pipeline so that
`logs/decision_dumps.jsonl` is populated for analysis.
"""
import argparse
import time
import logging
import sys
import os

# Ensure project and scripts paths are importable when executed from project root
proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)
scripts_path = os.path.abspath(os.path.dirname(__file__))
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

from live_trading_engine import LiveTradingEngine


def main():
    parser = argparse.ArgumentParser(
        description="Run simulated cycles per symbol"
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=150,
        help="Number of cycles to run per symbol (default: 150)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="Sleep (seconds) between cycles to avoid tight loop",
    )

    args = parser.parse_args()

    engine = LiveTradingEngine()
    # Debug level to see diagnostics minimally
    engine.logger.setLevel(logging.INFO)

    print(f"Running {args.cycles} cycles per symbol for: {engine.symbols}")

    # Initialize AI systems (will write active_model.txt when applicable)
    engine.initialize_ai_systems()

    total_cycles = args.cycles * len(engine.symbols)
    seen = 0

    try:
        for i in range(args.cycles):
            for sym in engine.symbols:
                # get data (simulation if MT5 unavailable)
                data = engine.get_live_data(sym, count=200)
                if data is None:
                    # generate fallback
                    data = engine.generate_simulation_data(200)
                # trigger AI pipeline and advanced decision
                _ = engine.get_ai_signals(data, symbol=sym)

                seen += 1
                if seen % max(1, (len(engine.symbols) * 10)) == 0:
                    print(f"Progress: {seen}/{total_cycles} cycles completed")

                # small sleep to avoid flooding the system
                time.sleep(args.sleep)

        print("All cycles completed")

    except KeyboardInterrupt:
        print("Interrupted by user - stopping early")


if __name__ == '__main__':
    main()
