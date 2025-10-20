"""Runner to train+backtest across all instruments found in data/.

It looks for CSV files under data/ (top-level). For each CSV it:
- copies it to data/features_sample.csv (the existing scripts expect
    that path),
        - runs the training script, then the backtest script,
            - collects the produced `artifacts/backtest_report.json` and stores it
    under `artifacts/reports/{instrument}.json`.

Finally it writes `artifacts/reports/aggregate.json` with simple
aggregates.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


def find_csvs(base: Path):
    d = base / "data"
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.suffix.lower() == ".csv"])


def run_for_csv(csv_path: Path, base: Path) -> dict:
    # copy to expected path
    target = base / "data" / "features_sample.csv"
    try:
        # avoid copying if it's the same file
        if csv_path.resolve() != target.resolve():
            shutil.copy(csv_path, target)
    except Exception:
        # best-effort: if resolving fails or any issue, attempt direct copy
        if csv_path.exists() and csv_path != target:
            shutil.copy(csv_path, target)
    # run training
    subprocess.run(["python", "scripts/train_lightgbm.py"], check=True)
    # run backtest
    subprocess.run(["python", "scripts/backtest_poc.py"], check=True)
    # read report
    rpt = base / "artifacts" / "backtest_report.json"
    if not rpt.exists():
        raise FileNotFoundError(rpt)
    return json.loads(rpt.read_text(encoding="utf-8"))


def main():
    base = Path.cwd()
    csvs = find_csvs(base)
    if not csvs:
        # fallback to the single sample file
        csvs = [base / "data" / "features_sample.csv"]

    out_dir = base / "artifacts" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_reports = {}
    for csv in csvs:
        name = csv.stem
        print(f"Processing {csv} -> instrument {name}")
        try:
            report = run_for_csv(csv, base)
        except Exception as e:
            report = {"error": str(e)}
        (out_dir / f"{name}.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        all_reports[name] = report

    # write aggregate
    (out_dir / "aggregate.json").write_text(
        json.dumps(all_reports, indent=2), encoding="utf-8"
    )
    print(f"Wrote reports to {out_dir}")


if __name__ == "__main__":
    main()
