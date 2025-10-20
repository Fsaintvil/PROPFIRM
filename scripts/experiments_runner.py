"""Run experiments over several horizons and cost settings.

Produces JSON reports under artifacts/experiments/<name>.json
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from itertools import product


def run_experiment(horizon: int, transaction_cost: float, slippage: float):
    base = Path.cwd()
    # train with horizon
    subprocess.run(
        ["python", "scripts/train_lightgbm.py", "--horizon", str(horizon)],
            check=True,
                )
    # backtest with cost/slippage
    # use conservative backtest params
    subprocess.run(
        [
            "python",
                "scripts/backtest_poc.py",
                    "--transaction-cost",
                    str(transaction_cost),
                    "--slippage",
                    str(slippage),
                    "--max-position-size",
                    str(0.1),
                    "--stop-loss",
                    str(0.02),
                    "--take-profit",
                    str(0.04),
                    ],
                    check=True,
                )
    # read artifacts
    metrics = json.loads(
        (base / "artifacts" / "train_metrics.json").read_text()
    )
    report = json.loads(
        (base / "artifacts" / "backtest_report.json").read_text()
    )
    return {
        "horizon": horizon,
            "transaction_cost": transaction_cost,
                "slippage": slippage,
                "train_metrics": metrics,
                "backtest": report,
                }


def main():
    base = Path.cwd()
    out_dir = base / "artifacts" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)

    horizons = [1, 5, 15]
    costs = [0.0, 0.0001]
    slippages = [0.0, 0.0002]

    results = []
    for h, c, s in product(horizons, costs, slippages):
        name = f"h{h}_c{c}_s{s}"
        print("Running", name)
        try:
            res = run_experiment(h, c, s)
        except Exception as e:
            res = {"error": str(e)}
        (out_dir / f"{name}.json").write_text(
            json.dumps(res, indent=2), encoding="utf-8"
        )
        results.append(res)

    (out_dir / "summary.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print("Experiments finished, reports in", out_dir)


if __name__ == "__main__":
    main()
