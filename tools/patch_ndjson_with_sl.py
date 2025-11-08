"""Patch NDJSON files by applying automatic SL/TP rules using tools.sl_helpers.apply_auto_sl_tp.

Usage:
  python tools/patch_ndjson_with_sl.py --file <path> --out <outpath> --sl-method atr --sl-value 3.0

This script initializes MetaTrader5 (requires MetaTrader5 package and terminal),
applies send options to each record and writes a patched NDJSON with updated sl/tp
fields where applicable.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="input ndjson file")
    p.add_argument("--out", required=True, help="output patched ndjson file")
    p.add_argument("--sl-method", default="atr")
    p.add_argument("--sl-value", default="3.0")
    p.add_argument("--tp-ratio", default="2.0")
    args = p.parse_args()

    inp = Path(args.file)
    outp = Path(args.out)
    if not inp.exists():
        raise SystemExit(f"Input file not found: {inp}")

    try:
        import MetaTrader5 as mt5
    except Exception as exc:
        raise SystemExit("MetaTrader5 package required: pip install MetaTrader5")

    # try to initialize mt5
    if not mt5.initialize():
        last = mt5.last_error()
        raise SystemExit(f"mt5.initialize() failed: {last}")

    from tools.sl_helpers import apply_auto_sl_tp, normalize_sl_tp

    send_opts = {"SL_METHOD": args.sl_method, "SL_VALUE": args.sl_value, "TP_RATIO": args.tp_ratio}

    updated = 0
    total = 0
    outp.parent.mkdir(parents=True, exist_ok=True)
    with inp.open('r', encoding='utf-8') as fr, outp.open('w', encoding='utf-8') as fw:
        for line in fr:
            line_strip = line.strip()
            if not line_strip:
                continue
            try:
                rec = json.loads(line_strip)
            except Exception:
                # copy as-is
                fw.write(line)
                continue
            total += 1
            before = dict(rec)
            try:
                patched = apply_auto_sl_tp(rec, mt5, send_opts)
                # ensure minimal stop distance and orientation
                try:
                    patched = normalize_sl_tp(patched, mt5, adjust_if_needed=True)
                except Exception:
                    pass
            except Exception:
                patched = rec
            fw.write(json.dumps(patched, ensure_ascii=False) + "\n")
            if patched.get('sl') != before.get('sl') or patched.get('tp') != before.get('tp'):
                updated += 1

    mt5.shutdown()
    print(f"Patched {updated}/{total} records -> {outp}")


if __name__ == '__main__':
    main()
