"""Validator for regime detector inputs.
Provides a lightweight API and CLI to check price columns and returns.
"""
import os
import json
import pandas as pd
import numpy as np
from datetime import datetime


def validate_price_dataframe(df, min_positive_rate=0.9, max_abs_return=0.5):
    """Validate dataframe for regime detection.

    Returns a dict with diagnostics and boolean 'ok'.
    """
    report = {
        "columns": {},
        "chosen_price": None,
        "ok": False,
    }

    # find candidate columns
    candidates = []
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        n = int(s.notna().sum())
        if n == 0:
            continue
        pos_rate = float((s > 0).sum()) / n
        med = float(s.median()) if n > 0 else None
        mins = float(s.min()) if n > 0 else None
        maxs = float(s.max()) if n > 0 else None
        candidates.append({
            "col": c,
            "n_numeric": n,
            "positive_rate": pos_rate,
            "median": med,
            "min": mins,
            "max": maxs,
        })
        report["columns"][c] = {
            "n_numeric": n,
            "positive_rate": pos_rate,
            "median": med,
            "min": mins,
            "max": maxs,
        }

    # prefer explicit 'close'
    price_col = None
    if "close" in df.columns:
        price_col = "close"
    else:
        close_like = [c for c in df.columns if "close" in c.lower()]
        price_col = close_like[0] if close_like else None

    # validate
    def plausible(col):
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) == 0:
            return False
        if (s > 0).sum() / len(s) < min_positive_rate:
            return False
        return True

    if price_col and plausible(price_col):
        report["chosen_price"] = price_col
        report["ok"] = True
    else:
        # try to auto-find
        found = None
        for c in df.columns:
            try:
                if plausible(c):
                    found = c
                    break
            except Exception:
                continue
        if found:
            report["chosen_price"] = found
            report["ok"] = True
        else:
            report["chosen_price"] = price_col
            report["ok"] = False

    # Check returns distribution if a price chosen
    if report["chosen_price"]:
        s = pd.to_numeric(df[report["chosen_price"]], errors="coerce").where(lambda x: x>0)
        returns = s.pct_change().dropna()
        if len(returns) > 0:
            report["returns_summary"] = {
                "count": int(len(returns)),
                "abs_gt_max": int((returns.abs() > max_abs_return).sum()),
                "max_abs": float(returns.abs().max()),
                "pct_99": float(np.nanpercentile(returns.dropna().values, 99)),
                "pct_1": float(np.nanpercentile(returns.dropna().values, 1)),
            }
            if report["returns_summary"]["abs_gt_max"] > 0:
                report["ok"] = False
        else:
            report["returns_summary"] = {"count": 0}

    return report


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--input", default="data/features_sample.csv")
    p.add_argument("--out", default="artifacts/diagnostics/regime_input_validation.json")
    p.add_argument("--min_positive_rate", type=float, default=0.9)
    p.add_argument("--max_abs_return", type=float, default=0.5)
    args = p.parse_args()

    if not os.path.exists(os.path.dirname(args.out)):
        os.makedirs(os.path.dirname(args.out), exist_ok=True)

    df = pd.read_csv(args.input)
    if "Unnamed: 0" in df.columns:
        df = df.set_index("Unnamed: 0")
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            pass

    report = validate_price_dataframe(df, min_positive_rate=args.min_positive_rate, max_abs_return=args.max_abs_return)
    report["timestamp"] = datetime.now().isoformat()

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print("Wrote validation report:", args.out)
    if not report["ok"]:
        print("INPUT INVALID: see report for details")
    else:
        print("INPUT OK")
