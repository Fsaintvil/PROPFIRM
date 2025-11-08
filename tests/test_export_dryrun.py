import subprocess
import sys
from pathlib import Path
import pandas as pd
import pytest
 
 
def test_export_dryrun_creates_csv(tmp_path):
    script = Path("scripts/export_mt5_ohlcv_7y.py")
    if not script.exists():
        pytest.skip("Export script missing; skipping export dry-run test.")
    outdir = tmp_path / "ohlcv"
    cmd = [sys.executable, str(script), "--symbols", "BTCUSD", "--out", str(outdir), "--dry-run"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    csv = outdir / "BTCUSD_15m.csv"
    assert csv.exists(), f"Expected CSV file at {csv}"
    df = pd.read_csv(csv)
    expected = {"time", "open", "high", "low", "close", "volume"}
    assert expected.issubset(set(df.columns)), f"Missing expected columns: {expected - set(df.columns)}"
