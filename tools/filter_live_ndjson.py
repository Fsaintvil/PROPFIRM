#!/usr/bin/env python3
"""Filter an NDJSON file keeping only records recognized as live.

Usage:
    python tools/filter_live_ndjson.py --in <input.ndjson> --out <output.ndjson>

If --out is omitted, writes to artifacts/ready_for_apply/replay_only_live_<ts>.ndjson
"""
import argparse
import json
from datetime import datetime
from pathlib import Path


def is_record_live(obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("mode") == "live":
        return True
    if obj.get("source_live") is True:
        return True
    if obj.get("live") is True:
        return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--out", dest="outfile", required=False)
    args = p.parse_args()

    infile = Path(args.infile)
    if not infile.exists():
        print(f"Input file not found: {infile}")
        raise SystemExit(2)

    if args.outfile:
        outfile = Path(args.outfile)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        outfile = Path("artifacts") / "ready_for_apply" / f"replay_only_live_{ts}.ndjson"

    outfile.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    with infile.open("r", encoding="utf-8") as inf, outfile.open("w", encoding="utf-8") as outf:
        for ln in inf:
            ln = ln.strip()
            if not ln:
                continue
            total += 1
            try:
                obj = json.loads(ln)
            except Exception:
                # skip malformed lines
                continue
            if is_record_live(obj):
                outf.write(json.dumps(obj, ensure_ascii=False) + "\n")
                kept += 1

    print(f"Filtered {total} lines -> kept {kept} live records. Wrote: {outfile}")


if __name__ == "__main__":
    main()
"""
Filter NDJSON to live-only records and fill defaults.
Usage: python tools/filter_live_ndjson.py <in.ndjson> <out.ndjson> [--default-volume 0.01]
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print('Usage: python tools/filter_live_ndjson.py <in.ndjson> <out.ndjson> [--default-volume 0.01]')
        return 2
    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2])
    default_vol = 0.01
    if '--default-volume' in sys.argv:
        try:
            idx = sys.argv.index('--default-volume')
            default_vol = float(sys.argv[idx+1])
        except Exception:
            pass
    if not inp.exists():
        print(f'Input not found: {inp}')
        return 1
    outp.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    total = 0
    with inp.open('r', encoding='utf-8') as fin, outp.open('w', encoding='utf-8') as fout:
        for line in fin:
            total += 1
            line=line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            # accept if explicitly live
            source_live = j.get('source_live')
            mode = j.get('mode')
            if source_live is True or (isinstance(mode, str) and mode.lower()=='live'):
                # ensure numeric fields
                if j.get('volume') in (None, 0):
                    j['volume'] = default_vol
                # prefer numeric sl/tp/price; skip if missing price
                if j.get('price') is None:
                    continue
                # write
                fout.write(json.dumps(j, ensure_ascii=False) + '\n')
                kept += 1
    print(f'Wrote {kept} live records out of {total} from {inp} to {outp}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
