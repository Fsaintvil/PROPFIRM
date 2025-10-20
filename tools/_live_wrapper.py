"""
Wrapper to run live execution for a selected set of symbols.

This script filters `logs/analysis_recommendations.csv` to keep only the
requested symbols and then calls the main execution routine.

Usage example:
    python tools/_live_wrapper.py --token <token> --symbols EURUSD,BTCUSD
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
RECS = LOGS / "analysis_recommendations.csv"
TMP_RECS = LOGS / "analysis_recommendations_filtered.csv"


def filter_recs(symbols):
    if not RECS.exists():
        raise SystemExit("Recommendations file not found")
    # write filtered file to TMP_RECS, then swap with original
    with RECS.open("r", encoding="utf-8") as src:
        header = src.readline()
        cols = [c.strip() for c in header.split(",")]
        try:
            sym_idx = cols.index("symbol")
        except ValueError:
            raise SystemExit("recommendations CSV missing symbol column")
        with TMP_RECS.open("w", encoding="utf-8") as dst:
            dst.write(header)
            for line in src:
                parts = line.split(",")
                if parts[sym_idx] in symbols:
                    dst.write(line)


def swap_and_run(token, lots=None):
    # backup original
    stamp = __import__("datetime").datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup = RECS.with_name(f"{RECS.name}.bak_{stamp}")
    RECS.replace(backup)
    # move filtered into place
    TMP_RECS.replace(RECS)
    try:
        script_path = (
            ROOT
            / "MT5_FTMO_IA"
            / "scripts"
            / "_execute_recommendations_live.py"
        )
        cmd = [
            "python",
            str(script_path),
            "--auth-token",
            token,
        ]
        if lots:
            cmd += ["--lots", str(lots)]
        import subprocess

        subprocess.run(cmd, check=False, cwd=str(ROOT))
    finally:
        # restore original
        if RECS.exists():
            RECS.unlink()
        backup.replace(RECS)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--token", required=True)
    p.add_argument("--symbols", required=True)
    p.add_argument("--lots", type=float)
    args = p.parse_args()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    filter_recs(syms)
    # call the main module
    cmd = [
        "python",
        "-m",
        "MT5_FTMO_IA.scripts._execute_recommendations_live",
        "--auth-token",
        args.token,
    ]
    if args.lots:
        cmd += ["--lots", str(args.lots)]
    # run the command
    import subprocess

    subprocess.run(cmd, check=False)
    # cleanup
    try:
        TMP_RECS.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()
